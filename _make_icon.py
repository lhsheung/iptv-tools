"""生成应用图标 app.ico。

用代码画而不是塞一张图：只有一个蓝色圆角方块 + 白色播放三角，
和界面左上角的标识一致，改色只要改这里的两个常量。
"""

import os

from PIL import Image, ImageDraw

ACCENT = "#007AFF"
WHITE = "#FFFFFF"

SIZES = (16, 24, 32, 48, 64, 128, 256)
SS = 4          # 超采样倍数，缩小后得到抗锯齿边缘


def render(size: int) -> Image.Image:
    w = size * SS
    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角方块：圆角半径约为边长的 23%，接近 macOS 图标的观感
    radius = int(w * 0.23)
    d.rounded_rectangle((0, 0, w - 1, w - 1), radius=radius, fill=ACCENT)

    # 白色播放三角，视觉居中（几何中心略偏右会让三角看起来才正中）
    cx, cy = w * 0.52, w * 0.5
    h = w * 0.34                    # 三角半高
    t = w * 0.30                    # 三角底边到顶点的水平距离
    d.polygon([(cx - t * 0.55, cy - h), (cx - t * 0.55, cy + h), (cx + t * 0.75, cy)],
              fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "app.ico")
    imgs = [render(s) for s in SIZES]
    imgs[-1].save(out, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"OK {out} {os.path.getsize(out)} bytes  sizes={SIZES}")

    # 顺带给界面留一张 png，方便文档引用
    png = os.path.join(here, "app.png")
    imgs[-1].save(png)
    print(f"OK {png}")


if __name__ == "__main__":
    main()
