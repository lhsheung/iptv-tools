# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置

两种模式（由环境变量 IPTVTOOLS_MODE 控制，build.py 会设置）

``lite``（默认）
    单文件 exe，**不含 FFmpeg**，约 20 MB。
    只有 HTTP 探活可用；FFmpeg 解码相关功能会自动降级并在界面提示。
    这是"拿来就能双击"的最小体积版本。

``full``
    目录版（onedir），**内含 FFmpeg**，约 496 MB。
    其中 435 MB 是 ffmpeg.exe + ffprobe.exe 两个静态二进制 ——
    这不是我们的代码，Gyan 的 full_build 把 x264/x265/AV1/CUDA/whisper
    等全部编进去了，单文件 218 MB。

为什么 full 用 onedir 而不是 onefile
-----------------------------------
onefile 每次启动都要把内容解压到临时目录。带 435 MB 二进制时启动要等几十秒，
而且每次往系统盘写 435 MB。onedir 启动几乎瞬发。

为什么 lite 用 onefile
----------------------
lite 只有 20 MB 左右，解压很快，而且用户拿到单个 exe 最省事。
"""

import os

HERE = os.path.abspath(os.getcwd())
MODE = os.environ.get("IPTVTOOLS_MODE", "lite").lower()


def _need(rel_path: str) -> str:
    """构建前校验资源存在，缺失立刻失败，避免打出跑不起来的包。"""
    full = os.path.join(HERE, rel_path)
    if not os.path.exists(full):
        raise SystemExit(f"打包缺少必要资源：{full}")
    return full


# ── 数据文件（两种模式都要的） ───────────────────────────────────────────────
datas = [
    # 离线 IP 库 + Python 绑定（绑定是纯 .py，运行时靠 sys.path 注入加载）
    (_need("ip2region.xdb"), "."),
    (_need("ip2region_master"), "ip2region_master"),
]

# ── 只有 full 模式才带 FFmpeg ────────────────────────────────────────────────
WITH_FFMPEG = MODE == "full"
if WITH_FFMPEG:
    ff_bin = os.path.join("tools", "ffmpeg", "bin")
    datas += [
        (_need(os.path.join(ff_bin, "ffmpeg.exe")), ff_bin),
        (_need(os.path.join(ff_bin, "ffprobe.exe")), ff_bin),
    ]

# ── 明确排除的重型/无关依赖 ─────────────────────────────────────────────────
# 只排确定用不到、且体量大的第三方库。
# 注意：不要排 email / http.client / sqlite3 这些标准库 ——
# requests → urllib3 → http.client 依赖 email.parser，
# 排掉 email 会让程序一启动就 ImportError（windowed 模式下表现为闪退无提示）。
excludes = [
    "numpy", "pandas", "scipy", "matplotlib", "openpyxl", "IPython",
    "jupyter", "notebook", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "pytest", "setuptools", "pip", "wheel",
    "lib2to3", "pydoc", "doctest",
]

a = Analysis(
    ["main.py"],
    pathex=[HERE],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "tkinter", "tkinter.ttk", "tkinter.filedialog",
        "tkinter.messagebox", "tkinter.font",
        "PIL._tkinter_finder",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

_COMMON_EXE = dict(
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # ffmpeg 已是压缩过的二进制，UPX 收益低且易被杀软误报
    console=False,             # GUI 程序，不弹控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_need("app.ico"),
)

if WITH_FFMPEG:
    # 目录版
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
              name="IPTVTools", **_COMMON_EXE)
    coll = COLLECT(exe, a.binaries, a.datas,
                   strip=False, upx=False, upx_exclude=[],
                   name="IPTVTools")
else:
    # 单文件版：binaries / datas 全部塞进 exe
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
              name="IPTVTools", **_COMMON_EXE)
