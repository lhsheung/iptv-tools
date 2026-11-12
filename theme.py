"""
IPTV Tools — 设计令牌（Apple 风格）

为什么单独一个模块
------------------
界面里最容易失控的是"到处硬编码颜色和字号"：改一次主题要翻遍所有文件，
而且不知不觉就攒出 17 种灰色。这里把颜色、字号、间距、圆角、动效时长
全部收敛成一份可枚举的令牌，界面只允许引用令牌，不允许写字面量。

设计取向（对齐 apple-design 规范）
---------------------------------
- **单一色相**：只有系统蓝一个强调色，其余都是中性灰阶。
- **不用渐变、不用彩色描边**：层级靠留白与材质（白卡片 + 发丝分隔线）建立。
- **字号 5 档**：大标题 / 标题 / 正文强调 / 正文 / 说明。不出现第 6 种。
- **圆角 3 档**：控件 8、卡片 12、浮层 18。
- **动效只用于反馈**：120~240ms 的缓出，不做装饰性动画。

关于透明度
----------
tkinter 没有 backdrop-filter，无法做真正的毛玻璃。这里的做法是
**用极浅的中性底色模拟"半透明层压在白底上"的观感**（见 ``SURFACE_ALT``），
而不是假装有模糊。这是平台限制，不做虚假承诺。
"""

from __future__ import annotations

# ── 颜色：浅色主题（主用） ────────────────────────────────────────────────────
# 命名沿用 Apple 的语义化命名习惯：canvas / surface / label / separator

CANVAS = "#F5F5F7"          # 窗口底色，比纯白略灰，让白卡片浮起来
SURFACE = "#FFFFFF"         # 卡片、表格、输入区
SURFACE_ALT = "#FAFAFC"     # 次级面板（模拟半透明层的观感）
SURFACE_SUNKEN = "#F0F0F3"  # 输入框内凹底、进度条槽
HOVER = "#EFEFF2"           # 悬停
PRESSED = "#E5E5EA"         # 按下

SEPARATOR = "#E3E3E8"       # 发丝分隔线
SEPARATOR_STRONG = "#D6D6DC"

LABEL = "#1D1D1F"           # 主文本
LABEL_SECONDARY = "#6E6E73" # 次要文本
LABEL_TERTIARY = "#9A9AA0"  # 说明文字、占位符
LABEL_QUATERNARY = "#BFBFC6"

ACCENT = "#007AFF"          # 系统蓝：唯一强调色
ACCENT_HOVER = "#0A6FE0"
ACCENT_PRESSED = "#0059C7"
ACCENT_SOFT = "#E8F1FF"     # 强调色的浅底（选中态背景）

SUCCESS = "#1DA34A"         # 可用
SUCCESS_SOFT = "#E7F6EC"
WARNING = "#C77700"         # 需注意
WARNING_SOFT = "#FDF3E3"
DANGER = "#D93A2B"          # 不可用
DANGER_SOFT = "#FDECEA"

ON_ACCENT = "#FFFFFF"       # 强调色上的文字

# 日志区用深色，和浅色主界面形成"代码块"的对比，不是主题切换
LOG_BG = "#1C1C1E"
LOG_FG = "#D8D8DC"
LOG_DIM = "#8A8A8E"

# ── 颜色：深色主题 ───────────────────────────────────────────────────────────
# 只做最小可用集：用户如果在 Windows 里切了深色，界面不要刺眼即可。

DARK = {
    "CANVAS": "#1B1B1D",
    "SURFACE": "#252527",
    "SURFACE_ALT": "#202022",
    "SURFACE_SUNKEN": "#2C2C2E",
    "HOVER": "#2E2E30",
    "PRESSED": "#3A3A3C",
    "SEPARATOR": "#38383A",
    "SEPARATOR_STRONG": "#48484A",
    "LABEL": "#F2F2F4",
    "LABEL_SECONDARY": "#A8A8AE",
    "LABEL_TERTIARY": "#7C7C82",
    "LABEL_QUATERNARY": "#5A5A5E",
    "ACCENT": "#0A84FF",
    "ACCENT_HOVER": "#3B9BFF",
    "ACCENT_PRESSED": "#0070D8",
    "ACCENT_SOFT": "#1E3A5F",
    "SUCCESS": "#34C759",
    "SUCCESS_SOFT": "#1D3325",
    "WARNING": "#E0A34E",
    "WARNING_SOFT": "#33291B",
    "DANGER": "#FF6B5E",
    "DANGER_SOFT": "#3A211F",
    "LOG_BG": "#141416",
    "LOG_FG": "#D8D8DC",
    "LOG_DIM": "#8A8A8E",
}

# ── 字体 ─────────────────────────────────────────────────────────────────────
# 中文界面以「微软雅黑 UI」为基，Windows 11 下与 Segoe UI Variable 视觉接近。
# 不引入第三方字体，避免打包体积与授权问题。

FONT_UI = "Microsoft YaHei UI"
FONT_MONO = "Cascadia Mono"
FONT_MONO_FALLBACK = "Consolas"

#: 字号阶梯（pt）。只有 5 档，禁止临时新增。
SIZE_LARGE_TITLE = 22    # 页面大标题
SIZE_TITLE = 15          # 区块标题
SIZE_HEADLINE = 12       # 正文强调 / 按钮
SIZE_BODY = 10           # 正文
SIZE_CAPTION = 9         # 说明、辅助

#: 行高（用于多行文本的 spacing）
LEADING_TIGHT = 1.15
LEADING_BODY = 1.55

