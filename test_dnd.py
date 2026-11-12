# -*- coding: utf-8 -*-
"""文件拖放测试。

分三层验证，不只是"函数返回了什么"：
1. 纯函数：扩展名过滤、目录展开、去重、路径清洗；
2. 真实 Win32 消息：构造 DROPFILES + HGLOBAL，向真实 Tk 窗口投递
   WM_DROPFILES，验证子类化的 WNDPROC 确实收到并派回主线程；
3. 界面集成：拖入的文件真的被解析成频道、状态文案与切页都生效。

不进入 mainloop，只用 ``update()`` 驱动事件循环。
"""
import ctypes
import os
import sys
import tempfile
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

import tkinter as tk

import dnd
import gui as gui_mod

fail = []
ok = 0


def check(cond, msg):
    global ok
    if cond:
        ok += 1
        print(f"  ok  {msg}")
    else:
        fail.append(msg)
        print(f"  FAIL {msg}")


# ════════════════════════════════════════════════════════════════════════════
print("== 1. 纯函数：播放列表过滤 ==")

check(dnd.is_playlist("a.m3u"), "识别 .m3u")
check(dnd.is_playlist("A.M3U8"), "识别大写 .M3U8")
check(dnd.is_playlist("x.dpl"), "识别 .dpl")
check(not dnd.is_playlist("x.exe"), "拒绝 .exe")
check(not dnd.is_playlist("noext"), "拒绝无扩展名")

a, r = dnd.collect_playlist_paths(["a.m3u", "A.M3U", "b.exe", None, "  "])
check(len(a) == 1, "大小写不敏感去重")
check(len(r) == 1 and r[0].endswith("b.exe"), "不支持的文件进 rejected")
check(all(os.path.isabs(p) for p in a), "输出均为绝对路径")

a2, _ = dnd.collect_playlist_paths(['"C:\\some path\\list.txt"', "b.dpl"])
check(a2[0] == os.path.abspath("C:\\some path\\list.txt"), "剥掉包裹引号")
check(len(a2) == 2, "保留两个不同文件")

# 目录只扫一层，且只收播单文件
tmpdir = tempfile.mkdtemp(prefix="iptv_dnd_")
sub = os.path.join(tmpdir, "nested")
os.makedirs(sub)
files = {}
for name, content in (
    ("one.m3u", "#EXTM3U\n#EXTINF:-1,频道一\nhttp://example.com/1.ts\n"
                "#EXTINF:-1,频道二\nhttp://example.com/2.ts\n"),
    ("skip.exe", "x"),
):
    p = os.path.join(tmpdir, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    files[name] = p
with open(os.path.join(sub, "deep.m3u8"), "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")

a3, r3 = dnd.collect_playlist_paths([tmpdir])
check(len(a3) == 1 and a3[0] == files["one.m3u"], "拖目录只收一层内的播单文件")
check(not any("deep" in p for p in a3), "不递归子目录")
check(not r3, "目录里的杂项不逐条报错")

empty_dir = tempfile.mkdtemp(prefix="iptv_dnd_empty_")
a4, r4 = dnd.collect_playlist_paths([empty_dir])
check(not a4 and r4 == [os.path.abspath(empty_dir)], "空目录进 rejected 一次")


# ════════════════════════════════════════════════════════════════════════════
print("== 2. 真实 WM_DROPFILES 消息 ==")


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _DROPFILES(ctypes.Structure):
    _fields_ = [
        ("pFiles", wintypes.DWORD),
        ("pt", _POINT),
        ("fNC", wintypes.BOOL),
        ("fWide", wintypes.BOOL),
    ]


def make_hdrop(paths):
    """伪造一个资源管理器发来的 HDROP（GMEM_MOVEABLE + 双 NUL 结尾 UTF-16 串）。"""
    payload = ("\0".join(paths) + "\0\0").encode("utf-16-le")
    size = ctypes.sizeof(_DROPFILES) + len(payload)
    k32 = ctypes.windll.kernel32
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    handle = k32.GlobalAlloc(0x0002, size)      # GMEM_MOVEABLE
    check(bool(handle), "GlobalAlloc 成功")
    base = k32.GlobalLock(handle)
    df = _DROPFILES.from_address(base)
    df.pFiles = ctypes.sizeof(_DROPFILES)
    df.pt.x, df.pt.y = 10, 10
    df.fNC, df.fWide = False, True
    ctypes.memmove(base + ctypes.sizeof(_DROPFILES), payload, len(payload))
    k32.GlobalUnlock(handle)
    return handle


def pump(root, seconds=2.0):
    t0 = time.time()
    while time.time() - t0 < seconds:
        root.update()
        time.sleep(0.01)


root = tk.Tk()
root.geometry("320x200")
root.withdraw()
received = []
enabled = dnd.enable_file_drop(root, lambda paths: received.append(list(paths)))
check(enabled, "enable_file_drop 注册成功")
check(len(dnd._HOOKS) >= 1, "窗口过程已子类化")

u = ctypes.windll.user32
u.GetParent.argtypes = [wintypes.HWND]
u.GetParent.restype = wintypes.HWND
child = int(root.winfo_id())
top = int(u.GetParent(child) or child)
u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                           wintypes.WPARAM, wintypes.LPARAM]

