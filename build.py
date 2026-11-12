"""一键打包。

用法::

    python build.py                # 精简单文件版（默认，约 20 MB，不含 FFmpeg）
    python build.py --full         # 完整目录版（约 496 MB，内含 FFmpeg）
    python build.py --clean        # 先清构建缓存

产物::

    dist/IPTVTools.exe                 ← 精简版，双击即用
    dist/IPTVTools/IPTVTools.exe       ← 完整版（--full）

体积说明
--------
精简版 ≈ 20 MB：Python 运行时 + tkinter + Pillow + 本项目代码 + IP 库。
完整版 ≈ 496 MB：其中 435 MB 是 ffmpeg.exe / ffprobe.exe。
FFmpeg 只影响"解码检测"，不带它也能正常做 HTTP 探活。

完整版的 FFmpeg 也可以事后补：把 ffmpeg.exe、ffprobe.exe 放进
精简版 exe 同级的 tools/ffmpeg/bin/ 目录，重启程序即自动启用。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
BUILD = os.path.join(HERE, "build")
TMP = os.path.join(HERE, "_pytmp")

FULL = "--full" in sys.argv


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def du(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def main() -> int:
    if "--clean" in sys.argv:
        for d in (BUILD, TMP):
            if os.path.isdir(d):
                print(f"清理 {d}")
                shutil.rmtree(d, ignore_errors=True)
        # dist 里的旧产物交给 PyInstaller 的 --noconfirm 处理

    os.makedirs(TMP, exist_ok=True)
    # 系统盘只剩一点空间，把临时目录指到项目盘，避免 PyInstaller 中途写满
    os.environ["TMP"] = TMP
    os.environ["TEMP"] = TMP
    os.environ["TMPDIR"] = TMP
    os.environ["IPTVTOOLS_MODE"] = "full" if FULL else "lite"

    mode_name = "完整版（含 FFmpeg）" if FULL else "精简版（不含 FFmpeg）"
    print("=" * 62)
    print(f"开始打包 IPTVTools · {mode_name}")
    print("=" * 62)

    t0 = time.time()
    cmd = [sys.executable, "-m", "PyInstaller", "build.spec",
           "--noconfirm", "--distpath", DIST, "--workpath", BUILD]
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        print(f"\n打包失败，退出码 {r.returncode}")
        return r.returncode

    target = os.path.join(DIST, "IPTVTools" if FULL else "IPTVTools.exe")
    if not os.path.exists(target):
        print(f"\n没有产出目标：{target}")
        return 1

    # 使用说明放在产物旁边
    readme = os.path.join(HERE, "README.md")
    if os.path.isfile(readme):
        if FULL:
            shutil.copy2(readme, os.path.join(DIST, "IPTVTools", "使用说明.md"))
        else:
            shutil.copy2(readme, os.path.join(DIST, "使用说明.md"))

    print()
    print("=" * 62)
    print(f"打包完成，耗时 {time.time() - t0:.0f}s")
    print(f"产物：{target}")
    print(f"体积：{human(du(target))}")
    print("=" * 62)

    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
