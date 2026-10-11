#!/usr/bin/env python3
"""程序化精绘扁平插画猫角色图集（8 状态 × 320×480）。

风格：扁平插画（vector flat）+ 夜色渐变背景 + 星点 + 月亮。4x 超采样
抗锯齿后缩小。输出 ≤120KB JPEG 直接可上传（设置 → 宠物 → 选择角色图）。

状态帧：
  working   打字（笔记本电脑+专注眼）
  thinking  托腮思考（?气泡）
  sleeping  蜷睡（闭眼+Zzz）
  greeting  挥手问候（微笑+挥手）
  happy     庆祝（开心眼+爱心）
  error     出错（皱眉+×）
  idle      端坐（普通眨眼）
  sleepy    犯困（半闭眼+Z）

用法：python3 tools/pet_artwork.py [输出目录]
"""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

S = 4                                   # 超采样倍数
W, H = 320 * S, 480 * S
OUT_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "..", "outputs", "petpack")

# 调色板（匹配实机校准深蓝主题）
BG_TOP = (8, 18, 44)
BG_BOT = (4, 9, 22)
MOON = (240, 228, 180)
STAR = (150, 180, 230)
FUR = (238, 160, 66)
FUR_DARK = (210, 132, 44)
FUR_BELLY = (250, 214, 150)
STRIPE = (176, 104, 30)
EAR_IN = (255, 178, 160)
EYE_WHITE = (245, 248, 255)
EYE = (36, 44, 70)
EYE_SHINE = (235, 242, 255)
NOSE = (255, 132, 118)
MOUTH = (150, 84, 48)
BLUSH = (255, 150, 130)
LAPTOP_BODY = (58, 70, 110)
LAPTOP_SCREEN = (16, 24, 48)
LAPTOP_GLOW = (110, 190, 255)
ACCENT = (110, 200, 250)
ACCENT_SOFT = (90, 130, 190)
Z_COL = (150, 200, 250)
HEART = (255, 108, 128)


def _lerp(a, b, k):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def _bg(d):
    for y in range(H):
        d.line([(0, y), (W, y)], fill=_lerp(BG_TOP, BG_BOT, y / H))
    # 星星（固定伪随机种子保证 8 帧背景一致）
    import random
    rnd = random.Random(20261011)
    for _ in range(90):
        x, y = rnd.randint(0, W), rnd.randint(0, int(H * 0.55))
        r = rnd.choice((1, 1, 2)) * S
        c = _lerp(STAR, (230, 240, 255), rnd.random())
        d.ellipse([x - r, y - r, x + r, y + r], fill=c)
    # 月亮（右上，带缺口）
    mx, my, mr = int(W * 0.82), int(H * 0.12), 34 * S
    d.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=MOON)
    d.ellipse([mx - mr * 0.7, my - mr, mx + mr * 1.3, my + mr * 0.6], fill=BG_TOP)


