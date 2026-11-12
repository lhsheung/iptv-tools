"""程序入口。

DPI 说明
--------
``SetProcessDpiAwareness(1)`` 必须早于 ``tk.Tk()``，否则窗口在 125%/150% 缩放下
会被系统位图拉伸（文字发虚）。声明感知后 Tk 的坐标即物理像素，所以窗口尺寸
要用 ``theme.scale()`` 换算，才能在任意缩放下保持相同的视觉大小。

打包后的 exe 由 PyInstaller manifest 声明 PerMonitorV2，上面那个调用会返回
E_ACCESSDENIED 并被忽略（进程已经是感知的）。该模式下 Tk 自报 96dpi，真实 DPI
由 ``theme`` 走 Win32 查询补齐，并同步 ``tk scaling`` 让字体一起缩放。

文件拖放
--------
拖到已运行窗口由 ``dnd`` 用 Win32 ``WM_DROPFILES`` 实现（零第三方依赖），
拖到 exe 图标启动则由 ``sys.argv`` 承接；两者都走 ``IPTVApp.open_files()``。
"""

import os
import sys

if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk

# 打包后资源在 sys._MEIPASS（PyInstaller 解包目录）。这里只把它加进 sys.path，
# 不 chdir —— chdir 会让 config.json、导出文件的相对路径落到内部目录里，
# 用户在外面根本找不到。配置路径由 gui._config_path() 自己判断。
if getattr(sys, "frozen", False):
    _MEIPASS = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    if _MEIPASS not in sys.path:
        sys.path.insert(0, _MEIPASS)
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dnd                      # noqa: E402
import theme                    # noqa: E402
from gui import IPTVApp         # noqa: E402

#: 设计稿尺寸（逻辑像素）；实际窗口按屏幕缩放换算
WIN_W, WIN_H = 1120, 780
MIN_W, MIN_H = 940, 620


def _log_crash(exc: BaseException) -> str:
    """把启动期异常写到 exe 同级，GUI 程序闪退时用户才有线索可查。"""
    import traceback
    base = (os.path.dirname(os.path.abspath(sys.executable))
            if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "启动失败.log")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
    except Exception:
        path = "(无法写入日志)"
    return path


def main() -> None:
    root = tk.Tk()
    root.title("IPTV 直播源测试工具")

    scale = theme.init_scale(root)

    # 可选诊断：设 IPTVTOOLS_DPI_DEBUG=1 时把 DPI 决策写到 exe 同级，
    # 便于在没有控制台的 GUI 程序里排查高分屏问题。默认不产生任何文件。
    if os.environ.get("IPTVTOOLS_DPI_DEBUG"):
        try:
            base = (os.path.dirname(os.path.abspath(sys.executable))
                    if getattr(sys, "frozen", False)
                    else os.path.dirname(os.path.abspath(__file__)))
            with open(os.path.join(base, "dpi_debug.txt"), "w", encoding="utf-8") as f:
                f.write(f"scale={scale}\n")
                f.write(f"win32_dpi={theme._dpi_from_win32(root)}\n")
                f.write(f"tk_fpixels={root.winfo_fpixels('1i')}\n")
                f.write(f"tk_scaling={root.tk.call('tk', 'scaling')}\n")
                f.write(f"screen={root.winfo_screenwidth()}x{root.winfo_screenheight()}\n")
        except Exception:
            pass

    w, h = int(WIN_W * scale), int(WIN_H * scale)
    root.minsize(int(MIN_W * scale), int(MIN_H * scale))
    root.configure(bg=theme.CANVAS)

    # 居中（略微上移，视觉重心更稳）
    try:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 24)}")
    except Exception:
        root.geometry(f"{w}x{h}")

    app = IPTVApp(root)

    # 两条"拖拽打开"路径共用 app.open_files()：
    #   · 文件拖到已运行窗口 → dnd 的 WM_DROPFILES 回调
    #   · 文件拖到 exe 图标启动 → Windows 把路径放进 sys.argv
    # 后者延后到主循环里执行，避免首帧还没画出来就做文件解析。
    startup_files = sys.argv[1:]
    if startup_files:
        root.after(120, lambda paths=startup_files: app.open_files(paths))

    dnd_ok = dnd.enable_file_drop(root, app.open_files)

    # 诊断开关顺带记录拖放注册结果，方便在没有控制台的 exe 里核对
    # WM_DROPFILES 钩子是否真的装上（复用同一个诊断文件）。
    if os.environ.get("IPTVTOOLS_DPI_DEBUG"):
        try:
            base = (os.path.dirname(os.path.abspath(sys.executable))
                    if getattr(sys, "frozen", False)
                    else os.path.dirname(os.path.abspath(__file__)))
            with open(os.path.join(base, "dpi_debug.txt"), "a", encoding="utf-8") as f:
                f.write(f"dnd_enabled={dnd_ok}\n")
                f.write(f"dnd_hooks={len(dnd._HOOKS)}\n")
        except Exception:
            pass

    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:                              # noqa: BLE001
        log = _log_crash(exc)
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                f"程序启动失败：\n{exc}\n\n详细信息已写入：\n{log}",
                "IPTV Tools", 0x10)
        except Exception:
            pass
        raise
