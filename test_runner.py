"""
批量检测调度器 —— 把「HTTP 探活」与「FFmpeg 真解码」组合成统一入口

为什么要单独一层
----------------
``test_logic.py`` 只管"能不能连上"（HTTP 响应、状态码、流特征、流畅度），
``ffmpeg_check.py`` 只管"能不能真的解出画面"。二者关注的失败模式不同：

- HTTP 200 但没有码流 → HTTP 说可用，FFmpeg 说不可用
- 能连上但编码不支持 / 花屏 → HTTP 说可用，FFmpeg 解不出帧
- 需要特定 UA/Referer → HTTP 带 header 能过，FFmpeg 裸连会失败

界面需要让用户在"快"和"准"之间选，所以这里提供三种模式，并把两条
链路的结果合并到同一个 ``Channel`` 对象上，供表格直接展示。

三种模式
--------
``http``    仅 HTTP 探活。最快，适合先粗筛几千条。
``ffmpeg``  仅 FFmpeg 真解码。慢（每条要解码 N 秒），但最接近"实际能不能看"。
``both``    先 HTTP，通过后再用 FFmpeg 复核。总体比纯 ffmpeg 快，
            因为大量死链在 HTTP 阶段就被淘汰了。

线程模型
--------
沿用原项目"多个 worker 线程抢任务队列"的做法（而不是 ThreadPoolExecutor），
原因是需要**暂停/继续**：worker 发现暂停标志就原地等待，队列不受影响。
调度器本身不碰 tkinter，所有回调都在工作线程里触发，由界面层自己
``after(0, ...)`` 编组到主线程 —— 这样调度器可以脱离 GUI 单独测试。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import ffmpeg_check
import test_logic
from test_logic import Channel, TestConfig, test_channel_via_config

#: 检测模式
MODE_HTTP = "http"
MODE_FFMPEG = "ffmpeg"
MODE_BOTH = "both"
MODES = (MODE_HTTP, MODE_FFMPEG, MODE_BOTH)


def mode_label(mode: str) -> str:
    return {
        MODE_HTTP: "HTTP 探活",
        MODE_FFMPEG: "FFmpeg 解码",
        MODE_BOTH: "叠加检测",
    }.get(mode, mode)


def mode_hint(mode: str) -> str:
    return {
        MODE_HTTP: "只检查响应与流特征，速度最快，适合先粗筛。",
        MODE_FFMPEG: "真实解码几秒，能出画面才算可用，最接近实际观看。",
        MODE_BOTH: "先探活再解码复核，准确度最高，耗时居中。",
    }.get(mode, "")


# ════════════════════════════════════════════════════════════════════════════
# 单条检测
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class FfmpegTools:
    """ffmpeg / ffprobe 的绝对路径缓存。"""

    ffmpeg: str = ""
    ffprobe: str = ""
    version: str = ""
    ffmpeg_ok: bool = False
    ffprobe_ok: bool = False

    @classmethod
    def detect(cls, configured_ffmpeg: str = "",
               configured_ffprobe: str = "") -> "FfmpegTools":
        info = ffmpeg_check.ffmpeg_available(configured_ffmpeg, configured_ffprobe)
        return cls(
            ffmpeg=info.get("ffmpeg") or "",
            ffprobe=info.get("ffprobe") or "",
            version=info.get("version") or "",
            ffmpeg_ok=bool(info.get("ffmpeg_ok")),
            ffprobe_ok=bool(info.get("ffprobe_ok")),
        )

    @property
    def ready(self) -> bool:
        """解码需要 ffmpeg；probe 需要 ffprobe，二者都有才是完整能力。"""
        return self.ffmpeg_ok and self.ffprobe_ok


def test_one(ch: Channel, config: TestConfig, mode: str,
             decode_seconds: float = 3.0,
             tools: Optional[FfmpegTools] = None) -> Channel:
    """检测单个频道，返回**同一个** Channel 对象（就地更新）。

    失败不会抛异常 —— 任何异常都会被收敛成 ``is_valid=False`` 加一条可读原因，
    因为批量场景里"某一条炸了"不该中断整批。
    """
    mode = mode if mode in MODES else MODE_HTTP
    tools = tools or FfmpegTools.detect()

    # ── 第一段：HTTP ────────────────────────────────────────────────────────
    if mode in (MODE_HTTP, MODE_BOTH):
        try:
            ch = test_channel_via_config(ch, config)
        except Exception as exc:                            # noqa: BLE001
            ch.is_valid = False
            ch.location = f"检测异常: {exc}"
            ch.response_time = ""
            return ch

    # ── 第二段：FFmpeg ──────────────────────────────────────────────────────
    if mode in (MODE_FFMPEG, MODE_BOTH):
        if not tools.ready:
            ch.decode_ok = False
            ch.decode_summary = "未找到 ffmpeg/ffprobe，无法解码检测"
            if mode == MODE_FFMPEG:
                ch.is_valid = False
                ch.location = "FFmpeg 不可用"
            return ch

        # both 模式下只对 HTTP 已通过的做复核，省掉大量无谓解码
        if mode == MODE_BOTH and not ch.is_valid:
            return ch

        target = ch.final_url or ch.link_str
        try:
            dp = ffmpeg_check.decode_probe(
                target, tools.ffmpeg, tools.ffprobe,
                seconds=decode_seconds,
            )
        except Exception as exc:                            # noqa: BLE001
            ch.decode_ok = False
            ch.decode_summary = f"解码异常: {exc}"
            if mode == MODE_FFMPEG:
                ch.is_valid = False
            return ch

        probe = dp.get("probe") or {}
        dec = dp.get("decode") or {}
        ch.decode_ok = bool(dp.get("ok"))
        ch.decode_summary = dp.get("summary") or ""
        ch.decode_frames = int(dec.get("frames") or 0)
        ch.probe_width = int(probe.get("width") or 0)
        ch.probe_height = int(probe.get("height") or 0)

        # 用 probe 拿到的编码/码率补全 HTTP 阶段拿不到的信息
        if probe.get("video_codec") and not ch.codec:
            ch.codec = probe["video_codec"]
        if probe.get("bitrate_kbps"):
            try:
                ch.bitrate_kbps = float(probe["bitrate_kbps"])
            except (TypeError, ValueError):
                pass

        if mode == MODE_FFMPEG:
            # 纯解码模式下，只有真解出帧才算可用
            ch.is_valid = bool(dp.get("ok"))
            if not ch.is_valid:
                ch.location = "无法解码"
        elif dp.get("ok"):
            ch.is_valid = True

    return ch


# ════════════════════════════════════════════════════════════════════════════
# 批量调度
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class BatchStats:
    """一批测试的实时统计，界面直接读这些字段。"""

    total: int = 0
    done: int = 0
    valid: int = 0
    failed: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def progress(self) -> float:
        return 0.0 if self.total <= 0 else self.done / self.total

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    @property
    def eta(self) -> Optional[float]:
        """按当前速率估算剩余秒数；样本不足时返回 None。"""
        if self.done <= 0 or self.done >= self.total:
            return None
        per_item = self.elapsed / self.done
        return per_item * (self.total - self.done)


class BatchRunner:
    """可暂停/可停止的批量检测器。

    用法::

        runner = BatchRunner(mode="both", decode_seconds=3, workers=8)
        runner.start(channels, config,
                     on_item=..., on_progress=..., on_finish=...)
        runner.pause() / runner.resume() / runner.stop()

    回调都在工作线程里触发，界面层需自行编组回主线程。
    """

    def __init__(self, mode: str = MODE_HTTP, decode_seconds: float = 3.0,
                 workers: int = 4):
        self.mode = mode if mode in MODES else MODE_HTTP
        self.decode_seconds = max(1.0, float(decode_seconds))
        self.workers = max(1, min(32, int(workers)))

        self.stats = BatchStats()
        self.tools = FfmpegTools.detect()

        self._queue: List[Channel] = []
        self._lock = threading.Lock()
        self._stop_flag = threading.Event()
        self._pause_flag = threading.Event()
        self._threads: list[threading.Thread] = []
        self._finish_lock = threading.Lock()
        self._finished = False
        #: 尚未退出的工作线程数。归零即整批结束 —— 这是唯一的结束判据，
        #: 因为"停止"时队列必然未耗尽，不能拿队列状态判断收尾。
        self._active = 0

    # ── 状态 ────────────────────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    @property
    def paused(self) -> bool:
        return self._pause_flag.is_set()

    # ── 控制 ────────────────────────────────────────────────────────────────
    def pause(self) -> None:
        self._pause_flag.set()

    def resume(self) -> None:
        self._pause_flag.clear()

    def stop(self) -> None:
        self._stop_flag.set()
        self._pause_flag.clear()          # 停止时解除暂停，让线程能走到退出检查

    # ── 启动 ────────────────────────────────────────────────────────────────
    def start(self, channels: List[Channel], config: TestConfig,
              on_item: Optional[Callable[[Channel, BatchStats], None]] = None,
              on_progress: Optional[Callable[[BatchStats], None]] = None,
              on_finish: Optional[Callable[[BatchStats], None]] = None,
              on_error: Optional[Callable[[Channel, Exception], None]] = None
              ) -> None:
        """启动批量检测。会重置统计，重复调用前请先确认上一批已结束。"""
        # 让界面上的"超时"真正生效：上游把各处的 timeout 写死了，
        # 这里统一覆盖到 request_with_retry 这个唯一入口。
        # 注意这是模块级全局量，故同一进程内只应有一批在跑（界面已保证）。
        test_logic.set_http_timeout(getattr(config, "timeout", None))

        with self._lock:
            # 队列用下标游标而不是 pop(0)：几千条时 pop(0) 是 O(n²)
            self._queue = list(channels)
            self._cursor = 0
            self.stats = BatchStats(total=len(self._queue))
            self._stop_flag.clear()
            self._pause_flag.clear()
            self._finished = False
            self._config = config
            self._on_item = on_item
            self._on_progress = on_progress
            self._on_finish = on_finish
            self._on_error = on_error

        self._threads = []
        n_workers = min(self.workers, max(1, len(channels)))
        with self._lock:
            self._active = n_workers if channels else 0

        if not channels:
            self._finish()
            return

        for idx in range(n_workers):
            t = threading.Thread(target=self._worker, args=(idx,),
                                 name=f"iptv-test-{idx}", daemon=True)
            t.start()
            self._threads.append(t)

    # ── 工作线程 ────────────────────────────────────────────────────────────
    def _next_channel(self) -> Optional[Channel]:
        with self._lock:
            if self._cursor >= len(self._queue):
                return None
            ch = self._queue[self._cursor]
            self._cursor += 1
            return ch

    def _worker(self, _idx: int) -> None:
        try:
            self._work_loop()
        finally:
            # 最后一个退出的线程负责发完成事件。
            # 用"活跃线程数归零"而不是"队列耗尽"判断收尾：用户中途停止时
            # 队列里必然还剩条目，按队列判断会导致 on_finish 永远不触发。
            with self._lock:
                self._active -= 1
                last = self._active <= 0
            if last:
                self._finish()

    def _work_loop(self) -> None:
        while not self._stop_flag.is_set():
            # 暂停：原地等待，不消耗队列
            while self._pause_flag.is_set() and not self._stop_flag.is_set():
                time.sleep(0.08)
            if self._stop_flag.is_set():
                break

            ch = self._next_channel()
            if ch is None:
                break

            try:
                result = test_one(ch, self._config, self.mode,
                                  self.decode_seconds, self.tools)
            except Exception as exc:                        # noqa: BLE001
                ch.is_valid = False
                ch.location = f"异常: {exc}"
                if self._on_error:
                    try:
                        self._on_error(ch, exc)
                    except Exception:
                        pass
                result = ch

            with self._lock:
                self.stats.done += 1
                if result.is_valid:
                    self.stats.valid += 1
                else:
                    self.stats.failed += 1
                snapshot = self.stats

            if self._on_item:
                try:
                    self._on_item(result, snapshot)
                except Exception:
                    pass                    # 界面回调出错不能拖垮工作线程
            if self._on_progress:
                try:
                    self._on_progress(snapshot)
                except Exception:
                    pass

    def _finish(self) -> None:
        with self._finish_lock:
            if self._finished:
                return
            self._finished = True
            if self.stats.finished_at is None:
                self.stats.finished_at = time.time()
            snapshot = self.stats
        if self._on_finish:
            try:
                self._on_finish(snapshot)
            except Exception:
                pass
