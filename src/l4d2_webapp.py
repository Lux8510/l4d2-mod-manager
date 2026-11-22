#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L4D2 模组管理器 · 桌面 WebView 版
=================================

在原生窗口里用 WebView2 显示本地网页界面(玻璃拟态 UI),不是浏览器标签页。
若系统缺少 WebView2 运行时,则自动回退到默认浏览器打开同一界面。

实现:
  * 后台线程启动本地 HTTP 服务(l4d2_dashboard.Handler)
  * pywebview 创建原生窗口加载 http://127.0.0.1:<port>/
  * 关闭窗口即退出(后台服务随进程结束)

命令行:
  python l4d2_webapp.py                 启动
  python l4d2_webapp.py --game-dir PATH 指定游戏目录
  python l4d2_webapp.py --selftest      仅自检(不开窗口)
"""

import argparse
import json
import os
import sys
import threading
import webbrowser

import l4d2_dashboard as dash
import l4d2_conflict_checker as core


def _addons_ok(addons):
    try:
        with os.scandir(addons):
            return True
    except OSError:
        return False


def resolve_addons_dir(game_dir=None):
    if game_dir:
        try:
            addons = core.resolve_addons_dir(game_dir)
        except FileNotFoundError:
            return None
        return addons if _addons_ok(addons) else None
    for game in core.find_l4d2_dirs():
        addons = os.path.join(game, "left4dead2", "addons")
        if _addons_ok(addons):
            return addons
    found = core.find_l4d2_dirs()
    if found:
        return os.path.join(found[0], "left4dead2", "addons")
    return None


def make_server(addons_dir):
    httpd = dash.create_server(addons_dir)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_port


def pick_game_dir():
    """自动检测失败时,弹出文件夹选择框让用户指定游戏目录。"""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except Exception:  # noqa
        return None
    try:
        r = tk.Tk()
        r.withdraw()
        r.attributes("-topmost", True)
        messagebox.showinfo(
            "L4D2 模组管理器",
            "没有自动找到《Left 4 Dead 2》。\n"
            "请选择游戏安装目录(即包含 left4dead2 文件夹的那一层)。")
        d = filedialog.askdirectory(title="选择 Left 4 Dead 2 目录")
        r.destroy()
        return d or None
    except Exception:  # noqa
        return None


def pick_among(paths):
    """多个有效安装时,让用户选择一个。"""
    if not paths:
        return None
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:  # noqa
        return paths[0]
    try:
        r = tk.Tk()
        r.title("选择 Left 4 Dead 2 安装")
        r.geometry("640x280")
        r.attributes("-topmost", True)
        tk.Label(r, text="检测到多个《Left 4 Dead 2》安装,请选择要管理的目录:").pack(
            anchor="w", padx=12, pady=(12, 6))
        lb = tk.Listbox(r, width=100, height=8)
        for p in paths:
            lb.insert("end", p)
        lb.selection_set(0)
        lb.pack(fill="both", expand=True, padx=12)
        result = {"path": paths[0]}

        def ok():
            sel = lb.curselection()
            if sel:
                result["path"] = paths[sel[0]]
            r.destroy()

        tk.Button(r, text="确定", command=ok).pack(pady=8)
        r.mainloop()
        return result["path"]
    except Exception:  # noqa
        return paths[0]


def _tk_available():
    try:
        import tkinter  # noqa
        return True
    except Exception:  # noqa
        return False


def show_error(msg):
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("L4D2 模组管理器", msg)
        r.destroy()
    except Exception:  # noqa
        print(msg, file=sys.stderr)


def run_selftest(game_dir=None):
    addons = resolve_addons_dir(game_dir)
    result = {"ok": False}
    try:
        if not addons:
            result = {"ok": False, "error": "找不到游戏目录"}
        else:
            if "%%L4D2_TOKEN%%" not in dash.load_page_html():
                raise ValueError("page.html 缺少授权标记")
            mgr = dash.Manager(addons)
            st = mgr.state()
            result = {
                "ok": True,
                "addons_dir": addons,
                "summary": st["summary"],
                "webview_available": _webview_available(),
                "tkinter_available": _tk_available(),
                "page_bundled": True,
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


def _webview_available():
    try:
        import webview  # noqa
        return True
    except Exception:
        return False


def _webview2_available():
    if os.name != "nt":
        return False
    import winreg
    path = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive, view in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                       (winreg.HKEY_CURRENT_USER, 0)):
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
            if isinstance(version, str) and version.strip() not in ("", "0.0.0.0"):
                return True
        except OSError:
            continue
    return False


def _wait_for_browser_close():
    try:
        import tkinter as tk
        window = tk.Tk()
    except Exception:
        print("按 Ctrl+C 停止本地服务。")
        threading.Event().wait()
        return
    window.title("L4D2 模组管理器")
    tk.Label(window, text="管理界面已在浏览器中打开。").pack(padx=24, pady=12)
    tk.Button(window, text="关闭本地服务", command=window.destroy).pack(pady=(0, 12))
    window.mainloop()


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa
        pass

    p = argparse.ArgumentParser()
    p.add_argument("--game-dir", default=None)
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--apply-on-exit", action="store_true",
                   help=argparse.SUPPRESS)
    p.add_argument("--install-identity", help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    if args.selftest:
        return run_selftest(args.game_dir)

    if args.apply_on_exit:
        import l4d2_dashboard as dash
        addons = args.game_dir
        if not addons or not _addons_ok(addons):
            return 2
        return dash.apply_on_exit(addons, args.install_identity)

    addons = None
    if args.game_dir:
        addons = resolve_addons_dir(args.game_dir)
    else:
        found = core.find_l4d2_dirs()
        usable = [g for g in found if _addons_ok(os.path.join(g, "left4dead2", "addons"))]
        if len(usable) > 1:
            chosen = pick_among(usable)
            if chosen:
                addons = os.path.join(chosen, "left4dead2", "addons")
        elif usable:
            addons = os.path.join(usable[0], "left4dead2", "addons")
    if not addons:
        d = pick_game_dir()
        if d:
            addons = resolve_addons_dir(d)
    if not addons:
        show_error("未自动找到《Left 4 Dead 2》。\n"
                   "请用 --game-dir 指定游戏目录,或先通过 Steam 安装游戏。")
        return 2

    httpd, port = make_server(addons)
    url = f"http://127.0.0.1:{port}/"
    try:
        try:
            import webview
        except Exception:
            webview = None

        if webview is not None and (os.name != "nt" or _webview2_available()):
            try:
                webview.create_window(
                    "L4D2 模组管理器", url,
                    width=1200, height=800, min_size=(900, 600),
                    background_color="#0a0c11",
                )
                webview.start(gui="edgechromium" if os.name == "nt" else None)
                return 0
            except Exception as e:  # noqa
                print(f"WebView 启动失败,回退到浏览器: {e}", file=sys.stderr)

        # 回退:默认浏览器打开,保持服务运行
        if not webbrowser.open(url):
            show_error(f"无法打开默认浏览器。可手动访问 {url}")
        _wait_for_browser_close()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            httpd.shutdown()
        except Exception:  # noqa
            pass
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
