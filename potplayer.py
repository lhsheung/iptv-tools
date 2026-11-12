"""
PotPlayer 调用与定位模块
开发维护: https://iptv-search.com

视频预览不内置解码器——直接把流交给本机 PotPlayer 播放。
好处是格式支持、硬件解码、字幕、录制、时移全部由 PotPlayer 负责，
软件本体保持轻量，也不用打包几十 MB 的 libVLC。

设计要点
--------
1. **零硬编码路径**：按「注册表 → 常见安装目录 → PATH → 用户手动指定」顺序探测，
   找不到时由界面引导用户手动选一次，选择结果写进 config.json。
2. **两种播放模式**：
   - ``replace``（默认）：以新窗口播放当前频道，符合"点一个看一个"的直觉。
   - ``append``：把地址追加到已在运行的 PotPlayer 播放列表末尾（``/add``），
     适合边测边攒一个待看清单。
3. **失败可回退**：进程启动失败时不抛异常打断 UI，而是返回带原因的结果对象。

PotPlayer 支持的命令行参数（摘自其 ``CmdLine64.txt``）::

    PotPlayerMini64.exe "地址" [选项]
        /new          新开一个播放器实例（忽略"禁止多重运行"）
        /current      交给已运行的实例播放
        /add          追加到播放列表末尾
        /insert       插到当前播放项之后
        /autoplay     自动播放
        /title="..."  指定标题
        /user_agent="..."  http(s) 打开时使用的 UA
        /referer="..."     http(s) 打开时使用的 referer
        /volume="N"   指定音量
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional

#: 可执行文件名候选（64 位优先）
EXE_NAMES = ("PotPlayerMini64.exe", "PotPlayer64.exe",
             "PotPlayerMini.exe", "PotPlayer.exe")

#: 常见安装位置（相对盘符根，会在所有存在的盘符上尝试）
COMMON_SUBDIRS = (
    r"Program Files\DAUM\PotPlayer",
    r"Program Files (x86)\DAUM\PotPlayer",
    r"Program Files\PotPlayer",
    r"Program Files (x86)\PotPlayer",
    r"DAUM\PotPlayer",
    r"PotPlayer",
    r"Video & Music\PotPlayer",
    r"Software\PotPlayer",
    r"Software\Tool\PotPlayer",
)

#: 注册表键（PotPlayer 安装版会写）
REGISTRY_KEYS = (
    (r"HKLM\SOFTWARE\DAUM\PotPlayer", "ProgramPath"),
    (r"HKLM\SOFTWARE\WOW6432Node\DAUM\PotPlayer", "ProgramPath"),
    (r"HKCU\SOFTWARE\DAUM\PotPlayer", "ProgramPath"),
)

#: 默认 UA —— 部分直播源会拒绝 Python/默认客户端
DEFAULT_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36")


@dataclass
class PlayResult:
    """一次播放尝试的结果。"""

    ok: bool
    message: str = ""
    exe: str = ""
    command: str = ""


def _existing_drives() -> List[str]:
    """返回当前存在的盘符根路径（Windows）。"""
    if sys.platform != "win32":
        return ["/"]
    drives = []
    for letter in "CDEFGHIJ":
        root = f"{letter}:\\"
        if os.path.exists(root):
            drives.append(root)
    return drives


def _read_registry_paths() -> List[str]:
    """从注册表读 PotPlayer 安装路径；读不到就返回空列表。

    用 winreg 而非 reg.exe，避免依赖外部命令与权限。
    """
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []

    found: List[str] = []
    for key_path, value_name in REGISTRY_KEYS:
        try:
            hive_name, sub_key = key_path.split("\\", 1)
            hive = getattr(winreg, hive_name, None)
            if hive is None:
                continue
            with winreg.OpenKey(hive, sub_key) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
                if value:
                    found.append(str(value))
        except OSError:
            continue
    return found


def find_potplayer(configured: str = "") -> Optional[str]:
    """定位 PotPlayer 可执行文件，找不到返回 ``None``。

    参数
    ----
    configured
        用户在界面里手动指定的路径，优先级最高。
    """
    # 1) 用户指定
    if configured:
        configured = os.path.expandvars(configured.strip().strip('"'))
        if os.path.isfile(configured):
            return configured

    # 2) 注册表
    for path in _read_registry_paths():
        path = os.path.expandvars(path.strip().strip('"'))
        if os.path.isfile(path):
            return path
        # 注册表可能只存目录
        for name in EXE_NAMES:
            candidate = os.path.join(path, name)
            if os.path.isfile(candidate):
                return candidate

    # 3) 常见目录
    for drive in _existing_drives():
        for sub in COMMON_SUBDIRS:
            folder = os.path.join(drive, sub)
            for name in EXE_NAMES:
                candidate = os.path.join(folder, name)
                if os.path.isfile(candidate):
                    return candidate

    # 4) PATH
    for name in EXE_NAMES:
        found = shutil.which(name)
        if found:
            return found

    return None


def _quote(value: str) -> str:
    """给需要加引号的参数值加引号（内部引号转义为空格，避免破坏命令行）。"""
    value = (value or "").replace('"', " ").strip()
    return f'"{value}"' if value else ""


def build_command(
    exe: str,
    url: str,
    title: str = "",
    mode: str = "replace",
    user_agent: str = "",
    referer: str = "",
) -> List[str]:
    """构造 PotPlayer 命令行参数列表（不含 exe 本身）。"""
    args: List[str] = []

    if mode == "append":
        # 追加到已运行实例的播放列表
        args.append("/add")
        args.append("/current")
    elif mode == "new":
        args.append("/new")

    args.append(url)

    if title:
        args.append(f"/title={_quote(title)}")
    if user_agent:
        args.append(f"/user_agent={_quote(user_agent)}")
    if referer:
        args.append(f"/referer={_quote(referer)}")

    return args


def play(
    url: str,
    exe: str,
    title: str = "",
    mode: str = "replace",
    user_agent: str = "",
    referer: str = "",
) -> PlayResult:
    """调用 PotPlayer 播放一个地址。

    返回 :class:`PlayResult`；失败时 ``ok=False`` 且 ``message`` 说明原因，
    调用方可直接拿去弹提示，不必 try/except。
    """
    if not url:
        return PlayResult(False, "地址为空")
    if not exe or not os.path.isfile(exe):
        return PlayResult(False, "未找到 PotPlayer，请在设置里指定 PotPlayerMini64.exe 路径")

    args = build_command(exe, url, title=title, mode=mode,
                         user_agent=user_agent, referer=referer)

    try:
        # 不等待进程结束：PotPlayer 会自己常驻
        subprocess.Popen([exe, *args], close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        return PlayResult(False, f"启动 PotPlayer 失败: {e}", exe=exe,
                          command=" ".join([exe, *args]))
    except Exception as e:  # 极端情况下兜底，不让 UI 崩
        return PlayResult(False, f"调用异常: {e}", exe=exe,
                          command=" ".join([exe, *args]))

    return PlayResult(True, "已发送到 PotPlayer", exe=exe,
                      command=" ".join([exe, *args]))
