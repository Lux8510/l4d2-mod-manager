#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L4D2 模组冲突检查器  (Left 4 Dead 2 Mod Conflict Checker)
============================================================

作用
----
扫描《求生之路2》所有已安装模组(创意工坊订阅 + 手动安装),解析每个 VPK
内部的文件列表,找出「多个模组包含同一个内部文件路径」的情况。

在 L4D2 里,游戏会把所有模组挂载到同一个虚拟文件系统上;当两个模组都提供
同一路径的文件(例如同一个武器模型/材质/脚本)时,后挂载的会覆盖先挂载的,
于是其中一个模组「失效」甚至导致花屏、模型错乱、崩溃——这就是模组冲突。

本工具会把每一处冲突的「内部路径 + 涉及的模组名称/工坊ID/磁盘位置」详细列出,
方便你决定保留哪个、取消订阅哪个。

用法示例
--------
    1) 自动检测游戏并扫描:
         python l4d2_conflict_checker.py

    2) 手动指定游戏目录(任意一层都行):
         python l4d2_conflict_checker.py --game-dir "E:\\SteamLibrary\\steamapps\\common\\Left 4 Dead 2"

    3) 生成 HTML + JSON 报告到桌面:
         python l4d2_conflict_checker.py --html report.html --json report.json

    4) 扫描后自动启动游戏(检查完再进游戏):
         python l4d2_conflict_checker.py --launch

    5) 常驻监视:模组文件一有变化就重新检查(适合边订阅边排查):
         python l4d2_conflict_checker.py --watch

    6) 只看冲突文件数 >= 3 的模组对(过滤掉只有零星 1 个文件重叠的误报):
         python l4d2_conflict_checker.py --min-files 3

退出码
------
    0 = 未发现冲突
    1 = 发现冲突(方便被 .bat 判断)
    2 = 出错(找不到游戏 / 解析失败等)

