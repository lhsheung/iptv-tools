"""控件库自检：把所有自定义控件摆一屏，截图存盘后自动关闭。

用途是快速验证"画得出来、尺寸对、点击有反应"，不替代 __main__ 里的完整冒烟测试。
"""

import os
import sys
import tkinter as tk

# DPI 声明必须早于 Tk 创建，否则窗口坐标与字体都会按 96dpi 走
if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import theme
import widgets as W


def main() -> int:
    root = tk.Tk()
    root.title("widgets self-check")
    root.geometry("760x560")
    root.configure(bg=theme.CANVAS)
    theme.init_scale(root)

    wrap = tk.Frame(root, bg=theme.CANVAS)
    wrap.pack(fill="both", expand=True, padx=24, pady=20)

    W.label(wrap, "控件自检", size=theme.SIZE_LARGE_TITLE,
            bold=True).pack(anchor="w", pady=(0, 12))

    # 按钮三种样式
    row = tk.Frame(wrap, bg=theme.CANVAS)
    row.pack(fill="x", pady=(0, 14))
    W.PillButton(row, "开始测试", variant="primary", glyph="▶",
                 width=104, height=32).pack(side="left", padx=(0, 8))
    W.PillButton(row, "导出 DPL", variant="secondary", glyph="↓",
                 width=104, height=32).pack(side="left", padx=(0, 8))
    W.PillButton(row, "关于", variant="ghost", width=64, height=32).pack(side="left")

    # 分段控件
    seg = W.SegmentedControl(wrap, ["导入源", "测试结果", "设置"],
                             selected=0, height=32)
    seg.pack(fill="x", pady=(0, 14))

    # 开关行
    sw_row = tk.Frame(wrap, bg=theme.CANVAS)
    sw_row.pack(fill="x", pady=(0, 14))
    for name, val in (("启用解码检测", True), ("严格校验", False)):
        W.label(sw_row, name).pack(side="left", padx=(0, 8))
        W.Switch(sw_row, value=val).pack(side="left", padx=(0, 20))

    # 状态点
    dot_row = tk.Frame(wrap, bg=theme.CANVAS)
    dot_row.pack(fill="x", pady=(0, 14))
    for st in ("valid", "invalid", "testing", "pending"):
        W.StatusDot(dot_row, st).pack(side="left", padx=(0, 6))
        W.label(dot_row, st, color=theme.LABEL_SECONDARY).pack(side="left", padx=(0, 14))

    # 进度条
    prog = W.ThinProgress(wrap, height=6)
    prog.pack(fill="x", pady=(0, 16))
    prog.set_value(0.42, animate=False)

    # 卡片
    card = W.Card(wrap, padding=16, auto_height=True)
    card.pack(fill="x")
    W.label(card.body, "卡片标题", size=theme.SIZE_TITLE, bold=True).pack(anchor="w")
    W.label(card.body, "这是卡片里的说明文字，用来验证内边距和自动高度。",
            size=theme.SIZE_CAPTION, color=theme.LABEL_SECONDARY).pack(anchor="w", pady=(4, 0))

    root.update_idletasks()
    root.update()

    # 截图
    import _shot
    _shot.grab_window(root, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "shot_widgets.png"))

    root.after(300, root.destroy)
    root.mainloop()
    print("OK 控件自检完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
