#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L4D2 模组管理器 · 本地网页控制台
==================================

由 l4d2_conflict_checker.py --serve 启动。仅监听 127.0.0.1,只在你自己电脑上可见。

功能:
  * 扫描所有模组与冲突,网页可视化查看
  * 搜索 / 按类型筛选 / 只看有冲突的模组
  * 打开创意工坊页面、打开模组所在文件夹
  * 「隔离 / 恢复」模组(移动到 addons_disabled,不参与游戏挂载,可逆)
  * 删除模组(二次确认)

安全约定:
  * 只绑定 127.0.0.1
  * 所有移动/删除操作都做了「路径必须在 addons / addons_disabled 内」的校验
  * 隔离是可逆操作;删除才会真正移除文件
"""

import json
import os
import hashlib
import re
import secrets
import socket
import shutil
import subprocess
import sys
import time
import threading
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import l4d2_conflict_checker as core


# ---------------------------------------------------------------------------
# 状态 / 操作
# ---------------------------------------------------------------------------

class ManagerError(Exception):
    pass


class _ObjChanged(ManagerError):
    pass


class _GameRunning(ManagerError):
    pass


class Manager:
    def __init__(self, addons_dir):
        self.addons_dir = os.path.abspath(addons_dir)
        self.game_dir = os.path.dirname(self.addons_dir)
        self.disabled_dir = os.path.join(self.game_dir, "addons_disabled")
        if os.path.basename(self.game_dir).lower() == "left4dead2":
            self.trusted_root = os.path.dirname(self.game_dir)
        else:
            self.trusted_root = self.game_dir
        self.lock = threading.RLock()
        self._root_identity = core.checked_stat(self.trusted_root)
        self._addons_identity = core.checked_stat(self.addons_dir)
        self._base_cache = None
        self._device_cache = None

    # ---------------- 路径安全 ----------------
    @staticmethod
    def _link_state(path):
        """ok / reparse / missing / error。error 表示无法读取属性。"""
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return "missing"
        except OSError:
            return "error"
        if os.name == "nt":
            return "reparse" if getattr(st, "st_file_attributes", 0) & 0x400 else "ok"
        return "reparse" if os.path.islink(path) else "ok"

    def _is_reparse(self, path):
        return self._link_state(path) == "reparse"

    @staticmethod
    def _within(path, root):
        try:
            path = os.path.abspath(path)
            root = os.path.abspath(root)
            return os.path.commonpath([path, root]) == root
        except ValueError:
            return False

    def _chain_ok(self, target):
        """从可信游戏根到 target 的每一级都不可是链接/重解析点,且属性可读。"""
        root = os.path.abspath(self.trusted_root)
        target = os.path.abspath(target)
        try:
            rel = os.path.relpath(target, root)
        except ValueError:
            return False
        if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
            return False
        try:
            current = core.checked_stat(root)
            if current is None or self._root_identity is None or not os.path.samestat(current, self._root_identity):
                return False
        except OSError:
            return False
        cur = root
        if self._link_state(cur) in ("reparse", "error"):
            return False
        for part in rel.split(os.sep):
            if part in ("", "."):
                continue
            cur = os.path.join(cur, part)
            if self._link_state(cur) in ("reparse", "error"):
                return False
        return True

    def _safe_unit(self, unit, root):
        if not self._within(unit, root):
            raise ManagerError("路径校验失败")
        if os.path.normcase(os.path.abspath(unit)) == os.path.normcase(os.path.abspath(root)):
            raise ManagerError("不能操作管理根目录")
        if not self._chain_ok(unit):
            raise ManagerError("路径包含链接/重解析点或无法读取属性,已拒绝")
        return os.path.abspath(unit)

    def _tree_has_reparse(self, path):
        if self._link_state(path) != "ok":
            return True
        if not os.path.isdir(path):
            return False
        errors = []
        for root, dirs, files in os.walk(path, followlinks=False, onerror=errors.append):
            if self._is_reparse(root):
                return True
            for name in list(dirs) + list(files):
                if self._link_state(os.path.join(root, name)) != "ok":
                    return True
        return bool(errors)

    # ---------------- 发现与物理身份 ----------------
    def _move_unit(self, addon):
        if addon.kind == "workshop":
            parent = os.path.dirname(addon.location)
            if os.path.basename(parent).lower() == "workshop":
                return addon.location       # 扁平: 单个 vpk
            return parent                   # 子目录: 整个 <id> 目录
        return addon.location

    def _unit_id(self, zone, unit):
        root = self.disabled_dir if zone == "disabled" else self.addons_dir
        rel = os.path.relpath(os.path.abspath(unit), os.path.abspath(root))
        return f"{zone}:{os.path.normcase(rel)}"

    def _discover(self):
        if not self._addons_ok() or not self._chain_ok(self.disabled_dir):
            raise ManagerError("模组目录路径不安全或已变化")
        warnings = []
        enabled = core.discover_addons(self.addons_dir, warnings=warnings)
        disabled = []
        if os.path.isdir(self.disabled_dir):
            disabled = core.discover_addons(self.disabled_dir, warnings=warnings)
        return enabled, disabled, warnings

    def _all_units(self):
        enabled, disabled, warnings = self._discover()
        units = {}
        for zone, lst in (("enabled", enabled), ("disabled", disabled)):
            for a in lst:
                unit = os.path.abspath(self._move_unit(a))
                uid = self._unit_id(zone, unit)
                rec = units.setdefault(uid, {"zone": zone, "unit": unit, "addons": []})
                rec["addons"].append(a)
        return units, enabled, disabled, warnings

    def _resolve(self, unit_id, version):
        if not isinstance(unit_id, str) or ":" not in unit_id:
            raise ManagerError("无效的对象标识")
        if not isinstance(version, str) or not version:
            raise ManagerError("缺少版本标识,请刷新页面")
        units, _e, _d, _w = self._all_units()
        rec = units.get(unit_id)
        if rec is None:
            raise _ObjChanged("对象已变化或标识无效,请重新扫描")
        if self._record_version(rec) != version:
            raise _ObjChanged("对象内容已变化,请重新扫描后再操作")
        return rec

    def _record_version(self, rec):
        unit = rec["unit"]
        version = self._unit_version(unit)
        if not os.path.isfile(unit):
            return version
        archives = sorted({path for addon in rec["addons"] for package in addon.vpks
                           for path in package.archive_paths})
        if not archives:
            return version
        digest = hashlib.sha256(version.encode())
        for path in archives:
            digest.update(repr((path, self._unit_version(path))).encode("utf-8", "surrogatepass"))
        return "v:" + digest.hexdigest()

    def _device(self):
        now = time.monotonic()
        if self._device_cache is None or now - self._device_cache[0] >= 5:
            self._device_cache = (now, core.get_device_info(self.game_dir))
        return self._device_cache[1]

    def _unit_size(self, unit):
        try:
            info = core.checked_stat(unit)
            if info is None:
                return 0
            if os.path.isfile(unit):
                return info.st_size
            total = 0
            for root, dirs, files in os.walk(unit, onerror=lambda e: None):
                dirs[:] = [name for name in dirs if core._scan_safe(os.path.join(root, name), None)]
                for fn in files:
                    try:
                        info = core.checked_stat(os.path.join(root, fn))
                        if info is not None:
                            total += info.st_size
                    except OSError:
                        pass
            return total
        except OSError:
            return 0

    def _base_index(self):
        identities = []
        for path in core.base_package_paths(self.game_dir):
            try:
                identities.append((path, core.file_identity(core.checked_stat(path))))
            except OSError:
                continue
        if self._base_cache and self._base_cache[0] == identities:
            return self._base_cache[1]
        index = core.build_base_index(self.game_dir)
        self._base_cache = (identities, index)
        return index

    def _unit_version(self, unit):
        if not self._chain_ok(unit):
            raise ManagerError("对象路径不安全")
        try:
            root_info = core.checked_stat(unit)
            if root_info is None:
                raise _ObjChanged("对象已移除，请重新扫描")
            identity = core.file_identity(root_info)
            if not os.path.isdir(unit):
                return "f:" + ":".join(map(str, identity))
            digest = hashlib.sha256(repr(identity).encode())
            remaining = core.Vpk.MAX_ENTRIES

            def fail(error):
                raise error
            for root, dirs, files in os.walk(unit, followlinks=False, onerror=fail):
                remaining -= len(dirs) + len(files)
                if remaining < 0:
                    raise ManagerError("目录对象超过版本校验上限")
                if not self._chain_ok(root):
                    raise _ObjChanged("对象目录已变化，请重新扫描")
                for name in sorted(dirs + files):
                    path = os.path.join(root, name)
                    info = os.lstat(path)
                    digest.update(repr((os.path.relpath(path, unit),
                                        core.file_identity(info))).encode("utf-8", "surrogatepass"))
                dirs[:] = sorted(name for name in dirs
                                 if self._link_state(os.path.join(root, name)) == "ok")
            return "d:" + digest.hexdigest()
        except OSError as e:
            raise ManagerError(f"无法读取对象状态: {e}") from e

    def _addons_ok(self):
        if not self._chain_ok(self.addons_dir):
            return False
        try:
            info = core.checked_stat(self.addons_dir)
            return (info is not None and self._addons_identity is not None and
                    os.path.samestat(info, self._addons_identity))
        except OSError:
            return False

    @contextmanager
    def _transaction(self):
        with self.lock:
            if not self._addons_ok():
                raise ManagerError("模组目录路径不安全或已变化")
            with core.installation_lock(self.addons_dir):
                if not self._addons_ok():
                    raise ManagerError("加锁期间模组目录已变化")
                yield

    def _require_stopped(self):
        if self._game_running():
            raise _GameRunning("游戏正在运行或无法检测运行状态，请退出游戏后再操作")

    def _mkdirs(self, path):
        if not self._chain_ok(path):
            raise ManagerError("目标目录路径不安全")
        if self._link_state(path) == "missing":
            self._mkdirs(os.path.dirname(path))
            core.checked_mkdir(path, exist_ok=True)
        elif not os.path.isdir(path):
            raise ManagerError("目标不是目录")

    def _rename_unit(self, source, source_root, target, target_root, version):
        self._safe_unit(source, source_root)
        self._safe_unit(target, target_root)
        parents = (os.path.dirname(source), os.path.dirname(target))
        identities = [core.checked_stat(parent) for parent in parents]
        with core.pinned_directories(*parents):
            for parent, earlier in zip(parents, identities):
                current = core.checked_stat(parent)
                if current is None or earlier is None or not os.path.samestat(current, earlier):
                    raise _ObjChanged("提交目录已变化，请重新扫描")
            if self._link_state(target) != "missing" or self._tree_has_reparse(source):
                raise ManagerError("目标已存在或源目录包含不安全的成员")
            original = core.file_identity(core.checked_stat(source))
            if self._unit_version(source) != version:
                raise _ObjChanged("对象已变化，请重新扫描")
            self._require_stopped()
            if (self._link_state(target) != "missing" or
                    core.file_identity(core.checked_stat(source)) != original):
                raise _ObjChanged("提交对象已变化，请重新扫描")
            os.rename(source, target)

    def _desired_file(self):
        return os.path.join(self.game_dir, ".l4d2_manager_state.json")

    def _load_desired(self):
        raw = core.read_checked(self._desired_file())
        if raw is None:
            return {}
        obj = json.loads(raw)
        out = {}
        for key, value in obj.get("keys", {}).items():
            if isinstance(value, bool):
                # Migrate versions that stored persistent preferences.
                value = [value, self._key_identity(key)]
            if (isinstance(value, list) and len(value) == 2 and
                    isinstance(value[0], bool) and value[1] is not None and
                    self._key_identity(key) == value[1]):
                out[core._norm_addon_key(key)] = [key, *value]
        return out

    def _key_identity(self, key):
        path = os.path.join(self.addons_dir, key.replace("\\", os.sep))
        self._safe_unit(path, self.addons_dir)
        info = core.checked_stat(path)
        return list(core.file_identity(info)) if info is not None else None

    def _save_desired(self, desired):
        data = {"version": 2, "keys": {item[0]: item[1:] for item in desired.values()}}
        core.atomic_write(self._desired_file(), json.dumps(data, ensure_ascii=True).encode())

    def reconcile(self):
        """应用待写入意图，成功后清除；游戏内后续修改保持有效。"""
        with self._transaction():
            return 0 if self._game_running() else self._reconcile_locked()

    def _reconcile_locked(self):
        self._require_stopped()
        desired = self._load_desired()
        if not desired:
            return 0
        listed = core.load_addonlist(self.addons_dir)
        pending = [(key, enabled) for key, enabled, _version in desired.values()
                   if listed.get(core._norm_addon_key(key)) != enabled]
        if pending:
            core.set_addonlist_entries(self.addons_dir, pending,
                                       before_replace=self._require_stopped)
        self._save_desired({})
        return len(pending)

    def _has_external_archive(self, addons):
        for a in addons:
            for v in getattr(a, "vpks", []) or []:
                for e in v.entries.values():
                    if e.archive_index != core.EMBEDDED_INDEX and e.length:
                        return True
        return False

    def _game_running(self):
        try:
            return core.game_running()
        except Exception:  # noqa
            return True

    def _pending_file(self):
        return os.path.join(self.game_dir, ".l4d2_pending.json")

    def _load_pending(self):
        data = core.read_checked(self._pending_file())
        return json.loads(data) if data is not None else []

    def _save_pending(self, pending):
        core.atomic_write(self._pending_file(), json.dumps(pending).encode())

    def enqueue(self, target_action, unit_id, version):
        with self._transaction():
            if target_action not in ("disable", "delete"):
                raise ManagerError("不支持的操作")
            rec = self._resolve(unit_id, version)
            name = rec["addons"][0].display_name
            pending = [p for p in self._load_pending()
                       if p["unit_id"] != unit_id and "expired" not in p]
            pending.append({"action": target_action, "unit_id": unit_id,
                            "version": version, "name": name})
            self._save_pending(pending)
            self._spawn_apply_helper()
            return True, f"已加入待处理: {name}(共 {len(pending)} 项,游戏退出后自动执行)"

    def apply_pending(self):
        with self._transaction():
            if self._game_running():
                return 0
            pending = self._load_pending()
            done = 0
            for p in list(pending):
                if "expired" in p:
                    continue
                try:
                    action = self.disable if p["action"] == "disable" else self.delete
                    ok, message = action(p["unit_id"], p["version"])
                except _GameRunning:
                    break
                except ManagerError as error:
                    ok, message = False, str(error)
                if ok:
                    pending.remove(p)
                    done += 1
                else:
                    p["expired"] = message
                self._save_pending(pending)
            return done

    # ---------------- 状态 ----------------
    def state(self, include_scripts=False, strict=False):
        try:
            with os.scandir(self.addons_dir):
                pass
        except OSError as e:
            raise ManagerError(f"无法读取模组目录: {e}")
        if not self._addons_ok():
            raise ManagerError("模组目录或其上级包含链接/重解析点或无法读取,已拒绝访问")
        if os.path.isdir(self.disabled_dir) and not self._chain_ok(self.disabled_dir):
            raise ManagerError("隔离目录或其上级包含链接/重解析点或无法读取,已拒绝访问")

        units, enabled, disabled, warnings = self._all_units()
        try:
            removal_plan = self._removal_plan()
        except (ManagerError, OSError) as error:
            removal_plan = None
            warnings.append({"path": self.addons_dir, "error": str(error)})
        with self.lock:
            listed = core.load_addonlist(self.addons_dir)
            desired = self._load_desired()
            pending = self._load_pending()
        warnings.extend({"path": p["name"], "error": p["expired"]}
                        for p in pending if "expired" in p)
        pending = [p for p in pending if "expired" not in p]

        def unit_keys(rec):
            keys = []
            for a in rec["addons"]:
                keys.extend(core.addon_keys(a, self.addons_dir))
            return list(dict.fromkeys(keys))

        def unit_state(rec):
            if rec["zone"] == "disabled":
                return False, False
            keys = unit_keys(rec)
            if not keys:
                return True, False
            eff = []
            pending = False
            for k in keys:
                nk = core._norm_addon_key(k)
                if nk in desired:
                    val = desired[nk][1]
                    if val != listed.get(nk, True):
                        pending = True
                else:
                    val = listed.get(nk, True)
                eff.append(val)
            return any(eff), pending

        effective = {**listed, **{key: item[1] for key, item in desired.items()}}
        active = [item for addon in enabled
                  if (item := core.enabled_vpks(addon, self.addons_dir, effective)) is not None]
        stats = {}
        _, conflicts = core.analyze(
            active, ignore_scripts=not strict and not include_scripts,
            base_index=None if strict else self._base_index(), stats=stats,
            skip_identical=not strict, skip_base=not strict)
        pairs = core.build_pair_summary(conflicts)
        if stats.get("truncated") or len(pairs) > 1000:
            warnings.append({"path": self.addons_dir, "error": "冲突配对或输出达到扫描上限"})
        active_by_path = {core.physical_id(a): a for a in active}

        conflict_count = {}
        for providers in conflicts.values():
            for addon in core._dedup_addons(providers):
                uid = self._unit_id("enabled", self._move_unit(addon))
                conflict_count[uid] = conflict_count.get(uid, 0) + 1

        version_cache = {}

        def unit_ver(unit_id):
            if unit_id not in version_cache:
                version_cache[unit_id] = self._record_version(units[unit_id])
            return version_cache[unit_id]

        out_addons = []
        for unit_id, rec in units.items():
            addons = rec["addons"]
            primary = addons[0]
            paths = set()
            for a in addons:
                paths |= a.paths()
            cc = conflict_count.get(unit_id, 0)
            root = self.disabled_dir if rec["zone"] == "disabled" else self.addons_dir
            physical_disabled = rec["zone"] == "disabled"
            listed_enabled, pending_apply = unit_state(rec)
            unit_bytes = self._unit_size(rec["unit"])
            if os.path.isfile(rec["unit"]):
                archives = {p for a in addons for v in a.vpks for p in v.archive_paths}
                unit_bytes += sum(self._unit_size(p) for p in archives)
            out_addons.append({
                "unit_id": unit_id,
                "version": unit_ver(unit_id),
                "uid": primary.uid,
                "zone": rec["zone"],
                "title": primary.title,
                "display_name": primary.display_name,
                "source": primary.source,
                "kind": primary.kind,
                "workshop_id": primary.workshop_id,
                "location": primary.location,
                "unit_rel": os.path.relpath(rec["unit"], root),
                "file_count": len(paths),
                "category": core.categorize_addon(paths),
                "target": core.annotate_target(paths),
                "conflict_count": cc,
                "disabled": physical_disabled or not listed_enabled,
                "physical_disabled": physical_disabled,
                "listed_disabled": (not physical_disabled and not listed_enabled),
                "pending_apply": pending_apply,
                "size_bytes": unit_bytes,
                "keys": unit_keys(rec),
            })
        out_addons.sort(key=lambda d: (d["disabled"], d["display_name"].lower()))

        rows_by_id = {row["unit_id"]: row for row in out_addons}
        conflict_items = []
        for _k, lst in sorted(conflicts.items(), key=lambda kv: kv[0]):
            providers = []
            seen_units = set()
            for _p, a in lst:
                u = os.path.abspath(self._move_unit(a))
                cid = self._unit_id("enabled", u)
                if cid in seen_units:
                    continue
                seen_units.add(cid)
                providers.append({
                    "uid": a.uid,
                    "unit_id": cid,
                    "version": unit_ver(cid),
                    "display_name": a.display_name,
                    "workshop_id": a.workshop_id,
                    "location": a.location,
                    "target": rows_by_id[cid]["target"],
                })
            conflict_items.append({
                "path": lst[0][0],
                "category": core.categorize(lst[0][0]),
                "addons": providers,
            })

        enabled_units = [d for d in out_addons if not d["disabled"]]
        enabled_count = len(enabled_units)
        disabled_count = len(out_addons) - enabled_count
        load_size = sum(d.get("size_bytes", 0) for d in enabled_units)
        load_files = sum(d.get("file_count", 0) for d in enabled_units)
        device = self._device()

        level = "ok"
        load_warnings = []
        if load_files > 80000:
            load_warnings.append(f"模组资源 {load_files:,} 项，超过文件数经验阈值")
        if enabled_count > 80 or load_size > 4 * 1024 ** 3 or load_files > 150000:
            level = "risk"
        elif enabled_count > 40 or load_size > 2 * 1024 ** 3 or load_files > 80000:
            level = "warn"
        if device.get("ram_avail_gb") is not None and device["ram_avail_gb"] < 2:
            level = "risk"
            load_warnings.append("可用内存不足 2GB")
        if device.get("disk_free_gb") is not None and device["disk_free_gb"] < 5:
            if level == "ok":
                level = "warn"
            load_warnings.append("磁盘剩余不足 5GB")
        if enabled_count > 80:
            load_warnings.append(f"已启用 {enabled_count} 个模组,超过经验阈值")
        elif enabled_count > 40:
            load_warnings.append(f"已启用 {enabled_count} 个模组,偏多")
        if load_size > 4 * 1024 ** 3:
            load_warnings.append(f"模组总体积 {load_size / 1024 ** 3:.1f} GB,超出建议")
        elif load_size > 2 * 1024 ** 3:
            load_warnings.append(f"模组总体积 {load_size / 1024 ** 3:.1f} GB,偏大")

        health = []
        try:
            vpk_keys = list(map(core._norm_addon_key, self._all_vpk_keys(enabled)))
            listed_keys = {core._norm_addon_key(k) for k in listed}
            unlisted = [k for k in vpk_keys if k not in listed_keys]
            if unlisted:
                health.append(f"{len(unlisted)} 个 VPK 未写入 addonlist,会被游戏默认启用")
        except Exception:
            pass
        try:
            dl = os.path.join(self.game_dir, "downloads")
            if os.path.isdir(dl):
                n = len([f for f in os.listdir(dl) if f.lower().endswith(".dat")])
                if n:
                    health.append(f"服务器自定义内容 {n} 个(downloads),可能导致报错")
        except Exception:
            pass
        if os.path.isdir(os.path.join(self.game_dir, "ems")):
            health.append("存在 EMS 自定义脚本(ems)")

        return {
            "game_dir": self.game_dir,
            "addons_dir": self.addons_dir,
            "disabled_dir": self.disabled_dir,
            "addonlist_path": core.addonlist_path(self.addons_dir),
            "removal_plan": removal_plan,
            "disable_plan": self._vpk_plan(out_addons, listed, unlisted=False),
            "unlisted_plan": self._vpk_plan(out_addons, listed, unlisted=True),
            "has_disabled": os.path.isdir(self.disabled_dir),
            "addons": out_addons,
            "conflict_count": len(conflicts),
            "pairs": [
                {
                    "a": active_by_path[u1].display_name,
                    "b": active_by_path[u2].display_name,
                    "count": cnt,
                }
                for (u1, u2), cnt in sorted(pairs.items(), key=lambda x: -x[1])[:1000]
            ],
            "conflicts": conflict_items,
            "warnings": warnings,
            "incomplete": bool(warnings),
            "game_running": self._game_running(),
            "device": device,
            "load": {
                "mods": enabled_count,
                "size_gb": round(load_size / 1024 ** 3, 2),
                "files": load_files,
                "level": level,
                "warnings": load_warnings,
            },
            "health": health,
            "pending": len(pending),
            "pending_items": [{"name": p["name"], "action": p["action"]}
                              for p in pending],
            "sync_pending": sum(1 for d in out_addons if d["pending_apply"]),
            "ignored": stats,
            "include_scripts": include_scripts,
            "strict": strict,
            "summary": {
                "enabled": enabled_count,
                "disabled": disabled_count,
                "conflicts": len(conflicts),
                "pairs": len(pairs),
            },
        }

    # ---------------- 操作(全部在写锁内) ----------------
    def _spawn_apply_helper(self):
        try:
            if getattr(sys, "frozen", False):
                args = [sys.executable, "--apply-on-exit", "--game-dir", self.addons_dir]
            else:
                script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "l4d2_webapp.py")
                if not os.path.isfile(script):
                    script = os.path.abspath(sys.argv[0]) if sys.argv else ""
                args = [sys.executable, script, "--apply-on-exit",
                        "--game-dir", self.addons_dir]
            kwargs = {"close_fds": True, "stdin": subprocess.DEVNULL,
                      "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if os.name == "nt":
                kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
            else:
                kwargs["start_new_session"] = True
            if self._addons_identity is None:
                return
            args.extend(("--install-identity",
                         f"{self._addons_identity.st_dev}:{self._addons_identity.st_ino}"))
            if getattr(sys, "frozen", False):
                kwargs["env"] = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
            subprocess.Popen(args, **kwargs)
        except Exception:  # noqa
            pass

    def _unit_keys(self, rec):
        keys = []
        for a in rec["addons"]:
            keys.extend(core.addon_keys(a, self.addons_dir))
        return list(dict.fromkeys(keys))

    def _isolation_destination(self, source):
        rel = os.path.relpath(source, self.addons_dir)
        target = self._safe_unit(os.path.join(self.disabled_dir, rel), self.disabled_dir)
        if self._link_state(target) != "missing":
            raise ManagerError("隔离目录中已存在同名项")
        return target

    def disable(self, unit_id, version):
        with self._transaction():
            rec = self._resolve(unit_id, version)
            if rec["zone"] != "enabled":
                return False, "该模组已在隔离区"
            name = rec["addons"][0].display_name
            keys = self._unit_keys(rec)
            if keys:
                applied = self._set_targets(keys, False)
                return True, f"已关闭: {name}" if applied else f"已记录关闭，退出游戏后应用: {name}"

            # 松散文件夹: 物理隔离
            self._require_stopped()
            try:
                unit = self._safe_unit(rec["unit"], self.addons_dir)
                dst = self._isolation_destination(unit)
                self._mkdirs(os.path.dirname(dst))
                self._safe_unit(unit, self.addons_dir)
                if not self._chain_ok(dst):
                    return False, "隔离目录路径包含链接/重解析点或无法读取,已拒绝"
                self._rename_unit(unit, self.addons_dir, dst, self.disabled_dir, version)
                return True, f"已隔离: {name}"
            except (_GameRunning, _ObjChanged):
                raise
            except ManagerError as e:
                return False, str(e)
            except OSError as e:
                if self._game_running():
                    raise _GameRunning("游戏正在运行,松散文件被占用;请退出游戏后再操作")
                return False, f"移动失败: {e}"

    def _set_targets(self, keys, enabled):
        desired = self._load_desired()
        for key in keys:
            desired[core._norm_addon_key(key)] = [key, enabled, self._key_identity(key)]
        self._save_desired(desired)
        if self._game_running():
            self._spawn_apply_helper()
            return False
        try:
            self._reconcile_locked()
        except _GameRunning:
            self._spawn_apply_helper()
            return False
        return True

    def _all_vpk_keys(self, addons):
        return list(dict.fromkeys(key for addon in addons
                                  for key in core.addon_keys(addon, self.addons_dir)))

    @staticmethod
    def _vpk_plan(rows, listed, *, unlisted):
        plan = {}
        for row in rows:
            if row["zone"] != "enabled":
                continue
            keys = sorted(key for key in row["keys"]
                          if not unlisted or core._norm_addon_key(key) not in listed)
            if keys:
                plan[row["unit_id"]] = {"version": row["version"], "keys": keys}
        return plan

    def _disable_confirmed(self, confirmed, *, unlisted):
        with self._transaction():
            listed = core.load_addonlist(self.addons_dir)
            units, _, _, warnings = self._all_units()
            if any("扫描上限" in item["error"] or "扫描预算" in item["error"] for item in warnings):
                raise ManagerError("已达到扫描上限，无法确认全部 VPK")
            rows = [{"unit_id": unit_id, "zone": rec["zone"],
                     "version": self._record_version(rec), "keys": keys}
                    for unit_id, rec in units.items()
                    if rec["zone"] == "enabled" and (keys := self._unit_keys(rec))]
            plan = self._vpk_plan(rows, listed, unlisted=unlisted)
            if not isinstance(confirmed, dict) or confirmed != plan:
                raise _ObjChanged("禁用清单已变化，请重新扫描并确认")
            return self._disable_keys([key for item in plan.values() for key in item["keys"]])

    def disable_unlisted(self, confirmed=None):
        return self._disable_confirmed(confirmed, unlisted=True)

    def disable_all_mods(self, confirmed=None):
        return self._disable_confirmed(confirmed, unlisted=False)

    def _disable_keys(self, keys):
        if not keys:
            return True, "没有符合条件的 VPK"
        applied = self._set_targets(keys, False)
        return True, f"已禁用 {len(keys)} 个 VPK" if applied else (
            f"已记录 {len(keys)} 个 VPK，游戏退出后禁用")

    def keep_only(self, keep, versions):
        if (not isinstance(keep, str) or not isinstance(versions, dict) or
                keep not in versions or any(not isinstance(v, str) for v in versions.values())):
            raise ValueError("无效的确认清单")
        with self._transaction():
            units, _, _, _ = self._all_units()
            keys = []
            keep_keys = []
            moves = []
            for unit_id, version in versions.items():
                rec = units.get(unit_id)
                if (rec is None or rec["zone"] != "enabled" or
                        self._record_version(rec) != version):
                    raise _ObjChanged("冲突组已变化，请重新扫描")
                unit_keys = self._unit_keys(rec)
                if unit_id == keep:
                    keep_keys = unit_keys
                elif not unit_keys:
                    source = self._safe_unit(rec["unit"], self.addons_dir)
                    if self._tree_has_reparse(source):
                        raise ManagerError("组内松散模组包含不安全的目录成员")
                    moves.append((source, self._isolation_destination(source), version))
                else:
                    keys.extend(unit_keys)
            if set(map(core._norm_addon_key, keys)) & set(map(core._norm_addon_key, keep_keys)):
                raise ManagerError("组内模组共用 addonlist 键")
            if not moves:
                return self._disable_keys(keys)
            self._require_stopped()
            self._load_desired()
            core.load_addonlist(self.addons_dir)
            for _source, target, _version in moves:
                self._mkdirs(os.path.dirname(target))
            moved = []
            try:
                for source, target, version in moves:
                    self._rename_unit(source, self.addons_dir, target, self.disabled_dir, version)
                    moved.append(target)
                if keys:
                    _, message = self._disable_keys(keys)
                    return True, f"已隔离 {len(moved)} 个松散模组；{message}"
                return True, f"已隔离 {len(moved)} 个松散模组"
            except (OSError, ValueError, ManagerError) as error:
                if not moved:
                    raise
                message = f"{error}。已隔离的对象可在隔离区恢复：" + "、".join(moved)
                if isinstance(error, ManagerError):
                    raise type(error)(message) from error
                return False, message

    def _removal_plan(self):
        if not self._chain_ok(self.addons_dir):
            raise ManagerError("模组目录已变化")
        plan = {}
        with os.scandir(self.addons_dir) as entries:
            names = [entry.name for entry in entries
                     if entry.name.lower() == "workshop" or entry.name.lower().endswith(".vpk")]
        if len(names) > 5000:
            raise ManagerError("可移除项目超过一次确认的上限")
        for name in sorted(names):
            path = self._safe_unit(os.path.join(self.addons_dir, name), self.addons_dir)
            is_source = os.path.isdir if name.lower() == "workshop" else os.path.isfile
            if is_source(path):
                plan[name] = self._unit_version(path)
        return plan

    def remove_all_mods(self, confirmed=None):
        with self._transaction():
            self._require_stopped()
            plan = self._removal_plan()
            if not isinstance(confirmed, dict) or confirmed != plan:
                raise _ObjChanged("移除清单已变化，请重新扫描并确认")
            if not plan:
                return True, "没有可移除的本地模组"
            for name in plan:
                if self._tree_has_reparse(os.path.join(self.addons_dir, name)):
                    raise ManagerError("移除源目录含链接或无法完整读取")
            backup = self._safe_unit(os.path.join(self.game_dir, "_manager_backup"), self.game_dir)
            addonlist = self._safe_unit(core.addonlist_path(self.addons_dir), self.game_dir)
            state = self._safe_unit(self._desired_file(), self.game_dir)
            snapshots = {path: core.read_checked(path) for path in (addonlist, state)}
            self._load_desired()
            core._parse_addonlist(snapshots[addonlist])
            enabled, _, _ = self._discover()
            keys = self._all_vpk_keys(enabled)
            core.checked_mkdir(backup, exist_ok=True)
            dest = self._safe_unit(os.path.join(backup, "removed_" +
                                   time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(8)), backup)
            core.checked_mkdir(dest)
            payload_dir = self._safe_unit(os.path.join(dest, "files"), dest)
            core.checked_mkdir(payload_dir)
            manifest_path = self._safe_unit(os.path.join(dest, "manifest.json"), dest)
            manifest = {
                "snapshots": {path: os.path.basename(path) if data is not None else None
                              for path, data in snapshots.items()},
                "moves": [{"source": os.path.join(self.addons_dir, name),
                           "target": os.path.join(payload_dir, name),
                           "version": version, "moved": False}
                          for name, version in plan.items()],
                "configuration_updated": False,
            }

            def save_manifest():
                core.atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=True,
                                                            indent=2).encode())

            try:
                for path, data in snapshots.items():
                    if data is not None:
                        copy = self._safe_unit(os.path.join(dest, os.path.basename(path)), dest)
                        core.atomic_write(copy, data)
                save_manifest()
                if self._removal_plan() != plan:
                    raise _ObjChanged("移除清单已变化，请重新扫描并确认")
                for item in manifest["moves"]:
                    self._rename_unit(item["source"], self.addons_dir, item["target"],
                                      payload_dir, item["version"])
                    item["moved"] = True
                    save_manifest()
                # Keep these VPKs disabled if Steam subsequently downloads them again.
                if keys:
                    core.set_addonlist_entries(self.addons_dir, [(key, False) for key in keys],
                                               before_replace=self._require_stopped)
                self._save_desired(self._load_desired())
                manifest["configuration_updated"] = True
                save_manifest()
            except (OSError, ValueError, ManagerError) as error:
                msg = f"{error}，请按备份清单核对和恢复：{manifest_path}"
                if isinstance(error, ManagerError):
                    raise type(error)(msg) from error
                return False, msg
            return True, f"已移除 {len(plan)} 组本地模组，恢复清单：{manifest_path}"

    def open_subscriptions(self):
        return self._open(
            "steam://openurl/https://steamcommunity.com/my/myworkshopfiles/?appid=550&browsefilter=mysubscriptions")

    def enable(self, unit_id, version):
        with self._transaction():
            rec = self._resolve(unit_id, version)
            name = rec["addons"][0].display_name
            if rec["zone"] == "disabled":
                self._require_stopped()
                try:
                    unit = self._safe_unit(rec["unit"], self.disabled_dir)
                    if os.path.isfile(unit) and self._has_external_archive(rec["addons"]):
                        return False, "该模组包含外部分卷，请将目录文件与分卷一并恢复"
                    rel = os.path.relpath(unit, self.disabled_dir)
                    dst = os.path.join(self.addons_dir, rel)
                    if not self._chain_ok(dst):
                        return False, "目标路径包含链接/重解析点或无法读取,已拒绝"
                    if os.path.exists(dst):
                        return False, "addons 中已存在同名项"
                    self._mkdirs(os.path.dirname(dst))
                    self._safe_unit(unit, self.disabled_dir)
                    if not self._chain_ok(dst):
                        return False, "目标路径包含链接/重解析点或无法读取,已拒绝"
                    self._rename_unit(unit, self.disabled_dir, dst, self.addons_dir, version)
                except (_GameRunning, _ObjChanged):
                    raise
                except ManagerError as e:
                    return False, str(e)
                except OSError as e:
                    if self._game_running():
                        raise _GameRunning("游戏正在运行,模组文件被占用;请退出游戏后再操作")
                    return False, f"移动失败: {e}"
                keys = [os.path.relpath(os.path.join(self.addons_dir,
                        os.path.relpath(v.dir_path, self.disabled_dir)), self.addons_dir)
                        for addon in rec["addons"] for v in addon.vpks]
                if keys:
                    self._set_targets(keys, True)
                return True, f"已恢复: {name}"
            keys = self._unit_keys(rec)
            if not keys:
                return False, "松散文件夹模组不支持在游戏内启用/禁用"
            applied = self._set_targets(keys, True)
            return True, f"已启用: {name}" if applied else f"已记录启用，退出游戏后应用: {name}"

    def delete(self, unit_id, version):
        with self._transaction():
            rec = self._resolve(unit_id, version)
            self._require_stopped()
            root = self.disabled_dir if rec["zone"] == "disabled" else self.addons_dir
            unit = self._safe_unit(rec["unit"], root)
            if os.path.isfile(unit) and self._has_external_archive(rec["addons"]):
                return False, "该模组包含外部分卷,暂不支持单独删除"
            if os.path.isdir(unit) and self._tree_has_reparse(unit):
                return False, "目录内含链接/重解析点,已拒绝删除"
            if self._is_reparse(unit):
                return False, "目标是链接/重解析点,已拒绝"
            keys = self._unit_keys(rec) if rec["zone"] == "enabled" else []
            trash = os.path.join(self.game_dir, ".l4d2_trash")
            if not self._chain_ok(trash):
                return False, "待清理区路径包含链接/重解析点或无法读取,已拒绝"
            self._mkdirs(trash)
            source_info = core.checked_stat(unit)
            container = self._safe_unit(os.path.join(trash, secrets.token_hex(12)), trash)
            core.checked_mkdir(container)
            container_info = core.checked_stat(container)
            dst = os.path.join(container, os.path.basename(unit))
            try:
                self._rename_unit(unit, root, dst, container, version)
            except OSError as e:
                if self._game_running():
                    raise _GameRunning("游戏正在运行,模组文件被占用;请退出游戏后再操作")
                return False, f"删除失败: {e}"
            try:
                with core.pinned_directories(container):
                    self._safe_unit(dst, container)
                    current_container = core.checked_stat(container)
                    if current_container is None or not os.path.samestat(container_info, current_container):
                        raise OSError("待清理目录身份已变化")
                    moved_info = core.checked_stat(dst)
                    if moved_info is None or source_info is None or not os.path.samestat(source_info, moved_info):
                        raise OSError("待清理对象身份已变化")
                    if self._tree_has_reparse(dst):
                        raise OSError("待清理对象已变化")
                    if os.path.isdir(dst):
                        shutil.rmtree(dst)
                    else:
                        os.remove(dst)
                os.rmdir(container)
            except OSError as e:
                return False, f"已移出游戏目录,但清理失败: {e}(文件在 {dst})"
            if keys:
                try:
                    desired = self._load_desired()
                    self._save_desired(desired)
                    if not self._game_running():
                        listed = core.load_addonlist(self.addons_dir)
                        remove = [(key, None) for key in keys if core._norm_addon_key(key) in listed]
                        if remove:
                            core.set_addonlist_entries(self.addons_dir, remove,
                                                       before_replace=self._require_stopped)
                except (OSError, ValueError, ManagerError) as error:
                    return False, f"文件已删除，但未能更新配置：{error}"
            return True, f"已删除: {rec['addons'][0].display_name}"

    def open_folder(self, unit_id, version):
        with self.lock:
            try:
                rec = self._resolve(unit_id, version)
                root = self.disabled_dir if rec["zone"] == "disabled" else self.addons_dir
                unit = self._safe_unit(rec["unit"], root)
                folder = unit if os.path.isdir(unit) else os.path.dirname(unit)
                if not self._within(folder, root):
                    return False, "路径校验失败"
                if not os.path.isdir(folder):
                    return False, "目标文件夹不存在"
                if not self._chain_ok(folder):
                    return False, "路径包含链接/重解析点或无法读取,已拒绝"
                return self._open(folder)
            except _ObjChanged:
                raise
            except ManagerError as e:
                return False, str(e)

    def open_workshop(self, workshop_id):
        wid = str(workshop_id)
        if not re.fullmatch(r"[0-9]+", wid):
            return False, "无效的工坊 ID"
        return self._open(f"steam://url/CommunityFilePage/{wid}")

    def apply_and_launch(self):
        with self._transaction():
            self._require_stopped()
            if any("expired" not in p for p in self._load_pending()):
                return False, "还有待执行的物理操作，请先处理"
            core.load_addonlist(self.addons_dir)
            n = self._reconcile_locked()
            self._require_stopped()
            ok, msg = self._open("steam://rungameid/550")
            if not ok:
                return False, f"已应用 {n} 项,但启动游戏失败: {msg}"
            return True, f"已应用 {n} 项目标状态并请求启动游戏"

    def open_disabled(self):
        try:
            if not self._chain_ok(self.disabled_dir):
                return False, "隔离目录路径包含链接/重解析点或无法读取,已拒绝"
            self._mkdirs(self.disabled_dir)
            return self._open(self.disabled_dir)
        except OSError as e:
            return False, f"打开失败: {e}"

    def _open(self, target):
        try:
            if os.name == "nt":
                os.startfile(target)  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
            return True, "已打开"
        except Exception as e:  # noqa
            return False, f"打开失败: {e}"


# ---------------------------------------------------------------------------
# HTTP 服务
# ---------------------------------------------------------------------------

def _page_candidates():
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return [base]
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    return [
        here,
        parent,
        os.path.join(parent, "build"),
        os.getcwd(),
    ]


def load_page_html():
    for base in _page_candidates():
        path = os.path.join(base, "page.html")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            pass
    raise FileNotFoundError("找不到 page.html，请检查安装或打包资源")


class HeaderReader:
    """Budget consumed by http.client.parse_headers, including folded lines."""

    def __init__(self, stream):
        self.stream = stream
        self.remaining = 16 * 1024

    def readline(self, limit):
        if self.remaining <= 0:
            return b"x" * (limit + 1)  # Triggers the standard 431 response.
        line = self.stream.readline(min(limit, self.remaining + 1))
        self.remaining -= len(line)
        if self.remaining < 0:
            return b"x" * (limit + 1)
        return line


class Handler(BaseHTTPRequestHandler):
    def parse_request(self):
        original = self.rfile
        self.rfile = HeaderReader(original)
        try:
            return super().parse_request()
        finally:
            self.rfile = original

    server_version = "L4D2ModManager/1.1"

    def log_message(self, fmt, *args):  # 静默
        pass

    def end_headers(self):
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _host_ok(self):
        port = self.server.server_port
        return self.headers.get_all("Host") in ([f"127.0.0.1:{port}"], [f"localhost:{port}"])

    def _authorized(self, need_origin):
        tokens = self.headers.get_all("X-L4D2-Token") or []
        if (not self._host_ok() or len(tokens) != 1 or not tokens[0].isascii() or
                not secrets.compare_digest(tokens[0], self.server.token)):
            return False
        return not need_origin or self.headers.get_all("Origin") == [
            "http://" + self.headers["Host"]]

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "请求未获授权"})
        path = self.path.partition("?")[0]
        if path in ("/", "/index.html"):
            try:
                html = load_page_html().replace("%%L4D2_TOKEN%%", self.server.token, 1)
            except OSError as error:
                return self._send(500, str(error), "text/plain; charset=utf-8")
            self._send(200, html, "text/html; charset=utf-8")
        elif path == "/favicon.ico":
            self._send(204, b"")
        elif path == "/api/scan":
            if not self._authorized(False):
                return self._json(403, {"ok": False, "error": "请求未获授权"})
            if not self.server.scan_slot.acquire(blocking=False):
                return self._json(503, {"ok": False, "error": "扫描进行中，请稍后重试"})
            try:
                qs = parse_qs(self.path.partition("?")[2], max_num_fields=16)
                inc = qs.get("include_scripts", ["0"])[0] in ("1", "true", "yes")
                strict = qs.get("strict", ["0"])[0] in ("1", "true", "yes")
                self._json(200, {"ok": True,
                                 "data": self.server.manager.state(
                                     include_scripts=inc, strict=strict)})
            except ValueError as e:
                self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa
                self._json(500, {"ok": False, "error": str(e)})
            finally:
                self.server.scan_slot.release()
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "请求未获授权"})
        if self.path.partition("?")[0] != "/api/action":
            return self._json(404, {"ok": False, "error": "not found"})
        if not self._authorized(True):
            return self._json(403, {"ok": False, "error": "请求未获授权"})
        if self.headers.get_content_type() != "application/json":
            return self._json(415, {"ok": False, "error": "需要 application/json"})
        try:
            lengths = self.headers.get_all("Content-Length") or []
            if (self.headers.get_all("Transfer-Encoding") or len(lengths) != 1 or
                    re.fullmatch(r"[0-9]{1,7}", lengths[0]) is None):
                raise ValueError
            n = int(lengths[0])
        except (TypeError, ValueError):
            return self._json(400, {"ok": False, "error": "无效请求"})
        if not (0 < n <= 2 * 1024 * 1024):
            return self._json(400, {"ok": False, "error": "无效请求"})
        try:
            self.connection.settimeout(5)
            raw = self.rfile.read(n)
            if len(raw) != n:
                raise ValueError
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, OSError, RecursionError):
            return self._json(400, {"ok": False, "error": "无效请求"})

        action = payload.get("action")
        m = self.server.manager
        try:
            if action in ("disable", "enable", "delete", "open_folder"):
                unit_id = payload.get("unit_id", "")
                version = payload.get("version")
                if not isinstance(unit_id, str) or not isinstance(version, str) or not version:
                    raise ValueError
                fn = {
                    "disable": m.disable,
                    "enable": m.enable,
                    "delete": m.delete,
                    "open_folder": m.open_folder,
                }[action]
                ok, msg = fn(unit_id, version)
            elif action == "enqueue":
                unit_id = payload.get("unit_id", "")
                version = payload.get("version")
                target = payload.get("target_action")
                if (not isinstance(unit_id, str) or not isinstance(version, str)
                        or not version or target not in ("disable", "delete")):
                    raise ValueError
                ok, msg = m.enqueue(target, unit_id, version)
            elif action == "open_workshop":
                wid = payload.get("workshop_id", "")
                if not isinstance(wid, (str, int)):
                    raise ValueError
                ok, msg = m.open_workshop(str(wid))
            elif action == "disable_all":
                ok, msg = m.disable_all_mods(payload.get("confirmed"))
            elif action == "remove_all":
                ok, msg = m.remove_all_mods(payload.get("confirmed"))
            elif action == "keep_only":
                ok, msg = m.keep_only(payload.get("keep"), payload.get("versions"))
            elif action == "open_subscriptions":
                ok, msg = m.open_subscriptions()
            elif action == "disable_unlisted":
                ok, msg = m.disable_unlisted(payload.get("confirmed"))
            elif action == "apply_and_launch":
                ok, msg = m.apply_and_launch()
            elif action == "open_disabled":
                ok, msg = m.open_disabled()
            else:
                ok, msg = False, "未知操作"
        except _GameRunning as e:
            return self._json(423, {"ok": False, "code": "game_running", "error": str(e)})
        except _ObjChanged as e:
            return self._json(409, {"ok": False, "error": str(e)})
        except ManagerError as e:
            return self._json(400, {"ok": False, "error": str(e)})
        except ValueError:
            return self._json(400, {"ok": False, "error": "无效请求"})
        except Exception as e:  # noqa
            ok, msg = False, f"操作异常: {e}"

        code = 200 if ok else 400
        self._json(code, {"ok": ok, "message": msg, "error": None if ok else msg})


def apply_on_exit(addons_dir, expected_identity=None):
    """等待游戏退出后,把目标状态写回 addonlist.txt(用于管理器关闭后的守护)。"""
    manager = Manager(addons_dir)
    info = manager._addons_identity
    if info is None or (expected_identity is not None and
                        expected_identity != f"{info.st_dev}:{info.st_ino}"):
        return 2
    with core.installation_lock(addons_dir, lock_name=".l4d2_apply.lock", blocking=False) as acquired:
        if not acquired:
            return 0
        idle_since = None
        while True:
            try:
                if manager._game_running():
                    idle_since = None
                else:
                    manager.reconcile()
                    manager.apply_pending()
                    if idle_since is None:
                        idle_since = time.monotonic()
                    if time.monotonic() - idle_since > 5:
                        return 0
            except (OSError, ValueError, ManagerError):
                if not manager._addons_ok():
                    return 1
            time.sleep(0.35)



class BoundedHTTPServer(ThreadingHTTPServer):
    request_queue_size = 16

    def __init__(self, *args):
        self.slots = threading.BoundedSemaphore(16)
        self.scan_slot = threading.BoundedSemaphore(1)
        self.stop_pending = threading.Event()
        super().__init__(*args)

    def server_close(self):
        self.stop_pending.set()
        super().server_close()

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        def stop_reading():
            try:
                request.shutdown(socket.SHUT_RD)
            except OSError:
                pass

        request.settimeout(5)
        deadline = threading.Timer(10, stop_reading)
        deadline.daemon = True
        deadline.start()
        try:
            super().process_request_thread(request, address)
        finally:
            deadline.cancel()
            self.slots.release()


def create_server(addons_dir, start_port=8765, tries=30):
    """绑定一次并保留监听器;失败顺延端口。返回已配置的 httpd。"""
    httpd = None
    last_err = None
    for p in range(start_port, start_port + tries):
        try:
            httpd = BoundedHTTPServer(("127.0.0.1", p), Handler)
            break
        except OSError as e:
            last_err = e
    if httpd is None:
        raise last_err
    httpd.manager = Manager(addons_dir)
    httpd.token = secrets.token_urlsafe(32)
    httpd.origin = f"http://127.0.0.1:{httpd.server_port}"

    def _pending_loop(manager):
        while not httpd.stop_pending.wait(0.5):
            try:
                manager.reconcile()
                manager.apply_pending()
            except Exception:  # noqa
                pass

    threading.Thread(target=_pending_loop, args=(httpd.manager,), daemon=True).start()
    return httpd


def serve(addons_dir, port=8765, open_browser=True):
    httpd = create_server(addons_dir, port)
    url = httpd.origin + "/"
    print("=" * 66)
    print("  L4D2 模组管理器已启动")
    print(f"  打开浏览器访问:  {url}")
    print("  按 Ctrl+C 停止服务")
    print("=" * 66)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        try:
            httpd.shutdown()
        except Exception:  # noqa
            pass
        httpd.server_close()
