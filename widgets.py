"""
IPTV Tools — Apple 风格自定义控件

为什么不用原生 ttk 控件
----------------------
ttk 在 Windows 上只能出 Win95 风格的方角控件，做不到：
圆角、悬停/按下的连续反馈、滑块式分段控件、动画开关。
这些恰恰是"看起来像现代应用"的全部来源，所以关键控件自己画。

实现方式
--------
所有控件都是 ``tk.Canvas`` + 图片背景 + 文本项：

- **抗锯齿圆角**用 Pillow 以 4 倍尺寸渲染再 LANCZOS 下采样。Tk 的 Canvas
  不做抗锯齿，直接画 arc 会出锯齿边 —— 这是"手工感"的主要来源。没装
  Pillow 时自动退回多边形绘制（略糙但能跑）。
- **状态过渡用缓动**：颜色按 ``ease_out_cubic`` 在 120ms 内插值，且**从当前
  颜色出发**（而不是从"应该的颜色"出发），所以悬停/离开可以随时被打断
  而不跳变。
- **按下即反馈**：绑定 ``<ButtonPress-1>`` 而不是 ``<ButtonRelease-1>``，
  按下瞬间就变暗，不等抬起。

公共约定
--------
- 控件都由 ``place``/``pack`` 正常布局，尺寸由外部决定，内部自适应。
- 所有回调都通过 ``command`` 传入，不暴露事件对象细节。
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

import theme

# ── Pillow 可选依赖 ──────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageTk
    _HAS_PIL = True
except Exception:                                    # pragma: no cover
    _HAS_PIL = False


# ════════════════════════════════════════════════════════════════════════════
# 基础设施：颜色插值、缓动动画、圆角图片
# ════════════════════════════════════════════════════════════════════════════

def hex_to_rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def rgb_to_hex(rgb) -> str:
    r, g, b = (max(0, min(255, int(round(v)))) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def lerp_color(a: str, b: str, t: float) -> str:
    """两色线性插值，t ∈ [0,1]。"""
    if t <= 0:
        return a
    if t >= 1:
        return b
    ra, ga, ba = hex_to_rgb(a)
    rb, gb, bb = hex_to_rgb(b)
    return rgb_to_hex((ra + (rb - ra) * t,
                       ga + (gb - ga) * t,
                       ba + (bb - ba) * t))


class Animator:
    """把 `after` 轮询包装成可打断的缓动动画。

    同一个 widget 上同时只允许一个动画在跑；新动画启动时旧的自动作废
    （靠 token 比对），这保证了"从当前值出发"的连续性。
    """

    _tokens: dict[str, int] = {}

    @classmethod
    def run(cls, widget: tk.Misc, duration: int,
            on_frame: Callable[[float], None],
            key: Optional[str] = None,
            easing: Callable[[float], float] = theme.ease_out_cubic,
            on_done: Optional[Callable[[], None]] = None) -> None:
        key = key or str(widget)
        cls._tokens[key] = cls._tokens.get(key, 0) + 1
        token = cls._tokens[key]

        start = time.perf_counter()
        dur = max(1, duration)

        def step():
            if cls._tokens.get(key, 0) != token:
                return                      # 已被新动画取代
            elapsed = (time.perf_counter() - start) * 1000.0
            t = min(1.0, elapsed / dur)
            try:
                on_frame(easing(t))
            except tk.TclError:
                return                      # widget 已销毁
            if t < 1.0:
                try:
                    widget.after(theme.FRAME_MS, step)
                except tk.TclError:
                    pass
            elif on_done is not None:
                try:
                    on_done()
                except tk.TclError:
                    pass

        step()

    @classmethod
    def cancel(cls, widget: tk.Misc) -> None:
        key = str(widget)
        cls._tokens[key] = cls._tokens.get(key, 0) + 1


# ── 圆角图片缓存 ─────────────────────────────────────────────────────────────

_IMG_CACHE: dict[tuple, "ImageTk.PhotoImage"] = {}
_SS = 4          # 超采样倍数


def rounded_image(w: int, h: int, radius: int, fill: str,
                  outline: Optional[str] = None, width: int = 1
                  ) -> Optional["ImageTk.PhotoImage"]:
    """生成抗锯齿圆角矩形图片；没有 Pillow 时返回 None。

    ``fill`` 传空字符串表示"完全透明"（ghost 按钮的正常态），
    此时只画描边（如果有）。
    """
    if not _HAS_PIL or w <= 0 or h <= 0:
        return None

    fill = fill or ""
    outline = outline or ""
    key = (w, h, radius, fill, outline, width)
    hit = _IMG_CACHE.get(key)
    if hit is not None:
        return hit

    W, H, R = w * _SS, h * _SS, max(0, radius) * _SS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Pillow 不接受空色值，统一翻译成"透明 / 不画"
    fill_ink = fill if fill else (0, 0, 0, 0)
    outline_ink = outline if outline else None
    line_w = width * _SS if outline_ink else 0

    boxes = (0, 0, W - 1, H - 1)
    if outline_ink and line_w > 0:
        d.rounded_rectangle(boxes, radius=R, fill=fill_ink,
                            outline=outline_ink, width=line_w)
    else:
        d.rounded_rectangle(boxes, radius=R, fill=fill_ink)

    img = img.resize((w, h), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)

    # 简单容量控制，避免长时间运行后无限膨胀
    if len(_IMG_CACHE) > 600:
        _IMG_CACHE.clear()
    _IMG_CACHE[key] = photo
    return photo


def draw_round_rect(cv: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                    radius: float, fill: str, outline: str = "",
                    width: int = 1, tags: str = "") -> list[int]:
    """无 Pillow 时的圆角矩形回退实现（6 段拼接，够用但边缘略硬）。"""
    r = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    ids: list[int] = []
    kw = dict(fill=fill, outline=outline, width=width, tags=tags)
    # 两个实心矩形覆盖主体
    ids.append(cv.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill,
                                   outline="", tags=tags))
    ids.append(cv.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill,
                                   outline="", tags=tags))
    # 四个角用弧补齐
    for cx, cy, s, e in ((x1, y1, 90, 90), (x2 - 2 * r, y1, 0, 90),
                         (x1, y2 - 2 * r, 180, 90), (x2 - 2 * r, y2 - 2 * r, 270, 90)):
        ids.append(cv.create_arc(cx, cy, cx + 2 * r, cy + 2 * r,
                                 start=s, extent=e, style="pieslice",
                                 fill=fill, outline=outline if width else "",
                                 width=width, tags=tags))
    return ids


class RoundedSurface(tk.Canvas):
    """圆角底：Card / Button / Segment 的公共基类。

    画法：先铺一张圆角图片（有 Pillow）或多边形（无 Pillow），
    再让子控件透过 ``create_window`` 叠在上面。
    """

    def __init__(self, master, *, radius: int = theme.RADIUS_CARD,
                 fill: str = theme.SURFACE,
                 outline: str = "",
                 bg: Optional[str] = None,
                 width: int = 0, height: int = 0):
        parent_bg = bg or self._parent_bg(master)
        super().__init__(master, bg=parent_bg, highlightthickness=0,
                         bd=0, width=width, height=height,
                         takefocus=0)
        self._radius = radius
        self._fill = fill
        self._outline = outline
        self._bg_item: Optional[int] = None
        self._img: Optional["ImageTk.PhotoImage"] = None
        self._bgimg_item: Optional[int] = None
        self.bind("<Configure>", self._on_configure)

    # ── 内部 ────────────────────────────────────────────────────────────────
    @staticmethod
    def _parent_bg(widget) -> str:
        try:
            return widget.cget("bg")
        except tk.TclError:
            try:
                return widget.cget("background")
            except tk.TclError:
                return theme.CANVAS

    def set_fill(self, fill: str, outline: Optional[str] = None) -> None:
        if fill == self._fill and (outline is None or outline == self._outline):
            return
        self._fill = fill
        if outline is not None:
            self._outline = outline
        self._redraw()

    def _on_configure(self, _evt=None) -> None:
        self._redraw()

    def _redraw(self) -> None:
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return

        if self._bg_item is not None:
            self.delete(self._bg_item)
            self._bg_item = None
        if self._bgimg_item is not None:
            self.delete(self._bgimg_item)
            self._bgimg_item = None

        photo = rounded_image(w, h, theme.px(self._radius), self._fill,
                              self._outline or None,
                              max(1, theme.px(1)))
        if photo is not None:
            self._img = photo                      # 保持引用，防 GC
            self._bgimg_item = self.create_image(0, 0, image=photo, anchor="nw")
            self.tag_lower(self._bgimg_item)
        else:
            ids = draw_round_rect(self, 0, 0, w - 1, h - 1,
                                  theme.px(self._radius), self._fill,
                                  self._outline, max(1, theme.px(1)))
            self._bg_item = ids[0]
            for i in ids[1:]:
                self.tag_lower(i)


# ════════════════════════════════════════════════════════════════════════════
# Card —— 白底卡片
# ════════════════════════════════════════════════════════════════════════════

class Card(RoundedSurface):
    """圆角白卡片，内部可放任意控件。

    用法::

        card = Card(parent, padding=16)
        card.pack(fill="x")
        tk.Label(card.body, ...).pack()

    ``auto_height=True`` 时高度跟随内容（适合"一列设置项"）；
    ``fill_body=True`` 时让内部 ``body`` 同时铺满宽高（适合放表格/列表），
    此时应配合 ``auto_height=False`` 与外部 ``pack(expand=True)`` 使用。
    """

    def __init__(self, master, *, padding: int = theme.SPACE_4,
                 radius: int = theme.RADIUS_CARD,
                 fill: str = theme.SURFACE,
                 outline: str = theme.SEPARATOR,
                 auto_height: bool = True,
                 fill_body: bool = False,
                 bg: Optional[str] = None, height: int = 0):
        super().__init__(master, radius=radius, fill=fill, outline=outline,
                         bg=bg, height=height)
        self._pad = padding
        self._auto = auto_height
        self._fill_body = fill_body

        self.body = tk.Frame(self, bg=fill)
        self._win = self.create_window(padding, padding, window=self.body,
                                       anchor="nw")
        self.body.bind("<Configure>", self._sync_size)

    def _sync_size(self, _evt=None) -> None:
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            return
        inner_w = max(1, w - self._pad * 2)
        try:
            if self._fill_body:
                # 让 body 正好填满卡片内容区，内部控件才能真正铺开
                inner_h = max(1, h - self._pad * 2)
                self.itemconfigure(self._win, width=inner_w, height=inner_h)
            else:
                self.itemconfigure(self._win, width=inner_w)
        except tk.TclError:
            return
        if self._auto:
            need = self.body.winfo_reqheight() + self._pad * 2
            if abs(int(self.cget("height")) - need) > 1:
                try:
                    self.configure(height=need)
                except tk.TclError:
                    pass

    def _on_configure(self, evt=None) -> None:
        super()._on_configure(evt)
        self._sync_size()


# ════════════════════════════════════════════════════════════════════════════
# PillButton —— 胶囊/圆角按钮
# ════════════════════════════════════════════════════════════════════════════

class PillButton(RoundedSurface):
    """三种样式的圆角按钮。

    - ``primary``   实心强调色，用于页面唯一的主操作
    - ``secondary`` 白底 + 发丝边，用于次要操作
    - ``ghost``     透明底，用于工具栏图标按钮
    """

    VARIANTS = ("primary", "secondary", "ghost")

    def __init__(self, master, text: str, command: Optional[Callable] = None,
                 *, variant: str = "secondary", glyph: str = "",
                 width: int = 0, height: int = 30,
                 font_size: int = theme.SIZE_HEADLINE,
                 bold: bool = False,
                 bg: Optional[str] = None):
        self._variant = variant if variant in self.VARIANTS else "secondary"
        fill, outline = self._palette(self._variant, "normal")
        super().__init__(master, radius=theme.RADIUS_CONTROL, fill=fill,
                         outline=outline, bg=bg, height=theme.px(height))
        self.command = command
        self._enabled = True
        self._hovering = False
        self._pressed = False

        self.configure(width=theme.px(width) if width else theme.px(96))

        fg = self._fg(self._variant, "normal")
        label = f"{glyph}  {text}".strip() if glyph else text
        self._text_id = self.create_text(
            0, 0, text=label, anchor="center",
            fill=fg,
            font=(theme.FONT_UI, font_size, "bold" if bold else "normal"),
        )

        self.bind("<Configure>", self._reposition, add="+")
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    # ── 配色 ────────────────────────────────────────────────────────────────
    @staticmethod
    def _palette(variant: str, state: str) -> tuple[str, str]:
        """返回 (填充色, 描边色)。"""
        if variant == "primary":
            fill = {"normal": theme.ACCENT,
                    "hover": theme.ACCENT_HOVER,
                    "pressed": theme.ACCENT_PRESSED,
                    "disabled": theme.SURFACE_SUNKEN}[state]
            return fill, ""
        if variant == "secondary":
            fill = {"normal": theme.SURFACE,
                    "hover": theme.HOVER,
                    "pressed": theme.PRESSED,
                    "disabled": theme.SURFACE_ALT}[state]
            return fill, theme.SEPARATOR_STRONG
        # ghost
        fill = {"normal": theme.SURFACE if False else "",
                "hover": theme.HOVER,
                "pressed": theme.PRESSED,
                "disabled": ""}[state]
        return fill, ""

    @staticmethod
    def _fg(variant: str, state: str) -> str:
        if variant == "primary":
            return theme.ON_ACCENT if state != "disabled" else "#B8B8BE"
        if variant == "ghost":
            return theme.LABEL_SECONDARY if state != "disabled" else theme.LABEL_QUATERNARY
        return theme.LABEL if state != "disabled" else theme.LABEL_QUATERNARY

    # ── 布局 ────────────────────────────────────────────────────────────────
    def _reposition(self, _evt=None) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w > 1:
            self.coords(self._text_id, w / 2, h / 2)

    # ── 交互 ────────────────────────────────────────────────────────────────
    def _state(self) -> str:
        if not self._enabled:
            return "disabled"
        if self._pressed:
            return "pressed"
        if self._hovering:
            return "hover"
        return "normal"

    def _paint(self, state: str, t: float = 1.0) -> None:
        target_fill, target_outline = self._palette(self._variant, state)
        base_fill, base_outline = self._palette(self._variant, "normal")

        # ghost 的"正常态"是透明的，用父容器底色代替
        if self._variant == "ghost" and not target_fill:
            target_fill = self._parent_bg(self.master)

        fill = lerp_color(base_fill or self._parent_bg(self.master),
                          target_fill, t)
        self.set_fill(fill, target_outline or None)
        try:
            self.itemconfigure(self._text_id, fill=self._fg(self._variant, state))
        except tk.TclError:
            pass

    def _animate_to(self, state: str) -> None:
        Animator.run(self, theme.DUR_HOVER,
                     lambda t: self._paint(state, t),
                     key=f"btn-{id(self)}")

    def _on_enter(self, _e=None):
        if not self._enabled:
            return
        self._hovering = True
        self._animate_to(self._state())
        self.configure(cursor="hand2")

    def _on_leave(self, _e=None):
        self._hovering = False
        self._pressed = False
        self._animate_to(self._state())
        try:
            self.configure(cursor="")
        except tk.TclError:
            pass

    def _on_press(self, _e=None):
        if not self._enabled:
            return
        self._pressed = True
        self._animate_to("pressed")          # 按下即反馈，不等抬起

    def _on_release(self, evt=None):
        if not self._enabled:
            return
        was_pressed = self._pressed
        self._pressed = False
        self._animate_to(self._state())
        # 指针在控件内抬起才算一次点击（按下后拖出即取消）
        inside = True
        if evt is not None:
            inside = (0 <= evt.x <= self.winfo_width()
                      and 0 <= evt.y <= self.winfo_height())
        if was_pressed and inside and self.command:
            try:
                self.command()
            except Exception as exc:                       # noqa: BLE001
                print(f"[ui] 按钮回调异常: {exc}")

    # ── 公开 ────────────────────────────────────────────────────────────────
    def set_text(self, text: str, glyph: str = "") -> None:
        label = f"{glyph}  {text}".strip() if glyph else text
        try:
            self.itemconfigure(self._text_id, text=label)
        except tk.TclError:
            pass

    def set_enabled(self, enabled: bool) -> None:
        if self._enabled == enabled:
            return
        self._enabled = enabled
        self._paint(self._state())
        try:
            self.configure(cursor="hand2" if enabled else "")
        except tk.TclError:
            pass


# ════════════════════════════════════════════════════════════════════════════
# SegmentedControl —— 分段控件
# ════════════════════════════════════════════════════════════════════════════

class SegmentedControl(RoundedSurface):
    """macOS 风分段切换器：整块圆角槽 + 一块会滑动的白色选中片。

    选中片用缓动迁移而不是瞬间跳位 —— 这是"位置迁移"类动效的典型场景
    （damping 1.0 的无回弹手感），tkinter 里用 ease_in_out 逼近。
    """

    def __init__(self, master, items: list[str],
                 command: Optional[Callable[[int], None]] = None,
                 *, selected: int = 0, height: int = theme.SEGMENT_HEIGHT,
                 bg: Optional[str] = None):
        super().__init__(master, radius=theme.RADIUS_CONTROL,
                         fill=theme.SURFACE_SUNKEN,
                         outline=theme.SEPARATOR, bg=bg,
                         height=theme.px(height))
        self._items = items
        self._command = command
        self._index = selected
        self._thumb_x = 0.0
        self._thumb_w = 0.0
        self._labels: list[int] = []
        self._thumb: Optional[int] = None
        self._thumb_img: Optional["ImageTk.PhotoImage"] = None

        self.bind("<Configure>", self._layout, add="+")
        self.bind("<Button-1>", self._on_click)

    def _seg_width(self) -> float:
        return max(1.0, (self.winfo_width() - theme.px(4)) / max(1, len(self._items)))

    def _layout(self, _evt=None) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return

        for i in self._labels:
            try:
                self.delete(i)
            except tk.TclError:
                pass
        self._labels.clear()

        sw = self._seg_width()
        inset = theme.px(2)
        for idx, text in enumerate(self._items):
            cx = inset + sw * idx + sw / 2
            self._labels.append(self.create_text(
                cx, h / 2, text=text, anchor="center",
                fill=theme.LABEL if idx == self._index else theme.LABEL_SECONDARY,
                font=(theme.FONT_UI, theme.SIZE_BODY,
                      "bold" if idx == self._index else "normal"),
            ))

        self._thumb_w = sw
        self._draw_thumb(inset + sw * self._index)
        self._paint_labels()

    def _draw_thumb(self, x: float) -> None:
        h = self.winfo_height()
        th = max(1, h - theme.px(4))
        tw = max(1, int(self._thumb_w))
        photo = rounded_image(tw, th, theme.px(theme.RADIUS_CONTROL - 2),
                              theme.SURFACE, theme.SEPARATOR)
        if self._thumb is not None:
            try:
                self.delete(self._thumb)
            except tk.TclError:
                pass
            self._thumb = None
        if photo is not None:
            self._thumb_img = photo
            self._thumb = self.create_image(x, theme.px(2), image=photo, anchor="nw")
        else:
            ids = draw_round_rect(self, x, theme.px(2), x + tw, theme.px(2) + th,
                                  theme.px(theme.RADIUS_CONTROL - 2),
                                  theme.SURFACE, theme.SEPARATOR)
            self._thumb = ids[0]
        # 选中片压在文字下面
        for lbl in self._labels:
            self.tag_raise(lbl)

    def _paint_labels(self) -> None:
        for idx, lbl in enumerate(self._labels):
            try:
                self.itemconfigure(
                    lbl,
                    fill=theme.LABEL if idx == self._index else theme.LABEL_SECONDARY,
                    font=(theme.FONT_UI, theme.SIZE_BODY,
                          "bold" if idx == self._index else "normal"),
                )
            except tk.TclError:
                pass

    def _on_click(self, evt) -> None:
        sw = self._seg_width()
        if sw <= 1:
            return
        idx = int((evt.x - theme.px(2)) // sw)
        idx = max(0, min(len(self._items) - 1, idx))
        self.select(idx, notify=True)

    def select(self, idx: int, notify: bool = False) -> None:
        if idx == self._index:
            return
        idx = max(0, min(len(self._items) - 1, idx))
        start_x = theme.px(2) + self._seg_width() * self._index
        end_x = theme.px(2) + self._seg_width() * idx
        self._index = idx
        self._paint_labels()

        def frame(t: float):
            if self._thumb is None:
                return
            x = start_x + (end_x - start_x) * t
            self.coords(self._thumb, x, theme.px(2))

        Animator.run(self, theme.DUR_TOGGLE, frame, key=f"seg-{id(self)}",
                     easing=theme.ease_in_out)
        if notify and self._command:
            self._command(idx)

    @property
    def index(self) -> int:
        return self._index


# ════════════════════════════════════════════════════════════════════════════
# Switch —— 开关
# ════════════════════════════════════════════════════════════════════════════

class Switch(tk.Canvas):
    """iOS 风开关：胶囊轨道 + 会滑动的圆钮。

    关闭时轨道是浅灰、钮在左；打开时轨道变强调色、钮滑到右。
    轨道颜色与钮位置同时缓动，避免"颜色先到位、钮还在路上"的割裂感。
    """

    def __init__(self, master, value: bool = False,
                 command: Optional[Callable[[bool], None]] = None,
                 *, bg: Optional[str] = None):
        parent_bg = bg or RoundedSurface._parent_bg(master)
        w, h = theme.px(theme.SWITCH_W), theme.px(theme.SWITCH_H)
        super().__init__(master, width=w, height=h, bg=parent_bg,
                         highlightthickness=0, bd=0, takefocus=1)
        self._value = bool(value)
        self._command = command
        self._t = 1.0 if value else 0.0
        self._track: Optional[int] = None
        self._knob = None
        self._track_img = None
        self._knob_img = None
        self.bind("<Button-1>", self._on_click)
        self.bind("<Configure>", lambda e: self._redraw(), add="+")
        self._redraw()

    def _redraw(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return
        if self._track is not None:
            self.delete(self._track)
        self._track = None
        track_h = max(theme.px(14), int(h * 0.86))
        y = (h - track_h) / 2
        color = lerp_color(theme.SEPARATOR_STRONG, theme.ACCENT, self._t)
        photo = rounded_image(w, track_h, track_h // 2, color)
        if photo is not None:
            self._track_img = photo
            self._track = self.create_image(0, y, image=photo, anchor="nw")
        else:
            ids = draw_round_rect(self, 0, y, w, y + track_h, track_h / 2, color)
            self._track = ids[0]

        # 圆钮
        if self._knob is not None:
            self.delete(self._knob)
        kr = track_h - theme.px(4)
        kx = theme.px(2) + (w - kr - theme.px(4)) * self._t
        ky = y + theme.px(2)
        kphoto = rounded_image(int(kr), int(kr), int(kr // 2), "#FFFFFF")
        if kphoto is not None:
            self._knob_img = kphoto
            self._knob = self.create_image(kx, ky, image=kphoto, anchor="nw")
        else:
            ids = draw_round_rect(self, kx, ky, kx + kr, ky + kr, kr / 2, "#FFFFFF")
            self._knob = ids[0]

    def _on_click(self, _e=None) -> None:
        self.toggle()

    def toggle(self) -> None:
        self.set_value(not self._value, notify=True)

    def set_value(self, value: bool, notify: bool = False) -> None:
        self._value = bool(value)
        start = self._t
        end = 1.0 if value else 0.0

        def frame(t: float):
            self._t = start + (end - start) * t
            self._redraw()

        Animator.run(self, theme.DUR_TOGGLE, frame, key=f"sw-{id(self)}")
        try:
            self.configure(cursor="hand2")
        except tk.TclError:
            pass
        if notify and self._command:
            self._command(self._value)

    @property
    def value(self) -> bool:
        return self._value


# ════════════════════════════════════════════════════════════════════════════
# StatusDot —— 状态点
# ════════════════════════════════════════════════════════════════════════════

class StatusDot(tk.Canvas):
    """表示行状态的小圆点（浅底 + 深色符号）。"""

    #: 状态 → (符号, 语义键)
    GLYPHS = {
        "valid": "✓",
        "invalid": "✕",
        "testing": "•",
        "pending": "–",
    }

    def __init__(self, master, state: str = "pending", size: int = 20,
                 bg: Optional[str] = None):
        parent_bg = bg or RoundedSurface._parent_bg(master)
        d = theme.px(size)
        super().__init__(master, width=d, height=d, bg=parent_bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._size = d
        self._state = state
        self._circle = None
        self._glyph = None
        self._img = None
        self._draw()

    def _draw(self) -> None:
        if self._circle is not None:
            self.delete(self._circle)
        if self._glyph is not None:
            self.delete(self._glyph)
        d = self._size
        fg, bg = theme.state_colors(self._state)
        photo = rounded_image(d, d, d // 2, bg)
        if photo is not None:
            self._img = photo
            self._circle = self.create_image(0, 0, image=photo, anchor="nw")
        else:
            ids = draw_round_rect(self, 0, 0, d, d, d / 2, bg)
            self._circle = ids[0]
        self._glyph = self.create_text(
            d / 2, d / 2, text=self.GLYPHS.get(self._state, "–"),
            fill=fg, font=(theme.FONT_UI, max(6, int(theme.SIZE_CAPTION * 0.92)), "bold"),
        )

    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self._draw()


# ════════════════════════════════════════════════════════════════════════════
# ThinProgress —— 细进度条
# ════════════════════════════════════════════════════════════════════════════

class ThinProgress(tk.Canvas):
    """细长圆角进度条。value ∈ [0,1]，变化时缓动推进。"""

    def __init__(self, master, height: int = 4, bg: Optional[str] = None):
        parent_bg = bg or RoundedSurface._parent_bg(master)
        super().__init__(master, height=theme.px(height), bg=parent_bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._value = 0.0
        self._shown = 0.0
        self._bar = None
        self._img = None
        self.bind("<Configure>", lambda e: self._draw(), add="+")

    def _draw(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return
        if self._bar is not None:
            self.delete(self._bar)
        r = h // 2
        # 槽
        self.delete("slot")
        photo = rounded_image(w, h, r, theme.SURFACE_SUNKEN)
        if photo is not None:
            self.create_image(0, 0, image=photo, anchor="nw", tags="slot")
        else:
            draw_round_rect(self, 0, 0, w, h, r, theme.SURFACE_SUNKEN, tags="slot")
        # 填充
        fw = max(0, int(w * self._shown))
        self._bar = None
        if fw >= 2:
            fp = rounded_image(fw, h, min(r, fw // 2), theme.ACCENT)
            if fp is not None:
                self._img = fp
                self._bar = self.create_image(0, 0, image=fp, anchor="nw")
            else:
                ids = draw_round_rect(self, 0, 0, fw, h, min(r, fw // 2), theme.ACCENT)
                self._bar = ids[0]

    def set_value(self, value: float, animate: bool = True) -> None:
        value = max(0.0, min(1.0, value))
        start = self._shown
        self._value = value
        if not animate:
            self._shown = value
            self._draw()
            return

        def frame(t: float):
            self._shown = start + (value - start) * t
            self._draw()

        Animator.run(self, theme.DUR_PANEL, frame, key=f"pr-{id(self)}",
                     easing=theme.ease_out_quint)

    @property
    def value(self) -> float:
        return self._value


# ════════════════════════════════════════════════════════════════════════════
# Slider —— 自绘滑块
# ════════════════════════════════════════════════════════════════════════════

class Slider(tk.Canvas):
    """细轨道 + 圆滑柄的滑块。

    为什么不用 ttk.Scale
    -------------------
    ttk 的 Scale 样式由主题引擎控制，Windows 上只能得到一条细竖条似的控件
    （trough 配色也基本不生效），跟这套界面完全不是一个语言。自绘反而更简单：
    轨道、填充、圆钮三样东西，位置全部由数值算出来。

    交互遵循直接操作：按下即跳转并开始 1:1 跟随，拖到哪算哪，不等到松手。
    """

    def __init__(self, master, from_: int = 0, to: int = 100, value: int = 0,
                 command: Optional[Callable[[int], None]] = None,
                 *, step: int = 1, suffix: str = "",
                 bg: Optional[str] = None, height: int = 22):
        parent_bg = bg or RoundedSurface._parent_bg(master)
        super().__init__(master, height=theme.px(height), bg=parent_bg,
                         highlightthickness=0, bd=0, takefocus=1)
        self._min = float(from_)
        self._max = float(to) if to != from_ else float(from_ + 1)
        self._step = max(1, int(step))
        self._value = float(value)
        self._command = command
        self._dragging = False
        self._grow = 0.0            # 0..1，按下时滑柄变大的进度
        self._track_h = max(3, theme.px(4))
        self._knob_d = theme.px(13)

        self.bind("<Configure>", lambda e: self._redraw(), add="+")
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    # ── 数值 ────────────────────────────────────────────────────────────────
    def get(self) -> int:
        return int(round(self._value))

    def set(self, value) -> None:
        v = self._snap(float(value))
        if abs(v - self._value) < 1e-9:
            self._redraw()
            return
        self._value = v
        self._redraw()
        if self._command:
            self._command(self.get())

    def _snap(self, v: float) -> float:
        v = max(self._min, min(self._max, v))
        if self._step > 1:
            v = self._min + round((v - self._min) / self._step) * self._step
            v = max(self._min, min(self._max, v))
        return v

    def _ratio(self) -> float:
        span = self._max - self._min
        return 0.0 if span <= 0 else (self._value - self._min) / span

    # ── 绘制 ────────────────────────────────────────────────────────────────
    def _redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2:
            return

        th = self._track_h
        cy = h / 2
        track_x1 = self._knob_d / 2
        track_x2 = w - self._knob_d / 2
        ratio = self._ratio()
        knob_x = track_x1 + (track_x2 - track_x1) * ratio

        # 槽
        photo = rounded_image(int(track_x2 - track_x1), th, th // 2,
                              theme.SURFACE_SUNKEN)
        if photo is not None:
            self._keep(photo)
            self.create_image(track_x1, cy - th / 2, image=photo, anchor="nw")
        else:
            draw_round_rect(self, track_x1, cy - th / 2, track_x2, cy + th / 2,
                            th / 2, theme.SURFACE_SUNKEN)

        # 已选段
        fw = max(0, int(knob_x - track_x1))
        if fw >= 2:
            fp = rounded_image(fw, th, th // 2, theme.ACCENT)
            if fp is not None:
                self._keep(fp)
                self.create_image(track_x1, cy - th / 2, image=fp, anchor="nw")
            else:
                draw_round_rect(self, track_x1, cy - th / 2, knob_x, cy + th / 2,
                                th / 2, theme.ACCENT)

        # 滑柄：按下时略微放大，给出"抓住了"的反馈
        d = self._knob_d * (1.0 + 0.12 * self._grow)
        kp = rounded_image(int(d), int(d), int(d / 2), "#FFFFFF", theme.SEPARATOR_STRONG)
        if kp is not None:
            self._keep(kp)
            self.create_image(knob_x - d / 2, cy - d / 2, image=kp, anchor="nw")
        else:
            draw_round_rect(self, knob_x - d / 2, cy - d / 2,
                            knob_x + d / 2, cy + d / 2, d / 2,
                            "#FFFFFF", theme.SEPARATOR_STRONG)

    def _keep(self, photo) -> None:
        cache = getattr(self, "_imgs", None)
        if cache is None:
            cache = self._imgs = []
        cache.append(photo)
        if len(cache) > 80:
            del cache[:40]

    # ── 交互 ────────────────────────────────────────────────────────────────
    def _value_at(self, x: int) -> float:
        w = self.winfo_width()
        x1 = self._knob_d / 2
        x2 = w - self._knob_d / 2
        if x2 <= x1:
            return self._min
        t = (x - x1) / (x2 - x1)
        return self._min + (self._max - self._min) * max(0.0, min(1.0, t))

    def _on_press(self, evt) -> None:
        self._dragging = True
        self.focus_set()
        self._animate_grow(1.0)
        self.set(self._value_at(evt.x))      # 点击轨道立即跳转，不等拖动

    def _on_drag(self, evt) -> None:
        if self._dragging:
            self.set(self._value_at(evt.x))  # 1:1 跟随

    def _on_release(self, _evt=None) -> None:
        self._dragging = False
        self._animate_grow(0.0)

    def _animate_grow(self, target: float) -> None:
        start = self._grow

        def frame(t: float):
            self._grow = start + (target - start) * t
            self._redraw()

        Animator.run(self, theme.DUR_HOVER, frame, key=f"sl-{id(self)}")


# ════════════════════════════════════════════════════════════════════════════
# 便捷构造：带说明的文本块
# ════════════════════════════════════════════════════════════════════════════

def label(master, text: str, *, size: int = theme.SIZE_BODY,
          color: str = theme.LABEL, bold: bool = False,
          bg: Optional[str] = None, **kw) -> tk.Label:
    """统一字体与配色的 Label，避免各处手写 font 元组。"""
    parent_bg = bg or RoundedSurface._parent_bg(master)
    return tk.Label(
        master, text=text, bg=parent_bg, fg=color,
        font=(theme.FONT_UI, size, "bold" if bold else "normal"),
        anchor="w", justify="left", **kw
    )


def hairline(master, *, bg: Optional[str] = None, padx: int = 0) -> tk.Frame:
    """发丝分隔线：1px 浅灰。"""
    parent_bg = bg or RoundedSurface._parent_bg(master)
    line = tk.Frame(master, height=1, bg=theme.SEPARATOR)
    line.pack(fill="x", padx=padx)
    return line


def text_entry(master, textvariable=None, *, width: int = 20,
               placeholder: str = "") -> tk.Entry:
    """扁平输入框：无边框、浅凹底，聚焦时换成白底 + 强调色描边。

    tkinter 的 Entry 没有占位符，这里手写一个：失焦且为空时显示灰色提示，
    获得焦点或已有内容时隐藏。用灰色文字模拟，而不是真的插入文本，
    避免污染 ``get()`` 的返回值。
    """
    entry = tk.Entry(
        master, textvariable=textvariable, width=width,
        bg=theme.SURFACE_SUNKEN, fg=theme.LABEL,
        insertbackground=theme.ACCENT,
        relief="flat", bd=0,
        highlightthickness=theme.px(1),
        highlightbackground=theme.SEPARATOR,
        highlightcolor=theme.ACCENT,
        font=(theme.FONT_UI, theme.SIZE_BODY),
    )

    if placeholder:
        state = {"showing": False}

        def show():
            if entry.get() == "" and entry.focus_get() is not entry:
                state["showing"] = True
                entry.configure(fg=theme.LABEL_TERTIARY)
                entry.insert(0, placeholder)

        def hide(_e=None):
            if state["showing"]:
                state["showing"] = False
                entry.delete(0, "end")
                entry.configure(fg=theme.LABEL)

        def on_key(_e=None):
            # 第一次敲键：先清掉灰字，否则输入的字符会接在提示后面
            hide()

        def on_focus_in(_e=None):
            hide()
            entry.configure(bg=theme.SURFACE)

        def on_focus_out(_e=None):
            entry.configure(bg=theme.SURFACE_SUNKEN)
            show()

        # 实例绑定先于类绑定执行，所以在 <Key> 里删空后，字符会落到空框里
        entry.bind("<Key>", on_key, add="+")
        entry.bind("<FocusIn>", on_focus_in, add="+")
        entry.bind("<FocusOut>", on_focus_out, add="+")
        if textvariable is not None:
            textvariable.trace_add("write", lambda *_: hide())
        entry._placeholder_state = state       # 供取值时判断
        entry._placeholder_text = placeholder
        entry.after(50, show)

    return entry


def entry_value(entry: tk.Entry) -> str:
    """取输入框的真实值（过滤掉占位提示）。

    不只依赖内部状态：只要当前文本恰好等于占位词，也按空值处理，
    这样即使外部代码直接 insert/delete 越过事件回调，也不会把提示词当成输入。
    """
    text = entry.get()
    ph = getattr(entry, "_placeholder_text", "")
    state = getattr(entry, "_placeholder_state", None) or {}
    if ph and state.get("showing") and text == ph:
        return ""
    return text


# ════════════════════════════════════════════════════════════════════════════
# ResultList —— Canvas 虚拟列表（替代 ttk.Treeview）
# ════════════════════════════════════════════════════════════════════════════

class ResultList(tk.Frame):
    """自绘的虚拟化列表。

    为什么不用 ttk.Treeview
    -----------------------
    Treeview 的行只能整行着色，做不出"状态小圆点 + 中性文字"的克制观感，
    表头也带着 Windows 原生控件的年代感。这里用 Canvas 自绘，只绘制可见行
    （虚拟化），几千条也不会卡。

    列定义形如 ``[("name", "频道", 200, "w"), ("resp", "响应", 90, "center")]``，
    取值时按 key 从行对象上找属性，找不到再查 ``row.extra`` 字典。
    """

    def __init__(self, master, columns, *, row_height: int = theme.ROW_HEIGHT,
                 on_activate: Optional[Callable] = None,
                 on_context: Optional[Callable] = None,
                 bg: str = theme.SURFACE):
        super().__init__(master, bg=bg)
        self._columns = columns
        self._row_h = theme.px(row_height)
        self._on_activate = on_activate
        self._on_context = on_context
        self._rows: list = []
        self._hover = -1
        self._selected = -1
        self._offset = 0.0          # 已滚动的像素
        self._bg = bg

        # 表头
        self._head = tk.Canvas(self, bg=theme.SURFACE_ALT, highlightthickness=0,
                              bd=0, height=theme.px(30))
        self._head.pack(fill="x")

        # 主体 + 滚动条
        body = tk.Frame(self, bg=bg)
        body.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(body, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)

        style = ttk.Style()
        self.vbar = ttk.Scrollbar(body, orient="vertical",
                                     command=self._on_scrollbar,
                                     style="Slim.Vertical.TScrollbar")
        self.vbar.pack(side="right", fill="y")

        self.canvas.configure(yscrollcommand=self._on_canvas_scroll)

        self.canvas.bind("<Configure>", lambda e: self._render())
        self._head.bind("<Configure>", lambda e: self._render_head())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<Button-3>", self._on_right)

    # ── 数据 ────────────────────────────────────────────────────────────────
    def set_rows(self, rows: list) -> None:
        self._rows = list(rows)
        self._hover = -1
        self._selected = -1
        self._offset = 0.0
        self._sync_scrollbar()
        self._render()
        # 首次切换页面时 canvas 还没有真实尺寸，等一帧再画一次
        self.after_idle(self._render)

    @property
    def rows(self) -> list:
        return self._rows

    # ── 尺寸与滚动 ──────────────────────────────────────────────────────────
    def _viewport_h(self) -> int:
        return max(1, self.canvas.winfo_height())

    def _content_h(self) -> int:
        return len(self._rows) * self._row_h

    def _max_offset(self) -> float:
        return max(0.0, self._content_h() - self._viewport_h())

    def _sync_scrollbar(self) -> None:
        total = max(1, self._content_h())
        vh = self._viewport_h()
        if total <= vh:
            self.vbar.set(0.0, 1.0)
        else:
            first = self._offset / total
            last = (self._offset + vh) / total
            self.vbar.set(first, min(1.0, last))

    def _on_scrollbar(self, *args) -> None:
        if args[0] == "moveto":
            self._offset = float(args[1]) * self._content_h()
        elif args[0] == "scroll":
            step = self._row_h * 3
            amount = int(args[1])
            self._offset += amount * step if args[2] == "units" else amount * self._row_h
        self._offset = max(0.0, min(self._max_offset(), self._offset))
        self._sync_scrollbar()
        self._render()

    def _on_canvas_scroll(self, first, last) -> None:
        # Canvas 自己不管滚动，由我们控制 offset；这里只同步滚动条外观
        self.vbar.set(first, last)

    def _on_wheel(self, evt) -> None:
        delta = -1 if evt.delta > 0 else 1
        self._offset += delta * self._row_h * 3
        self._offset = max(0.0, min(self._max_offset(), self._offset))
        self._sync_scrollbar()
        self._render()

    # ── 绘制 ────────────────────────────────────────────────────────────────
    def _col_x(self) -> list[tuple[int, int]]:
        """返回每列的 (起始x, 宽度)。按给定宽度等比铺满可用宽度。"""
        avail = max(100, self.canvas.winfo_width())
        weights = [c[2] for c in self._columns]
        total = sum(weights) or 1
        spans = []
        x = theme.px(10)
        usable = avail - theme.px(20)
        for w in weights:
            cw = int(usable * w / total)
            spans.append((x, cw))
            x += cw
        return spans

    def _render_head(self) -> None:
        self._head.delete("all")
        h = theme.px(30)
        spans = self._col_x()
        for (key, title, _w, align), (x, cw) in zip(self._columns, spans):
            anchor = {"w": "w", "center": "center", "e": "e"}[align]
            tx = x + (cw / 2 if align == "center" else (cw - theme.px(8) if align == "e" else 0))
            self._head.create_text(
                tx, h / 2, text=title, anchor=anchor,
                fill=theme.LABEL_TERTIARY,
                font=(theme.FONT_UI, theme.SIZE_CAPTION, "bold"),
            )

    def _row_values(self, row) -> dict:
        vals = {}
        extra = getattr(row, "extra", None) or {}
        for key, *_ in self._columns:
            if key in extra:
                vals[key] = extra[key]
            else:
                vals[key] = getattr(row, key, "")
        return vals

    def _render(self) -> None:
        self.canvas.delete("all")
        self._render_head()

        h = self._viewport_h()
        rh = self._row_h
        if not self._rows:
            self.canvas.create_text(
                self.canvas.winfo_width() / 2, h / 2 - theme.px(10),
                text="还没有结果", fill=theme.LABEL_TERTIARY,
                font=(theme.FONT_UI, theme.SIZE_BODY),
            )
            return

        first = max(0, int(self._offset // rh))
        last = min(len(self._rows), first + h // rh + 2)
        spans = self._col_x()

        for idx in range(first, last):
            row = self._rows[idx]
            y0 = idx * rh - self._offset
            y1 = y0 + rh

            # 行背景：悬停/选中
            if idx == self._selected:
                self._pill(0, y0 + 1, self.canvas.winfo_width(), y1 - 1,
                           theme.ACCENT_SOFT)
            elif idx == self._hover:
                self._pill(0, y0 + 1, self.canvas.winfo_width(), y1 - 1,
                           theme.HOVER)

            state = getattr(row, "state", "pending")
            fg, soft = theme.state_colors(state)
            vals = self._row_values(row)

            for (key, title, _w, align), (x, cw) in zip(self._columns, spans):
                text = str(vals.get(key, "") or "")
                if key == "state":
                    # 状态列：实心小圆点 + 短词，颜色只出现在这里
                    label_text = getattr(row, "state_text", text)
                    dot = theme.px(7)
                    cy = (y0 + y1) / 2
                    self._dot(x + dot / 2 + theme.px(6), cy, dot, fg)
                    self.canvas.create_text(
                        x + dot + theme.px(12), cy, text=label_text, anchor="w",
                        fill=fg, font=(theme.FONT_UI, theme.SIZE_CAPTION, "bold"),
                    )
                    continue

                anchor = {"w": "w", "center": "center", "e": "e"}[align]
                tx = x + (cw / 2 if align == "center" else (cw - theme.px(8) if align == "e" else 0))
                color = theme.LABEL if key in ("name",) else theme.LABEL_SECONDARY
                if key == "name":
                    font = (theme.FONT_UI, theme.SIZE_BODY, "bold")
                else:
                    font = (theme.FONT_UI, theme.SIZE_CAPTION)
                self.canvas.create_text(
                    tx, (y0 + y1) / 2,
                    text=self._ellipsize(text, cw - theme.px(12), font),
                    anchor=anchor, fill=color, font=font,
                )

            # 行分隔线（最后一行不画）
            if idx < last - 1:
                self.canvas.create_line(
                    theme.px(10), y1, self.canvas.winfo_width() - theme.px(10), y1,
                    fill=theme.SEPARATOR,
                )

    def _ellipsize(self, text: str, max_w: int, font) -> str:
        """超宽裁断并补省略号。Canvas 的 text 支持 width 换行但不截断，只能自己算。"""
        if not text:
            return ""
        try:
            # 延迟导入：tkinter.font 只有在 Tk 初始化后才可靠可用
            from tkinter import font as tkfont
            f = tkfont.Font(font=font)
        except Exception:
            return text
        if f.measure(text) <= max_w:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if f.measure(text[:mid] + "…") <= max_w:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + "…"

    def _pill(self, x1, y1, x2, y2, color) -> None:
        photo = rounded_image(int(x2 - x1), int(y2 - y1),
                              theme.px(6), color)
        if photo is not None:
            self._keep(photo)
            self.canvas.create_image(x1, y1, image=photo, anchor="nw")
        else:
            self.canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="")

    def _dot(self, cx, cy, d, color) -> None:
        photo = rounded_image(int(d), int(d), int(d // 2), color)
        if photo is not None:
            self._keep(photo)
            self.canvas.create_image(cx - d / 2, cy - d / 2, image=photo, anchor="nw")
        else:
            self.canvas.create_oval(cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2,
                                    fill=color, outline="")

    def _keep(self, photo) -> None:
        """防 GC：Canvas 不会持有 PhotoImage 引用。"""
        cache = getattr(self, "_imgs", None)
        if cache is None:
            cache = self._imgs = []
        cache.append(photo)
        if len(cache) > 200:
            del cache[:100]

    # ── 交互 ────────────────────────────────────────────────────────────────
    def _index_at(self, y: int) -> int:
        idx = int((y + self._offset) // self._row_h)
        return idx if 0 <= idx < len(self._rows) else -1

    def _on_motion(self, evt) -> None:
        idx = self._index_at(evt.y)
        if idx != self._hover:
            self._hover = idx
            self._render()
        try:
            self.canvas.configure(cursor="hand2" if idx >= 0 else "")
        except tk.TclError:
            pass

    def _on_leave(self, _e=None) -> None:
        if self._hover != -1:
            self._hover = -1
            self._render()

    def _on_click(self, evt) -> None:
        idx = self._index_at(evt.y)
        self._selected = idx
        self._render()

    def _on_double(self, evt) -> None:
        idx = self._index_at(evt.y)
        if idx >= 0 and self._on_activate:
            self._on_activate(self._rows[idx])

    def _on_right(self, evt) -> None:
        idx = self._index_at(evt.y)
        if idx >= 0:
            self._selected = idx
            self._render()
            if self._on_context:
                self._on_context(self._rows[idx], evt)


def _style_scrollbar() -> None:
    """细滚动条 + 细滑块：ttk 默认样式太抢眼，压成中性灰。"""
    try:
        style = ttk.Style()
        # 滚动条：去掉两端箭头，只留一条细拇指
        style.layout("Slim.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})
            ]})
        ])
        style.configure("Slim.Vertical.TScrollbar",
                        background=theme.LABEL_QUATERNARY,
                        troughcolor=theme.SURFACE,
                        bordercolor=theme.SURFACE,
                        arrowcolor=theme.SURFACE,
                        width=theme.px(9),
                        relief="flat")

        # 滑块：浅槽 + 强调色滑柄
        style.layout("Slim.Horizontal.TScale", [
            ("Horizontal.Scale.trough", {"sticky": "ew", "children": [
                ("Horizontal.Scale.slider", {"side": "left", "sticky": ""})
            ]})
        ])
        style.configure("Slim.Horizontal.TScale",
                        background=theme.SURFACE,
                        troughcolor=theme.SURFACE_SUNKEN,
                        bordercolor=theme.SURFACE_SUNKEN,
                        lightcolor=theme.ACCENT,
                        darkcolor=theme.ACCENT,
                        sliderlength=theme.px(14),
                        gripcount=0)
    except Exception:
        pass
