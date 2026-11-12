"""
IPTV 直播源测试工具 — Apple 风格界面（tkinter/ttk）

设计说明
--------
- **三页 + 常驻底栏**：导入源 / 测试结果 / 设置用分段控件切换；开始测试与进度
  常驻底部，切页不打断操作。
- **白卡片承载内容**：一页只有 2~3 张卡，卡内用留白和发丝线分组，不画边框盒子。
- **强调色只出现在主操作**：一页最多一个实心蓝按钮，其余都是白底/无底。
- **动效只做反馈**：悬停 120ms、选中迁移 180ms、进度 220ms，不做装饰动画。

相对上一版的功能增量
--------------------
- 检测方式可选 HTTP 探活 / FFmpeg 真解码 / 叠加检测，并显示 FFmpeg 版本状态
- 结果表新增「解码」列；右键单条可做解码复核
- 底部进度条显示剩余时间估算

顺带修掉的两个 bug
------------------
- 原 `_on_toggle_pause` 里用了 ``time.sleep`` 但文件没 ``import time``，一暂停就崩
- 原 `_on_save_config` 读 ``self.cfg_whitelist``，这个控件从未创建，一保存就崩
"""

import json
import os
import sys
import threading
import time
import webbrowser

import tkinter as tk
from tkinter import BooleanVar, StringVar, filedialog, messagebox, ttk

import dnd
import dpl_format
import theme
import test_runner as TR
import widgets as W
from test_logic import (
    Channel,
    TestConfig,
    init_xdb,
    is_valid_playlist_content,
    parse_dpl_source,
    parse_source,
)


# ════════════════════════════════════════════════════════════════════════════
# 配置读写
# ════════════════════════════════════════════════════════════════════════════

