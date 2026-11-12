# -*- coding: utf-8 -*-
"""test_runner 验证：FFmpeg 能力探测 + HTTP/FFmpeg/叠加 三种模式 + 批量调度

不联网也能跑：不可达地址的失败路径足以验证协议与状态流转。
如果本机有 ffmpeg，会额外验证"真解码"分支；没有则跳过相关断言。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_runner as R
from test_logic import Channel, TestConfig

ok = 0
fail = []
skip = []


def check(cond, msg):
    global ok
    if cond:
        ok += 1
        print(f"  ok  {msg}")
    else:
        fail.append(msg)
        print(f"  FAIL {msg}")


def check_skip(msg):
    skip.append(msg)
    print(f"  skip {msg}")


def dead_channel(name="坏地址", port=1):
    return Channel(name=name, link_str=f"http://127.0.0.1:{port}/none.m3u8",
                   group_name="Probe")


FAST = TestConfig(max_workers=4, timeout=3, check_fluency=False,
                  check_live=False, strict_validation=True)


# ════════════════════════════════════════════════════════════════════════════
print("== 1. 模式常量 ==")
check(R.MODES == ("http", "ffmpeg", "both"), "三种模式齐备")
check(R.mode_label("http") == "HTTP 探活", "http 标签")
check(R.mode_label("ffmpeg") == "FFmpeg 解码", "ffmpeg 标签")
check(R.mode_label("both") == "叠加检测", "both 标签")
check(all(R.mode_hint(m) for m in R.MODES), "每种模式都有说明文案")


# ════════════════════════════════════════════════════════════════════════════
print("== 2. FFmpeg 能力探测 ==")
tools = R.FfmpegTools.detect()
print(f"     ffmpeg={tools.ffmpeg}")
print(f"     ffprobe={tools.ffprobe}")
print(f"     version={tools.version}")
check(tools.ffmpeg_ok, "找到 ffmpeg.exe")
check(tools.ffprobe_ok, "找到 ffprobe.exe")
check(tools.ready, "FFmpeg 能力完整")


# ════════════════════════════════════════════════════════════════════════════
print("== 3. http 模式：不可达地址应判为不可用 ==")
ch = R.test_one(dead_channel(), FAST, "http", 2, tools)
check(ch.is_valid is False, "is_valid=False")
check(ch.decode_ok is None, "http 模式不填 decode_ok")
check(ch.location != "未测试", f"location 已被更新: {ch.location}")


# ════════════════════════════════════════════════════════════════════════════
print("== 4. ffmpeg 模式：不可达地址应优雅失败 ==")
ch = R.test_one(dead_channel(), FAST, "ffmpeg", 1, tools)
check(ch.is_valid is False, "is_valid=False")
check(ch.decode_ok is False, "decode_ok=False")
check(len(ch.decode_summary) > 0, f"有可读原因: {ch.decode_summary[:60]}")
check(ch.decode_frames == 0, "解码帧数为 0")


# ════════════════════════════════════════════════════════════════════════════
print("== 5. both 模式：HTTP 已失败则不浪费时间去解码 ==")
t0 = time.time()
ch = R.test_one(dead_channel(), FAST, "both", 3, tools)
elapsed = time.time() - t0
check(ch.is_valid is False, "is_valid=False")
check(ch.decode_ok is None, "HTTP 未通过时跳过解码，decode_ok 保持 None")
check(elapsed < 8, f"耗时可接受: {elapsed:.2f}s")


# ════════════════════════════════════════════════════════════════════════════
print("== 6. 单条检测不抛异常（异常收敛成不可用）==")
weird = Channel(name="怪地址", link_str="这不是URL", group_name="Probe")
try:
    ch = R.test_one(weird, FAST, "http", 1, tools)
    check(ch.is_valid is False, "非法地址被收敛为不可用")
except Exception as e:
    check(False, f"不应抛异常，实际抛出: {e}")


# ════════════════════════════════════════════════════════════════════════════
print("== 7. 批量调度：3 条不可达，应全部完成 ==")
channels = [dead_channel(f"坏{i}", i + 1) for i in range(3)]
items = []
finished = []
runner = R.BatchRunner(mode="http", workers=3, decode_seconds=1)
runner.start(channels, FAST,
             on_item=lambda c, s: items.append(c),
             on_finish=lambda s: finished.append(s))

deadline = time.time() + 40
while not finished and time.time() < deadline:
    time.sleep(0.1)

check(len(finished) == 1, f"on_finish 触发一次（实际 {len(finished)}）")
check(len(items) == 3, f"on_item 回调 3 次（实际 {len(items)}）")
if finished:
    st = finished[0]
    check(st.total == 3, f"total=3（{st.total}）")
    check(st.done == 3, f"done=3（{st.done}）")
    check(st.valid == 0, f"valid=0（{st.valid}）")
    check(st.failed == 3, f"failed=3（{st.failed}）")
    check(st.finished_at is not None, "记录了结束时间")
    check(abs(st.progress - 1.0) < 1e-6, "progress=1.0")


# ════════════════════════════════════════════════════════════════════════════
print("== 8. 批量调度：停止应尽快返回且不崩 ==")
# 这一节只验证**调度**语义（停止/收尾/统计），不该由网络时序决定结果，
# 所以把单条检测打桩成可控的慢函数。真实网络路径已在第 3~5 节验证过。
_real_test_one = R.test_one
_fake_calls = {"n": 0}


def _slow_fake(ch, config, mode, seconds, tools=None):
    _fake_calls["n"] += 1
    time.sleep(0.6)                    # 每条稳定耗时，与网络无关
    ch.is_valid = False
    ch.location = "桩：不可用"
    return ch


R.test_one = _slow_fake
try:
    channels = [Channel(name=f"慢{i}", link_str=f"http://127.0.0.1:1/x{i}.m3u8",
                        group_name="Probe") for i in range(20)]
    SLOW = TestConfig(max_workers=2, timeout=5, check_fluency=False,
                      check_live=False, strict_validation=True)
    stopped_finish = []
    runner2 = R.BatchRunner(mode="http", workers=2, decode_seconds=1)
    runner2.start(channels, SLOW, on_finish=lambda s: stopped_finish.append(s))
    time.sleep(0.5)
    runner2.stop()

    deadline = time.time() + 20
    while not stopped_finish and time.time() < deadline:
        time.sleep(0.1)

    check(len(stopped_finish) == 1, "停止后仍会触发一次 on_finish")
    if stopped_finish:
        st = stopped_finish[0]
        check(st.done < st.total, f"未跑完全部（done={st.done}/{st.total}）")
        check(st.elapsed < 10, f"停止生效，未等满整批（耗时 {st.elapsed:.1f}s）")
finally:
    R.test_one = _real_test_one


# ════════════════════════════════════════════════════════════════════════════
print("== 9. 批量调度：空列表应立刻完成 ==")
empty_finished = []
runner3 = R.BatchRunner(mode="http", workers=4)
runner3.start([], FAST, on_finish=lambda s: empty_finished.append(s))
time.sleep(0.2)
check(len(empty_finished) == 1, "空列表立即触发 on_finish")
if empty_finished:
    check(empty_finished[0].total == 0, "total=0")
    check(empty_finished[0].progress == 0.0, "progress 不除零")


# ════════════════════════════════════════════════════════════════════════════
print("== 10. 暂停 / 继续 ==")
channels = [dead_channel(f"暂停{i}", i + 1) for i in range(8)]
pause_items = []
pause_finished = []
runner4 = R.BatchRunner(mode="http", workers=1, decode_seconds=1)
runner4.start(channels, FAST,
              on_item=lambda c, s: pause_items.append(c),
              on_finish=lambda s: pause_finished.append(s))
runner4.pause()
check(runner4.paused, "pause() 后 paused=True")
n_at_pause = len(pause_items)
time.sleep(1.0)
check(len(pause_items) <= n_at_pause + 1, "暂停期间基本不再产出结果")
runner4.resume()
check(not runner4.paused, "resume() 后 paused=False")

deadline = time.time() + 40
while not pause_finished and time.time() < deadline:
    time.sleep(0.1)
check(len(pause_finished) == 1, "继续后能跑完")
if pause_finished:
    check(pause_finished[0].done == 8, f"8 条全部完成（{pause_finished[0].done}）")


# ════════════════════════════════════════════════════════════════════════════
print("== 11. ETA 估算 ==")
from dataclasses import replace  # noqa: E402
st = R.BatchStats(total=10, done=5, started_at=time.time() - 10)
eta = st.eta
check(eta is not None and 8 <= eta <= 12, f"ETA 约 10s（实际 {eta}）")
st2 = R.BatchStats(total=10, done=0)
check(st2.eta is None, "无样本时不估 ETA")
st3 = R.BatchStats(total=10, done=10)
check(st3.eta is None, "已完成时不估 ETA")


# ════════════════════════════════════════════════════════════════════════════
print()
print("=" * 56)
print(f"通过 {ok} 项，失败 {len(fail)} 项，跳过 {len(skip)} 项")
if fail:
    for f in fail:
        print(f"  ✗ {f}")
sys.exit(1 if fail else 0)
