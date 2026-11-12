"""启动真实界面并按页截图，用于人工核对布局。

用法::

    python _capture.py                # 截三页
    python _capture.py import         # 只截导入页

之所以用真实 ``main.IPTVApp`` 而不是摆一个简化版，是因为要验证的就是
布局在真实内容下的表现：卡片是否撑破、文字是否被裁、分段控件是否对齐。
"""

from __future__ import annotations

import os
import sys
import tkinter as tk

if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import theme                     # noqa: E402
import _shot                     # noqa: E402
from gui import IPTVApp          # noqa: E402

WIN_W, WIN_H = 1120, 780


def main() -> int:
    pages = sys.argv[1:] or ["import", "results", "settings"]
    valid = {"import", "results", "settings"}
    pages = [p for p in pages if p in valid] or ["import"]

    root = tk.Tk()
    root.title("IPTV 直播源测试工具")
    scale = theme.init_scale(root)
    w, h = int(WIN_W * scale), int(WIN_H * scale)
    root.geometry(f"{w}x{h}+60+40")
    root.configure(bg=theme.CANVAS)

    app = IPTVApp(root)

    # 造几条假数据，让结果页/统计不是空的 —— 只为验证排版，不写回任何文件
    _seed_demo_rows(app)

    out_dir = HERE
    shots = []

    def shoot(i: int) -> None:
        if i >= len(pages):
            root.destroy()
            return
        name = pages[i]
        app._switch_page(name)
        app.seg_pages.select({"import": 0, "results": 1, "settings": 2}[name])
        root.update_idletasks()
        root.update()

        def do_shot():
            path = os.path.join(out_dir, f"shot_{name}.png")
            _shot.grab_window(root, path)
            shots.append(path)
            shoot(i + 1)

        root.after(450, do_shot)

    root.after(300, lambda: shoot(0))
    root.mainloop()

    for s in shots:
        print("OK", s)
    return 0 if shots else 1


def _seed_demo_rows(app: IPTVApp) -> None:
    """构造演示数据。"""
    from test_logic import Channel
    from gui import Row

    samples = [
        ("CCTV-1 综合", "央视", True, "120 ms", 92, "北京市 联通", "h264", True, 48),
        ("CCTV-5 体育", "央视", True, "340 ms", 61, "广东省 深圳市 电信", "h265", True, 30),
        ("湖南卫视", "卫视", True, "480 ms", 44, "湖南省 长沙市", "h264", False, 0),
        ("浙江卫视", "卫视", False, "", 0, "测试失败/过滤", None, None, 0),
        ("东方卫视", "卫视", True, "260 ms", 78, "上海市 电信", "h264", True, 45),
        ("江苏卫视", "卫视", False, "超时", 0, "连接超时", None, False, 0),
        ("北京卫视", "卫视", True, "190 ms", 85, "北京市 联通", "h264", True, 49),
        ("广东珠江", "地方", True, "520 ms", 33, "广东省 广州市", "h264", False, 0),
    ]
    rows = []
    for name, group, valid, resp, score, loc, codec, decode_ok, frames in samples:
        ch = Channel(name=name, link_str=f"http://demo.example.com/{abs(hash(name))}.m3u8",
                     group_name=group)
        ch.is_valid = valid
        ch.response_time = resp
        ch.fluency_score = score
        ch.location = loc
        ch.codec = codec
        ch.decode_ok = decode_ok
        ch.decode_frames = frames
        rows.append(Row(ch))

    app.rows = rows
    app._refresh_results()
    valid = sum(1 for r in rows if r.state == "valid")
    app.lbl_progress.configure(text=f"演示数据 · {len(rows)}/{len(rows)} · 可用 {valid}")
    app.progress.set_value(1.0, animate=False)


if __name__ == "__main__":
    raise SystemExit(main())