def _config_path() -> str:
    """配置文件位置。

    源码运行时放在脚本旁边；打包后 ``__file__`` 指向 PyInstaller 的
    ``_internal`` 目录，普通用户根本找不到，所以改成**放在 exe 同级**，
    这样"绿色版"拷走整个文件夹时配置也跟着走。
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "config.json")


def _load_config() -> dict:
    path = _config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_config(cfg: dict) -> None:
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _default_config() -> dict:
    return {
        "max_workers": 8,
        "min_fluency_score": 10,
        "timeout": 8,
        "check_fluency": True,
        "check_live": True,
        "quick_live_check": False,
        "strict_validation": True,
        "mode": TR.MODE_HTTP,
        "decode_seconds": 3,
        "data_source_whitelist": [],
        "domain_blacklist": [],
        "source_history": [],
    }


def _merge_config() -> dict:
    return {**_default_config(), **_load_config()}


# ════════════════════════════════════════════════════════════════════════════
# 结果行视图对象
# ════════════════════════════════════════════════════════════════════════════

class Row:
    """把 Channel 映射成列表要显示的字段。

    单独一层是为了让列表控件不依赖 Channel 的具体结构 —— 将来换数据源
    只要改这里，不用动绘制代码。
    """

    __slots__ = ("ch", "state", "name", "group", "resp", "fluency",
                 "decode", "location", "extra")

    def __init__(self, ch: Channel):
        self.ch = ch
        if ch.is_valid:
            self.state = "valid"
        elif ch.location and ("未测试" not in ch.location) and ch.response_time == "":
            self.state = "invalid"
        elif ch.is_valid is False and ch.location == "未测试":
            self.state = "pending"
        else:
            self.state = "invalid" if not ch.is_valid else "valid"

        self.name = ch.name or "（未命名）"
        self.group = ch.group_name or "未分组"
        self.resp = ch.response_time or "—"
        self.fluency = (f"{ch.fluency_score}" if ch.fluency_score is not None else "—")
        self.location = ch.location or "—"
        self.decode = self._decode_text(ch)
        self.extra = {}

    @staticmethod
    def _decode_text(ch: Channel) -> str:
        if ch.decode_ok is None:
            return "—"
        if ch.decode_ok:
            frames = f"{ch.decode_frames}帧" if ch.decode_frames else "可解码"
            return f"✓ {frames}"
        return "✕ 失败"

    @property
    def state_text(self) -> str:
        return {"valid": "可用", "invalid": "不可用",
                "testing": "检测中", "pending": "未测"}[self.state]


#: (key, 列标题, 相对宽度, 对齐)
RESULT_COLUMNS = [
    ("state", "状态", 90, "w"),
    ("name", "频道", 250, "w"),
    ("group", "分组", 105, "w"),
    ("resp", "响应", 85, "e"),
    ("fluency", "流畅度", 75, "center"),
    ("decode", "解码", 95, "w"),
    ("location", "归属地", 130, "w"),
]


# ════════════════════════════════════════════════════════════════════════════
# 主界面
# ════════════════════════════════════════════════════════════════════════════

class IPTVApp(ttk.Frame):
    """整窗外壳。外部只需 ``IPTVApp(root)``。"""

    PAGES = ("import", "results", "settings")

    def __init__(self, master):
        super().__init__(master, padding=0)
        self.master = master
        self.master.title("IPTV 直播源测试工具")
        self.master.configure(bg=theme.CANVAS)

        theme.init_scale(master)
        W._style_scrollbar()

        # ── 运行时状态 ──────────────────────────────────────────────────────
        self.channels: list[Channel] = []
        self.rows: list[Row] = []
        self.config: dict = _merge_config()
        self.runner: TR.BatchRunner | None = None
        self.testing = False
        self._sources_used: list[str] = []
        self._page = "import"
        self._log_open = False

        # 筛选状态
        self._filter = "all"
        self._keyword = ""

        init_xdb()

        self.pack(fill="both", expand=True)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_toolbar()
        self._build_body()
        self._build_log_drawer()
        self._build_actionbar()

        self._load_config_to_ui()
        self._switch_page("import")

    # ════════════════════════════════════════════════════════════════════
    # 结构搭建
    # ════════════════════════════════════════════════════════════════════

    def _build_toolbar(self) -> None:
        bar = tk.Frame(self, bg=theme.SURFACE_ALT, height=theme.px(theme.TOOLBAR_HEIGHT))
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(1, weight=1)

        # 左：应用标识
        left = tk.Frame(bar, bg=theme.SURFACE_ALT)
        left.grid(row=0, column=0, sticky="w", padx=(theme.SPACE_4, 0))
        mark = W.RoundedSurface(left, radius=7, fill=theme.ACCENT,
                                bg=theme.SURFACE_ALT,
                                width=theme.px(22), height=theme.px(22))
        mark.pack(side="left")
        mark.create_text(theme.px(11), theme.px(11), text="▶",
                         fill=theme.ON_ACCENT,
                         font=(theme.FONT_UI, 8, "bold"))
        W.label(left, "IPTV Tools", size=theme.SIZE_HEADLINE, bold=True,
                bg=theme.SURFACE_ALT).pack(side="left", padx=(theme.SPACE_2, 0))

        # 中：页面切换
        mid = tk.Frame(bar, bg=theme.SURFACE_ALT)
        mid.grid(row=0, column=1)
        self.seg_pages = W.SegmentedControl(
            mid, ["导入源", "测试结果", "设置"],
            command=self._on_segment, height=30, bg=theme.SURFACE_ALT,
        )
        self.seg_pages.configure(width=theme.px(300), height=theme.px(30))
        self.seg_pages.pack()

        # 右：日志 / 关于
        right = tk.Frame(bar, bg=theme.SURFACE_ALT)
        right.grid(row=0, column=2, sticky="e", padx=(0, theme.SPACE_4))
        self.btn_log = W.PillButton(right, "日志", variant="ghost",
                                    command=self._toggle_log, height=28,
                                    bg=theme.SURFACE_ALT)
        self.btn_log.pack(side="left", padx=(0, theme.SPACE_1))
        W.PillButton(right, "关于", variant="ghost", command=self._show_about,
                     height=28, bg=theme.SURFACE_ALT).pack(side="left")

        # 底部分隔线
        tk.Frame(self, bg=theme.SEPARATOR, height=1).grid(row=0, column=0, sticky="sew")

    def _build_body(self) -> None:
        holder = tk.Frame(self, bg=theme.CANVAS)
        holder.grid(row=1, column=0, sticky="nsew")
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)

        self.page_import = self._build_import_page(holder)
        self.page_results = self._build_results_page(holder)
        self.page_settings = self._build_settings_page(holder)

        self._pages = {
            "import": self.page_import,
            "results": self.page_results,
            "settings": self.page_settings,
        }

    # ── 页：导入源 ──────────────────────────────────────────────────────
    def _build_import_page(self, parent) -> tk.Frame:
        page = tk.Frame(parent, bg=theme.CANVAS)

        head = tk.Frame(page, bg=theme.CANVAS)
        head.pack(fill="x", padx=theme.SPACE_6, pady=(theme.SPACE_5, theme.SPACE_4))
        W.label(head, "导入源", size=theme.SIZE_LARGE_TITLE, bold=True).pack(anchor="w")
        W.label(head, "支持 M3U / M3U8 / TXT / PotPlayer DPL，可粘贴地址、选本地文件、载入网址，或直接把文件拖进窗口。",
                size=theme.SIZE_CAPTION, color=theme.LABEL_SECONDARY).pack(anchor="w",
                                                                          pady=(theme.SPACE_1, 0))

        # 卡片一：源地址
        card = W.Card(page, padding=theme.SPACE_4)
        card.pack(fill="x", padx=theme.SPACE_6, pady=(0, theme.SPACE_3))
        W.label(card.body, "源地址", size=theme.SIZE_TITLE, bold=True).pack(anchor="w")
        W.label(card.body, "每行一个地址，也支持逗号分隔；本地路径同理。也可把 .m3u / .txt / .dpl 文件拖到窗口任意位置。",
                size=theme.SIZE_CAPTION, color=theme.LABEL_TERTIARY).pack(
            anchor="w", pady=(2, theme.SPACE_2))

        wrap = tk.Frame(card.body, bg=theme.SURFACE_SUNKEN,
                        highlightthickness=1, highlightbackground=theme.SEPARATOR)
        wrap.pack(fill="x")
        self.url_text = tk.Text(wrap, height=4, wrap="none",
                             bg=theme.SURFACE_SUNKEN, fg=theme.LABEL,
                             insertbackground=theme.ACCENT,
                             relief="flat", bd=0, padx=theme.SPACE_2, pady=theme.SPACE_2,
                             font=(theme.FONT_MONO_FALLBACK, theme.SIZE_BODY))
        self.url_text.pack(fill="both", expand=True)

        btnrow = tk.Frame(card.body, bg=theme.SURFACE)
        btnrow.pack(fill="x", pady=(theme.SPACE_3, 0))
        W.PillButton(btnrow, "打开文件", variant="secondary",
                     command=self._on_select_file, width=104).pack(side="left")
        W.PillButton(btnrow, "解析并载入", variant="primary", glyph="▶",
                     command=self._on_preview, width=118).pack(
            side="left", padx=(theme.SPACE_2, 0))
        W.PillButton(btnrow, "历史源", variant="ghost",
                     command=self._on_load_history, width=72).pack(
            side="left", padx=(theme.SPACE_2, 0))
        W.PillButton(btnrow, "清空输入", variant="ghost",
                     command=lambda: self.url_text.delete("1.0", "end"),
                     width=80).pack(side="right")

        self.lbl_parse = W.label(card.body, "", size=theme.SIZE_CAPTION,
                                 color=theme.LABEL_SECONDARY)
        self.lbl_parse.pack(anchor="w", pady=(theme.SPACE_2, 0))

        # 卡片二：DPL 导入选项
        dpl_card = W.Card(page, padding=theme.SPACE_4)
        dpl_card.pack(fill="x", padx=theme.SPACE_6)
        W.label(dpl_card.body, "PotPlayer DPL 导入选项",
                size=theme.SIZE_TITLE, bold=True).pack(anchor="w")
        W.label(dpl_card.body, "只影响 .dpl 文件的解析结果，改完立即生效。",
                size=theme.SIZE_CAPTION, color=theme.LABEL_TERTIARY).pack(
            anchor="w", pady=(2, theme.SPACE_2))

        self.var_carry_title = BooleanVar(value=True)
        self.var_skip_invalid = BooleanVar(value=False)
        self.var_dedupe = BooleanVar(value=True)

        self._switch_row(dpl_card.body, "沿用上一条标题",
                         "DPL 里常用连续条目表示同一频道的多个镜像，缺 title 时沿用上一条。",
                         self.var_carry_title)
        self._switch_row(dpl_card.body, "跳过失效条目",
                         "忽略 PotPlayer 标记为 invalid 的地址，减少无意义检测。",
                         self.var_skip_invalid)
        self._switch_row(dpl_card.body, "相同地址去重",
                         "同一 URL 只保留一条；同一频道的不同镜像仍会保留。",
                         self.var_dedupe, last=True)

        return page

    def _switch_row(self, parent, title: str, desc: str, var: BooleanVar,
                    last: bool = False) -> W.Switch:
        """一行"标题 + 说明 + 开关"。说明文字放在标题下方，避免右侧挤成一团。"""
        row = tk.Frame(parent, bg=theme.SURFACE)
        row.pack(fill="x", pady=(theme.SPACE_2 if not last else theme.SPACE_2, 0))
        texts = tk.Frame(row, bg=theme.SURFACE)
        texts.pack(side="left", fill="x", expand=True)
        W.label(texts, title, size=theme.SIZE_BODY).pack(anchor="w")
        W.label(texts, desc, size=theme.SIZE_CAPTION,
                color=theme.LABEL_TERTIARY).pack(anchor="w")
        sw = W.Switch(row, value=var.get(), command=lambda v: var.set(v),
                      bg=theme.SURFACE)
        sw.pack(side="right", padx=(theme.SPACE_3, 0))
        var.trace_add("write", lambda *_: sw.set_value(bool(var.get())))
        return sw

    # ── 页：测试结果 ────────────────────────────────────────────────────
    def _build_results_page(self, parent) -> tk.Frame:
        page = tk.Frame(parent, bg=theme.CANVAS)

        head = tk.Frame(page, bg=theme.CANVAS)
        head.pack(fill="x", padx=theme.SPACE_6, pady=(theme.SPACE_5, theme.SPACE_4))
        head.grid_columnconfigure(0, weight=1)

        titles = tk.Frame(head, bg=theme.CANVAS)
        titles.grid(row=0, column=0, sticky="w")
        W.label(titles, "测试结果", size=theme.SIZE_LARGE_TITLE, bold=True).pack(anchor="w")
        self.lbl_summary = W.label(titles, "还没有开始检测",
                                   size=theme.SIZE_CAPTION,
                                   color=theme.LABEL_SECONDARY)
        self.lbl_summary.pack(anchor="w", pady=(theme.SPACE_1, 0))

        stats = tk.Frame(head, bg=theme.CANVAS)
        stats.grid(row=0, column=1, sticky="e")
        self.pill_valid = self._stat_pill(stats, "可用", theme.SUCCESS)
        self.pill_valid.pack(side="left", padx=(0, theme.SPACE_2))
        self.pill_invalid = self._stat_pill(stats, "不可用", theme.DANGER)
        self.pill_invalid.pack(side="left", padx=(0, theme.SPACE_2))
        self.pill_total = self._stat_pill(stats, "总计", theme.LABEL)
        self.pill_total.pack(side="left")

        # 工具行
        tools = tk.Frame(page, bg=theme.CANVAS)
        tools.pack(fill="x", padx=theme.SPACE_6, pady=(0, theme.SPACE_3))
        tools.grid_columnconfigure(1, weight=1)

        self.seg_filter = W.SegmentedControl(
            tools, ["全部", "可用", "不可用"], command=self._on_filter_seg,
            height=28, bg=theme.CANVAS)
        self.seg_filter.configure(width=theme.px(210), height=theme.px(28))
        self.seg_filter.grid(row=0, column=0, sticky="w")

        # 搜索框不用 StringVar：占位提示会写进 entry，走 trace 会把提示词当成条件
        self.entry_search = W.text_entry(tools, None, width=22,
                                         placeholder="搜索频道或分组")
        self.entry_search.grid(row=0, column=1, sticky="e", ipady=theme.px(3))
        self.entry_search.bind("<KeyRelease>", lambda e: self._on_search())

        # 列表卡片：内容要铺满，所以 fill_body + auto_height=False
        list_card = W.Card(page, padding=theme.SPACE_2, auto_height=False,
                           fill_body=True, height=theme.px(300))
        list_card.pack(fill="both", expand=True, padx=theme.SPACE_6,
                       pady=(0, theme.SPACE_4))
        self.result_list = W.ResultList(
            list_card.body, RESULT_COLUMNS,
            on_activate=self._on_row_activate,
            on_context=self._on_row_context,
        )
        self.result_list.pack(fill="both", expand=True)

        return page

    def _stat_pill(self, parent, title: str, color: str) -> W.RoundedSurface:
        """统计胶囊：浅底 + 数字。数字单独更新，所以返回外层容器。"""
        pill = W.RoundedSurface(parent, radius=theme.RADIUS_CONTROL,
                                fill=theme.SURFACE, outline=theme.SEPARATOR,
                                bg=theme.CANVAS,
                                width=theme.px(86), height=theme.px(30))
        W.label(pill, title, size=theme.SIZE_CAPTION,
                color=theme.LABEL_TERTIARY, bg=theme.SURFACE).place(
            x=theme.px(10), rely=0.5, anchor="w")
        num = W.label(pill, "0", size=theme.SIZE_BODY, color=color, bold=True,
                      bg=theme.SURFACE)
        num.place(relx=1.0, x=-theme.px(10), rely=0.5, anchor="e")
        pill.number_label = num          # 供外部更新
        return pill

    # ── 页：设置 ────────────────────────────────────────────────────────
    def _build_settings_page(self, parent) -> tk.Frame:
        page = tk.Frame(parent, bg=theme.CANVAS)

        head = tk.Frame(page, bg=theme.CANVAS)
        head.pack(fill="x", padx=theme.SPACE_6, pady=(theme.SPACE_5, theme.SPACE_4))
        W.label(head, "设置", size=theme.SIZE_LARGE_TITLE, bold=True).pack(anchor="w")
        W.label(head, "改动会在下批检测生效；点「保存」写入 config.json。",
                size=theme.SIZE_CAPTION, color=theme.LABEL_SECONDARY).pack(
            anchor="w", pady=(theme.SPACE_1, 0))

        cols = tk.Frame(page, bg=theme.CANVAS)
        cols.pack(fill="both", expand=True, padx=theme.SPACE_6)
        cols.grid_columnconfigure(0, weight=1, uniform="c")
        cols.grid_columnconfigure(1, weight=1, uniform="c")

        left = tk.Frame(cols, bg=theme.CANVAS)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, theme.SPACE_2))
        right = tk.Frame(cols, bg=theme.CANVAS)
        right.grid(row=0, column=1, sticky="nsew", padx=(theme.SPACE_2, 0))

        # ── 左：检测方式
        mode_card = W.Card(left, padding=theme.SPACE_4)
        mode_card.pack(fill="x", pady=(0, theme.SPACE_3))
        W.label(mode_card.body, "检测方式", size=theme.SIZE_TITLE,
                bold=True).pack(anchor="w")
        self.lbl_mode_hint = W.label(mode_card.body, "", size=theme.SIZE_CAPTION,
                                     color=theme.LABEL_TERTIARY, wraplength=330)
        self.seg_mode = W.SegmentedControl(
            mode_card.body, ["HTTP 探活", "FFmpeg 解码", "叠加检测"],
            command=self._on_mode_seg, height=30, bg=theme.SURFACE)
        self.seg_mode.pack(fill="x", pady=(theme.SPACE_2, theme.SPACE_2))
        self.lbl_mode_hint.pack(anchor="w")

        self.frame_decode = tk.Frame(mode_card.body, bg=theme.SURFACE)
        self.frame_decode.pack(fill="x", pady=(theme.SPACE_2, 0))
        self.sl_decode, self.lbl_decode = self._slider_row(
            self.frame_decode, "解码时长", 1, 15, 3, " 秒",
            "每条流实际解码多久。越长越准，也越慢。")

        self.lbl_ffmpeg = W.label(mode_card.body, "", size=theme.SIZE_CAPTION,
                                  color=theme.LABEL_TERTIARY, wraplength=330)
        self.lbl_ffmpeg.pack(anchor="w", pady=(theme.SPACE_2, 0))

        # ── 左：性能
        perf = W.Card(left, padding=theme.SPACE_4)
        perf.pack(fill="x")
        W.label(perf.body, "性能", size=theme.SIZE_TITLE, bold=True).pack(anchor="w")
        self.sl_workers, self.lbl_workers = self._slider_row(
            perf.body, "并发数", 1, 32, 8, "")
        self.sl_timeout, self.lbl_timeout = self._slider_row(
            perf.body, "请求超时", 3, 30, 8, " 秒",
            "单次请求预算。上游对每条源会做多轮校验，最坏耗时约为该值的数倍。")
        self.sl_fluency, self.lbl_fluency = self._slider_row(
            perf.body, "流畅度下限", 0, 100, 10, "",
            "低于该分数的频道在结果里标记为不可用。")

        # ── 右：检测开关
        toggle_card = W.Card(right, padding=theme.SPACE_4)
        toggle_card.pack(fill="x", pady=(0, theme.SPACE_3))
        W.label(toggle_card.body, "检测项", size=theme.SIZE_TITLE,
                bold=True).pack(anchor="w")
        self.var_live = BooleanVar(value=True)
        self.var_fluency = BooleanVar(value=True)
        self.var_strict = BooleanVar(value=True)
        self.var_quick = BooleanVar(value=False)
        self._switch_row(toggle_card.body, "直播状态", "解析 m3u8 判断是否为直播流。",
                         self.var_live)
        self._switch_row(toggle_card.body, "流畅度评分", "下载片段估算码率与稳定性，较耗时。",
                         self.var_fluency)
        self._switch_row(toggle_card.body, "严格校验", "二次校验播放列表与分片，减少误判。",
                         self.var_strict)
        self._switch_row(toggle_card.body, "快速直播检测", "只看首个分片，牺牲准确度换速度。",
                         self.var_quick, last=True)

        # ── 右：DPL 导出
        dpl_card = W.Card(right, padding=theme.SPACE_4)
        dpl_card.pack(fill="x")
        W.label(dpl_card.body, "DPL 导出", size=theme.SIZE_TITLE, bold=True).pack(anchor="w")
        self.var_playname = StringVar(value="")
        row = tk.Frame(dpl_card.body, bg=theme.SURFACE)
        row.pack(fill="x", pady=(theme.SPACE_2, 0))
        W.label(row, "playname", size=theme.SIZE_BODY).pack(side="left")
        e = W.text_entry(row, self.var_playname, width=18)
        e.pack(side="left", padx=(theme.SPACE_2, 0), fill="x", expand=True,
               ipady=theme.px(2))
        self.var_dpl_played = BooleanVar(value=False)
        self.var_dpl_invalid = BooleanVar(value=False)
        self.var_dpl_savepos = BooleanVar(value=True)
        self._switch_row(dpl_card.body, "写入播放标记", "导出 *played* 字段。",
                         self.var_dpl_played)
        self._switch_row(dpl_card.body, "写入失效标记", "导出 *invalid* 字段。",
                         self.var_dpl_invalid)
        self._switch_row(dpl_card.body, "记住播放位置", "saveplaypos=1。",
                         self.var_dpl_savepos, last=True)

        # ── 底部按钮
        actions = tk.Frame(page, bg=theme.CANVAS)
        actions.pack(fill="x", padx=theme.SPACE_6, pady=(theme.SPACE_3, theme.SPACE_4))
        W.PillButton(actions, "保存设置", variant="primary",
                     command=self._on_save_config, width=104).pack(side="left")
        W.PillButton(actions, "恢复默认", variant="ghost",
                     command=self._on_reset_config, width=88).pack(
            side="left", padx=(theme.SPACE_2, 0))

        return page

    def _slider_row(self, parent, title: str, lo: int, hi: int, value: int,
                    suffix: str = "", desc: str = ""):
        """一行"标题 + 数值 + 滑块（+ 可选说明）"，返回 (slider, 数值标签)。"""
        box = tk.Frame(parent, bg=theme.SURFACE)
        box.pack(fill="x", pady=(theme.SPACE_2, 0))

        head = tk.Frame(box, bg=theme.SURFACE)
        head.pack(fill="x")
        W.label(head, title, size=theme.SIZE_BODY).pack(side="left")
        val = W.label(head, f"{value}{suffix}", size=theme.SIZE_BODY,
                      color=theme.LABEL_SECONDARY)
        val.pack(side="right")

        slider = W.Slider(box, lo, hi, value,
                          command=lambda v: self._set_slider_label(val, v, suffix),
                          bg=theme.SURFACE)
        slider.pack(fill="x", pady=(theme.px(3), 0))

        if desc:
            W.label(box, desc, size=theme.SIZE_CAPTION,
                    color=theme.LABEL_TERTIARY, wraplength=330).pack(
                anchor="w", pady=(theme.px(1), 0))
        return slider, val

    @staticmethod
    def _set_slider_label(lbl: tk.Label, value: int, suffix: str) -> None:
        try:
            lbl.configure(text=f"{value}{suffix}")
        except tk.TclError:
            pass

    # ── 日志抽屉 ────────────────────────────────────────────────────────
    def _build_log_drawer(self) -> None:
        self.log_frame = tk.Frame(self, bg=theme.LOG_BG, height=theme.px(150))
        self.log_frame.grid(row=2, column=0, sticky="ew")
        self.log_frame.grid_propagate(False)
        self.log_frame.grid_remove()

        head = tk.Frame(self.log_frame, bg=theme.LOG_BG)
        head.pack(fill="x", padx=theme.SPACE_3, pady=(theme.SPACE_2, 0))
        W.label(head, "运行日志", size=theme.SIZE_CAPTION,
                color=theme.LOG_DIM, bg=theme.LOG_BG).pack(side="left")
        W.PillButton(head, "清空", variant="ghost", command=self._clear_log,
                     height=24, bg=theme.LOG_BG).pack(side="right")

        self.log_text = tk.Text(self.log_frame, height=6, wrap="none",
                             bg=theme.LOG_BG, fg=theme.LOG_FG,
                             insertbackground=theme.LOG_FG,
                             relief="flat", bd=0,
                             padx=theme.SPACE_3, pady=theme.px(2),
                             font=(theme.FONT_MONO_FALLBACK, theme.SIZE_CAPTION))
        self.log_text.pack(fill="both", expand=True, padx=(0, 0), pady=(0, theme.SPACE_2))
        self.log_text.configure(state="disabled")

    # ── 底部操作栏 ──────────────────────────────────────────────────────
    def _build_actionbar(self) -> None:
        bar = tk.Frame(self, bg=theme.SURFACE)
        bar.grid(row=3, column=0, sticky="ew")
        tk.Frame(bar, bg=theme.SEPARATOR, height=1).pack(fill="x")

        # 用 pack 而不是固定高度 + grid：混用时 grid 会覆盖 frame 的 height，
        # 导致操作栏被压到 40px 左右、按钮被切边。
        inner = tk.Frame(bar, bg=theme.SURFACE)
        inner.pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_2)

        left = tk.Frame(inner, bg=theme.SURFACE)
        left.pack(side="left")
        self.btn_start = W.PillButton(left, "开始测试", variant="primary",
                                      glyph="▶", command=self._on_start_test,
                                      width=112, height=30, bg=theme.SURFACE)
        self.btn_start.pack(side="left")
        self.btn_pause = W.PillButton(left, "暂停", variant="secondary",
                                      command=self._on_toggle_pause,
                                      width=68, height=30, bg=theme.SURFACE)
        self.btn_pause.pack(side="left", padx=(theme.SPACE_2, 0))
        self.btn_stop = W.PillButton(left, "停止", variant="secondary",
                                     command=self._on_stop_test,
                                     width=68, height=30, bg=theme.SURFACE)
        self.btn_stop.pack(side="left", padx=(theme.SPACE_2, 0))
        self.btn_pause.set_enabled(False)
        self.btn_stop.set_enabled(False)

        right = tk.Frame(inner, bg=theme.SURFACE)
        right.pack(side="right")
        self.btn_export = W.PillButton(right, "导出 DPL", variant="secondary",
                                       command=self._on_export_dpl,
                                       width=92, height=30, bg=theme.SURFACE)
        self.btn_export.pack(side="left")
        self.btn_export_m3u = W.PillButton(right, "导出 M3U", variant="ghost",
                                           command=self._on_export_m3u,
                                           width=84, height=30, bg=theme.SURFACE)
        self.btn_export_m3u.pack(side="left", padx=(theme.SPACE_1, 0))
        self.btn_copy = W.PillButton(right, "复制", variant="ghost",
                                     command=self._on_copy_m3u,
                                     width=60, height=30, bg=theme.SURFACE)
        self.btn_copy.pack(side="left", padx=(theme.SPACE_1, 0))

        center = tk.Frame(inner, bg=theme.SURFACE)
        center.pack(side="left", fill="x", expand=True, padx=(theme.SPACE_4, 0))
        self.lbl_progress = W.label(center, "就绪", size=theme.SIZE_CAPTION,
                                    color=theme.LABEL_SECONDARY, bg=theme.SURFACE)
        self.lbl_progress.pack(side="left")

        # 最底一条细进度
        self.progress = W.ThinProgress(bar, height=3, bg=theme.SURFACE)
        self.progress.pack(fill="x")

    # ════════════════════════════════════════════════════════════════════
    # 页面切换
    # ════════════════════════════════════════════════════════════════════

    def _switch_page(self, name: str) -> None:
        if name not in self._pages:
            return
        for key, page in self._pages.items():
            if key == name:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        self._page = name
        if name == "results":
            self._refresh_results()

    def _on_segment(self, idx: int) -> None:
        self._switch_page(self.PAGES[idx])

    # ════════════════════════════════════════════════════════════════════
    # 配置 <-> 界面
    # ════════════════════════════════════════════════════════════════════

    def _load_config_to_ui(self) -> None:
        cfg = self.config
        self.sl_workers.set(cfg.get("max_workers", 8))
        self.sl_timeout.set(cfg.get("timeout", 8))
        self.sl_fluency.set(cfg.get("min_fluency_score", 10))
        self.sl_decode.set(cfg.get("decode_seconds", 3))

        self.var_live.set(bool(cfg.get("check_live", True)))
        self.var_fluency.set(bool(cfg.get("check_fluency", True)))
        self.var_strict.set(bool(cfg.get("strict_validation", True)))
        self.var_quick.set(bool(cfg.get("quick_live_check", False)))

        self.var_dpl_played.set(bool(cfg.get("dpl_export_played", False)))
        self.var_dpl_invalid.set(bool(cfg.get("dpl_export_invalid", False)))
        self.var_dpl_savepos.set(bool(cfg.get("dpl_export_savepos", True)))
        self.var_playname.set(cfg.get("dpl_playname", ""))

        mode = cfg.get("mode", TR.MODE_HTTP)
        idx = {"http": 0, "ffmpeg": 1, "both": 2}.get(mode, 0)
        self.seg_mode.select(idx)
        self._on_mode_seg(idx, initial=True)

        # FFmpeg 状态
        tools = TR.FfmpegTools.detect()
        if tools.ready:
            self.lbl_ffmpeg.configure(
                text=f"FFmpeg {tools.version}  已就绪，可做真实解码检测。",
                fg=theme.LABEL_TERTIARY)
        else:
            self.lbl_ffmpeg.configure(
                text="未找到 ffmpeg / ffprobe：解码相关功能不可用，"
                     "请把 ffmpeg.exe 与 ffprobe.exe 放到 tools/ffmpeg/bin/ 下。",
                fg=theme.WARNING)

    def _read_ui_config(self) -> TestConfig:
        return TestConfig(
            max_workers=int(self.sl_workers.get()),
            min_fluency_score=int(self.sl_fluency.get()),
            timeout=int(self.sl_timeout.get()),
            check_fluency=bool(self.var_fluency.get()),
            check_live=bool(self.var_live.get()),
            quick_live_check=bool(self.var_quick.get()),
            strict_validation=bool(self.var_strict.get()),
            domain_blacklist=list(self.config.get("domain_blacklist", [])),
        )

    def _on_save_config(self) -> None:
        cfg = self.config
        cfg.update({
            "max_workers": int(self.sl_workers.get()),
            "min_fluency_score": int(self.sl_fluency.get()),
            "timeout": int(self.sl_timeout.get()),
            "decode_seconds": int(self.sl_decode.get()),
            "check_fluency": bool(self.var_fluency.get()),
            "check_live": bool(self.var_live.get()),
            "quick_live_check": bool(self.var_quick.get()),
            "strict_validation": bool(self.var_strict.get()),
            "mode": self._current_mode(),
            "dpl_export_played": bool(self.var_dpl_played.get()),
            "dpl_export_invalid": bool(self.var_dpl_invalid.get()),
            "dpl_export_savepos": bool(self.var_dpl_savepos.get()),
            "dpl_playname": self.var_playname.get(),
            "source_history": self._sources_used[-20:],
        })
        try:
            _save_config(cfg)
            self._log("设置已保存")
            self._toast("设置已保存到 config.json")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def _on_reset_config(self) -> None:
        self.config = _default_config()
        self._load_config_to_ui()
        self._log("设置已恢复默认值")

    # ════════════════════════════════════════════════════════════════════
    # 检测方式
    # ════════════════════════════════════════════════════════════════════

    def _current_mode(self) -> str:
        return (TR.MODE_HTTP, TR.MODE_FFMPEG, TR.MODE_BOTH)[self.seg_mode.index]

    def _on_mode_seg(self, idx: int, initial: bool = False) -> None:
        mode = (TR.MODE_HTTP, TR.MODE_FFMPEG, TR.MODE_BOTH)[idx]
        self.lbl_mode_hint.configure(text=TR.mode_hint(mode))
        if mode == TR.MODE_HTTP:
            self.frame_decode.pack_forget()
        else:
            if not self.frame_decode.winfo_ismapped():
                self.frame_decode.pack(fill="x", pady=(theme.SPACE_2, 0))
        if not initial:
            self._log(f"检测方式：{TR.mode_label(mode)}")

    # ════════════════════════════════════════════════════════════════════
    # 导入
    # ════════════════════════════════════════════════════════════════════

    def _on_select_file(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择播放列表文件",
            filetypes=[
                ("播放列表", "*.m3u *.m3u8 *.txt *.dpl"),
                ("M3U", "*.m3u *.m3u8"),
                ("PotPlayer DPL", "*.dpl"),
                ("TXT", "*.txt"),
                ("所有文件", "*.*"),
            ],
        )
        if paths:
            self.open_files(paths)

    def open_files(self, paths) -> None:
        """载入一批本地播单文件。

        「打开文件」按钮、拖入已运行的窗口、拖到 exe 图标启动三条路径
        都汇到这里，保证载入逻辑、提示文案与切页行为完全一致。
        """
        accepted, rejected = dnd.collect_playlist_paths(paths)
        if not accepted:
            self._toast("不是播放列表文件（支持 .m3u / .m3u8 / .txt / .dpl）")
            return

        before = len(self.channels)
        for path in accepted:
            self._load_source(path, is_path=True)
        added = len(self.channels) - before

        self.lbl_parse.configure(
            text=(f"已载入 {len(self.channels)} 个地址"
                  f"（打开 {len(accepted)} 个文件，本次新增 {added}）")
            if self.channels else "这些文件里没有解析到任何地址")
        if rejected:
            self._log(f"已忽略 {len(rejected)} 个不支持的文件")
        self._toast(f"已载入 {len(accepted)} 个播放列表，新增 {added} 个地址")
        if added:
            self._switch_page("results")
            self.seg_pages.select(1)

    def _on_preview(self) -> None:
        raw = self.url_text.get("1.0", "end-1c").strip()
        if not raw:
            self._toast("请先输入地址或选择文件")
            return
        sources = [s.strip() for s in raw.replace(",", "\n").splitlines() if s.strip()]
        before = len(self.channels)
        for src in sources:
            self._load_source(src, is_path=not src.startswith(("http://", "https://")))
        added = len(self.channels) - before
        self.lbl_parse.configure(
            text=f"已载入 {len(self.channels)} 个地址（本次新增 {added}）"
            if self.channels else "没有解析到任何地址")
        if added:
            self._switch_page("results")
            self.seg_pages.select(1)

    def _load_source(self, src: str, is_path: bool) -> None:
        """解析单个来源（本地路径或 URL），结果并入 self.channels。"""
        try:
            if is_path:
                if not os.path.exists(src):
                    self._log(f"文件不存在：{src}")
                    return
                content = dpl_format.read_playlist_file(src)
                label = os.path.basename(src)
            else:
                import requests
                self._log(f"下载：{src[:70]}")
                resp = requests.get(src, timeout=20, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                })
                resp.raise_for_status()
                content = dpl_format.decode_bytes(resp.content)
                label = src[:60]

            ok, fmt = is_valid_playlist_content(content)
            if not ok:
                self._log(f"格式不支持（{fmt}）：{label}")
                return

            if fmt == "dpl":
                parsed = parse_dpl_source(
                    content, src,
                    carry_title=bool(self.var_carry_title.get()),
                    skip_invalid=bool(self.var_skip_invalid.get()),
                    dedupe=bool(self.var_dedupe.get()),
                )
            else:
                parsed = parse_source(content, src)

            self.channels.extend(parsed)
            if src not in self._sources_used:
                self._sources_used.append(src)
            self._log(f"{fmt.upper()} 载入 {len(parsed)} 个地址：{label}")
        except Exception as exc:
            self._log(f"载入失败：{label if 'label' in dir() else src} → {exc}")

    def _on_load_history(self) -> None:
        history = list(self.config.get("source_history", [])) or self._sources_used
        if not history:
            self._toast("还没有历史记录")
            return

        dlg = tk.Toplevel(self.master)
        dlg.title("历史源")
        dlg.configure(bg=theme.CANVAS)
        dlg.geometry(f"{theme.px(520)}x{theme.px(360)}")
        dlg.transient(self.master)

        wrap = tk.Frame(dlg, bg=theme.CANVAS)
        wrap.pack(fill="both", expand=True, padx=theme.SPACE_4, pady=theme.SPACE_4)
        W.label(wrap, "选择要重新载入的来源", size=theme.SIZE_TITLE,
                bold=True).pack(anchor="w", pady=(0, theme.SPACE_2))

        lb = tk.Listbox(wrap, bg=theme.SURFACE, fg=theme.LABEL, relief="flat",
                        bd=0, highlightthickness=1,
                        highlightbackground=theme.SEPARATOR,
                        selectbackground=theme.ACCENT_SOFT,
                        selectforeground=theme.LABEL,
                        font=(theme.FONT_MONO_FALLBACK, theme.SIZE_CAPTION))
        lb.pack(fill="both", expand=True)
        for item in history:
            lb.insert("end", item)

        btns = tk.Frame(wrap, bg=theme.CANVAS)
        btns.pack(fill="x", pady=(theme.SPACE_2, 0))

        def use():
            sel = [history[i] for i in lb.curselection()]
            for s in sel:
                self._load_source(s, is_path=not s.startswith(("http://", "https://")))
            self.lbl_parse.configure(text=f"已载入 {len(self.channels)} 个地址")
            dlg.destroy()

        W.PillButton(btns, "载入所选", variant="primary", command=use,
                     width=96, height=28).pack(side="right")
        W.PillButton(btns, "取消", variant="ghost", command=dlg.destroy,
                     width=64, height=28).pack(side="right", padx=(0, theme.SPACE_2))

    # ════════════════════════════════════════════════════════════════════
    # 检测
    # ════════════════════════════════════════════════════════════════════

    def _on_start_test(self) -> None:
        if self.testing:
            return
        if not self.channels:
            self._toast("没有可检测的频道，请先导入源")
            return

        mode = self._current_mode()
        tools = TR.FfmpegTools.detect()
        if mode in (TR.MODE_FFMPEG, TR.MODE_BOTH) and not tools.ready:
            messagebox.showwarning(
                "缺少 FFmpeg",
                "当前检测方式需要 ffmpeg / ffprobe，但没有找到。\n"
                "请把它们放到 tools/ffmpeg/bin/ 下，或改用 HTTP 探活。")
            return

        config = self._read_ui_config()
        self.testing = True
        self.rows = []
        self._refresh_results()
        self.progress.set_value(0.0, animate=False)

        self.runner = TR.BatchRunner(
            mode=mode,
            decode_seconds=float(self.sl_decode.get()),
            workers=int(config.max_workers),
        )

        self.btn_start.set_enabled(False)
        self.btn_pause.set_enabled(True)
        self.btn_stop.set_enabled(True)
        self.btn_pause.set_text("暂停")

        self._log(f"开始检测：{len(self.channels)} 个地址 · "
                  f"{TR.mode_label(mode)} · 并发 {config.max_workers}")

        # 工作线程回调统一编组回主线程
        def on_item(ch: Channel, stats: TR.BatchStats) -> None:
            self.master.after(0, self._on_item_done, ch, stats)

        def on_finish(stats: TR.BatchStats) -> None:
            self.master.after(0, self._on_finished, stats)

        self.runner.start(list(self.channels), config,
                          on_item=on_item, on_finish=on_finish)

    def _on_item_done(self, ch: Channel, stats: TR.BatchStats) -> None:
        self.rows.append(Row(ch))
        frac = stats.progress
        self.progress.set_value(frac)
        eta = stats.eta
        tail = f" · 剩余约 {eta:.0f}s" if eta else ""
        self.lbl_progress.configure(
            text=f"{stats.done}/{stats.total} · 可用 {stats.valid}{tail}")
        # 每条都全量重绘在几千条时会卡，这里节流到每 8 条或最后一条
        if stats.done % 8 == 0 or stats.done == stats.total:
            self._refresh_results(rebuild=False)

    def _on_finished(self, stats: TR.BatchStats) -> None:
        self.testing = False
        self.btn_start.set_enabled(True)
        self.btn_pause.set_enabled(False)
        self.btn_stop.set_enabled(False)
        self.btn_pause.set_text("暂停")
        self.progress.set_value(1.0 if stats.total else 0.0)
        self.lbl_progress.configure(
            text=f"完成 · {stats.done} 个地址 · 可用 {stats.valid} · "
                 f"耗时 {stats.elapsed:.1f}s")
        self._refresh_results()
        self._log(f"检测结束：{stats.done} 个地址，可用 {stats.valid}，"
                  f"耗时 {stats.elapsed:.1f}s")

    def _on_toggle_pause(self) -> None:
        if self.runner is None:
            return
        if self.runner.paused:
            self.runner.resume()
            self.btn_pause.set_text("暂停")
            self._log("已继续")
        else:
            self.runner.pause()
            self.btn_pause.set_text("继续")
            self._log("已暂停")

    def _on_stop_test(self) -> None:
        if self.runner is None:
            return
        self.runner.stop()
        self._log("已停止，等待在途任务收尾…")

    # ════════════════════════════════════════════════════════════════════
    # 结果渲染
    # ════════════════════════════════════════════════════════════════════

    def _visible_rows(self) -> list[Row]:
        rows = self.rows
        if self._filter == "valid":
            rows = [r for r in rows if r.state == "valid"]
        elif self._filter == "invalid":
            rows = [r for r in rows if r.state == "invalid"]
        kw = self._keyword.strip().lower()
        if kw:
            rows = [r for r in rows
                    if kw in (r.name or "").lower()
                    or kw in (r.group or "").lower()
                    or kw in (r.ch.link_str or "").lower()]
        return rows

    def _refresh_results(self, rebuild: bool = True) -> None:
        if rebuild:
            self.result_list.set_rows(self._visible_rows())
        else:
            self.result_list.set_rows(self._visible_rows())

        total = len(self.rows)
        valid = sum(1 for r in self.rows if r.state == "valid")
        invalid = sum(1 for r in self.rows if r.state == "invalid")
        self.pill_total.number_label.configure(text=str(total))
        self.pill_valid.number_label.configure(text=str(valid))
        self.pill_invalid.number_label.configure(text=str(invalid))
        if total:
            self.lbl_summary.configure(
                text=f"{total} 个地址 · 可用 {valid} · 不可用 {invalid}")
        else:
            self.lbl_summary.configure(text="还没有开始检测")

    def _on_filter_seg(self, idx: int) -> None:
        self._filter = ("all", "valid", "invalid")[idx]
        self._refresh_results()

    def _on_search(self) -> None:
        self._keyword = W.entry_value(self.entry_search)
        self._refresh_results()

    # ── 行交互 ──────────────────────────────────────────────────────────
    def _on_row_activate(self, row: Row) -> None:
        """双击：复制地址。原版就是这个行为，保留手感。"""
        self._copy_to_clipboard(row.ch.final_url or row.ch.link_str)

    def _on_row_context(self, row: Row, evt) -> None:
        menu = tk.Menu(self.master, tearoff=0,
                       bg=theme.SURFACE, fg=theme.LABEL,
                       activebackground=theme.ACCENT_SOFT,
                       activeforeground=theme.LABEL,
                       relief="flat", bd=0)
        menu.add_command(label="复制地址",
                         command=lambda: self._copy_to_clipboard(
                             row.ch.final_url or row.ch.link_str))
        menu.add_separator()
        menu.add_command(label="用 FFmpeg 复核这一条",
                         command=lambda: self._decode_one(row))
        menu.add_command(label="只重测这一条",
                         command=lambda: self._retest_one(row))
        try:
            menu.tk_popup(evt.x_root, evt.y_root)
        finally:
            menu.grab_release()

    def _decode_one(self, row: Row) -> None:
        """对单条做 FFmpeg 真解码复核，后台跑，完成后回填。"""
        tools = TR.FfmpegTools.detect()
        if not tools.ready:
            self._toast("未找到 FFmpeg，无法解码复核")
            return
        seconds = float(self.sl_decode.get())
        self._log(f"解码复核：{row.name}")

        def work():
            import ffmpeg_check
            try:
                dp = ffmpeg_check.decode_probe(
                    row.ch.final_url or row.ch.link_str,
                    tools.ffmpeg, tools.ffprobe, seconds=seconds)
                self.master.after(0, lambda: self._apply_decode(row, dp))
            except Exception as exc:                      # noqa: BLE001
                self.master.after(0, lambda: self._log(f"解码复核失败：{exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _apply_decode(self, row: Row, dp: dict) -> None:
        probe = dp.get("probe") or {}
        dec = dp.get("decode") or {}
        row.ch.decode_ok = bool(dp.get("ok"))
        row.ch.decode_summary = dp.get("summary") or ""
        row.ch.decode_frames = int(dec.get("frames") or 0)
        row.decode = Row._decode_text(row.ch)
        if probe.get("video_codec") and not row.ch.codec:
            row.ch.codec = probe["video_codec"]
        self._log(f"解码复核结果：{row.name} → {row.ch.decode_summary[:60]}")
        self._refresh_results()

    def _retest_one(self, row: Row) -> None:
        if self.testing:
            self._toast("正在批量检测，请稍后再试")
            return
        config = self._read_ui_config()
        mode = self._current_mode()
        tools = TR.FfmpegTools.detect()

        def work():
            try:
                TR.test_one(row.ch, config, mode, float(self.sl_decode.get()), tools)
                new_row = Row(row.ch)
                def apply():
                    row.state = new_row.state
                    row.resp = new_row.resp
                    row.fluency = new_row.fluency
                    row.decode = new_row.decode
                    row.location = new_row.location
                    self._refresh_results()
                self.master.after(0, apply)
            except Exception as exc:                      # noqa: BLE001
                self.master.after(0, lambda: self._log(f"重测失败：{exc}"))

        threading.Thread(target=work, daemon=True).start()

    # ════════════════════════════════════════════════════════════════════
    # 导出
    # ════════════════════════════════════════════════════════════════════

    def _exportable(self) -> list[Channel]:
        """当前筛选结果里可导出的频道：优先可用项。"""
        rows = self._visible_rows()
        valid = [r.ch for r in rows if r.state == "valid"]
        return valid or [r.ch for r in rows]

    def _on_export_dpl(self) -> None:
        chans = self._exportable()
        if not chans:
            self._toast("没有可导出的频道")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".dpl",
            filetypes=[("PotPlayer DPL", "*.dpl"), ("所有文件", "*.*")],
            title="导出 DPL",
        )
        if not path:
            return
        try:
            count = dpl_format.write_dpl_file(
                path, chans,
                playname=self.var_playname.get() or None,
                saveplaypos=1 if self.var_dpl_savepos.get() else 0,
                include_played=bool(self.var_dpl_played.get()),
                include_invalid=bool(self.var_dpl_invalid.get()),
                newline="\r\n", with_bom=True,
            )
            self._log(f"已导出 DPL：{path}（{count} 条地址）")
            self._toast(f"已导出 {len(chans)} 个频道到 {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))

    def _on_export_m3u(self) -> None:
        chans = self._exportable()
        if not chans:
            self._toast("没有可导出的频道")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".m3u",
            filetypes=[("M3U", "*.m3u"), ("所有文件", "*.*")],
            title="导出 M3U",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("#EXTM3U\n")
                for ch in chans:
                    attrs = f' group-title="{ch.group_name or "未分组"}"'
                    if ch.fluency_score is not None:
                        attrs += f' fluency_score="{ch.fluency_score}"'
                    if ch.response_time:
                        attrs += f' response_time="{ch.response_time}"'
                    logo = ch.dpl_thumbnail or (ch.extinf_headers or {}).get("tvg-logo", "")
                    if logo:
                        attrs += f' tvg-logo="{logo}"'
                    f.write(f"#EXTINF:-1{attrs},{ch.name}\n")
                    f.write(f"{ch.final_url or ch.link_str}\n")
            self._log(f"已导出 M3U：{path}（{len(chans)} 个频道）")
            self._toast(f"已导出 {len(chans)} 个频道")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))

    def _on_copy_m3u(self) -> None:
        chans = self._exportable()
        if not chans:
            self._toast("没有可复制的内容")
            return
        lines = ["#EXTM3U"]
        for ch in chans:
            lines.append(f'#EXTINF:-1 group-title="{ch.group_name or "未分组}"},'
                         f'{ch.name}')
            lines.append(ch.final_url or ch.link_str)
        self._copy_to_clipboard("\n".join(lines))
        self._log(f"已复制 {len(chans)} 个频道到剪贴板")

    def _copy_to_clipboard(self, text: str) -> None:
        try:
            self.master.clipboard_clear()
            self.master.clipboard_append(text)
            self._toast("已复制到剪贴板")
        except Exception:
            pass

    # ════════════════════════════════════════════════════════════════════
    # 杂项
    # ════════════════════════════════════════════════════════════════════

    def _toggle_log(self) -> None:
        self._log_open = not self._log_open
        if self._log_open:
            self.log_frame.grid()
        else:
            self.log_frame.grid_remove()

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _log(self, msg: str) -> None:
        def do():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"{time.strftime('%H:%M:%S')}  {msg}\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        try:
            self.master.after(0, do)
        except Exception:
            pass

    def _toast(self, msg: str) -> None:
        """轻提示：直接写进底部状态行，不打断操作（不用 messagebox）。"""
        self.lbl_progress.configure(text=msg)
        self._log(msg)

    def _show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            "IPTV 直播源测试工具\n\n"
            "批量检测 M3U / TXT / PotPlayer DPL 播放列表的可用性，\n"
            "支持 HTTP 探活与 FFmpeg 真实解码两种方式。\n\n"
            "原始项目：github.com/goplay-source/IPTV-tools\n"
            "维护：iptv-search.com",
        )