依赖: 仅使用 Python 3 标准库。
"""

import argparse
import json
import os
import re
import struct
import sys
import time
from collections import defaultdict

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

VPK_MAGIC = 0x55AA1234       # 'U' 'A' 0xAA 0x34 (小端读作 0x55AA1234)
EMBEDDED_INDEX = 0x7FFF      # archive_index == 0x7FFF 表示数据就在 *_dir.vpk 里

# 这些是模组元信息文件,多个模组同时拥有是很正常的,不算冲突
META_BASENAMES = {
    "addoninfo.txt",
    "addonimage.jpg",
    "addonimage.jpeg",
    "addonimage.png",
    "addonimage.tga",
}

# 文件类型 -> 中文说明(用于给冲突分级,越靠前的越需要关注)
CATEGORY_RULES = [
    ("scripts/", "脚本 (可能影响玩法/稳定性,优先排查)"),
    ("missions/", "任务/关卡脚本"),
    ("maps/", "地图"),
    ("resource/", "界面/UI 资源"),
    ("materials/", "材质"),
    ("models/", "模型"),
    ("sound/", "声音"),
    ("particles/", "粒子特效"),
    ("scenes/", "场景"),
    ("anims/", "动画"),
    ("expressions/", "表情"),
    ("cfg/", "配置"),
    ("media/", "媒体(图标等)"),
]


# ---------------------------------------------------------------------------
# VPK 读取
# ---------------------------------------------------------------------------

class VpkError(Exception):
    pass


class VpkEntry:
    __slots__ = ("crc", "preload", "archive_index", "offset", "length")

    def __init__(self, crc, preload, archive_index, offset, length):
        self.crc = crc
        self.preload = preload
        self.archive_index = archive_index
        self.offset = offset
        self.length = length


class Vpk:
    """最小可用的 VPK 读取器:只解析目录树,并可按需提取单个文件。"""

    def __init__(self, dir_path):
        self.dir_path = dir_path
        self.version = None
        self.header_size = None
        self.tree_size = None
        self.entries = {}          # 内部路径(原样大小写) -> VpkEntry
        self._lower_index = {}      # 小写路径 -> 原样路径
        self.file_data_section_size = None
        self.file_size = 0
        self._parse()

    # -- 内部工具 -----------------------------------------------------------
    def _is_archive_part(self):
        """名称形如 xxx_000.vpk 的是数据分卷,没有目录树。"""
        base = os.path.basename(self.dir_path).lower()
        stem = base[:-4] if base.endswith(".vpk") else base
        return bool(re.search(r"_\d{3}$", stem))

    MAX_TREE = 64 * 1024 * 1024

    def _parse(self):
        try:
            f = open(self.dir_path, "rb")
        except OSError as e:
            raise VpkError(f"无法打开文件: {e}")
        with f:
            file_size = os.fstat(f.fileno()).st_size
            head = f.read(12)
            if len(head) < 12:
                raise VpkError("文件太小,不是有效的 VPK")
            magic, version, tree_size = struct.unpack("<III", head)
            if magic != VPK_MAGIC:
                raise VpkError("Magic 不匹配,不是 VPK 目录文件")
            if self._is_archive_part():
                raise VpkError("这是数据分卷(_NNN.vpk),不含目录树")
            data_size = None
            if version == 1:
                header_size = 12
            elif version == 2:
                extra = f.read(16)
                if len(extra) < 16:
                    raise VpkError("VPK v2 头部不完整")
                data_size, _amd5, _omd5, _sig = struct.unpack("<IIII", extra)
                header_size = 28
            else:
                raise VpkError(f"不支持的 VPK 版本: {version}")

            if tree_size > self.MAX_TREE:
                raise VpkError("目录树过大")
            if header_size + tree_size > file_size:
                raise VpkError("目录树超出文件范围")

            self.version = version
            self.tree_size = tree_size
            self.header_size = header_size
            self.file_data_section_size = data_size
            self.file_size = file_size

            f.seek(header_size)
            tree = f.read(tree_size)
            if len(tree) != tree_size:
                raise VpkError("目录树被截断,文件可能损坏")
            self._parse_tree(tree)

    def _parse_tree(self, tree):
        pos = 0
        n = len(tree)

        def read_cstr(what):
            nonlocal pos
            if pos >= n:
                raise VpkError(f"目录树在读取{what}时意外结束")
            end = tree.find(b"\x00", pos)
            if end < 0:
                raise VpkError(f"目录字符串缺少结束符({what})")
            raw = tree[pos:end]
            pos = end + 1
            return raw

        while True:
            ext = read_cstr("扩展名")
            if ext == b"":
                break
            ext_s = ext.decode("utf-8", "replace")
            while True:
                path = read_cstr("路径")
                if path == b"":
                    break
                path_s = path.decode("utf-8", "replace")
                # VPK 用单个空格 " " 表示根目录("" 是路径列表结束符)
                if path_s == " ":
                    path_s = ""
                while True:
                    name = read_cstr("文件名")
                    if name == b"":
                        break
                    name_s = name.decode("utf-8", "replace")
                    if pos + 18 > n:
                        raise VpkError("目录树损坏(条目越界)")
                    crc, preload, arch_idx, off, length, term = struct.unpack_from(
                        "<IHHIIH", tree, pos)
                    pos += 18
                    if term != 0xFFFF:
                        raise VpkError("非法条目终止符")
                    if preload > n - pos:
                        raise VpkError("preload 越界")
                    preload_data = tree[pos:pos + preload]
                    pos += preload
                    if ext_s == " ":
                        full = (path_s + "/" if path_s else "") + name_s
                    else:
                        full = (path_s + "/" if path_s else "") + name_s + "." + ext_s
                    self.entries[full] = VpkEntry(
                        crc, preload_data, arch_idx, off, length)
                    self._lower_index[full.lower()] = full

    # -- 提取 ---------------------------------------------------------------
    def _data_source(self, entry):
        base = os.path.basename(self.dir_path)
        folder = os.path.dirname(self.dir_path)
        if entry.archive_index == EMBEDDED_INDEX:
            return self.dir_path, self.header_size + self.tree_size + entry.offset
        stem = base
        if stem.lower().endswith("_dir.vpk"):
            stem = stem[:-8]
        elif stem.lower().endswith(".vpk"):
            stem = stem[:-4]
        archive = os.path.join(folder, f"{stem}_{entry.archive_index:03d}.vpk")
        return archive, entry.offset

    def extract(self, fullpath):
        key = fullpath if fullpath in self.entries else self._lower_index.get(fullpath.lower())
        if key is None:
            raise VpkError(f"VPK 中不存在文件: {fullpath}")
        entry = self.entries[key]
        if entry.length == 0:
            return entry.preload
        src, offset = self._data_source(entry)
        try:
            with open(src, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                if entry.archive_index == EMBEDDED_INDEX:
                    data_start = self.header_size + self.tree_size
                    if self.version == 2 and self.file_data_section_size is not None:
                        data_end = data_start + self.file_data_section_size
                    else:
                        data_end = size
                    if offset < data_start or offset > data_end \
                            or entry.length > data_end - offset:
                        raise VpkError("条目超出数据段")
                else:
                    if offset > size or entry.length > size - offset:
                        raise VpkError("条目超出分卷范围")
                f.seek(offset)
                data = f.read(entry.length)
        except OSError as e:
            raise VpkError(f"读取数据失败 ({src}): {e}")
        if len(data) != entry.length:
            raise VpkError("条目正文被截断")
        return entry.preload + data

    def find(self, name_lower):
        return self._lower_index.get(name_lower)


# ---------------------------------------------------------------------------
# 模组模型
# ---------------------------------------------------------------------------

class Addon:
    def __init__(self, uid, source, kind, location, vpk=None, loose_paths=None,
                 title=None):
        self.uid = uid                # 唯一标识, 例如 workshop:123456
        self.source = source          # '创意工坊' / '手动安装' / '松散文件夹'
        self.kind = kind              # 'workshop' / 'manual' / 'loose'
        self.location = location      # VPK 文件路径或文件夹路径
        self.vpk = vpk
        self.vpks = [vpk] if vpk is not None else []
        self.loose_paths = loose_paths or set()
        self.workshop_id = None
        self.title = title or ""
        if kind == "workshop" and uid.startswith("workshop:"):
            wid = uid.split(":", 1)[1]
            if wid.isdigit():
                self.workshop_id = wid

    @property
    def display_name(self):
        name = self.title.strip()
        if not name:
            if self.workshop_id:
                name = f"未命名模组 (工坊ID {self.workshop_id})"
            else:
                name = os.path.basename(self.location)
        if self.workshop_id:
            return f"{name}  [工坊ID {self.workshop_id}]"
        return name

    def paths(self):
        if self.vpks:
            out = set()
            for v in self.vpks:
                out.update(v.entries.keys())
            return out
        return set(self.loose_paths)


def read_addon_title(vpk):
    """从 VPK 内 addoninfo.txt 读取 addontitle(有大小上限)。"""
    key = vpk.find("addoninfo.txt")
    if not key:
        return None
    entry = vpk.entries.get(key)
    if entry is not None and len(entry.preload) + entry.length > 1_000_000:
        return None
    try:
        raw = vpk.extract(key)
    except VpkError:
        return None
    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None
    # addontitle  "标题"   (忽略注释 // 行)
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("//"):
            continue
        m = re.match(r'addontitle\s+"([^"]*)"', line, re.IGNORECASE)
        if m:
            title = m.group(1).strip()
            return title or None
    return None


# ---------------------------------------------------------------------------
# 查找游戏 / 模组目录
# ---------------------------------------------------------------------------

def _unescape_vdf_path(p):
    # VDF 里反斜杠是转义的: "E:\\SteamLibrary" -> E:\SteamLibrary
    return p.replace("\\\\", "\\")


def _to_local_path(p):
    """在 WSL/Linux 下把 Windows 路径 (E:\\SteamLibrary) 映射成 /mnt/e/SteamLibrary。"""
    if os.name == "nt":
        return p
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
    if m:
        return "/mnt/" + m.group(1).lower() + "/" + m.group(2).replace("\\", "/")
    return p


def _library_paths_from_vdf(vdf_file):
    """解析 libraryfolders.vdf,兼容新旧两种格式。"""
    paths = []
    try:
        with open(vdf_file, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return paths
    # 新格式: "path"  "E:\\SteamLibrary"
    for m in re.finditer(r'"path"\s*"([^"]*)"', text):
        paths.append(_unescape_vdf_path(m.group(1)))
    # 旧格式: "1"  "E:\\SteamLibrary"
    for m in re.finditer(r'"(\d+)"\s*"([A-Za-z]:[^"]*)"', text):
        paths.append(_unescape_vdf_path(m.group(2)))
    return paths


def _steamconfig_library_paths(root):
    """老版本 Steam 在 config/config.vdf 里用 BaseInstallFolder_N 记录库路径。"""
    paths = []
    cfg = os.path.join(root, "config", "config.vdf")
    try:
        with open(cfg, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return paths
    for m in re.finditer(r'"BaseInstallFolder_\d+"\s*"([^"]+)"', text):
        paths.append(_unescape_vdf_path(m.group(1)))
    return paths


def _manifest_installdir(library):
    """从 appmanifest_550.acf 读取安装目录名。"""
    acf = os.path.join(library, "steamapps", "appmanifest_550.acf")
    try:
        with open(acf, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r'"installdir"\s*"([^"]*)"', text)
    return _unescape_vdf_path(m.group(1)) if m else None


def _windows_drive_steam_roots():
    """枚举所有盘符下常见的 Steam / SteamLibrary 位置。"""
    if os.name != "nt":
        return []
    out = []
    for d in "CDEFGHIJKLMNOPQRSTUVWXYZAB":
        base = d + ":\\"
        try:
            if not os.path.exists(base):
                continue
        except OSError:
            continue
        for sub in (
            "Steam", "SteamLibrary", "SteamGames", "Games",
            "Games\\Steam", "Games\\SteamLibrary",
            "Program Files (x86)\\Steam", "Program Files\\Steam",
        ):
            out.append(os.path.join(base, sub))
    return out


def _direct_game_candidates():
    """不依赖 Steam 记录的兜底:直接找常见的游戏目录。"""
    out = []
    if os.name == "nt":
        for d in "CDEFGHIJKLMNOPQRSTUVWXYZAB":
            base = d + ":\\"
            try:
                if not os.path.exists(base):
                    continue
            except OSError:
                continue
            for sub in (
                "Left 4 Dead 2",
                "Games\\Left 4 Dead 2",
                "SteamLibrary\\steamapps\\common\\Left 4 Dead 2",
                "Steam\\steamapps\\common\\Left 4 Dead 2",
                "Program Files (x86)\\Steam\\steamapps\\common\\Left 4 Dead 2",
            ):
                out.append(os.path.join(base, sub))
    elif os.path.isdir("/mnt"):
        try:
            drives = sorted(os.listdir("/mnt"))
        except OSError:
            drives = []
        for d in drives:
            for sub in (
                "Left 4 Dead 2",
                "Games/Left 4 Dead 2",
                "SteamLibrary/steamapps/common/Left 4 Dead 2",
                "Steam/steamapps/common/Left 4 Dead 2",
            ):
                out.append(os.path.join("/mnt", d, sub))
    return out


def _running_game_dirs():
    """通过进程快照找到正在运行的 left4dead2.exe 所在的游戏目录。"""
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa
        return []

    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    MAX_PATH = 260

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * MAX_PATH),
        ]

    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return []
        dirs = []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                if entry.szExeFile.lower() in ("left4dead2.exe", "left4dead2"):
                    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                        entry.th32ProcessID)
                    if h:
                        try:
                            buf = ctypes.create_unicode_buffer(32768)
                            size = wintypes.DWORD(len(buf))
                            if k32.QueryFullProcessImageNameW(h, 0, buf,
                                                              ctypes.byref(size)):
                                dirs.append(os.path.dirname(buf.value))
                        finally:
                            k32.CloseHandle(h)
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
        return dirs
    except Exception:  # noqa
        return []


def game_running():
    """是否有正在运行的 left4dead2.exe。"""
    try:
        return bool(_running_game_dirs())
    except Exception:  # noqa
        return False


def _is_l4d2_root(p):
    return bool(p) and os.path.isdir(os.path.join(p, "left4dead2"))


def find_steam_roots():
    roots = []
    # 1) Windows 注册表
    try:
        import winreg  # noqa
        entries = [
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamExe"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ]
        for hive, key, val in entries:
            try:
                with winreg.OpenKey(hive, key) as k:
                    p, _ = winreg.QueryValueEx(k, val)
                    if p:
                        if val == "SteamExe":
                            p = os.path.dirname(p)
                        roots.append(os.path.normpath(p))
            except OSError:
                pass
    except ImportError:
        pass

    # 2) 常见默认路径 + 环境变量
    for p in (
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
        r"D:\Steam",
        r"E:\Steam",
    ):
        roots.append(p)
    env = os.environ.get("STEAM_PATH") or os.environ.get("SteamPath")
    if env:
        roots.append(env)

    # 3) Windows 全盘符常见位置
    roots.extend(_windows_drive_steam_roots())

    # 4) POSIX/WSL: 扫 /mnt/<盘符>
    if os.name != "nt" and os.path.isdir("/mnt"):
        try:
            drives = sorted(os.listdir("/mnt"))
        except OSError:
            drives = []
        for d in drives:
            for sub in ("Steam", "SteamLibrary"):
                cand = os.path.join("/mnt", d, sub)
                if os.path.isdir(cand):
                    roots.append(cand)
            for sub in ("Program Files (x86)/Steam", "Program Files/Steam"):
                cand = os.path.join("/mnt", d, sub)
                if os.path.isdir(cand):
                    roots.append(cand)

    # 去重保序
    seen, out = set(), []
    for r in roots:
        if not r:
            continue
        r = os.path.normpath(r)
        if r.lower() not in seen:
            seen.add(r.lower())
            out.append(r)
    return out


def _usable_addons(game):
    addons = os.path.join(game, "left4dead2", "addons")
    try:
        with os.scandir(addons):
            return True
    except OSError:
        return False


def find_l4d2_dirs():
    """返回找到的 Left 4 Dead 2 安装根目录,按可信度排序。"""
    cands = {}

    def add(game, pri):
        if not _is_l4d2_root(game):
            return
        game = os.path.normpath(game)
        key = os.path.normcase(game)
        old = cands.get(key)
        if old is None or pri < old[0]:
            cands[key] = (pri, game)

    # 0. 正在运行的游戏进程
    for d in _running_game_dirs():
        add(d, 0)
        if os.path.basename(d).lower() == "left4dead2":
            add(os.path.dirname(d), 0)

    # 1/2. Steam 库 + appmanifest
    libraries = []
    seen_lib = set()
    for root in find_steam_roots():
        if not os.path.isdir(root):
            continue
        if root.lower() not in seen_lib:
            seen_lib.add(root.lower())
            libraries.append(root)
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        for lib in _library_paths_from_vdf(vdf):
            lib = _to_local_path(lib)
            if os.path.isdir(lib) and lib.lower() not in seen_lib:
                seen_lib.add(lib.lower())
                libraries.append(lib)
        for lib in _steamconfig_library_paths(root):
            lib = _to_local_path(lib)
            if os.path.isdir(lib) and lib.lower() not in seen_lib:
                seen_lib.add(lib.lower())
                libraries.append(lib)

    for lib in libraries:
        inst = _manifest_installdir(lib)
        if inst:
            add(os.path.join(lib, "steamapps", "common", inst), 1)
        add(os.path.join(lib, "steamapps", "common", "Left 4 Dead 2"), 2)
        add(os.path.join(lib, "common", "Left 4 Dead 2"), 2)
        add(os.path.join(lib, "Left 4 Dead 2"), 2)

    # 3. 直接目录兜底
    for cand in _direct_game_candidates():
        add(cand, 3)

    ordered = sorted(cands.values(), key=lambda t: (t[0], t[1].lower()))
    usable = [g for _p, g in ordered if _usable_addons(g)]
    if usable:
        return usable
    return [g for _p, g in ordered]


def resolve_addons_dir(path):
    """把用户给的任意层级路径解析成 …/left4dead2/addons。"""
    path = os.path.abspath(path)
    candidates = [
        path,
        os.path.join(path, "addons"),
        os.path.join(path, "left4dead2", "addons"),
        os.path.join(path, "Left 4 Dead 2", "left4dead2", "addons"),
    ]
    for c in candidates:
        if os.path.isdir(c) and os.path.basename(c).lower() == "addons":
            return c
    # 再宽松一点:只要存在某个 addons 目录
    for c in candidates:
        if os.path.isdir(c):
            for name in os.listdir(c):
                if name.lower() == "addons" and os.path.isdir(os.path.join(c, name)):
                    return os.path.join(c, name)
    raise FileNotFoundError(f"在 {path} 下找不到 addons 目录")


# ---------------------------------------------------------------------------
# 扫描模组
# ---------------------------------------------------------------------------

def addonlist_path(addons_dir):
    return os.path.join(os.path.dirname(os.path.normpath(addons_dir)), "addonlist.txt")


def _norm_addon_key(k):
    return k.replace("/", "\\").strip().lower()


def load_addonlist(addons_dir):
    """读取 addonlist.txt -> {规范化相对路径: 是否启用}。缺失/损坏时视为全部启用。"""
    path = addonlist_path(addons_dir)
    out = {}
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except OSError:
        return out
    for m in re.finditer(r'"([^"]+)"\s+"([01])"', text):
        out[_norm_addon_key(m.group(1))] = (m.group(2) == "1")
    return out


def addon_keys(addon, addons_dir):
    """模组在 addonlist.txt 中的键(相对 addons 目录,反斜杠)。松散文件夹无键。"""
    if addon is None:
        return []
    vpks = getattr(addon, "vpks", None)
    if vpks is None:
        vpks = [addon.vpk] if addon.vpk is not None else []
    keys = []
    for v in vpks:
        try:
            rel = os.path.relpath(v.dir_path, addons_dir)
        except ValueError:
            continue
        if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
            continue
        keys.append(rel.replace("/", "\\"))
    return keys


def keys_for_physical_path(path, addons_dir):
    """给一个物理单元(文件或目录)生成 addonlist 键。"""
    keys = []
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for fn in files:
                if fn.lower().endswith(".vpk"):
                    rel = os.path.relpath(os.path.join(root, fn), addons_dir)
                    if not rel.startswith(os.pardir):
                        keys.append(rel.replace("/", "\\"))
    else:
        rel = os.path.relpath(path, addons_dir)
        if not rel.startswith(os.pardir):
            keys.append(rel.replace("/", "\\"))
    return keys


def is_addon_listed_enabled(addon, addons_dir, listed=None):
    if listed is None:
        listed = load_addonlist(addons_dir)
    keys = addon_keys(addon, addons_dir)
    if not keys:
        return True
    return any(listed.get(_norm_addon_key(k), True) for k in keys)


def filter_enabled_by_addonlist(addons, addons_dir):
    listed = load_addonlist(addons_dir)
    return [a for a in addons if is_addon_listed_enabled(a, addons_dir, listed)]


def set_addonlist_entries(addons_dir, items):
    """原子写入多个 (key, enabled),保留其它条目与原始大小写/顺序。"""
    path = addonlist_path(addons_dir)
    entries = {}
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except OSError:
        text = ""
    for m in re.finditer(r'"([^"]+)"\s+"([01])"', text):
        entries[_norm_addon_key(m.group(1))] = [m.group(1), m.group(2) == "1"]
    for k, enabled in items:
        nk = _norm_addon_key(k)
        if nk in entries:
            entries[nk][1] = enabled
        else:
            entries[nk] = [k, enabled]
    parts = ['"AddonList"', '{']
    for _nk, (orig, val) in entries.items():
        parts.append('\t"%s"\t\t"%s"' % (orig, "1" if val else "0"))
    parts.append('}')
    data = "\n".join(parts) + "\n"
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(data)
    os.replace(tmp, path)


def set_addonlist_enabled(addons_dir, keys, enabled):
    set_addonlist_entries(addons_dir, [(k, enabled) for k in keys])


def _iter_loose_files(folder, warnings=None):
    """返回松散模组文件夹内所有文件的相对路径(正斜杠);不可读分支记入 warnings。"""
    rels = set()

    def onerror(e):
        if warnings is not None:
            warnings.append({"path": getattr(e, "filename", folder),
                             "error": str(e)})

    for root, _dirs, files in os.walk(folder, onerror=onerror):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, folder).replace("\\", "/")
            rels.add(rel)
    return rels


def _looks_like_loose_mod(folder, rels):
    """判断一个文件夹是不是松散文件模组(而不是含 VPK 的容器)。"""
    tops = {r.split("/", 1)[0].lower() for r in rels if "/" in r}
    markers = {"materials", "models", "sound", "scripts", "maps",
               "resource", "particles", "scenes", "missions", "anims",
               "expressions", "cfg", "media"}
    return bool(tops & markers)


def discover_addons(addons_dir, verbose=False, warnings=None):
    addons = []

    def load_vpk(path):
        try:
            v = Vpk(path)
        except VpkError as e:
            if verbose:
                print(f"  [跳过] {path}: {e}")
            return None
        if not v.entries:
            if verbose:
                print(f"  [空] {path}")
            return None
        return v

    def safe_listdir(path):
        try:
            return sorted(os.listdir(path))
        except OSError as e:
            if verbose:
                print(f"  [无法读取] {path}: {e}")
            if warnings is not None:
                warnings.append({"path": path, "error": str(e)})
            return []

    # 1) 创意工坊
    ws = os.path.join(addons_dir, "workshop")
    if os.path.isdir(ws):
        if verbose:
            print(f"扫描创意工坊目录: {ws}")
        for entry in safe_listdir(ws):
            full = os.path.join(ws, entry)
            if os.path.isdir(full):
                # 结构 A: addons/workshop/<id>/<file>.vpk —— 整个目录是一个物理单元
                cid = entry
                vpk_files = [f for f in safe_listdir(full) if f.lower().endswith(".vpk")]
                vpk_files.sort(key=lambda x: (not x.lower().endswith("_dir.vpk"), x.lower()))
                vpks = []
                title = None
                for fn in vpk_files:
                    v = load_vpk(os.path.join(full, fn))
                    if v is None:
                        continue
                    vpks.append(v)
                    if not title:
                        t = read_addon_title(v)
                        if t:
                            title = t
                if vpks:
                    a = Addon(f"workshop:{cid}", "创意工坊", "workshop",
                              vpks[0].dir_path, vpk=vpks[0], title=title)
                    a.vpks = vpks
                    a.workshop_id = cid if cid.isdigit() else None
                    addons.append(a)
                elif verbose:
                    print(f"  [工坊 {cid}] 未找到可解析的 .vpk")
            elif os.path.isfile(full) and entry.lower().endswith(".vpk"):
                # 结构 B: addons/workshop/<id>.vpk —— 单个文件是一个物理单元
                v = load_vpk(full)
                if v is None:
                    if verbose:
                        print(f"  [工坊 {entry}] 无法解析(可能是分卷)或为空")
                    continue
                m = re.match(r"^(\d+)\.vpk$", entry, re.IGNORECASE)
                if m:
                    uid = f"workshop:{m.group(1)}"
                    wid = m.group(1)
                else:
                    uid = f"file:{os.path.normpath(full)}"
                    wid = None
                a = Addon(uid, "创意工坊", "workshop", full, vpk=v,
                          title=read_addon_title(v))
                a.workshop_id = wid
                addons.append(a)

    # 2) 手动安装: addons/*.vpk
    for fn in safe_listdir(addons_dir):
        full = os.path.join(addons_dir, fn)
        if os.path.isfile(full) and fn.lower().endswith(".vpk"):
            v = load_vpk(full)
            if v is None:
                continue
            addons.append(Addon(f"file:{os.path.normpath(full)}", "手动安装", "manual",
                                full, vpk=v, title=read_addon_title(v)))

    # 3) 松散文件夹模组
    for fn in safe_listdir(addons_dir):
        full = os.path.join(addons_dir, fn)
        if not os.path.isdir(full) or fn.lower() == "workshop":
            continue
        rels = _iter_loose_files(full, warnings)
        if rels and _looks_like_loose_mod(full, rels):
            addons.append(Addon(
                uid=f"loose:{os.path.normpath(full)}",
                source="松散文件夹",
                kind="loose",
                location=full,
                loose_paths=rels,
                title=os.path.basename(full),
            ))

    return addons


# ---------------------------------------------------------------------------
# 冲突分析
# ---------------------------------------------------------------------------

def categorize(path):
    lp = path.lower()
    for prefix, label in CATEGORY_RULES:
        if lp.startswith(prefix):
            return label
    return "其他"


def categorize_addon(paths):
    """根据模组内含的文件路径推断模组类别(取占比最高的类别)。"""
    scores = defaultdict(int)
    for p in paths:
        lp = p.lower()
        if lp.startswith("maps/") or lp.startswith("missions/"):
            scores["地图"] += 1
        elif "weapons/" in lp or lp.startswith("models/weapons"):
            scores["武器"] += 1
        elif ("survivors/" in lp or "player/" in lp or "humans/" in lp
              or "v_models/" in lp or "characters/" in lp):
            scores["角色"] += 1
        elif lp.startswith("sound/"):
            scores["声音"] += 1
        elif "vgui" in lp or lp.startswith("resource/"):
            scores["界面"] += 1
        elif lp.startswith("particles/"):
            scores["特效"] += 1
        elif lp.startswith("models/"):
            scores["模型"] += 1
        elif lp.startswith("materials/"):
            scores["材质"] += 1
        elif lp.startswith("scripts/"):
            scores["脚本"] += 1
    if not scores:
        return "其他"
    return max(scores.items(), key=lambda kv: kv[1])[0]


# --- 目标推断:角色 / 武器 / 特感 ---
SURVIVOR_TOKENS = [
    ("coach", "教练"), ("nick", "尼克"), ("rochelle", "罗谢尔"), ("ellis", "埃利斯"),
    ("bill", "比尔"), ("zoey", "佐伊"), ("louis", "路易斯"), ("francis", "弗朗西斯"),
]
SPECIAL_TOKENS = [
    ("boomer", "呕吐者"), ("smoker", "烟鬼"), ("hunter", "猎人"), ("witch", "女巫"),
    ("tank", "坦克"), ("jockey", "骑师"), ("charger", "冲锋者"), ("spitter", "喷吐者"),
]
# 具体武器优先,通用类别在末尾(若已识别到具体武器则丢弃通用)
GENERIC_WEAPONS = {"手枪", "冲锋枪", "霰弹枪", "步枪", "狙击枪", "近战武器", "斧头"}
WEAPON_TOKENS = [
    ("ak47", "AK47"), ("m16", "M16"), ("m4a1", "M4A1"), ("sg552", "SG552"),
    ("mp5", "MP5"), ("uzi", "Uzi"), ("sg550", "SG550"), ("g3sg1", "G3SG1"),
    ("scar", "SCAR"), ("desert", "沙鹰"), ("magnum", "马格南"), ("awp", "AWP"),
    ("scout", "侦察兵"), ("m60", "M60"), ("spas", "SPAS"), ("chrome", "铬合金喷"),
    ("autoshotgun", "自动霰弹枪"), ("hunting_rifle", "猎枪"),
    ("sniper_military", "军用狙击枪"), ("grenade_launcher", "榴弹发射器"),
    ("chainsaw", "电锯"), ("katana", "武士刀"), ("machete", "砍刀"),
    ("crowbar", "撬棍"), ("cricket_bat", "板球棒"), ("baseball_bat", "棒球棍"),
    ("golfclub", "高尔夫球杆"), ("frying_pan", "平底锅"), ("tonfa", "警棍"),
    ("nightstick", "警棍"), ("shovel", "铁锹"), ("pitchfork", "干草叉"),
    ("fireaxe", "消防斧"), ("combat_knife", "战斗刀"), ("knife", "小刀"),
    ("bayonet", "刺刀"), ("wrench", "扳手"), ("hammer", "锤子"),
    ("electric_guitar", "电吉他"), ("guitar", "吉他"), ("pipe_bomb", "管状炸弹"),
    ("molotov", "燃烧瓶"), ("vomitjar", "呕吐罐"),
    # 通用类别(放最后)
    ("axe", "斧头"), ("pistol", "手枪"), ("smg", "冲锋枪"), ("shotgun", "霰弹枪"),
    ("rifle", "步枪"), ("sniper", "狙击枪"), ("melee", "近战武器"),
]


def annotate_target(paths):
    """根据模组内文件路径推断它作用于哪个角色 / 武器 / 特感。返回中文标注串。"""
    joined = []
    words = set()
    for pth in paths:
        lp = pth.lower()
        joined.append(lp)
        words.update(re.split(r"[^a-z0-9]+", lp))

    chars = {name for tok, name in SURVIVOR_TOKENS if tok in words}
    specials = {name for tok, name in SPECIAL_TOKENS if tok in words}
    # 只在“武器相关”路径里识别武器,避免粒子/清单类整合包误标一堆武器
    weapon_paths = [p for p in joined if
                    ("weapons/" in p or "weapon_" in p or "/v_models/" in p
                     or "/w_models/" in p or p.startswith("models/v_models")
                     or p.startswith("models/w_models"))]
    weapons = set()
    for tok, name in WEAPON_TOKENS:
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(tok) + r"(?![a-z0-9])")
        if any(pat.search(p) for p in weapon_paths):
            weapons.add(name)
    if weapons & GENERIC_WEAPONS:
        specific = weapons - GENERIC_WEAPONS
        if specific:
            weapons = specific

    def fmt(items):
        items = sorted(items)
        if len(items) > 3:
            return "、".join(items[:3]) + " 等"
        return "、".join(items)

    parts = []
    if chars:
        parts.append("角色·" + fmt(chars))
    if weapons:
        parts.append("武器·" + fmt(weapons))
    if specials:
        parts.append("特感·" + fmt(specials))
    return "  ".join(parts)


def build_base_index(game_dir):
    """解析游戏本体 pak01_dir.vpk,返回 {小写路径: (crc,length)}。失败返回 None。"""
    path = os.path.join(game_dir, "pak01_dir.vpk")
    try:
        v = Vpk(path)
    except (VpkError, OSError):
        return None
    return {k.lower(): (e.crc, e.length) for k, e in v.entries.items()}


def _addon_entry(addon, key_lower):
    for v in getattr(addon, "vpks", []) or []:
        k = v.find(key_lower)
        if k:
            return v.entries[k]
    return None


def analyze(addons, include_meta=False, ignore_scripts=True, base_index=None,
            stats=None, skip_identical=True, skip_base=True):
    """返回 (path_map, conflicts)。
    path_map: 规范化小写路径 -> [(原样路径, addon), ...]
    conflicts: 规范化小写路径 -> [(原样路径, addon), ...]  (仅 >=2 个不同 addon)
    """
    path_map = defaultdict(list)
    for addon in addons:
        for p in addon.paths():
            base = p.rsplit("/", 1)[-1].lower()
            if not include_meta:
                if base.endswith(".cache"):
                    continue
                if "/" not in p and base in META_BASENAMES:
                    continue
            path_map[p.lower()].append((p, addon))

    if stats is not None:
        stats.setdefault("scripts", 0)
        stats.setdefault("identical", 0)
        stats.setdefault("base", 0)

    conflicts = {}
    for key, lst in path_map.items():
        uids = {a.uid for _p, a in lst}
        if len(uids) < 2:
            continue

        vals = []
        for _p, a in lst:
            e = _addon_entry(a, key)
            if e is not None:
                vals.append((e.crc, e.length))

        # 1) 所有提供者内容完全相同 -> 不是冲突
        if skip_identical and vals and len(set(vals)) == 1:
            if stats is not None:
                stats["identical"] += 1
            continue

        # 2) 所有提供者都与游戏本体相同 -> 无实际改变,不是冲突
        if skip_base and base_index is not None and vals:
            bv = base_index.get(key)
            if bv is not None and all(v == bv for v in vals):
                if stats is not None:
                    stats["base"] += 1
                continue

        # 3) 脚本类默认不算冲突(可通过 --include-scripts / 界面开关显示)
        if ignore_scripts and key.startswith("scripts/"):
            if stats is not None:
                stats["scripts"] += 1
            continue

        conflicts[key] = lst
    return path_map, conflicts


def build_pair_summary(conflicts):
    """统计每一对模组之间有多少个冲突文件。"""
    pair_count = defaultdict(int)
    for _key, lst in conflicts.items():
        # 去重同一 addon
        by_uid = {}
        for _p, a in lst:
            by_uid.setdefault(a.uid, a)
        uid_list = list(by_uid.values())
        for i in range(len(uid_list)):
            for j in range(i + 1, len(uid_list)):
                a, b = uid_list[i], uid_list[j]
                pair = tuple(sorted((a.uid, b.uid)))
                pair_count[pair] += 1
    return pair_count


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def print_header(text):
    line = "=" * 78
    print(line)
    print(text)
    print(line)


def report_console(addons, conflicts, pair_count, min_files=1, max_files=200):
    print_header("L4D2 模组冲突检查结果")

    print(f"共扫描模组: {len(addons)} 个")
    for a in addons:
        print(f"  - {a.display_name}")
        print(f"      位置: {a.location}")
    print()

    if not conflicts:
        print("✔ 未发现模组之间的文件冲突。")
        return

    print_header(f"发现 {len(conflicts)} 处冲突(同一内部文件被多个模组提供)")
    print()

    # --- 模组对汇总 ---
    shown_pairs = [(p, c) for p, c in pair_count.items() if c >= min_files]
    shown_pairs.sort(key=lambda x: (-x[1], x[0]))
    uid2addon = {a.uid: a for a in addons}
    if shown_pairs:
        print("【模组对汇总】冲突文件数从多到少:")
        for (u1, u2), cnt in shown_pairs:
            a1, a2 = uid2addon.get(u1), uid2addon.get(u2)
            n1 = a1.display_name if a1 else u1
            n2 = a2.display_name if a2 else u2
            print(f"  [{cnt:>4} 个冲突文件]  {n1}  <-->  {n2}")
        print()

    # --- 按冲突文件详细列出 ---
    items = sorted(conflicts.items(), key=lambda kv: kv[0])
    print(f"【冲突文件明细】共 {len(items)} 条,下面最多显示 {max_files} 条:")
    print()
    shown = 0
    for key, lst in items:
        if shown >= max_files:
            print(f"  ... 还有 {len(items) - shown} 条未显示(用 --max-files 调整或看 HTML 报告)")
            break
        shown += 1
        orig_path = lst[0][0]
        cat = categorize(orig_path)
        print(f"{shown}. 内部路径: {orig_path}")
        print(f"      类型: {cat}")
        # 去重 addon
        seen = set()
        for _p, a in lst:
            if a.uid in seen:
                continue
            seen.add(a.uid)
            print(f"      · {a.display_name}")
            print(f"          位置: {a.location}")
        print()


def report_json(path, addons, conflicts, pair_count):
    uid2addon = {a.uid: a for a in addons}
    data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "addon_count": len(addons),
        "conflict_count": len(conflicts),
        "addons": [
            {
                "uid": a.uid,
                "name": a.title,
                "display_name": a.display_name,
                "source": a.source,
                "workshop_id": a.workshop_id,
                "location": a.location,
                "file_count": len(a.paths()),
            }
            for a in addons
        ],
        "pairs": [
            {
                "addon_a": uid2addon[u1].display_name if u1 in uid2addon else u1,
                "addon_b": uid2addon[u2].display_name if u2 in uid2addon else u2,
                "conflict_files": cnt,
            }
            for (u1, u2), cnt in sorted(pair_count.items(), key=lambda x: -x[1])
        ],
        "conflicts": [
            {
                "path": lst[0][0],
                "category": categorize(lst[0][0]),
                "addons": [
                    {
                        "uid": a.uid,
                        "display_name": a.display_name,
                        "workshop_id": a.workshop_id,
                        "location": a.location,
                    }
                    for a in _dedup_addons(lst)
                ],
            }
            for _key, lst in sorted(conflicts.items(), key=lambda kv: kv[0])
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _dedup_addons(lst):
    seen, out = set(), []
    for _p, a in lst:
        if a.uid in seen:
            continue
        seen.add(a.uid)
        out.append(a)
    return out


def _html_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def report_html(path, addons, conflicts, pair_count, min_files=1):
    uid2addon = {a.uid: a for a in addons}
    gen = time.strftime("%Y-%m-%d %H:%M:%S")
    parts = []
    parts.append("""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>L4D2 模组冲突报告</title>
