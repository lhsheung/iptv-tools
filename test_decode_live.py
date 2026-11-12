# -*- coding: utf-8 -*-
"""FFmpeg 真解码端到端验证（完全离线）

做法：用 ffmpeg 现场生成一段 6 秒 HLS，起一个本地 HTTP 服务把它喂进去，
再走 ``test_runner.test_one`` 的 ffmpeg / both 分支，断言确实解出了帧。

这样验证的是"解码链路真的通"，而不是"坏地址能优雅失败"——
后者只证明错误处理没崩，证明不了能解出画面。
"""
import http.server
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import test_runner as TR
from test_logic import Channel, TestConfig

ok = 0
fail = []


def check(cond, msg):
    global ok
    if cond:
        ok += 1
        print(f"  ok  {msg}")
    else:
        fail.append(msg)
        print(f"  FAIL {msg}")


STREAM_DIR = os.path.join(HERE, "_tmp_stream")
FFMPEG = os.path.join(HERE, "tools", "ffmpeg", "bin", "ffmpeg.exe")

print("== 1. 准备本地 HLS ==")
if not os.path.exists(os.path.join(STREAM_DIR, "test.m3u8")):
    os.makedirs(STREAM_DIR, exist_ok=True)
    r = subprocess.run(
        [FFMPEG, "-y",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "25",
         "-c:a", "aac", "-b:a", "64k",
         "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
         "-hls_segment_filename", os.path.join(STREAM_DIR, "seg%d.ts"),
         os.path.join(STREAM_DIR, "test.m3u8")],
        capture_output=True)
    check(r.returncode == 0, "ffmpeg 生成测试流成功")
else:
    check(True, "复用已生成的测试流")

check(os.path.exists(os.path.join(STREAM_DIR, "test.m3u8")), "m3u8 存在")
seg_count = len([f for f in os.listdir(STREAM_DIR) if f.endswith(".ts")])
check(seg_count >= 2, f"分片数 {seg_count}")

print("== 2. 启动本地 HTTP 服务 ==")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


# 必须显式指定 directory：SimpleHTTPRequestHandler 默认按 os.getcwd() 解析路径，
# 而本脚本稍后会把 cwd 切回脚本目录，那样请求就会 404。
handler = lambda *a, **kw: Quiet(*a, directory=STREAM_DIR, **kw)   # noqa: E731
httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.2)
url = f"http://127.0.0.1:{port}/test.m3u8"
check(True, f"服务已起：{url}")

print("== 3. FFprobe 解析媒体信息 ==")
tools = TR.FfmpegTools.detect()
check(tools.ready, "ffmpeg + ffprobe 就绪")

import ffmpeg_check
info = ffmpeg_check.probe(url, tools.ffprobe, timeout=15)
check(info.ok, f"probe 成功（error={info.error or '无'}）")
check(info.video_codec == "h264", f"识别视频编码 {info.video_codec}")
check(info.audio_codec == "aac", f"识别音频编码 {info.audio_codec}")
check(info.width == 320 and info.height == 240,
      f"识别分辨率 {info.width}x{info.height}")

print("== 4. FFmpeg 真解码出帧 ==")
dec = ffmpeg_check.decode(url, tools.ffmpeg, seconds=3, timeout=30)
check(dec.ok, f"解码成功（error={dec.error or '无'}）")
check(dec.frames > 10, f"解出 {dec.frames} 帧")
check(dec.has_video, "检测到视频流")
check(dec.has_audio, "检测到音频流")

print("== 5. test_one 走 ffmpeg 模式 ==")
cfg = TestConfig(max_workers=1, timeout=8, check_fluency=False,
                 check_live=False, strict_validation=False)
ch = Channel(name="本地测试流", link_str=url, group_name="Probe")
ch = TR.test_one(ch, cfg, "ffmpeg", 3, tools)
check(ch.is_valid is True, "ffmpeg 模式判定为可用")
check(ch.decode_ok is True, "decode_ok=True")
check(ch.decode_frames > 0, f"decode_frames={ch.decode_frames}")
check(ch.codec == "h264", f"编码回填 {ch.codec}")
check(bool(ch.decode_summary), f"摘要：{ch.decode_summary[:50]}")

print("== 6. test_one 走 both 模式 ==")
ch2 = Channel(name="本地测试流", link_str=url, group_name="Probe")
ch2 = TR.test_one(ch2, cfg, "both", 3, tools)
check(ch2.is_valid is True, "both 模式判定为可用")
check(ch2.decode_ok is True, "both 模式也做了解码")

print("== 7. decode_probe 组合输出 ==")
dp = ffmpeg_check.decode_probe(url, tools.ffmpeg, tools.ffprobe, seconds=2)
check(dp["ok"] is True, "decode_probe.ok=True")
check(dp["probe"]["width"] == 320, "probe 分辨率进入结果")
check(dp["decode"]["frames"] > 0, "decode 帧数进入结果")
check("/" in dp["summary"], f"摘要可读：{dp['summary'][:60]}")

print("== 8. 清理 ==")
httpd.shutdown()
httpd.server_close()
check(True, "服务已关闭")
shutil.rmtree(STREAM_DIR, ignore_errors=True)
check(not os.path.exists(STREAM_DIR), "临时流目录已清理")

print()
print(f"通过 {ok} / {ok + len(fail)}")
if fail:
    print("失败项:")
    for f in fail:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
