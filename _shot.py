"""截图助手：把 tkinter 窗口抓成 png，供人工核对界面。

坐标换算的坑
------------
本机 DPI 缩放 125%。在**声明了 DPI 感知**之后（``SetProcessDpiAwareness(1)``），
``winfo_rootx/winfo_width`` 返回的就是物理像素，可直接用于 ``ImageGrab.grab``；
若再乘一次 ``theme.scale()``，反而会截偏（右侧混进桌面、左侧被切）。

反之，若进程**没有**声明 DPI 感知，Windows 会做坐标虚拟化，
Tk 报的是逻辑值，此时才需要乘缩放系数。

所以这里探测一下当前进程的 DPI 感知状态，动态决定是否换算，而不是硬编码。
"""

from __future__ import annotations

import sys

import theme


def _dpi_aware() -> bool:
    """当前进程是否已声明 DPI 感知。"""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        # GetProcessDpiAwareness 在 shcore.dll；失败说明系统太老，按未感知处理
        awareness = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness))
        return awareness.value != 0          # 0 = UNAWARE
    except Exception:
        return False


def grab_window(root, path: str, pad: int = 0) -> str | None:
    """抓取窗口客户区并保存，返回文件路径（失败返回 None）。

    抓屏拿的是"屏幕上此刻的像素"，如果窗口被别的程序盖住，截到的就是别人。
    所以截图前先把窗口提到最前并临时置顶，抓完再还原。
    """
    try:
        from PIL import ImageGrab
    except Exception as exc:                              # pragma: no cover
        print(f"[shot] 缺少 Pillow，无法截图: {exc}")
        return None

    was_topmost = False
    try:
        root.deiconify()
        root.lift()
        was_topmost = bool(root.attributes("-topmost"))
        root.attributes("-topmost", True)
        root.focus_force()
        root.update_idletasks()
        root.update()
    except Exception:
        pass

    factor = 1.0 if _dpi_aware() else (theme.scale() or 1.0)

    x = int(round(root.winfo_rootx() * factor)) - pad
    y = int(round(root.winfo_rooty() * factor)) - pad
    w = int(round(root.winfo_width() * factor)) + pad * 2
    h = int(round(root.winfo_height() * factor)) + pad * 2

    try:
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        img.save(path)
        print(f"[shot] {path}  size={img.size}  factor={factor:.2f}")
        return path
    except Exception as exc:                              # pragma: no cover
        print(f"[shot] 截图失败: {exc}")
        return None
    finally:
        try:
            if not was_topmost:
                root.attributes("-topmost", False)
        except Exception:
            pass


def grab_after(root, path: str, delay_ms: int = 600, pad: int = 0) -> None:
    """延迟抓图后关闭窗口，用于"启动即截图"的自动化验证。"""
    def _do():
        grab_window(root, path, pad=pad)
        root.destroy()

    root.after(delay_ms, _do)
