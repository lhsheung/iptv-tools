# -*- coding: utf-8 -*-
"""真实 tkinter 冒烟测试（Apple 风格界面）

不进入 mainloop，只用 ``update()`` 驱动事件循环，避免卡住。
覆盖：三页构建 / 页面切换 / DPL 导入去重开关 / 导出 / 筛选 / 搜索 /
      自定义控件行为 / 两个已修 bug 的回归。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

import tkinter as tk

import dpl_format

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


# 配置写到临时文件，避免污染真实 config.json
import gui as gui_mod

_tmp_cfg = os.path.join(tempfile.gettempdir(), "iptv_gui_test_config.json")
gui_mod._config_path = lambda: _tmp_cfg
if os.path.exists(_tmp_cfg):
    os.remove(_tmp_cfg)

root = tk.Tk()
root.geometry("1120x780")
root.withdraw()          # 不上屏，但控件真实构建

app = gui_mod.IPTVApp(root)
root.update()

import theme
import widgets as W

print("== 1. 三页与工具栏 ==")
check(set(app._pages) == {"import", "results", "settings"}, "三个页面都已构建")
check(app._page == "import", "默认停在导入页")
app._switch_page("results")
root.update()
check(app._page == "results", "切到结果页")
app._switch_page("settings")
root.update()
check(app._page == "settings", "切到设置页")
app._switch_page("import")
root.update()
check(app._page == "import", "切回导入页")

check(app.seg_pages is not None, "顶部分段控件存在")
check(len(app.seg_pages._items) == 3, "分段控件有 3 项")

print("== 2. 自定义控件行为 ==")
s = app.sl_workers
s.set(16)
check(s.get() == 16, f"滑块 set/get 一致（{s.get()}）")
s.set(999)
check(s.get() == 32, f"滑块上限钳制（{s.get()}）")
s.set(-5)
check(s.get() == 1, f"滑块下限钳制（{s.get()}）")
s.set(8)

sw = W.Switch(root, value=False)
check(sw.value is False, "开关初值 False")
sw.set_value(True)
check(sw.value is True, "开关可置 True")

seg = W.SegmentedControl(root, ["A", "B", "C"], selected=0)
seg.select(2)
check(seg.index == 2, "分段控件可选中第 3 项")

print("== 3. DPL 导入 ==")
sample = os.path.join(HERE, os.pardir, "test", "sample_gdty.dpl")
content = dpl_format.read_playlist_file(sample)

check(app.var_carry_title.get() is True, "carry_title 默认开启")
check(app.var_skip_invalid.get() is False, "skip_invalid 默认关闭")
check(app.var_dedupe.get() is True, "dedupe 默认开启")

app._load_source(sample, is_path=True)
root.update()
check(len(app.channels) == 40, f"去重后 40 个频道（实际 {len(app.channels)}）")
check(app.channels[0].name == "广东体育", f"首个频道名（{app.channels[0].name}）")

# 关闭去重 → 应得到 54 条（含镜像）
app.var_dedupe.set(False)
app.channels = []
app._load_source(sample, is_path=True)
root.update()
check(len(app.channels) == 54, f"关闭去重后 54 条（实际 {len(app.channels)}）")
app.var_dedupe.set(True)

# 跳过 invalid → 条数应少于 54
app.var_skip_invalid.set(True)
app.channels = []
app._load_source(sample, is_path=True)
root.update()
n_skip = len(app.channels)
check(n_skip < 54, f"跳过 invalid 后条数减少（{n_skip}）")
app.var_skip_invalid.set(False)

print("== 4. 结果行与统计 ==")
app.channels = []
app._load_source(sample, is_path=True)
root.update()
for c in app.channels:
    c.is_valid = True
    c.response_time = "150 ms"
    c.fluency_score = 75
    c.location = "广东 电信"
    c.decode_ok = True
    c.decode_frames = 30

app.rows = [gui_mod.Row(c) for c in app.channels]
app._refresh_results()
root.update()
check(len(app.rows) == 40, f"行数 40（{len(app.rows)}）")
check(app.pill_total.number_label.cget("text") == "40", "总数胶囊显示 40")
check(app.pill_valid.number_label.cget("text") == "40", "可用胶囊显示 40")
check(app.rows[0].decode.startswith("✓"), f"解码列有勾（{app.rows[0].decode}）")

print("== 5. 筛选与搜索 ==")
app._filter = "invalid"
app._refresh_results()
root.update()
check(len(app.result_list.rows) == 0, "筛「不可用」后列表为空")
app._filter = "valid"
app._refresh_results()
root.update()
check(len(app.result_list.rows) == 40, "筛「可用」后 40 行")
app._filter = "all"

app.entry_search.delete(0, "end")
app.entry_search.insert(0, "广东体育")
app._on_search()
root.update()
check(1 <= len(app.result_list.rows) < 40,
      f"搜索缩小结果（{len(app.result_list.rows)} 行）")
app.entry_search.delete(0, "end")
app._on_search()
root.update()
check(len(app.result_list.rows) == 40, "清空搜索后恢复 40 行")

print("== 6. 导出 ==")
exportable = app._exportable()
check(len(exportable) == 40, f"可导出 40（{len(exportable)}）")

out = os.path.join(tempfile.gettempdir(), "iptv_gui_export.dpl")
count = dpl_format.write_dpl_file(
    out, exportable,
    playname=app.var_playname.get() or None,
    saveplaypos=1 if app.var_dpl_savepos.get() else 0,
    include_played=bool(app.var_dpl_played.get()),
    include_invalid=bool(app.var_dpl_invalid.get()),
)
check(count == 40, f"导出 40 条（{count}）")
raw = open(out, "rb").read()
check(raw.startswith(b"\xef\xbb\xbfDAUMPLAYLIST"), "文件头正确")
back = dpl_format.parse_dpl(raw.decode("utf-8-sig"))
check(len(back.entries) == 40, "回读 40 条")

print("== 7. 配置保存（回归：原版引用不存在的 cfg_whitelist）==")
app.sl_workers.set(12)
app.var_strict.set(False)
try:
    app._on_save_config()
    root.update()
    saved = True
except Exception as exc:                                  # noqa: BLE001
    saved = False
    print(f"      异常: {exc}")
check(saved, "保存配置不抛异常")
check(os.path.exists(_tmp_cfg), "配置文件已写出")
if os.path.exists(_tmp_cfg):
    import json
    data = json.load(open(_tmp_cfg, encoding="utf-8"))
    check(data.get("max_workers") == 12, f"保存了并发数（{data.get('max_workers')}）")
    check(data.get("strict_validation") is False, "保存了开关状态")
    check("domain_blacklist" in data, "保留了域名黑名单字段")

print("== 8. 暂停/停止（回归：原版 time 未导入）==")
check(hasattr(gui_mod, "time"), "gui 模块导入了 time")


class _StubRunner:
    def __init__(self):
        self.paused = False
        self.stopped = False

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        self.stopped = True


app.runner = _StubRunner()
app._on_toggle_pause()
check(app.runner.paused is True, "暂停生效")
app._on_toggle_pause()
check(app.runner.paused is False, "继续生效")
app._on_stop_test()
check(app.runner.stopped is True, "停止生效")
app.runner = None

print("== 9. 检测方式切换 ==")
app.seg_mode.select(1)          # FFmpeg 解码
root.update()
check(app._current_mode() == "ffmpeg", f"模式切到 ffmpeg（{app._current_mode()}）")
check(app.seg_mode.index == 1, "分段控件索引同步")
app.seg_mode.select(2)          # 叠加检测
check(app._current_mode() == "both", "模式切到 both")
app.seg_mode.select(0)
check(app._current_mode() == "http", "模式回到 http")

print("== 10. 清理 ==")
root.destroy()
check(True, "窗口正常销毁")
try:
    os.remove(out)
except Exception:
    pass
try:
    os.remove(_tmp_cfg)
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