# ── 间距 ─────────────────────────────────────────────────────────────────────
# 4 的倍数递增，够用且不会出现 7px、13px 这种随手值。
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 24
SPACE_6 = 32
SPACE_7 = 48

# ── 圆角 ─────────────────────────────────────────────────────────────────────
RADIUS_CONTROL = 8
RADIUS_CARD = 12
RADIUS_SHEET = 18

# ── 尺寸 ─────────────────────────────────────────────────────────────────────
ROW_HEIGHT = 34             # 表格行高，Apple 列表的常见密度
TOOLBAR_HEIGHT = 52
SEGMENT_HEIGHT = 30
SWITCH_W = 40
SWITCH_H = 24

# ── 动效 ─────────────────────────────────────────────────────────────────────
# tkinter 没有 spring，这里用缓出曲线逼近"快速起步、柔和收尾"的手感。
DUR_HOVER = 120             # 悬停/按下反馈：必须快，慢一点就显迟钝
DUR_TOGGLE = 180            # 开关、选中态迁移
DUR_PANEL = 220             # 页面切换

#: 帧间隔（约 60fps）
FRAME_MS = 16


# ── DPI 缩放 ─────────────────────────────────────────────────────────────────

_scale = 1.0


def _dpi_from_win32(root) -> float | None:
    """用 Win32 查窗口所在显示器的真实 DPI。

    为什么不能只信 ``winfo_fpixels('1i')``
    --------------------------------------
    PyInstaller 打包的 exe 自带 manifest，声明的是 **PerMonitorV2** 感知。
    在这种模式下，Tk 通过 ``GetDeviceCaps(LOGPIXELSX)`` 拿到的永远是 96，
    于是 ``winfo_fpixels('1i')`` 也报 96 —— 结果 125% 缩放下窗口会偏小一圈。

    ``GetDpiForWindow`` 返回真实值，但如果该函数不可用或句柄无效会返回 0，
    所以还要往下兜到 ``GetDpiForSystem``（不依赖窗口句柄，V2 下仍返回主屏
    DPI）。三级都失败才让调用方回退到 Tk。

    进程若是 DPI-unaware，这些调用一律返回 96，此时 scale 保持 1.0 是**正确**
    的：Windows 会自行把整个窗口位图拉伸，程序不该再乘一次缩放。
    """
    dpi = 0
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        # 64 位下必须显式声明，否则 HWND / HDC 会被截成 32 位 c_int
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.restype = wintypes.UINT
        hwnd = root.winfo_id()
        top = user32.GetParent(hwnd) or hwnd
        dpi = user32.GetDpiForWindow(top)
    except Exception:
        pass

    if not (72 <= dpi <= 480):
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            user32.GetDpiForSystem.argtypes = []
            user32.GetDpiForSystem.restype = wintypes.UINT
            dpi = user32.GetDpiForSystem()
        except Exception:
            dpi = 0

    if not (72 <= dpi <= 480):
        try:
            import ctypes
            from ctypes import wintypes

            user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
            user32.GetDC.argtypes = [wintypes.HWND]
            user32.GetDC.restype = wintypes.HDC
            user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
            gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
            hdc = user32.GetDC(0)
            try:
                dpi = gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
            finally:
                user32.ReleaseDC(0, hdc)
        except Exception:
            dpi = 0

    return float(dpi) if 72 <= dpi <= 480 else None


def init_scale(root) -> float:
    """按屏幕实际 DPI 计算缩放系数，让像素尺寸跟着系统缩放走。

    Tk 的字号（pt）会自动跟随 DPI，但 Canvas 里画的像素不会，
    所以凡是 Canvas 的像素尺寸都必须过一遍 :func:`px`。
    """
    global _scale
    dpi = _dpi_from_win32(root)
    if dpi is None:
        try:
            dpi = float(root.winfo_fpixels("1i"))
        except Exception:
            dpi = 96.0
    _scale = max(0.75, min(3.0, dpi / 96.0))
    # 字体与 Canvas 像素必须同源。PerMonitorV2 下 Tk 自报 96dpi，若只在布局上
    # 乘 scale，字号就会比控件小一圈；显式把 tk scaling 对齐真实 DPI 才能同步。
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass
    return _scale


def px(value: float) -> int:
    """逻辑像素 → 物理像素。"""
    return int(round(value * _scale))


def scale() -> float:
    return _scale


# ── 缓动函数 ─────────────────────────────────────────────────────────────────

def ease_out_cubic(t: float) -> float:
    """标准缓出：起步快、收尾柔。用于绝大多数过渡。"""
    return 1.0 - pow(1.0 - t, 3)


def ease_out_quint(t: float) -> float:
    """更"急"的缓出，用于需要在 200ms 内完成感强的过渡。"""
    return 1.0 - pow(1.0 - t, 5)


def ease_in_out(t: float) -> float:
    """两端都柔，用于位置迁移（如分段控件滑块）。"""
    return 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2


# ── 语义色便捷映射 ───────────────────────────────────────────────────────────

def state_colors(state: str) -> tuple[str, str]:
    """行状态 → (前景色, 浅底色)。

    state 取值：valid / invalid / testing / pending
    """
    return {
        "valid": (SUCCESS, SUCCESS_SOFT),
        "invalid": (DANGER, DANGER_SOFT),
        "testing": (ACCENT, ACCENT_SOFT),
        "pending": (LABEL_TERTIARY, SURFACE_SUNKEN),
    }.get(state, (LABEL_TERTIARY, SURFACE_SUNKEN))
