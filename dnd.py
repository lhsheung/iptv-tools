"""Windows 原生文件拖放（WM_DROPFILES）——零依赖。

为什么不用 tkinterdnd2
----------------------
tkinterdnd2 需要额外分发 tkdnd 的平台 DLL，还必须把 ``tk.Tk()`` 换成
``TkinterDnD.Tk()``。对一个 28 MB 的单文件 exe 来说这是纯负担，且 PyInstaller
要专门写 hook 收集二进制。这里直接用 Win32：

1. ``DragAcceptFiles`` 给窗口打上接受文件的样式；
2. 子类化窗口过程，拦截 ``WM_DROPFILES``；
3. ``DragQueryFileW`` 取出 UTF-16 路径列表，``DragFinish`` 释放句柄；
4. WNDPROC 只把路径放进队列，由主线程 80ms 轮询取走并交给界面。

第 4 步不能省：**Tk 在消息循环里会释放 GIL**，从窗口过程直接调
``root.after()`` 会让 Python 3.14 因线程状态为 NULL 直接 fatal
（PyEval_RestoreThread）。窗口过程里只允许纯 Python + ctypes 调用。

全程只用标准库 ctypes，源码模式与冻结模式行为一致，不增加包体积。

覆盖的两种"拖拽打开"
--------------------
- 文件拖到**已运行的窗口** → 本模块的 WM_DROPFILES；
- 文件拖到 **exe 图标**上启动 → Windows 把路径放进 ``sys.argv``，
  由 ``main.py`` 走同一个 ``app.open_files()`` 入口。

已知限制
--------
只有"放下"这一刻有消息，做不到悬停高亮（那要实现 IDropTarget COM 接口）。
系统会在拖拽经过时自动显示复制光标，已经是基本反馈。
若程序以管理员身份运行、资源管理器是普通权限，UIPI 会拦截跨完整性级别的
拖放消息 —— 以普通权限运行即可，这是 Windows 的安全策略。
"""

from __future__ import annotations

import atexit
import ctypes
import os
import sys
import time
from ctypes import wintypes

__all__ = [
    "PLAYLIST_EXTS",
    "is_playlist",
    "collect_playlist_paths",
    "enable_file_drop",
    "disable_file_drop",
]

#: 与「打开文件」对话框保持同一套扩展名
PLAYLIST_EXTS = frozenset({".m3u", ".m3u8", ".txt", ".dpl"})

#: 拖进整个目录时最多扫多少个条目，避免一次拖入素材盘根目录卡死界面
_MAX_DIR_ENTRIES = 300


# ── 纯函数部分（不碰 Win32，可在任何平台 import / 单测） ──────────────────────

def is_playlist(path: str) -> bool:
    """扩展名是否是可解析的播放列表。"""
    return os.path.splitext(path)[1].lower() in PLAYLIST_EXTS


def collect_playlist_paths(paths):
    """把拖入项整理成 ``(accepted, rejected)`` 两个列表。

    - 支持一次拖多个文件、多目录；目录只扫一层，不做递归。
    - ``accepted`` 去重（Windows 路径大小写不敏感）并转成绝对路径。
    - 不支持的扩展名进 ``rejected``，界面据此提示"已忽略 N 项"。
    - 路径两端可能带引号（某些 shell / 快捷方式），统一剥掉。
    """
    accepted, rejected = [], []
    seen = set()

    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            continue
        path = os.path.abspath(os.path.expandvars(raw.strip().strip('"')))

        if os.path.isdir(path):
            found = []
            try:
                with os.scandir(path) as it:
                    for i, entry in enumerate(it):
                        if i >= _MAX_DIR_ENTRIES:
                            break
                        if entry.is_file() and is_playlist(entry.path):
                            found.append(entry.path)
            except OSError:
                pass
            if not found:
                rejected.append(path)
            candidates = found
        else:
            candidates = [path]

        for candidate in candidates:
            if not is_playlist(candidate):
                rejected.append(candidate)
                continue
            key = os.path.normcase(candidate)
            if key in seen:
                continue
            seen.add(key)
            accepted.append(candidate)

    return accepted, rejected


