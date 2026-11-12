"""验证打包产物：启动 exe → 等到窗口出现 → 截图 → 关闭。

窗口式程序（console=False）出问题时不会打印任何东西，所以这里不靠 stdout 判断，
而是用 Win32 找窗口、抓图，直接看它到底有没有把界面画出来。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "dist", "IPTVTools", "IPTVTools.exe")
TITLE = "IPTV 直播源测试工具"

user32 = ctypes.windll.user32


def find_window(title: str) -> int:
    hwnds = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title in buf.value:
            hwnds.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return hwnds[0] if hwnds else 0


def main() -> int:
    if not os.path.isfile(EXE):
        print(f"FAIL 找不到 exe: {EXE}")
        return 1
    print(f"启动: {EXE}")

    proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
    hwnd = 0
    deadline = time.time() + 40
    while time.time() < deadline:
        if proc.poll() is not None:
            print(f"FAIL 进程提前退出，退出码 {proc.returncode}")
            return 1
        hwnd = find_window(TITLE)
        if hwnd:
            break
        time.sleep(0.5)

    if not hwnd:
        print("FAIL 40 秒内未出现窗口")
        proc.kill()
        return 1

    print(f"OK 窗口已出现 hwnd={hwnd}")

    # 提到最前，否则截图会被别的窗口盖住
    try:
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass

    # 给它一点时间画完首帧（Worker 启动、FFmpeg 探测都在初始化里做）
    time.sleep(2.5)

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    print(f"OK 窗口尺寸 {w}x{h}")

    shot = os.path.join(HERE, "shot_packaged.png")
    try:
        img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
        img.save(shot)
        print(f"OK 截图 {shot} size={img.size}")
    except Exception as exc:                              # noqa: BLE001
        print(f"WARN 截图失败: {exc}")

    # 确认配置写在 exe 同级（而不是 _internal 里）
    cfg = os.path.join(os.path.dirname(EXE), "config.json")
    print(f"配置文件位置检查: {cfg} 存在={os.path.exists(cfg)}（未保存设置时不存在属正常）")

    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("OK 已关闭")

    ok = os.path.isfile(shot)
    print("\n" + ("PASS 打包产物可运行" if ok else "PARTIAL 运行成功但没截图"))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
