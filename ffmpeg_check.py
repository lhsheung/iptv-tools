"""
FFmpeg / FFprobe 真实解码检测
开发维护: https://iptv-search.com

与 ``test_logic.py`` 的 HTTP 探测互补：那边判断"能不能连上"，这里判断
"能不能真的解出画面和声音"。直播源常见「HTTP 200 但没码流」「能连但不是
视频」这类假可用，只有实际解码才能识别。

实现要点
--------
- **FFprobe**：先快速解析媒体信息（容器、视频/音频编码、分辨率、码率）。
- **FFmpeg**：真解码若干秒，统计实际产出帧数与音视频包数，判断是否真有内容。
- **subprocess 独立进程**：每个检测一个进程，超时即 kill，避免线程卡死。
- **进度解析**：读取 ``-progress pipe:1`` 的 key=value 输出，得到实时
  帧数/码率，用于前端显示"正在解码 N 帧"。
- 所有函数都不依赖 GUI，可被 Worker 或测试直接调用。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

#: 创建子进程时不弹黑框
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass
class MediaInfo:
    """FFprobe 解析出的媒体信息。"""

    ok: bool = False
    error: str = ""
    format_name: str = ""
    duration: Optional[float] = None
    bitrate_kbps: Optional[float] = None
    video_codec: str = ""
    width: int = 0
    height: int = 0
    fps: str = ""
    audio_codec: str = ""
    sample_rate: str = ""
    channels: int = 0

    def to_dict(self) -> Dict:
        return {
            "ok": self.ok,
            "error": self.error,
            "format": self.format_name,
            "duration": self.duration,
            "bitrate_kbps": self.bitrate_kbps,
            "video_codec": self.video_codec,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "audio_codec": self.audio_codec,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
        }


@dataclass
class DecodeResult:
    """FFmpeg 解码结果。"""

    ok: bool = False
    error: str = ""
    frames: int = 0
    audio_frames: int = 0
    decoded_seconds: float = 0.0
    output_bytes: int = 0
    speed: str = ""
    has_video: bool = False
    has_audio: bool = False
    #: 逐秒帧数，用于判断是否卡顿（后面全 0 说明中途断了）
    frame_timeline: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "ok": self.ok,
            "error": self.error,
            "frames": self.frames,
            "audio_frames": self.audio_frames,
            "decoded_seconds": round(self.decoded_seconds, 2),
            "output_bytes": self.output_bytes,
            "speed": self.speed,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "frame_timeline": self.frame_timeline,
        }


# ── 可执行文件定位 ────────────────────────────────────────────────────────────

def _base_dirs() -> List[str]:
    """可能存放 ffmpeg 的目录（按优先级）。

    打包版把 FFmpeg 做成**可选扩展**：精简版不含二进制，用户把
    ``ffmpeg.exe`` / ``ffprobe.exe`` 放到 exe 同级的 ``tools/ffmpeg/bin/``
    就能启用解码功能。所以 exe 所在目录排在前面，优先级高于内置资源，
    这样用户想换版本直接覆盖即可。
    """
    dirs: List[str] = []

    # 1) 打包后 exe 同级 —— 用户手动放置的可选扩展
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        dirs += [
            os.path.join(exe_dir, "tools", "ffmpeg", "bin"),
            os.path.join(exe_dir, "ffmpeg", "bin"),
            os.path.join(exe_dir, "ffmpeg"),
        ]

    # 2) PyInstaller 解包目录（完整版会把 ffmpeg 打进来）
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        dirs.append(os.path.join(meipass, "tools", "ffmpeg", "bin"))
        dirs.append(os.path.join(meipass, "tools"))

    # 3) 源码运行时的相对位置
    here = os.path.dirname(os.path.abspath(__file__))
    dirs += [
        os.path.join(here, "tools", "ffmpeg", "bin"),
        os.path.join(here, "..", "tools", "ffmpeg", "bin"),
        os.path.join(here, "ffmpeg", "bin"),
        os.path.join(here, "ffmpeg"),
    ]
    return dirs


def find_tool(name: str, configured: str = "") -> Optional[str]:
    """定位 ffmpeg / ffprobe 可执行文件。

    顺序：用户指定 → 内置目录 → PATH。
    """
    exe = f"{name}.exe" if sys.platform == "win32" else name

    if configured:
        configured = os.path.expandvars(configured.strip().strip('"'))
        if os.path.isfile(configured):
            return configured

    for d in _base_dirs():
        candidate = os.path.join(os.path.normpath(d), exe)
        if os.path.isfile(candidate):
            return candidate

    return shutil.which(exe)


def ffmpeg_available(configured_ffmpeg: str = "", configured_ffprobe: str = "") -> Dict:
    """检查工具可用性，返回给前端用的状态字典。"""
    ffmpeg = find_tool("ffmpeg", configured_ffmpeg)
    ffprobe = find_tool("ffprobe", configured_ffprobe)
    out = {"ffmpeg": ffmpeg, "ffprobe": ffprobe,
           "ffmpeg_ok": bool(ffmpeg), "ffprobe_ok": bool(ffprobe),
           "version": ""}
    if ffmpeg:
        try:
            r = subprocess.run([ffmpeg, "-version"], capture_output=True,
                               timeout=10, creationflags=_CREATE_NO_WINDOW)
            first = r.stdout.decode("utf-8", "replace").splitlines()
            if first:
                m = re.search(r"ffmpeg version (\S+)", first[0])
                out["version"] = m.group(1) if m else first[0][:60]
        except Exception as e:
            out["version"] = f"读取版本失败: {e}"
    return out


# ── FFprobe ──────────────────────────────────────────────────────────────────

def probe(url: str, ffprobe: str, timeout: int = 15,
          extra_args: Optional[List[str]] = None) -> MediaInfo:
    """用 FFprobe 解析媒体信息。

    直播流没有 duration，此时 ``duration`` 为 ``None`` 属正常。
    """
    info = MediaInfo()
    if not ffprobe or not os.path.isfile(ffprobe):
        info.error = "未找到 ffprobe"
        return info
    if not url:
        info.error = "地址为空"
        return info

    args = [ffprobe, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams",
            "-analyzeduration", "5000000", "-probesize", "5000000"]
    if extra_args:
        args += extra_args
    args.append(url)

    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=_CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        info.error = f"FFprobe 超时({timeout}s)"
        return info
    except Exception as e:
        info.error = f"FFprobe 调用失败: {e}"
        return info

    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()
        info.error = err[-1][:200] if err else f"FFprobe 返回码 {r.returncode}"
        return info

    try:
        data = json.loads(r.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        info.error = f"FFprobe 输出解析失败: {e}"
        return info

    fmt = data.get("format", {}) or {}
    info.format_name = fmt.get("format_name", "")
    try:
        if fmt.get("duration"):
            info.duration = float(fmt["duration"])
    except (TypeError, ValueError):
        pass
    try:
        if fmt.get("bit_rate"):
            info.bitrate_kbps = round(float(fmt["bit_rate"]) / 1000, 1)
    except (TypeError, ValueError):
        pass

    for s in data.get("streams", []) or []:
        codec_type = s.get("codec_type")
        if codec_type == "video" and not info.video_codec:
            info.video_codec = s.get("codec_name", "") or ""
            info.width = int(s.get("width") or 0)
            info.height = int(s.get("height") or 0)
            info.fps = s.get("avg_frame_rate") or s.get("r_frame_rate") or ""
            if info.fps in ("0/0", "0"):
                info.fps = ""
        elif codec_type == "audio" and not info.audio_codec:
            info.audio_codec = s.get("codec_name", "") or ""
            info.sample_rate = str(s.get("sample_rate") or "")
            info.channels = int(s.get("channels") or 0)

    info.ok = bool(info.video_codec or info.audio_codec)
    if not info.ok and not info.error:
        info.error = "未解析到音视频流"
    return info


# ── FFmpeg 解码 ──────────────────────────────────────────────────────────────

_PROGRESS_RE = re.compile(r"^(\w+)=(.*)$")


def decode(url: str, ffmpeg: str, seconds: float = 3.0, timeout: int = 20,
           extra_args: Optional[List[str]] = None,
           on_progress=None) -> DecodeResult:
    """真解码若干秒，统计实际帧数。

    参数
    ----
    seconds
        解码时长。2~3 秒足够判断直播源是否真有画面。
    on_progress
        可选回调 ``fn(dict)``，在解码过程中接收进度事件。
    """
    res = DecodeResult()
    if not ffmpeg or not os.path.isfile(ffmpeg):
        res.error = "未找到 ffmpeg"
        return res
    if not url:
        res.error = "地址为空"
        return res

    # -t 限制时长；输出到 null 只解码不落盘；-progress 输出结构化进度
    # loglevel 用 info 而不是 error：需要在 stderr 里看到
    # "Stream #0:1: Audio: aac ..." 这类流信息，才能判断有没有音轨。
    # -nostats 会抑制逐帧刷屏，剩下的只有十几行输入/流信息，量很小。
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "info",
        "-analyzeduration", "5000000", "-probesize", "5000000",
        "-i", url,
        "-t", str(seconds),
        "-map", "0:v?", "-map", "0:a?",
        "-f", "null", "-",
        "-progress", "pipe:1", "-nostats",
    ]
    # 直播源常需要更长读超时
    args[args.index("-loglevel"):args.index("-loglevel")] = ["-rw_timeout", "15000000"]
    if extra_args:
        args += extra_args

    proc = None
    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_CREATE_NO_WINDOW, bufsize=1,
            universal_newlines=True, encoding="utf-8", errors="replace",
        )
    except Exception as e:
        res.error = f"FFmpeg 启动失败: {e}"
        return res

    # 独立线程读 stderr，避免管道写满阻塞子进程
    stderr_lines: List[str] = []

    def _drain():
        try:
            for line in proc.stderr:
                if line.strip():
                    stderr_lines.append(line.strip())
        except Exception:
            pass

    t = threading.Thread(target=_drain, daemon=True)
    t.start()

    progress: Dict[str, str] = {}
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            m = _PROGRESS_RE.match(line)
            if not m:
                continue
            key, value = m.group(1), m.group(2)
            progress[key] = value
            if key == "progress":
                snap = dict(progress)
                if on_progress:
                    try:
                        on_progress(snap)
                    except Exception:
                        pass
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        res.error = f"FFmpeg 解码超时({timeout}s)"
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        res.error = f"FFmpeg 运行异常: {e}"

    t.join(timeout=2)

    # 解析进度终值
    def _num(key: str, default: float = 0) -> float:
        raw = progress.get(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    res.frames = int(_num("frame"))
    res.output_bytes = int(_num("total_size"))
    res.decoded_seconds = _num("out_time_ms") / 1_000_000 if "out_time_ms" in progress else 0.0
    res.speed = progress.get("speed", "")

    # out_time 形如 00:00:03.000000，作为 out_time_ms 缺失时的兜底
    if res.decoded_seconds == 0 and progress.get("out_time"):
        parts = progress["out_time"].split(":")
        if len(parts) == 3:
            try:
                res.decoded_seconds = (float(parts[0]) * 3600 + float(parts[1]) * 60
                                       + float(parts[2]))
            except ValueError:
                pass

    res.has_video = res.frames > 0

    # 音轨判断必须看流信息，不能用 output_bytes 推测。
    # 旧实现写成 ``output_bytes > 0 and not has_video``，等于"有视频就必然没音频"，
    # 结果所有正常音视频流都被误报成无音频。
    def _has_stream(kind: str) -> bool:
        marker = f": {kind}:"
        return any(marker in line for line in stderr_lines)

    res.has_audio = _has_stream("Audio")
    if not res.has_video:
        # 解不出视频帧但确实有音频流时，has_video 也按流信息兜底
        res.has_video = _has_stream("Video") and res.frames > 0

    if not res.error:
        if res.frames == 0:
            # 只在错误行里找提示，避免把正常的流信息当成失败原因
            errs = [l for l in stderr_lines
                    if any(k in l for k in ("Error", "error", "Invalid",
                                            "failed", "Failed", "No such",
                                            "Connection", "timed out"))]
            hint = (errs[-1] if errs else (stderr_lines[-1] if stderr_lines else ""))[:160]
            res.error = f"未解出视频帧（{hint or '无输出'}）"
        else:
            res.ok = True

    return res


def decode_probe(url: str, ffmpeg: str, ffprobe: str,
                 seconds: float = 3.0, probe_timeout: int = 15,
                 decode_timeout: int = 20) -> Dict:
    """组合检测：先 probe 拿媒体信息，再 decode 验证真实可解码。"""
    info = probe(url, ffprobe, timeout=probe_timeout)
    dec = decode(url, ffmpeg, seconds=seconds, timeout=decode_timeout)

    # probe 已经明确列出了流组成，用它校准 decode 的流判断：
    # decode 只能从 stderr 猜，遇到 ffmpeg 静默或日志被截断时会漏判。
    if info.ok:
        dec.has_video = bool(info.video_codec) or dec.frames > 0
        dec.has_audio = bool(info.audio_codec)

    return {
        "url": url,
        "probe": info.to_dict(),
        "decode": dec.to_dict(),
        "ok": bool(dec.ok),
        "summary": _summary(info, dec),
    }


def _summary(info: MediaInfo, dec: DecodeResult) -> str:
    """给前端展示的一行摘要。"""
    if dec.ok:
        bits = []
        if info.video_codec:
            size = f"{info.width}x{info.height}" if info.width and info.height else ""
            bits.append(f"{info.video_codec} {size}".strip())
        if info.audio_codec:
            bits.append(info.audio_codec)
        bits.append(f"{dec.frames} 帧")
        if info.bitrate_kbps:
            bits.append(f"{info.bitrate_kbps}kbps")
        return " / ".join(bits)
    return dec.error or info.error or "不可用"