# ── Win32 部分（所有 windll 访问都在函数内，非 Windows 也能 import） ──────────

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
INVALID_INDEX = 0xFFFFFFFF

_LRESULT = ctypes.c_ssize_t          # 32/64 位都跟随指针宽度
_WNDPROC = ctypes.WINFUNCTYPE(
    _LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

#: hwnd -> {"old", "proc", "root", "on_files", "queue"}
#: 必须强引用 WNDPROC 对象，否则被 GC 后系统会调到已释放的回调而崩溃。
_HOOKS: dict[int, dict] = {}
_last_hdrop: tuple[int, float] = (0, 0.0)
#: WNDPROC 只入队，由主线程轮询取走；80ms 的延迟用户无感
_POLL_MS = 80
_atexit_registered = False


def _setter():
    """64 位进程用 SetWindowLongPtrW，32 位只有 SetWindowLongW。"""
    user32 = ctypes.windll.user32
    if ctypes.sizeof(ctypes.c_void_p) == 8 and hasattr(user32, "SetWindowLongPtrW"):
        fn = user32.SetWindowLongPtrW
    else:
        fn = user32.SetWindowLongW
    fn.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    fn.restype = ctypes.c_void_p
    return fn


def _query_drop_files(hdrop: int) -> list[str]:
    """从 HDROP 句柄读出路径列表，并负责调用 DragFinish。"""
    shell32 = ctypes.windll.shell32
    shell32.DragQueryFileW.argtypes = [
        ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT
    ]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragFinish.argtypes = [ctypes.c_void_p]

    hd = ctypes.c_void_p(hdrop)
    try:
        count = shell32.DragQueryFileW(hd, INVALID_INDEX, None, 0)
        paths = []
        for i in range(count):
            length = shell32.DragQueryFileW(hd, i, None, 0)
            if not length:
                continue
            buf = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(hd, i, buf, length + 1)
            paths.append(buf.value)
        return paths
    finally:
        # 不论成功与否都要释放，否则资源管理器那边会一直挂着这次拖放
        shell32.DragFinish(hd)


def _call_original(entry, hwnd, msg, wparam, lparam) -> int:
    user32 = ctypes.windll.user32
    try:
        if entry and entry.get("old"):
            user32.CallWindowProcW.argtypes = [
                ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                wintypes.WPARAM, wintypes.LPARAM
            ]
            user32.CallWindowProcW.restype = _LRESULT
            return user32.CallWindowProcW(
                ctypes.c_void_p(entry["old"]), hwnd, msg, wparam, lparam
            )
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        user32.DefWindowProcW.restype = _LRESULT
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    except Exception:
        return 0


def _wnd_proc(hwnd, msg, wparam, lparam) -> int:
    """窗口过程。**绝不能在这里调用任何 Tk / Tcl API。**

    Tk 在消息循环里会释放 GIL；若从 WNDPROC 调 ``root.after()``，
    Python 3.14 会因线程状态为 NULL 直接 fatal（PyEval_RestoreThread）。
    所以这里只解析路径、放进队列，由主线程的定时轮询交给界面。
    """
    global _last_hdrop
    entry = _HOOKS.get(int(hwnd or 0))

    if msg == WM_DROPFILES and entry is not None:
        try:
            hdrop = int(wparam or 0)
            if not hdrop:
                return 0
            now = time.monotonic()
            # 顶层框架和客户区子窗口都注册了接受文件样式。若系统把同一
            # HDROP 投递两次，DragFinish 只能调一次 —— 所以去重必须放在
            # DragQueryFile 之前，否则会对已释放的句柄二次操作。
            if hdrop == _last_hdrop[0] and now - _last_hdrop[1] < 0.3:
                return 0
            _last_hdrop = (hdrop, now)
            paths = _query_drop_files(hdrop)
            if paths:
                entry["queue"].append(paths)
        except Exception:
            pass
        return 0

    return _call_original(entry, hwnd, msg, wparam, lparam)


def _poll(root, queue, on_files) -> None:
    """运行在主线程：把窗口过程收集到的路径交给界面，并续上下一次轮询。"""
    if not any(e.get("queue") is queue for e in _HOOKS.values()):
        return                                  # 钩子已卸载，停止轮询
    try:
        while queue:
            paths = queue.pop(0)
            try:
                on_files(paths)
            except Exception:
                pass
    except Exception:
        pass
    try:
        root.after(_POLL_MS, lambda: _poll(root, queue, on_files))
    except Exception:
        pass                                    # 窗口已销毁，静默结束


def disable_file_drop(root=None) -> None:
    """恢复原窗口过程并取消接受文件样式。传 root 只清理该窗口的钩子。"""
    try:
        setter = _setter()
        # 注意：DragAcceptFiles / DragQueryFile / DragFinish 都在 shell32.dll，
        # 不在 user32.dll —— 用错库会直接 AttributeError，注册静默失败。
        shell32 = ctypes.windll.shell32
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        for hwnd, entry in list(_HOOKS.items()):
            if root is not None and entry["root"] is not root:
                continue
            try:
                setter(hwnd, GWLP_WNDPROC, ctypes.c_void_p(entry["old"]))
                shell32.DragAcceptFiles(hwnd, False)
            except Exception:
                pass
            _HOOKS.pop(hwnd, None)
    except Exception:
        pass


def _restore_all() -> None:
    disable_file_drop()


def enable_file_drop(root, on_files) -> bool:
    """让窗口接受拖入的文件，返回是否启用成功。

    ``on_files(paths)`` 会在 Tk 主线程被调用，参数是原始路径列表（未过滤），
    由界面决定如何提示"忽略了多少个不支持的文件"。
    """
    if sys.platform != "win32":
        return False
    global _atexit_registered
    try:
        disable_file_drop(root)               # 重复调用先拆旧钩子，避免嵌套
        root.update_idletasks()
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]

        child = int(root.winfo_id())
        top = int(user32.GetParent(child) or child)
        # 拖到标题栏落在顶层框架，拖到内容区落在 Tk 的子窗口，两个都注册
        targets = list(dict.fromkeys(h for h in (top, child) if h))

        setter = _setter()
        queue: list[list[str]] = []           # 所有 hwnd 共享，便于统一去重
        installed = 0
        for hwnd in targets:
            proc = _WNDPROC(_wnd_proc)
            old = setter(hwnd, GWLP_WNDPROC, ctypes.cast(proc, ctypes.c_void_p))
            if not old:
                continue
            _HOOKS[hwnd] = {
                "old": int(old), "proc": proc,
                "root": root, "on_files": on_files, "queue": queue,
            }
            shell32.DragAcceptFiles(hwnd, True)
            installed += 1

        if not installed:
            disable_file_drop(root)
            return False

        # 轮询必须在主线程、GIL 正常持有的时候注册（不能在 WNDPROC 里）
        root.after(_POLL_MS, lambda: _poll(root, queue, on_files))

        # 窗口销毁时恢复，避免 Tk 退出后系统还调到已被回收的 Python 回调
        def cleanup(event=None):
            if event is not None and event.widget is not root:
                return                      # 子控件的 Destroy 不管
            disable_file_drop(root)

        root.bind("<Destroy>", cleanup, add="+")
        if not _atexit_registered:
            atexit.register(_restore_all)
            _atexit_registered = True
        return True
    except Exception:
        # 部分窗口已装钩子时必须回滚，否则残留的 WNDPROC 会一直转发到
        # 一个没有注册 DragAcceptFiles 的窗口过程上，行为不可预期。
        disable_file_drop(root)
        return False