<style>
 body{font-family:"Microsoft YaHei",Segoe UI,Arial,sans-serif;background:#1e1f22;color:#e6e6e6;margin:0;padding:24px}
 h1{color:#ff8a3d;border-bottom:2px solid #ff8a3d;padding-bottom:8px}
 h2{color:#7ec8ff;margin-top:32px}
 .meta{color:#9aa0a6;font-size:13px;margin-bottom:16px}
 .ok{background:#123d1a;border-left:4px solid #3ddc84;padding:14px;border-radius:6px;font-size:16px}
 .badge{display:inline-block;background:#33363b;border-radius:4px;padding:1px 7px;font-size:12px;color:#ffd166;margin-left:6px}
 table{border-collapse:collapse;width:100%;margin-top:8px}
 th,td{border:1px solid #33363b;padding:8px 10px;text-align:left;font-size:14px;vertical-align:top}
 th{background:#2a2c30;color:#ffb27a}
 tr:nth-child(even){background:#24262a}
 .path{font-family:Consolas,monospace;color:#8be9a0;word-break:break-all}
 .addon{color:#e6e6e6}
 .id{color:#7ec8ff;font-family:Consolas,monospace;font-size:12px}
 .loc{color:#9aa0a6;font-size:12px;font-family:Consolas,monospace;word-break:break-all}
 .cat{color:#ffd166;font-size:12px}
 .n{color:#ff8a3d;font-weight:bold}
 details summary{cursor:pointer;color:#7ec8ff}
</style></head><body>
""")
    parts.append(f"<h1>Left 4 Dead 2 · 模组冲突报告</h1>")
    parts.append(f'<div class="meta">生成时间: {gen} ｜ 扫描模组: {len(addons)} 个 ｜ 冲突文件: {len(conflicts)} 处</div>')

    if not conflicts:
        parts.append('<div class="ok">✔ 未发现模组之间的文件冲突。</div>')
    else:
        parts.append("<h2>模组对汇总</h2><table><tr><th>冲突文件数</th><th>模组 A</th><th>模组 B</th></tr>")
        shown = [(p, c) for p, c in pair_count.items() if c >= min_files]
        for (u1, u2), cnt in sorted(shown, key=lambda x: -x[1]):
            a1 = uid2addon.get(u1)
            a2 = uid2addon.get(u2)
            n1 = _html_escape(a1.display_name if a1 else u1)
            n2 = _html_escape(a2.display_name if a2 else u2)
            parts.append(f'<tr><td class="n">{cnt}</td><td class="addon">{n1}</td>'
                         f'<td class="addon">{n2}</td></tr>')
        parts.append("</table>")

        parts.append(f"<h2>冲突文件明细 ({len(conflicts)} 处)</h2>")
        parts.append("<table><tr><th style='width:55%'>内部路径 / 类型</th><th>涉及的模组</th></tr>")
        for key, lst in sorted(conflicts.items(), key=lambda kv: kv[0]):
            orig = lst[0][0]
            cat = categorize(orig)
            addon_cells = []
            for a in _dedup_addons(lst):
                addon_cells.append(
                    f'<div class="addon">· {_html_escape(a.display_name)}</div>'
                    f'<div class="loc">{_html_escape(a.location)}</div>')
            parts.append(
                f'<tr><td><span class="path">{_html_escape(orig)}</span>'
                f'<br><span class="cat">{_html_escape(cat)}</span></td>'
                f'<td>{"".join(addon_cells)}</td></tr>')
        parts.append("</table>")

    parts.append("</body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


# ---------------------------------------------------------------------------
# 运行一次扫描
# ---------------------------------------------------------------------------

def resolve_game_paths(args):
    """返回 (game_root, addons_dir);失败返回 (None, None)。"""
    if args.game_dir:
        try:
            addons_dir = resolve_addons_dir(args.game_dir)
        except FileNotFoundError as e:
            print(f"错误: {e}", file=sys.stderr)
            return None, None
        game_root = os.path.dirname(os.path.dirname(addons_dir))
        return game_root, addons_dir

    found = find_l4d2_dirs()
    if not found:
        print("错误: 未自动找到《Left 4 Dead 2》安装目录。", file=sys.stderr)
        print("请用 --game-dir 指定,例如:", file=sys.stderr)
        print('  --game-dir "E:\\SteamLibrary\\steamapps\\common\\Left 4 Dead 2"',
              file=sys.stderr)
        return None, None
    if len(found) > 1:
        print("检测到多个安装,使用第一个。如需指定请用 --game-dir:")
        for g in found:
            print("  " + g)
    game_root = found[0]
    addons_dir = os.path.join(game_root, "left4dead2", "addons")
    return game_root, addons_dir


def run_once(args):
    # 定位 addons 目录
    game_root, addons_dir = resolve_game_paths(args)
    if not addons_dir:
        return 2

    print(f"游戏目录: {game_root}")
    print(f"模组目录: {addons_dir}")
    print()

    addons = discover_addons(addons_dir, verbose=args.verbose)
    addons = filter_enabled_by_addonlist(addons, addons_dir)
    if not addons:
        print("未在 addons 目录中找到任何 VPK / 松散模组。")
        print("(如果你还没订阅或安装模组,这是正常的。)")
        if args.json:
            report_json(args.json, [], {}, {})
            print(f"已写出 JSON: {args.json}")
        if args.html:
            report_html(args.html, [], {}, {})
            print(f"已写出 HTML: {args.html}")
        return 0

    base_index = build_base_index(os.path.dirname(addons_dir))
    stats = {}
    _path_map, conflicts = analyze(
        addons, include_meta=args.include_meta,
        ignore_scripts=(not args.strict) and (not args.include_scripts),
        base_index=base_index, stats=stats,
        skip_identical=not args.strict, skip_base=not args.strict)
    if stats and (stats.get("scripts") or stats.get("identical") or stats.get("base")):
        print(f"已忽略 {stats.get('scripts', 0)} 处脚本类、"
              f"{stats.get('identical', 0)} 处内容相同、"
              f"{stats.get('base', 0)} 处与本体相同的冲突")
    pair_count = build_pair_summary(conflicts)

    report_console(addons, conflicts, pair_count,
                   min_files=args.min_files, max_files=args.max_files)

    if args.json:
        report_json(args.json, addons, conflicts, pair_count)
        print(f"已写出 JSON 报告: {args.json}")
    if args.html:
        report_html(args.html, addons, conflicts, pair_count, min_files=args.min_files)
        print(f"已写出 HTML 报告: {args.html}")

    return 1 if conflicts else 0


# ---------------------------------------------------------------------------
# 监视模式
# ---------------------------------------------------------------------------

def _snapshot(addons_dir):
    snap = {}
    if not os.path.isdir(addons_dir):
        return snap
    for root, _dirs, files in os.walk(addons_dir, onerror=lambda e: None):
        for fn in files:
            full = os.path.join(root, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            mtime = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
            snap[full] = (st.st_size, mtime)
    return snap


def run_watch(args):
    # 复用 run_once 的定位逻辑
    if args.game_dir:
        addons_dir = resolve_addons_dir(args.game_dir)
    else:
        found = find_l4d2_dirs()
        if not found:
            print("错误: 未自动找到游戏目录,请用 --game-dir 指定。", file=sys.stderr)
            return 2
        addons_dir = os.path.join(found[0], "left4dead2", "addons")

    print(f"监视目录: {addons_dir}")
    print("监视中…… 模组 VPK 有新增/改动时会自动重新检查 (Ctrl+C 退出)。")
    print()

    last = _snapshot(addons_dir)
    args.json = None
    args.html = None
    while True:
        try:
            run_once(args)
        except Exception as e:  # noqa
            print(f"扫描出错: {e}")
        sys.stdout.flush()
        print(f"\n[下一次检查将在 5 秒后,或模组变化时立即触发]  (Ctrl+C 退出)\n")
        for _ in range(10):
            time.sleep(0.5)
            cur = _snapshot(addons_dir)
            if cur != last:
                last = cur
                print("检测到模组文件变化,重新检查……\n")
                break


# ---------------------------------------------------------------------------
# 启动游戏
# ---------------------------------------------------------------------------

def launch_game():
    """通过 steam:// 协议启动 L4D2 (appid 550)。"""
    try:
        if os.name == "nt":
            os.startfile("steam://rungameid/550")  # noqa
        else:
            import subprocess
            subprocess.Popen(["xdg-open", "steam://rungameid/550"])
        print("已请求 Steam 启动《Left 4 Dead 2》。")
        return True
    except Exception as e:  # noqa
        print(f"启动游戏失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def build_argparser():
    p = argparse.ArgumentParser(
        prog="l4d2_conflict_checker",
        description="检查《求生之路2》模组之间的文件冲突,并列出详细位置与名称。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--game-dir", "-g", metavar="PATH",
                   help="L4D2 目录(游戏根目录 / left4dead2 / addons 任意一层均可)")
    p.add_argument("--json", metavar="FILE", help="输出 JSON 报告到指定文件")
    p.add_argument("--html", metavar="FILE", help="输出 HTML 报告到指定文件")
    p.add_argument("--min-files", type=int, default=1, metavar="N",
                   help="仅显示冲突文件数 >= N 的模组对汇总 (默认 1)")
    p.add_argument("--max-files", type=int, default=200, metavar="N",
                   help="控制台最多显示多少条冲突明细 (默认 200,HTML 报告不受限)")
    p.add_argument("--strict", action="store_true",
                   help="严格模式:按文件路径判冲突(含脚本/内容相同),接近游戏本体")
    p.add_argument("--include-scripts", action="store_true",
                   help="把 scripts/ 类冲突也计入(默认忽略,视为不影响游戏)")
    p.add_argument("--include-meta", action="store_true",
                   help="把 addoninfo.txt / addonimage 等元信息文件也算作冲突(一般不需要)")
    p.add_argument("--launch", action="store_true",
                   help="检查完成后自动启动游戏")
    p.add_argument("--watch", action="store_true",
                   help="常驻监视模式:模组变化时自动重新检查")
    p.add_argument("--serve", action="store_true",
                   help="启动本地网页管理器(在浏览器里查看冲突、隔离/删除模组)")
    p.add_argument("--port", type=int, default=8765, metavar="N",
                   help="网页管理器的端口 (默认 8765)")
    p.add_argument("--no-browser", action="store_true",
                   help="--serve 时不自动打开浏览器")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="显示扫描细节(跳过的文件等)")
    return p


def main(argv=None):
    # 让中文在 Windows 控制台正常显示
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa
        pass

    args = build_argparser().parse_args(argv)

    if args.serve:
        game_root, addons_dir = resolve_game_paths(args)
        if not addons_dir:
            return 2
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import l4d2_dashboard
        except ImportError as e:
            print(f"错误: 无法加载网页管理器模块 l4d2_dashboard.py ({e})", file=sys.stderr)
            return 2
        try:
            l4d2_dashboard.serve(addons_dir, port=args.port,
                                 open_browser=not args.no_browser)
        except OSError as e:
            print(f"错误: 启动网页服务失败: {e}", file=sys.stderr)
            print("可能端口被占用,试试换一个端口:  --port 8790", file=sys.stderr)
            return 2
        return 0

    if args.watch:
        try:
            code = run_watch(args)
        except KeyboardInterrupt:
            print("\n已退出监视。")
            code = 0
        return code

    code = run_once(args)

    if args.launch and code in (0, 1):
        launch_game()

    return code


if __name__ == "__main__":
    sys.exit(main())