def _cat_head(d, cx, cy, scale, eye="open", brow_worry=False):
    """头：耳朵/脸/条纹/眼睛/鼻嘴/腮红。scale=1 时头宽 150*S。"""
    u = scale * S
    hw = 75 * u // 2
    # 耳朵
    for sx in (-1, 1):
        ex = cx + sx * int(hw * 0.72)
        ey = cy - int(46 * u // 2)
        d.polygon([(ex - 18 * u // 2, ey + 22 * u // 2),
                   (ex, ey - 20 * u // 2),
                   (ex + 18 * u // 2, ey + 20 * u // 2)], fill=FUR)
        d.polygon([(ex - 10 * u // 2, ey + 12 * u // 2),
                   (ex, ey - 6 * u // 2),
                   (ex + 10 * u // 2, ey + 10 * u // 2)], fill=EAR_IN)
    # 脸
    d.ellipse([cx - hw, cy - int(48 * u // 2), cx + hw, cy + int(48 * u // 2)], fill=FUR)
    # 条纹
    for off in (-30, 0, 30):
        d.polygon([(cx + off * u // 2 - 6 * u // 2, cy - int(44 * u // 2)),
                   (cx + off * u // 2 + 6 * u // 2, cy - int(44 * u // 2)),
                   (cx + off * u // 2, cy - int(30 * u // 2))], fill=STRIPE)
    # 眼睛
    ey = cy - int(4 * u // 2)
    exo = 26 * u // 2
    if eye == "closed":                      # 睡觉：弧线
        for sx in (-1, 1):
            d.arc([cx + sx * exo - 14 * u // 2, ey - 8 * u // 2,
                   cx + sx * exo + 14 * u // 2, ey + 10 * u // 2],
                  180, 360, fill=EYE, width=3 * u // 2)
    elif eye == "sleepy":                    # 半闭
        for sx in (-1, 1):
            d.ellipse([cx + sx * exo - 13 * u // 2, ey - 3 * u // 2,
                       cx + sx * exo + 13 * u // 2, ey + 9 * u // 2], fill=EYE)
    elif eye == "happy":                     # 开心弯眼（倒弧）
        for sx in (-1, 1):
            d.arc([cx + sx * exo - 14 * u // 2, ey - 10 * u // 2,
                   cx + sx * exo + 14 * u // 2, ey + 8 * u // 2],
                  0, 180, fill=EYE, width=3 * u // 2)
    elif eye == "worry":                     # 皱眉小眼
        for sx in (-1, 1):
            d.ellipse([cx + sx * exo - 10 * u // 2, ey - 4 * u // 2,
                       cx + sx * exo + 10 * u // 2, ey + 8 * u // 2], fill=EYE)
        if brow_worry:
            d.line([cx + sx * exo - 10 * u // 2, ey - 14 * u // 2,
                    cx + sx * exo + 10 * u // 2, ey - 8 * u // 2],
                   fill=STRIPE, width=2 * u // 2)
    else:                                    # open：大圆眼+高光
        for sx in (-1, 1):
            d.ellipse([cx + sx * exo - 14 * u // 2, ey - 12 * u // 2,
                       cx + sx * exo + 14 * u // 2, ey + 12 * u // 2], fill=EYE)
            d.ellipse([cx + sx * exo - 9 * u // 2, ey - 10 * u // 2,
                       cx + sx * exo + 5 * u // 2, ey + 2 * u // 2], fill=EYE_WHITE)
            d.ellipse([cx + sx * exo - 2 * u // 2, ey - 12 * u // 2,
                       cx + sx * exo + 8 * u // 2, ey - 2 * u // 2],
                      fill=EYE_SHINE)
    # 鼻+嘴（w 形）
    d.ellipse([cx - 7 * u // 2, cy + int(12 * u // 2),
               cx + 7 * u // 2, cy + int(22 * u // 2)], fill=NOSE)
    d.arc([cx - 12 * u // 2, cy + int(20 * u // 2),
           cx, cy + int(34 * u // 2)], 0, 90, fill=MOUTH, width=2 * u // 2)
    d.arc([cx, cy + int(20 * u // 2),
           cx + 12 * u // 2, cy + int(34 * u // 2)], 90, 180, fill=MOUTH, width=2 * u // 2)
    # 腮红
    for sx in (-1, 1):
        bx = cx + sx * int(hw * 0.78)
        d.ellipse([bx - 12 * u // 2, cy + int(12 * u // 2),
                   bx + 12 * u // 2, cy + int(24 * u // 2)],
                  fill=_lerp(FUR, BLUSH, 0.55))


def _zzz(d, x, y, u, n=3):
    """Zzz 气泡文字（画线 Z，避免字体依赖）。"""
    for i in range(n):
        zx = x + i * 16 * u // 2
        zy = y - i * 14 * u // 2
        w = max(2, (8 - i * 2) * u // 2)
        d.line([(zx, zy), (zx + 10 * u // 2, zy)], fill=Z_COL, width=w)
        d.line([(zx + 10 * u // 2, zy), (zx, zy + 10 * u // 2)], fill=Z_COL, width=w)
        d.line([(zx, zy + 10 * u // 2), (zx + 10 * u // 2, zy + 10 * u // 2)],
               fill=Z_COL, width=w)


def _heart(d, x, y, u, size=10):
    r = size * u // 2
    d.ellipse([x - r, y - r, x, y], fill=HEART)
    d.ellipse([x, y - r, x + r, y], fill=HEART)
    d.polygon([(x - r, y - r // 2), (x + r, y - r // 2), (x, y + int(r * 1.4))],
              fill=HEART)


def _bubble_q(d, x, y, u):
    r = 26 * u // 2
    d.rounded_rectangle([x - r, y - r, x + r, y + r], radius=8 * u // 2,
                        fill=(230, 240, 255))
    d.polygon([(x - 8 * u // 2, y + r), (x + 8 * u // 2, y + r),
               (x - 2 * u // 2, y + r + 14 * u // 2)], fill=(230, 240, 255))
    # "?" 用两段线+点（避免字体依赖）
    c = (60, 80, 130)
    w = 4 * u // 2
    d.arc([x - 8 * u // 2, y - r + 8 * u // 2, x + 8 * u // 2, y],
          180, 360, fill=c, width=w)
    d.line([(x + 8 * u // 2, y - r // 2), (x, y + 2 * u // 2)], fill=c, width=w)
    d.ellipse([x - 2 * u // 2, y + 6 * u // 2, x + 2 * u // 2, y + 10 * u // 2],
              fill=c)


def _x_mark(d, x, y, u):
    w = 5 * u // 2
    d.line([(x - 10 * u // 2, y - 10 * u // 2), (x + 10 * u // 2, y + 10 * u // 2)],
           fill=(255, 120, 110), width=w)
    d.line([(x - 10 * u // 2, y + 10 * u // 2), (x + 10 * u // 2, y - 10 * u // 2)],
           fill=(255, 120, 110), width=w)


def _laptop(d, cx, cy, u):
    """小笔记本：屏幕发光（working 状态道具）。"""
    w, h = 96 * u // 2, 60 * u // 2
    d.rounded_rectangle([cx - w // 2, cy - h, cx + w // 2, cy],
                        radius=4 * u // 2, fill=LAPTOP_BODY)
    d.rounded_rectangle([cx - w // 2 + 6 * u // 2, cy - h + 6 * u // 2,
                         cx + w // 2 - 6 * u // 2, cy - 8 * u // 2],
                        radius=2 * u // 2, fill=LAPTOP_SCREEN)
    # 屏幕发光条
    for i, (bx, bw) in enumerate(((0.16, 0.3), (0.52, 0.2), (0.16, 0.42))):
        gx = cx - w // 2 + int(w * bx) + 4 * u // 2
        d.rounded_rectangle([gx, cy - h + 10 * u // 2 + i * 10 * u // 2,
                             gx + int(w * bw), cy - h + 16 * u // 2 + i * 10 * u // 2],
                            radius=2 * u // 2, fill=LAPTOP_GLOW)


def draw_frame(state):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    _bg(d)

    cx = W // 2
    if state == "working":
        body_cy = int(H * 0.52)
        d.polygon([(cx - 30 * S, int(H * 0.40)), (cx + 30 * S, int(H * 0.40)),
                   (cx + 38 * S, body_cy + 10 * S), (cx - 38 * S, body_cy + 10 * S)],
                  fill=FUR)
        # 身体（坐姿，前爪在笔记本上）
        d.ellipse([cx - 70 * S, body_cy - 10 * S, cx + 70 * S, body_cy + 90 * S],
                  fill=FUR)
        d.ellipse([cx - 40 * S, body_cy + 10 * S, cx + 40 * S, body_cy + 86 * S],
                  fill=FUR_BELLY)
        # 尾巴
        d.arc([cx + 40 * S, body_cy + 20 * S, cx + 130 * S, body_cy + 110 * S],
              250, 20, fill=FUR_DARK, width=14 * S)
        _laptop(d, cx, int(H * 0.72), S)
        _cat_head(d, cx, int(H * 0.36), 2, eye="open")
        # 前爪搭键盘
        for sx in (-1, 1):
            d.ellipse([cx + sx * 24 * S - 16 * S, int(H * 0.70) - 8 * S,
                       cx + sx * 24 * S + 16 * S, int(H * 0.70) + 12 * S], fill=FUR)
    elif state == "thinking":
        body_cy = int(H * 0.54)
        d.polygon([(cx - 26 * S, int(H * 0.42)), (cx + 22 * S, int(H * 0.42)),
                   (cx + 34 * S, body_cy + 8 * S), (cx - 34 * S, body_cy + 8 * S)],
                  fill=FUR)
        d.ellipse([cx - 66 * S, body_cy - 10 * S, cx + 66 * S, body_cy + 88 * S],
                  fill=FUR)
        _cat_head(d, cx - 14 * S, int(H * 0.38), 2, eye="worry", brow_worry=True)
        _bubble_q(d, cx + 74 * S, int(H * 0.24), S)
        # 一只爪托腮
        d.ellipse([cx - 44 * S, int(H * 0.44) - 10 * S,
                   cx - 12 * S, int(H * 0.44) + 18 * S], fill=FUR)
    elif state == "sleeping":
        body_cy = int(H * 0.60)
        # 蜷睡：大椭圆身体+头枕在身侧
        d.ellipse([cx - 92 * S, body_cy - 60 * S, cx + 92 * S, body_cy + 70 * S],
                  fill=FUR)
        d.ellipse([cx - 60 * S, body_cy - 20 * S, cx + 60 * S, body_cy + 50 * S],
                  fill=FUR_BELLY)
        # 尾巴环身
        d.arc([cx - 110 * S, body_cy - 30 * S, cx + 110 * S, body_cy + 90 * S],
              60, 180, fill=FUR_DARK, width=14 * S)
        _cat_head(d, cx + 44 * S, body_cy - 34 * S, 2, eye="closed")
        _zzz(d, cx + 96 * S, body_cy - 90 * S, S)
    elif state == "greeting":
        body_cy = int(H * 0.52)
        d.polygon([(cx - 30 * S, int(H * 0.40)), (cx + 30 * S, int(H * 0.40)),
                   (cx + 38 * S, body_cy + 10 * S), (cx - 38 * S, body_cy + 10 * S)],
                  fill=FUR)
        d.ellipse([cx - 68 * S, body_cy - 10 * S, cx + 68 * S, body_cy + 90 * S],
                  fill=FUR)
        d.ellipse([cx - 38 * S, body_cy + 8 * S, cx + 38 * S, body_cy + 84 * S],
                  fill=FUR_BELLY)
        _cat_head(d, cx, int(H * 0.36), 2, eye="happy")
        # 挥手爪（右侧举起）
        d.ellipse([cx + 62 * S, int(H * 0.30) - 14 * S,
                   cx + 96 * S, int(H * 0.30) + 20 * S], fill=FUR)
        d.ellipse([cx - 96 * S, body_cy + 6 * S,
                   cx - 62 * S, body_cy + 40 * S], fill=FUR)
        _heart(d, cx + 104 * S, int(H * 0.22), S)
        _heart(d, cx - 104 * S, int(H * 0.26), S, size=8)
    elif state == "happy":
        body_cy = int(H * 0.52)
        d.polygon([(cx - 30 * S, int(H * 0.40)), (cx + 30 * S, int(H * 0.40)),
                   (cx + 38 * S, body_cy + 10 * S), (cx - 38 * S, body_cy + 10 * S)],
                  fill=FUR)
        d.ellipse([cx - 68 * S, body_cy - 10 * S, cx + 68 * S, body_cy + 90 * S],
                  fill=FUR)
        _cat_head(d, cx, int(H * 0.36), 2, eye="happy")
        for i, hx in enumerate((cx - 90 * S, cx + 84 * S)):
            _heart(d, hx, int(H * 0.24) - i * 6 * S, S, size=12 - i * 2)
    elif state == "error":
        body_cy = int(H * 0.54)
        d.polygon([(cx - 26 * S, int(H * 0.42)), (cx + 26 * S, int(H * 0.42)),
                   (cx + 34 * S, body_cy + 8 * S), (cx - 34 * S, body_cy + 8 * S)],
                  fill=FUR)
        d.ellipse([cx - 66 * S, body_cy - 10 * S, cx + 66 * S, body_cy + 88 * S],
                  fill=FUR)
        _cat_head(d, cx, int(H * 0.38), 2, eye="worry", brow_worry=True)
        _x_mark(d, cx + 76 * S, int(H * 0.26), S)
    elif state == "sleepy":
        body_cy = int(H * 0.54)
        d.polygon([(cx - 26 * S, int(H * 0.42)), (cx + 26 * S, int(H * 0.42)),
                   (cx + 34 * S, body_cy + 8 * S), (cx - 34 * S, body_cy + 8 * S)],
                  fill=FUR)
        d.ellipse([cx - 66 * S, body_cy - 10 * S, cx + 66 * S, body_cy + 88 * S],
                  fill=FUR)
        _cat_head(d, cx, int(H * 0.38), 2, eye="sleepy")
        _zzz(d, cx + 84 * S, int(H * 0.30), S, n=2)
    else:  # idle 端坐
        body_cy = int(H * 0.53)
        d.polygon([(cx - 28 * S, int(H * 0.41)), (cx + 28 * S, int(H * 0.41)),
                   (cx + 38 * S, body_cy + 10 * S), (cx - 38 * S, body_cy + 10 * S)],
                  fill=FUR)
        d.ellipse([cx - 68 * S, body_cy - 10 * S, cx + 68 * S, body_cy + 90 * S],
                  fill=FUR)
        d.ellipse([cx - 36 * S, body_cy + 8 * S, cx + 36 * S, body_cy + 84 * S],
                  fill=FUR_BELLY)
        _cat_head(d, cx, int(H * 0.37), 2, eye="open")
        for sx in (-1, 1):
            d.ellipse([cx + sx * 58 * S - 16 * S, body_cy + 66 * S - 8 * S,
                       cx + sx * 58 * S + 16 * S, body_cy + 66 * S + 16 * S], fill=FUR)

    # 底部压暗（状态条/按钮可读性）
    for y in range(int(H * 0.78), H):
        k = (y - H * 0.78) / (H * 0.22)
        d.line([(0, y), (W, y)], fill=_lerp((6, 12, 28), (3, 7, 16), k))
    return img


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else OUT_DEFAULT
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    states = ["working", "thinking", "sleeping", "greeting",
              "happy", "error", "idle", "sleepy"]
    for st in states:
        img = draw_frame(st).resize((320, 480), Image.LANCZOS)
        path = os.path.join(out, f"pet_{st}.jpg")
        q = 88
        while True:
            img.save(path, "JPEG", quality=q)
            if os.path.getsize(path) <= 118_000 or q <= 40:
                break
            q -= 8
        print(f"{path}  {os.path.getsize(path)} bytes (q={q})")


if __name__ == "__main__":
    main()