drop_paths = [files["one.m3u"], os.path.join(tmpdir, "another 中文.dpl")]
h = make_hdrop(drop_paths)
# 连发两条同一 HDROP：模拟顶层框架与客户区子窗口都收到消息的情况。
# 去重必须在 DragQueryFile 之前，否则会对已 DragFinish 的句柄二次操作。
u.PostMessageW(top, dnd.WM_DROPFILES, h, 0)
u.PostMessageW(top, dnd.WM_DROPFILES, h, 0)
pump(root, 1.0)
check(len(received) == 1, "同一 HDROP 重复投递只载入一次")
check(bool(received) and received[0] == drop_paths, "多文件与中文路径完整保真")

# 另一句柄的第二次拖放不能被误杀
h2 = make_hdrop([os.path.join(tmpdir, "another 中文.dpl")])
u.PostMessageW(top, dnd.WM_DROPFILES, h2, 0)
pump(root, 0.6)
check(len(received) == 2, "新的拖放句柄不被去重误杀")

# 解冻后不再响应
dnd.disable_file_drop(root)
check(not dnd._HOOKS, "disable_file_drop 恢复全部钩子")
before = len(received)
h_off = make_hdrop([files["one.m3u"]])
u.PostMessageW(top, dnd.WM_DROPFILES, h_off, 0)
pump(root, 0.6)
check(len(received) == before, "取消注册后不再触发回调")
root.destroy()


# ════════════════════════════════════════════════════════════════════════════
print("== 3. 界面集成：open_files ==")

_cfg = os.path.join(tempfile.gettempdir(), "iptv_dnd_test_config.json")
gui_mod._config_path = lambda: _cfg
if os.path.exists(_cfg):
    os.remove(_cfg)

root2 = tk.Tk()
root2.geometry("1120x780")
root2.withdraw()
app = gui_mod.IPTVApp(root2)
root2.update()

before_count = len(app.channels)
app.open_files([files["one.m3u"]])
root2.update()
check(len(app.channels) > before_count, "拖入的 m3u 解析出频道")
check(app._page == "results", "载入后自动切到结果页")
check("已载入" in app.lbl_parse.cget("text"), "导入页状态文案已更新")

count_after = len(app.channels)
app.open_files([files["skip.exe"]])
root2.update()
check(len(app.channels) == count_after, "拖入不支持的文件不改变频道列表")

app.open_files([files["one.m3u"], files["one.m3u"]])
root2.update()
check("1 个文件" in app.lbl_parse.cget("text"), "重复路径去重后只按一个文件载入")

root2.destroy()


# 清理测试临时文件（只删本测试自己创建的内容）
try:
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    shutil.rmtree(empty_dir, ignore_errors=True)
    os.remove(_cfg)
except Exception:
    pass

print()
print(f"通过 {ok} / {ok + len(fail)}")
if fail:
    print("失败项:")
    for f in fail:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
