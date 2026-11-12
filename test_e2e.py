# -*- coding: utf-8 -*-
"""端到端数据管线验证（不依赖 GUI）

为什么不用假 tkinter
--------------------
上一版为了让 ``import gui`` 在无显示器环境跑通，往 ``sys.modules`` 里塞了一个
假的 tkinter 桩，然后去断言私有方法名 —— 这种测试既脆弱（改个方法名就红）
又没验证真实行为（桩不执行任何逻辑）。

界面行为已由 ``test_gui_smoke.py`` 用**真实 Tk**覆盖。这里专注验证
数据从「文件」到「结果」再到「导出文件」的完整链路，也就是界面背后
真正会犯错的那部分：

    真实 DPL 文件
      → dpl_format 读取（编码自适应）
      → test_logic 解析（去重 / 沿用标题 / 跳过 invalid）
      → test_runner 批量检测（打桩，避免依赖网络）
      → dpl_format 导出（BOM + CRLF + 多镜像）
      → 重新读回，条数与字段一致
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import dpl_format
import test_logic
import test_runner as TR
from test_logic import TestConfig

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


SAMPLE = os.path.join(HERE, os.pardir, "test", "sample_gdty.dpl")

print("== 1. 读取真实 DPL ==")
check(os.path.exists(SAMPLE), f"样例文件存在: {os.path.basename(SAMPLE)}")
raw = dpl_format.read_playlist_file(SAMPLE)
check(raw.startswith("DAUMPLAYLIST"), "首行为 DAUMPLAYLIST")
pl = dpl_format.parse_dpl(raw)
check(len(pl.entries) == 54, f"原始条目 54 条（{len(pl.entries)}）")
check(pl.playname.startswith("http://"), f"playname 已读出: {pl.playname[:40]}")

print("== 2. 解析为频道（三种选项组合）==")
ch_default = test_logic.parse_dpl_source(raw, SAMPLE)
check(len(ch_default) == 40, f"默认（去重）→ 40 个频道（{len(ch_default)}）")

ch_nodedupe = test_logic.parse_dpl_source(raw, SAMPLE, dedupe=False)
check(len(ch_nodedupe) == 54, f"不去重 → 54 条镜像（{len(ch_nodedupe)}）")

ch_noinvalid = test_logic.parse_dpl_source(raw, SAMPLE, skip_invalid=True)
check(len(ch_noinvalid) < 40, f"跳过 invalid → {len(ch_noinvalid)} 个")

# 多镜像语义：同一频道名应对应多个 URL
from collections import Counter
name_count = Counter(c.name for c in ch_nodedupe)
multi = [n for n, k in name_count.items() if k > 1]
check(len(multi) > 0, f"存在多镜像频道（{len(multi)} 个频道有多个地址）")

print("== 3. 批量检测（打桩，不依赖网络）==")
_real = TR.test_one


def fake_test_one(ch, config, mode, seconds, tools=None):
    """模拟：名字里带「体育」的判为可用，其余不可用。"""
    time.sleep(0.01)
    ok_flag = "体育" in ch.name
    ch.is_valid = ok_flag
    ch.response_time = "120 ms" if ok_flag else ""
    ch.fluency_score = 88 if ok_flag else 0
    ch.location = "广东省 广州市 电信" if ok_flag else "测试失败/过滤"
    if mode in ("ffmpeg", "both"):
        ch.decode_ok = ok_flag
        ch.decode_frames = 40 if ok_flag else 0
    return ch


TR.test_one = fake_test_one
try:
    channels = test_logic.parse_dpl_source(raw, SAMPLE)
    finished = []
    items = []
    runner = TR.BatchRunner(mode="http", workers=4, decode_seconds=1)
    runner.start(channels, TestConfig(max_workers=4, timeout=5,
                                      check_fluency=False, check_live=False),
                 on_item=lambda c, s: items.append(c),
                 on_finish=lambda s: finished.append(s))

    deadline = time.time() + 30
    while not finished and time.time() < deadline:
        time.sleep(0.05)

    check(len(finished) == 1, "整批结束且只结束一次")
    stats = finished[0] if finished else None
    check(stats is not None and stats.done == 40, f"完成 40 条（{stats.done if stats else '?'}）")
    check(stats is not None and stats.valid > 0, f"有可用结果（{stats.valid if stats else 0}）")
    check(stats is not None and stats.valid + stats.failed == 40, "可用 + 不可用 = 总数")
finally:
    TR.test_one = _real

print("== 4. 导出 DPL 并回读 ==")
valid = [c for c in channels if c.is_valid]
check(len(valid) > 0, f"有 {len(valid)} 个可用频道可导出")

tmp_dpl = os.path.join(tempfile.gettempdir(), "iptv_e2e_pipeline.dpl")
count = dpl_format.write_dpl_file(
    tmp_dpl, valid,
    playname="端到端测试",
    saveplaypos=1,
    include_played=False,
    include_invalid=False,
)
check(count == len(valid), f"写入 {count} 条（期望 {len(valid)}）")

blob = open(tmp_dpl, "rb").read()
check(blob.startswith(b"\xef\xbb\xbfDAUMPLAYLIST"), "带 UTF-8 BOM 且首行正确")
check(b"\r\n" in blob, "使用 CRLF 换行")
check(b"playname=" in blob, "包含 playname 头")
check(b"saveplaypos=1" in blob, "包含 saveplaypos 头")

back = dpl_format.parse_dpl(blob.decode("utf-8-sig"))
check(len(back.entries) == len(valid), f"回读条数一致（{len(back.entries)}）")
check(back.entries[0].file.startswith("http"), "回读首条 file 有效")

# 多镜像：同一频道多个地址导出后应仍是多条独立条目
by_name = {}
for c in valid:
    by_name.setdefault(c.name, 0)
    by_name[c.name] += 1
dup_names = [n for n, k in by_name.items() if k > 1]
if dup_names:
    n_in_file = sum(1 for e in back.entries
                    if any(n in (e.title or "") for n in dup_names))
    check(n_in_file >= 2, "多镜像频道导出为多条独立条目")

print("== 5. 导出 M3U ==")
tmp_m3u = os.path.join(tempfile.gettempdir(), "iptv_e2e_pipeline.m3u")
with open(tmp_m3u, "w", encoding="utf-8", newline="\n") as f:
    f.write("#EXTM3U\n")
    for c in valid:
        attrs = f' group-title="{c.group_name or "未分组"}"'
        if c.fluency_score is not None:
            attrs += f' fluency_score="{c.fluency_score}"'
        f.write(f"#EXTINF:-1{attrs},{c.name}\n")
        f.write(f"{c.final_url or c.link_str}\n")

text = open(tmp_m3u, encoding="utf-8").read()
check(text.startswith("#EXTM3U\n"), "M3U 首行正确")
check(text.count("#EXTINF") == len(valid), f"EXTINF 行数 {len(valid)}")
check("fluency_score=" in text, "带出流畅度字段")
check("group-title=" in text, "带出分组字段")

print("== 6. 回归：解析结果可直接喂给导出（字段完整）==")
c0 = valid[0]
check(bool(c0.name), "频道有名称")
check(bool(c0.link_str), "频道有地址")
check(c0.group_name is not None, "频道有分组字段")
check(isinstance(c0.is_valid, bool), "is_valid 是布尔值")

print("== 7. 清理 ==")
for p in (tmp_dpl, tmp_m3u):
    try:
        os.remove(p)
    except Exception:
        pass
check(not os.path.exists(tmp_dpl), "临时 DPL 已清理")

print()
print(f"通过 {ok} / {ok + len(fail)}")
if fail:
    print("失败项:")
    for f in fail:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
