#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L4D2 模组管理器 · 桌面版(CustomTkinter 现代深色界面)
=====================================================

原生窗口,不依赖浏览器。复用 l4d2_conflict_checker 的扫描逻辑与
l4d2_dashboard 的管理操作。

命令行:
  python l4d2_gui.py                 启动界面
  python l4d2_gui.py --game-dir PATH 指定游戏目录
  python l4d2_gui.py --selftest      仅自检(不开窗口)
"""

import argparse
import json
import os
import sys
import threading

import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog

import l4d2_conflict_checker as core
import l4d2_dashboard as dash


# ---------------------------------------------------------------------------
# 配色 / 字体
# ---------------------------------------------------------------------------
BG = "#131417"
SIDEBAR = "#181a1f"
SURFACE = "#1e2026"
SURFACE_ALT = "#22252c"
SURFACE_HI = "#282b33"
BORDER = "#2c303a"
TEXT = "#e7e9ee"
MUTED = "#8a909c"
ACCENT = "#ff7a45"
ACCENT_HOVER = "#ff8f61"
ACCENT_DIM = "#5a3323"
BLUE = "#5b9dff"
GREEN = "#3ddc84"
RED = "#ff5f5f"
YELLOW = "#f0b429"
ROW_A = SURFACE
ROW_B = SURFACE_ALT
SEL_BG = "#353b47"

FONT = "Microsoft YaHei UI"


def font(size=11, weight="normal"):
    return ctk.CTkFont(family=FONT, size=size, weight=weight)

CATEGORY_ORDER = ["武器", "角色", "声音", "界面", "地图", "特效", "模型", "材质", "脚本", "其他"]


def _cat_key(name):
    return (CATEGORY_ORDER.index(name) if name in CATEGORY_ORDER else 99, name)


def resolve_addons_dir(game_dir=None):
    if game_dir:
        try:
            return core.resolve_addons_dir(game_dir)
        except FileNotFoundError:
            return None
    found = core.find_l4d2_dirs()
    if found:
        return os.path.join(found[0], "left4dead2", "addons")
    return None


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root, addons_dir):
        self.root = root
        self.manager = dash.Manager(addons_dir)
        self.state = None
        self.conflict_rows = {}
        self.addon_rows = {}
        self.scanning = False
        self.page = "conf"

        root.title("L4D2 模组管理器")
        root.geometry("1160x720")
        root.minsize(960, 600)
        root.configure(fg_color=BG)

        self._style_tree()
        self._build()
        self.refresh()

    # ------------------------------------------------------------------
    # 样式
    # ------------------------------------------------------------------
    def _style_tree(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.layout("Dark.Treeview", [("Dark.Treeview.treearea", {"sticky": "nswe"})])
        st.configure(
            "Dark.Treeview",
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            borderwidth=0,
            rowheight=32,
            font=(FONT, 10),
        )
        st.configure(
            "Dark.Treeview.Heading",
            background=SURFACE_HI,
            foreground=MUTED,
            borderwidth=0,
            font=(FONT, 10, "bold"),
            relief="flat",
            padding=(8, 8),
        )
        st.map(
            "Dark.Treeview",
            background=[("selected", SEL_BG)],
            foreground=[("selected", "#ffffff")],
        )
        st.map(
            "Dark.Treeview.Heading",
            background=[("active", SURFACE_HI)],
        )

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------
    def _build(self):
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=1)
        self._build_sidebar()
        self._build_main()

    def _build_sidebar(self):
        side = ctk.CTkFrame(self.root, width=214, corner_radius=0, fg_color=SIDEBAR)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)

        # Logo
        logo = ctk.CTkFrame(side, fg_color="transparent")
        logo.pack(fill="x", padx=20, pady=(24, 18))
        ctk.CTkLabel(logo, text="L4D2", font=font(22, "bold"),
                     text_color=ACCENT).pack(anchor="w")
        ctk.CTkLabel(logo, text="模组管理器", font=font(12),
                     text_color=MUTED).pack(anchor="w")

        # 导航
        self.nav_btns = {}
        for key, text in (("conf", "  冲突视图"), ("addons", "  模组列表")):
            b = ctk.CTkButton(
                side, text=text, anchor="w", height=40, corner_radius=9,
                font=font(12), fg_color="transparent", hover_color=SURFACE_ALT,
                text_color=MUTED, command=lambda k=key: self.show_page(k))
            b.pack(fill="x", padx=12, pady=3)
            self.nav_btns[key] = b

        # 分隔
        ctk.CTkFrame(side, height=1, fg_color=BORDER).pack(fill="x", padx=18, pady=14)

        # 工具按钮
        ctk.CTkButton(side, text="↻  重新扫描", height=36, corner_radius=9,
                      font=font(12, "bold"), fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color="#1a1008", command=self.refresh).pack(
            fill="x", padx=12, pady=3)
        ctk.CTkButton(side, text="📂  隔离目录", height=34, corner_radius=9,
                      font=font(11), fg_color=SURFACE_ALT, hover_color=SURFACE_HI,
                      text_color=TEXT, command=self.open_disabled).pack(
            fill="x", padx=12, pady=3)
        ctk.CTkButton(side, text="🗁  选择游戏目录", height=34, corner_radius=9,
                      font=font(11), fg_color=SURFACE_ALT, hover_color=SURFACE_HI,
                      text_color=TEXT, command=self.choose_dir).pack(
            fill="x", padx=12, pady=3)

        # 底部路径
        bot = ctk.CTkFrame(side, fg_color="transparent")
        bot.pack(side="bottom", fill="x", padx=14, pady=14)
        ctk.CTkLabel(bot, text="游戏模组目录", font=font(9),
                     text_color=MUTED).pack(anchor="w")
        self.path_lbl = ctk.CTkLabel(bot, text="", font=font(9), text_color=MUTED,
                                     wraplength=180, justify="left")
        self.path_lbl.pack(anchor="w")

    def _build_main(self):
        main = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(2, weight=1)
        main.grid_columnconfigure(0, weight=1)

        # 标题行
        head = ctk.CTkFrame(main, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=22, pady=(20, 10))
        self.title_lbl = ctk.CTkLabel(head, text="冲突视图", font=font(19, "bold"),
                                      text_color=TEXT)
        self.title_lbl.pack(side="left")
        self.status_lbl = ctk.CTkLabel(head, text="就绪", font=font(10),
                                       text_color=MUTED)
        self.status_lbl.pack(side="left", padx=14)

        # 统计卡
        stats = ctk.CTkFrame(main, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))
        for i in range(4):
            stats.grid_columnconfigure(i, weight=1)
        self.stat_vals = {}
        cards = [
            ("enabled", "已启用模组", BLUE),
            ("conflicts", "冲突文件", RED),
            ("pairs", "冲突模组对", YELLOW),
            ("disabled", "已隔离", MUTED),
        ]
        for i, (key, label, color) in enumerate(cards):
            card = ctk.CTkFrame(stats, fg_color=SURFACE, corner_radius=14,
                                border_width=1, border_color=BORDER)
            card.grid(row=0, column=i, sticky="ew", padx=7)
            val = ctk.CTkLabel(card, text="-", font=font(26, "bold"), text_color=color)
            val.pack(pady=(14, 0))
            ctk.CTkLabel(card, text=label, font=font(10), text_color=MUTED).pack(pady=(0, 14))
            self.stat_vals[key] = val

        # 内容容器
        self.content = ctk.CTkFrame(main, fg_color="transparent")
        self.content.grid(row=2, column=0, sticky="nsew", padx=22, pady=(0, 18))
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.pages = {}
        self.pages["conf"] = self._build_conf_page()
        self.pages["addons"] = self._build_addon_page()
        for pg in self.pages.values():
            pg.grid(row=0, column=0, sticky="nsew")
        self.show_page("conf")

    # ------------------------------------------------------------------
    def _tree_container(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=12,
                            border_width=1, border_color=BORDER)
        wrap.pack(fill="both", expand=True)
        tree = ttk.Treeview(wrap, show="headings", style="Dark.Treeview")
        sb = ctk.CTkScrollbar(wrap, command=tree.yview, width=14)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        sb.pack(side="right", fill="y", padx=(4, 8), pady=10)
        empty = ctk.CTkLabel(wrap, text="", font=font(12), text_color=MUTED)
        return tree, empty, wrap

    def _filter_bar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 10))
        return bar

    def _build_conf_page(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        bar = self._filter_bar(page)
        self.conf_q = ctk.CTkEntry(bar, placeholder_text="搜索路径 / 模组名…",
                                   width=300, height=34, corner_radius=9,
                                   fg_color=SURFACE, border_color=BORDER, font=font(11))
        self.conf_q.pack(side="left")
        self.conf_q.bind("<KeyRelease>", lambda e: self.render_conflicts())

        self.conf_cat = ctk.CTkOptionMenu(
            bar, values=["全部"], width=150, height=34, corner_radius=9,
            font=font(11), fg_color=SURFACE, button_color=SURFACE_HI,
            button_hover_color=SURFACE_HI, dropdown_fg_color=SURFACE_ALT,
            dropdown_hover_color=SURFACE_HI, command=lambda v: self.render_conflicts())
        self.conf_cat.pack(side="left", padx=10)
        self.conf_count = ctk.CTkLabel(bar, text="", font=font(10), text_color=MUTED)
        self.conf_count.pack(side="right")

        ctk.CTkLabel(page, text="每处冲突下列出相关模组:拨动开关即可关闭;或点「只保留此」一键关掉其余(均可逆)",
                     font=font(9), text_color=MUTED).pack(anchor="w", pady=(0, 6))
        self.conf_scroll = ctk.CTkScrollableFrame(page, fg_color="transparent")
        self.conf_scroll.pack(fill="both", expand=True)
        return page

    def _build_addon_page(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        bar = self._filter_bar(page)
        self.addon_q = ctk.CTkEntry(bar, placeholder_text="搜索模组名 / 路径 / 工坊ID…",
                                    width=280, height=34, corner_radius=9,
                                    fg_color=SURFACE, border_color=BORDER, font=font(11))
        self.addon_q.pack(side="left")
        self.addon_q.bind("<KeyRelease>", lambda e: self.render_addons())

        self.addon_cat = ctk.CTkOptionMenu(
            bar, values=["全部"], width=120, height=34, corner_radius=9,
            font=font(11), fg_color=SURFACE, button_color=SURFACE_HI,
            button_hover_color=SURFACE_HI, dropdown_fg_color=SURFACE_ALT,
            dropdown_hover_color=SURFACE_HI, command=lambda v: self.render_addons())
        self.addon_cat.pack(side="left", padx=10)

        self.only_conf = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(bar, text="只看有冲突的模组", variable=self.only_conf,
                        font=font(11), checkbox_width=18, checkbox_height=18,
                        fg_color=ACCENT, hover_color=ACCENT_HOVER,
                        border_color=BORDER, text_color=TEXT,
                        command=self.render_addons).pack(side="left", padx=6)
        self.addon_count = ctk.CTkLabel(bar, text="", font=font(10), text_color=MUTED)
        self.addon_count.pack(side="right")

        ctk.CTkLabel(page, text="右侧开关可随时启用 / 关闭模组(关闭 = 隔离,可逆)",
                     font=font(9), text_color=MUTED).pack(anchor="w", pady=(0, 6))
        self.addon_scroll = ctk.CTkScrollableFrame(page, fg_color="transparent")
        self.addon_scroll.pack(fill="both", expand=True)
        return page

    def show_page(self, key):
        self.page = key
        for k, pg in self.pages.items():
            if k == key:
                pg.tkraise()
        for k, b in self.nav_btns.items():
            if k == key:
                b.configure(fg_color=ACCENT_DIM, text_color=ACCENT, hover_color=ACCENT_DIM)
            else:
                b.configure(fg_color="transparent", text_color=MUTED, hover_color=SURFACE_ALT)
        self.title_lbl.configure(text="冲突视图" if key == "conf" else "模组列表")

    # ------------------------------------------------------------------
    # 扫描
    # ------------------------------------------------------------------
    def set_status(self, text):
        self.status_lbl.configure(text=text)

    def refresh(self):
        if self.scanning:
            return
        self.scanning = True
        self.set_status("正在扫描模组…")
        self.path_lbl.configure(text=self.manager.addons_dir)

        def work():
            try:
                st = self.manager.state()
                self.root.after(0, lambda: self._apply_state(st, None))
            except Exception as e:  # noqa
                self.root.after(0, lambda: self._apply_state(None, e))

        threading.Thread(target=work, daemon=True).start()

    def _apply_state(self, st, err):
        self.scanning = False
        if err is not None:
            self.set_status("扫描出错")
            messagebox.showerror("扫描出错", str(err))
            return
        self.state = st
        s = st["summary"]
        self.stat_vals["enabled"].configure(text=str(s["enabled"]))
        self.stat_vals["conflicts"].configure(text=str(s["conflicts"]))
        self.stat_vals["pairs"].configure(text=str(s["pairs"]))
        self.stat_vals["disabled"].configure(text=str(s["disabled"]))
        self.path_lbl.configure(text=st["addons_dir"])
        cats = sorted({c["category"] for c in st["conflicts"]})
        self.conf_cat.configure(values=["全部"] + cats)
        if self.conf_cat.get() not in (["全部"] + cats):
            self.conf_cat.set("全部")
        self.render_conflicts()
        self.render_addons()
        self.set_status(f"完成 · {s['enabled'] + s['disabled']} 个模组 · {s['conflicts']} 处冲突")

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def render_conflicts(self):
        if self.state is None:
            return
        frame = self.conf_scroll
        for w in frame.winfo_children():
            w.destroy()
        cats = sorted({c["category"] for c in self.state["conflicts"]})
        self.conf_cat.configure(values=["全部"] + cats)
        if self.conf_cat.get() not in (["全部"] + cats):
            self.conf_cat.set("全部")

        q = self.conf_q.get().strip().lower()
        cat = self.conf_cat.get()
        rows = []
        for c in self.state["conflicts"]:
            if cat and cat != "全部" and c["category"] != cat:
                continue
            if q:
                hay = c["path"].lower() + " " + " ".join(
                    a["display_name"].lower() for a in c["addons"])
                if q not in hay:
                    continue
            rows.append(c)
        self.conf_count.configure(text=f"{len(rows)} / {len(self.state['conflicts'])} 处冲突")

        if not rows:
            txt = "未发现模组冲突" if not self.state["conflicts"] else "没有匹配当前筛选条件的冲突"
            ctk.CTkLabel(frame, text=txt, font=font(12), text_color=MUTED).pack(pady=40)
            return
        for c in rows:
            self._conflict_group(frame, c)

    def _conflict_group(self, parent, c):
        card = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=12,
                            border_width=1, border_color=BORDER)
        card.pack(fill="x", pady=6, padx=2)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(top, text=c["path"],
                     font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
                     text_color=GREEN, wraplength=700, justify="left").pack(side="left")
        ctk.CTkLabel(top, text="  " + c["category"], font=font(9),
                     text_color=YELLOW).pack(side="left")
        ctk.CTkLabel(top, text=f'{len(c["addons"])} 个模组', font=font(9),
                     text_color=MUTED).pack(side="right")
        for a in c["addons"]:
            self._conflict_mod_row(card, c, a)

    def _conflict_mod_row(self, parent, c, a):
        row = ctk.CTkFrame(parent, fg_color=SURFACE_ALT, corner_radius=9)
        row.pack(fill="x", padx=14, pady=4)
        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=12, pady=7)
        name_line = ctk.CTkFrame(info, fg_color="transparent")
        name_line.pack(anchor="w")
        ctk.CTkLabel(name_line, text=a["display_name"], font=font(11, "bold"),
                     text_color=TEXT).pack(side="left")
        if a.get("target"):
            ctk.CTkLabel(name_line, text="   " + a["target"], font=font(9),
                         text_color=BLUE).pack(side="left")
        wid = f"  ·  工坊 {a['workshop_id']}" if a.get("workshop_id") else ""
        ctk.CTkLabel(info, text=(a.get("location", "") + wid), font=font(9),
                     text_color=MUTED, wraplength=600, justify="left").pack(anchor="w")

        ctrl = ctk.CTkFrame(row, fg_color="transparent")
        ctrl.pack(side="right", padx=10)
        ctk.CTkButton(ctrl, text="只保留此", width=74, height=30, corner_radius=8,
                      font=font(10), fg_color="#2f3a24", hover_color="#3a4a2c",
                      text_color="#bfe08a",
                      command=lambda c=c, a=a: self._keep_only(c, a["uid"])
                      ).pack(side="left", padx=4)
        if a.get("workshop_id"):
            ctk.CTkButton(ctrl, text="工坊", width=46, height=30, corner_radius=8,
                          font=font(10), fg_color=SURFACE, hover_color=SURFACE_HI,
                          command=lambda w=a["workshop_id"]: self._safe_open_ws(w)
                          ).pack(side="left", padx=3)
        ctk.CTkButton(ctrl, text="文件夹", width=58, height=30, corner_radius=8,
                      font=font(10), fg_color=SURFACE, hover_color=SURFACE_HI,
                      command=lambda a=a: self._do(self.manager.open_folder,
                                                   a.get("unit_id") or a["location"], refresh=False)
                      ).pack(side="left", padx=3)
        sw = ctk.CTkSwitch(ctrl, text="启用", font=font(10), width=44,
                           progress_color=GREEN, button_color="#ffffff",
                           button_hover_color="#ffffff")
        sw.select()
        sw.pack(side="left", padx=(8, 0))
        sw.configure(command=lambda c=c, a=a, sw=sw: self._on_conflict_switch(c, a, sw))

    def _on_conflict_switch(self, c, a, sw):
        if bool(sw.get()):
            return  # 目前是启用状态,无需处理
        try:
            ok, msg = self.manager.disable(a.get("unit_id") or a["uid"])
        except Exception as e:  # noqa
            ok, msg = False, str(e)
        if not ok:
            messagebox.showerror("操作失败", msg)
            sw.select()
            return
        self.set_status(msg)
        self.refresh()

    def _keep_only(self, c, keep_uid):
        others = [a for a in c["addons"] if a["uid"] != keep_uid]
        if not others:
            return
        names = "\n".join("· " + a["display_name"] for a in others)
        if not messagebox.askyesno(
                "只保留此模组",
                f"将关闭以下 {len(others)} 个模组(可逆):\n\n{names}\n\n继续?"):
            return
        for a in others:
            try:
                ok, msg = self.manager.disable(a.get("unit_id") or a["uid"])
            except Exception as e:  # noqa
                ok, msg = False, str(e)
            if not ok:
                messagebox.showerror("操作失败", msg)
                break
        self.refresh()

    def render_addons(self):
        if self.state is None:
            return
        frame = self.addon_scroll
        for w in frame.winfo_children():
            w.destroy()
        cats = sorted({a["category"] for a in self.state["addons"]}, key=_cat_key)
        self.addon_cat.configure(values=["全部"] + cats)
        if self.addon_cat.get() not in (["全部"] + cats):
            self.addon_cat.set("全部")

        q = self.addon_q.get().strip().lower()
        only = self.only_conf.get()
        cat = self.addon_cat.get()
        rows = []
        for a in self.state["addons"]:
            if only and not a["conflict_count"]:
                continue
            if cat and cat != "全部" and a["category"] != cat:
                continue
            if q:
                hay = (a["display_name"] + " " + a["location"] + " " +
                       (a["workshop_id"] or "") + " " + a["category"]).lower()
                if q not in hay:
                    continue
            rows.append(a)
        self.addon_count.configure(text=f"{len(rows)} / {len(self.state['addons'])} 个模组")

        if not rows:
            ctk.CTkLabel(frame, text="没有模组", font=font(12), text_color=MUTED).pack(pady=40)
            return
        groups = {}
        for a in rows:
            groups.setdefault(a["category"], []).append(a)
        for category in sorted(groups, key=_cat_key):
            self._addon_group_header(frame, category, len(groups[category]))
            for a in groups[category]:
                self._addon_row(frame, a)

    def _addon_group_header(self, parent, category, n):
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", padx=2, pady=(14, 5))
        ctk.CTkLabel(hdr, text=category, font=font(12, "bold"),
                     text_color=ACCENT).pack(side="left")
        ctk.CTkLabel(hdr, text=f"{n} 个", font=font(10), text_color=MUTED).pack(
            side="left", padx=8)
        ctk.CTkFrame(hdr, height=1, fg_color=BORDER).pack(
            side="left", fill="x", expand=True, padx=(6, 0), pady=6)

    def _addon_row(self, parent, a):
        disabled = a["disabled"]
        border = RED if a["conflict_count"] else BORDER
        card = ctk.CTkFrame(parent, fg_color=(SURFACE_ALT if disabled else SURFACE),
                            corner_radius=10, border_width=1, border_color=border)
        card.pack(fill="x", pady=3, padx=2)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=14, pady=8)
        line = ctk.CTkFrame(info, fg_color="transparent")
        line.pack(anchor="w", fill="x")
        ctk.CTkLabel(line, text=a["display_name"], font=font(11, "bold"),
                     text_color=(MUTED if disabled else TEXT)).pack(side="left")
        if a.get("target"):
            ctk.CTkLabel(line, text="   " + a["target"], font=font(9),
                         text_color=BLUE).pack(side="left")
        if a["conflict_count"]:
            ctk.CTkLabel(line, text=f"   ⚠ {a['conflict_count']} 处冲突",
                         font=font(10, "bold"), text_color="#ff7a6a").pack(side="left")
        if disabled:
            ctk.CTkLabel(line, text="   · 已关闭", font=font(10),
                         text_color=MUTED).pack(side="left")
        meta = a["source"] + (f"  ·  工坊 {a['workshop_id']}" if a["workshop_id"] else "") \
            + f"  ·  {a['file_count']} 个文件"
        ctk.CTkLabel(info, text=meta, font=font(9), text_color=MUTED).pack(anchor="w")
        ctk.CTkLabel(info, text=a["location"], font=font(9), text_color="#6f7580",
                     wraplength=560, justify="left").pack(anchor="w")

        ctrl = ctk.CTkFrame(card, fg_color="transparent")
        ctrl.pack(side="right", padx=12, pady=8)
        sw_text = "已关闭" if disabled else "启用"
        sw = ctk.CTkSwitch(ctrl, text=sw_text, font=font(10),
                           progress_color=(SURFACE_HI if disabled else GREEN),
                           button_color="#ffffff", button_hover_color="#ffffff",
                           width=44)
        if not disabled:
            sw.select()
        sw.pack(side="right", padx=(10, 0))
        sw.configure(command=lambda a=a, sw=sw: self._on_switch(a, sw))

        if a["workshop_id"]:
            ctk.CTkButton(ctrl, text="工坊", width=46, height=30, corner_radius=8,
                          font=font(10), fg_color=SURFACE_ALT, hover_color=SURFACE_HI,
                          command=lambda w=a["workshop_id"]: self._safe_open_ws(w)
                          ).pack(side="left", padx=3)
        ctk.CTkButton(ctrl, text="文件夹", width=58, height=30, corner_radius=8,
                      font=font(10), fg_color=SURFACE_ALT, hover_color=SURFACE_HI,
                      command=lambda a=a: self._do(self.manager.open_folder,
                                                   a.get("unit_id") or a["location"], refresh=False)
                      ).pack(side="left", padx=3)
        ctk.CTkButton(ctrl, text="删除", width=46, height=30, corner_radius=8,
                      font=font(10), fg_color="#3a2526", hover_color="#4a2c2d",
                      text_color="#ff8a8a", command=lambda a=a: self.delete_one(a)
                      ).pack(side="left", padx=3)

    def _toggle_empty(self, label, tree, empty, text):
        if empty:
            label.configure(text=text)
            label.place(relx=0.5, rely=0.45, anchor="center")
        else:
            label.place_forget()

    # ------------------------------------------------------------------
    # 选中 / 操作
    # ------------------------------------------------------------------
    def _do(self, fn, *args, refresh=True):
        try:
            ok, msg = fn(*args)
        except Exception as e:  # noqa
            ok, msg = False, f"操作异常: {e}"
        if ok:
            self.set_status(msg)
        else:
            messagebox.showerror("操作失败", msg)
        if refresh and ok:
            self.refresh()

    def _safe_open_ws(self, wid):
        self._do(self.manager.open_workshop, wid, refresh=False)

    def _on_switch(self, a, sw):
        want_enable = bool(sw.get())
        if want_enable == (not a["disabled"]):
            return
        fn = self.manager.enable if want_enable else self.manager.disable
        try:
            ok, msg = fn(a.get("unit_id") or a["uid"])
        except Exception as e:  # noqa
            ok, msg = False, str(e)
        if not ok:
            messagebox.showerror("操作失败", msg)
            if a["disabled"]:
                sw.deselect()
            else:
                sw.select()
            return
        self.set_status(msg)
        self.refresh()

    def delete_one(self, a):
        if not messagebox.askyesno(
                "确认删除",
                f"确定删除这个模组的本地文件吗?\n\n· {a['display_name']}\n\n"
                "此操作不可恢复!若只是暂时不用,请用右侧开关关闭(可逆)。"):
            return
        from tkinter import simpledialog
        typed = simpledialog.askstring("二次确认", "请输入 DELETE 以确认删除:",
                                       parent=self.root)
        if typed is None or typed.strip() != "DELETE":
            self.set_status("已取消删除")
            return
        try:
            ok, msg = self.manager.delete(a.get("unit_id") or a["uid"])
        except Exception as e:  # noqa
            ok, msg = False, str(e)
        if ok:
            self.set_status(msg)
            self.refresh()
        else:
            messagebox.showerror("删除失败", msg)

    def open_disabled(self):
        self._do(self.manager.open_disabled, refresh=False)

    def choose_dir(self):
        d = filedialog.askdirectory(title="选择 L4D2 目录(游戏根目录或 left4dead2)")
        if not d:
            return
        addons = resolve_addons_dir(d)
        if not addons:
            messagebox.showerror("错误", "该目录下找不到 left4dead2/addons。")
            return
        self.manager = dash.Manager(addons)
        self.refresh()

    # ------------------------------------------------------------------
    def on_conflict_double(self, _event=None):
        sel = self.conf_tree.selection()
        if not sel:
            return
        c = self.conflict_rows.get(sel[0])
        if c:
            ConflictDialog(self.root, self.manager, c, on_action=self.refresh)


# ---------------------------------------------------------------------------
# 冲突详情弹窗
# ---------------------------------------------------------------------------

class ConflictDialog:
    def __init__(self, parent, manager, conflict, on_action=None):
        self.manager = manager
        self.on_action = on_action
        win = ctk.CTkToplevel(parent)
        self.win = win
        win.title("冲突详情")
        win.geometry("760x480")
        win.configure(fg_color=BG)
        win.transient(parent)

        ctk.CTkLabel(win, text="冲突文件", font=font(10), text_color=MUTED).pack(
            anchor="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(win, text=conflict["path"], font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
                     text_color=GREEN, wraplength=700, justify="left").pack(
            anchor="w", padx=20)
        ctk.CTkLabel(win, text="类型: " + conflict["category"], font=font(10),
                     text_color=MUTED).pack(anchor="w", padx=20, pady=(2, 10))
        ctk.CTkLabel(win, text="提供该文件的模组(后挂载者覆盖先挂载者):",
                     font=font(10), text_color=MUTED).pack(anchor="w", padx=20)

        body = ctk.CTkScrollableFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=10)
        for a in conflict["addons"]:
            row = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=12,
                               border_width=1, border_color=BORDER)
            row.pack(fill="x", pady=5)
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=12, pady=10)
            ctk.CTkLabel(info, text=a["display_name"], font=font(11, "bold"),
                         text_color=TEXT).pack(anchor="w")
            wid = f"   [工坊ID {a['workshop_id']}]" if a.get("workshop_id") else ""
            ctk.CTkLabel(info, text=(a.get("location", "") + wid), font=font(9),
                         text_color=MUTED, wraplength=420, justify="left").pack(anchor="w")
            btns = ctk.CTkFrame(row, fg_color="transparent")
            btns.pack(side="right", padx=10)
            sw = ctk.CTkSwitch(btns, text="启用", font=font(10), width=44,
                               progress_color=GREEN, button_color="#ffffff",
                               button_hover_color="#ffffff")
            sw.select()
            sw.configure(command=lambda u=a.get("unit_id") or a["uid"]: self._toggle_off(u))
            sw.pack(side="left", padx=(0, 10))
            if a.get("workshop_id"):
                ctk.CTkButton(btns, text="工坊", width=56, height=30, corner_radius=8,
                              font=font(10), fg_color=SURFACE_ALT, hover_color=SURFACE_HI,
                              command=lambda w=a["workshop_id"]: self._safe(self.manager.open_workshop, w)
                              ).pack(side="left", padx=3)
            ctk.CTkButton(btns, text="文件夹", width=64, height=30, corner_radius=8,
                          font=font(10), fg_color=SURFACE_ALT, hover_color=SURFACE_HI,
                          command=lambda p=a.get("unit_id") or a["location"]: self._safe(self.manager.open_folder, p)
                          ).pack(side="left", padx=3)
            ctk.CTkButton(btns, text="隔离", width=56, height=30, corner_radius=8,
                          font=font(10), fg_color=ACCENT, hover_color=ACCENT_HOVER,
                          text_color="#1a1008",
                          command=lambda u=a.get("unit_id") or a["uid"]: self._isolate(u)).pack(side="left", padx=3)

        ctk.CTkButton(win, text="关闭", width=100, height=36, corner_radius=9,
                      font=font(11), fg_color=SURFACE_ALT, hover_color=SURFACE_HI,
                      command=win.destroy).pack(pady=(0, 16))

    def _safe(self, fn, *args):
        ok, msg = fn(*args)
        if not ok:
            messagebox.showerror("操作失败", msg, parent=self.win)

    def _isolate(self, uid):
        ok, msg = self.manager.disable(uid)
        if ok:
            messagebox.showinfo("完成", msg, parent=self.win)
            self.win.destroy()
            if self.on_action:
                self.on_action()
        else:
            messagebox.showerror("操作失败", msg, parent=self.win)

    def _toggle_off(self, uid):
        ok, msg = self.manager.disable(uid)
        if ok:
            messagebox.showinfo("已关闭", msg, parent=self.win)
            self.win.destroy()
            if self.on_action:
                self.on_action()
        else:
            messagebox.showerror("操作失败", msg, parent=self.win)


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def run_selftest(game_dir=None):
    addons = resolve_addons_dir(game_dir)
    try:
        if not addons:
            result = {"ok": False, "error": "找不到游戏目录"}
        else:
            mgr = dash.Manager(addons)
            st = mgr.state()
            result = {
                "ok": True,
                "addons_dir": addons,
                "summary": st["summary"],
                "conflicts": [c["path"] for c in st["conflicts"]],
                "ctk": getattr(ctk, "__version__", "?"),
            }
    except Exception as e:  # noqa
        result = {"ok": False, "error": repr(e)}
    out = os.path.join(os.getcwd(), "_exe_selftest.txt")
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa
        pass
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa
        pass

    p = argparse.ArgumentParser()
    p.add_argument("--game-dir", default=None)
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)

    if args.selftest:
        return run_selftest(args.game_dir)

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    addons = resolve_addons_dir(args.game_dir)
    root = ctk.CTk()
    if not addons:
        root.withdraw()
        messagebox.showinfo("未找到游戏",
                            "没有自动找到《Left 4 Dead 2》。\n请选择游戏目录。")
        d = filedialog.askdirectory(title="选择 L4D2 目录(游戏根目录或 left4dead2)")
        root.deiconify()
        addons = resolve_addons_dir(d) if d else None
        if not addons:
            messagebox.showerror("错误", "未选择有效的游戏目录,程序退出。")
            return 2

    App(root, addons)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
