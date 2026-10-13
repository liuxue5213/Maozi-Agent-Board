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

# 角色毛色调色板（合并原则：同一模板 × 不同毛色 = 形象多样化）。
# 键为模块常量名，use_palette 时覆盖全局；orange 即上面默认值（橘猫）。
_PAL_DEFAULTS = None   # 首次 use_palette 前由 use_palette 快照
_PALETTES = {
    "orange": {},
    "white": {"FUR": (245, 242, 238), "FUR_DARK": (225, 218, 210),
              "FUR_BELLY": (255, 255, 252), "STRIPE": (228, 220, 210),
              "EAR_IN": (255, 205, 205), "NOSE": (255, 150, 160)},
    "grey": {"FUR": (96, 102, 118), "FUR_DARK": (74, 80, 96),
             "FUR_BELLY": (168, 174, 190), "STRIPE": (58, 64, 80),
             "EAR_IN": (130, 120, 140), "EYE": (235, 215, 120),
             "EYE_WHITE": (60, 66, 86), "NOSE": (200, 190, 170)},
    "cream": {"FUR": (245, 220, 180), "FUR_DARK": (225, 195, 150),
              "FUR_BELLY": (252, 240, 215), "STRIPE": (228, 200, 158),
              "EAR_IN": (255, 210, 190)},
}


def use_palette(name):
    """切换毛色调色板（模块常量按名覆盖/恢复）。"""
    global _PAL_DEFAULTS
    if _PAL_DEFAULTS is None:
        _PAL_DEFAULTS = {k: globals()[k] for k in _PALETTES["white"]}
    pal = _PALETTES.get(name)
    globals().update(_PAL_DEFAULTS)          # 先复位再覆盖（防串色）
    if pal:
        globals().update(pal)


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


SKY_TOP = (168, 205, 235)
SKY_BOT = (215, 228, 210)
GRASS = (120, 168, 96)
TREE = (86, 138, 74)
WOOD = (172, 128, 84)
WOOD_DARK = (140, 100, 62)
SKY_GLOW = (250, 226, 170)


def _bg_window(d):
    """窗边小院（白天）：暖天空 + 窗外草地树影 + 木窗框 + 宽窗台桌面。"""
    for y in range(H):
        d.line([(0, y), (W, y)], fill=_lerp(SKY_TOP, SKY_BOT, y / H))
    # 太阳光晕（左上）
    d.ellipse([-60 * S, -60 * S, 140 * S, 140 * S], fill=SKY_GLOW)
    # 窗外草地（下半）
    d.rectangle([0, int(H * 0.52), W, int(H * 0.78)], fill=GRASS)
    # 窗外树冠（几团圆形）
    import random
    rnd = random.Random(77)
    for _ in range(6):
        tx = rnd.randint(int(W * 0.05), int(W * 0.9))
        ty = int(H * (0.44 + rnd.random() * 0.1))
        tr = rnd.randint(30, 55) * S
        d.ellipse([tx - tr, ty - tr, tx + tr, ty + tr], fill=TREE)
    # 远处小屋剪影
    d.rectangle([int(W * 0.62), int(H * 0.40), int(W * 0.78), int(H * 0.52)],
                fill=(210, 190, 160))
    d.polygon([(int(W * 0.60), int(H * 0.40)), (int(W * 0.70), int(H * 0.33)),
               (int(W * 0.80), int(H * 0.40))], fill=(150, 90, 70))
    # 木窗框（十字）
    fw = 16 * S
    d.rectangle([0, int(H * 0.30), W, int(H * 0.30) + fw], fill=WOOD)
    d.rectangle([0, int(H * 0.76), W, int(H * 0.76) + fw], fill=WOOD)
    d.rectangle([int(W * 0.47), 0, int(W * 0.47) + fw, int(H * 0.78)], fill=WOOD)
    # 窗台桌面（猫坐的平台，占下部）
    d.rectangle([0, int(H * 0.80), W, H], fill=(196, 168, 128))
    d.rectangle([0, int(H * 0.80), W, int(H * 0.80) + 8 * S], fill=(224, 200, 158))
    # 桌上咖啡杯（右侧小物）
    cw, ch = 26 * S, 30 * S
    ccx, ccy = int(W * 0.87), int(H * 0.80) - ch
    d.rounded_rectangle([ccx - cw // 2, ccy - ch // 2, ccx + cw // 2, ccy + ch // 2],
                        radius=6 * S, fill=(240, 240, 238))
    d.arc([ccx + cw // 2 - 4 * S, ccy - 6 * S, ccx + cw // 2 + 14 * S, ccy + 10 * S],
          270, 90, fill=(180, 150, 120), width=3 * S)
    d.ellipse([ccx - 10 * S, ccy - ch // 2 + 4 * S, ccx + 10 * S,
               ccy - ch // 2 + 14 * S], fill=(94, 60, 34))


def _bg_rain(d):
    """雨夜街窗（降水包）：青灰雨云天 + 远楼灯火 + 湿漉街面。"""
    TOP, BOT = (38, 48, 70), (22, 30, 46)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=_lerp(TOP, BOT, y / H))
    import random
    rnd = random.Random(4242)
    bx = 0
    while bx < W:   # 远处楼群剪影 + 暖窗灯
        bw = rnd.randint(50, 110) * S
        bh = rnd.randint(90, 220) * S
        by = int(H * 0.62) - bh
        d.rectangle([bx, by, bx + bw, int(H * 0.62)],
                    fill=_lerp((28, 36, 56), (20, 26, 42), rnd.random()))
        for _ in range(rnd.randint(3, 8)):
            wx = bx + rnd.randint(6, max(7, bw // S - 6)) * S
            wy = by + rnd.randint(8, max(9, bh // S - 8)) * S
            d.rectangle([wx, wy, wx + 6 * S, wy + 8 * S], fill=(250, 214, 140))
        bx += bw + rnd.randint(6, 18) * S
    d.rectangle([0, int(H * 0.62), W, H], fill=(30, 38, 56))   # 湿漉街面


def _overlay_rain(d):
    """雨丝斜线 + 地面涟漪（画在角色之上，降水氛围）。"""
    import random
    rnd = random.Random(909)
    for _ in range(140):
        x, y = rnd.randint(0, W), rnd.randint(0, H)
        ln = rnd.randint(10, 26) * S
        d.line([(x, y), (x - ln // 3, y + ln)], fill=(140, 165, 205), width=2 * S)
    for _ in range(14):
        x, y = rnd.randint(0, W), rnd.randint(int(H * 0.66), H)
        r = rnd.randint(6, 16) * S
        d.arc([x - r, y - r // 2, x + r, y + r // 2], 200, 340,
              fill=(120, 145, 185), width=2 * S)


def _bg_snow(d):
    """黄昏雪国（雪天包）：淡青灰天 + 雪地 + 松林剪影 + 飘雪。"""
    TOP, BOT = (150, 168, 195), (96, 110, 138)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=_lerp(TOP, BOT, y / H))
    d.rectangle([0, int(H * 0.66), W, H], fill=(214, 224, 238))
    d.rectangle([0, int(H * 0.66), W, int(H * 0.66) + 10 * S], fill=(235, 242, 250))
    import random
    rnd = random.Random(1212)
    for _ in range(7):   # 松林剪影
        tx = rnd.randint(int(W * 0.02), int(W * 0.92))
        base = int(H * (0.66 + rnd.random() * 0.04))
        th = rnd.randint(60, 110) * S
        tw = th // 3
        for k, wk in enumerate((0.45, 0.7, 1.0)):
            wt = int(tw * wk)
            d.polygon([(tx - wt, base - k * th // 3), (tx + wt, base - k * th // 3),
                       (tx, base - (k + 1) * th // 3 - 8 * S)], fill=(70, 88, 112))
    rnd = random.Random(1213)
    for _ in range(120):   # 飘雪
        x, y = rnd.randint(0, W), rnd.randint(0, H)
        r = rnd.choice((1, 1, 2, 3)) * S
        d.ellipse([x - r, y - r, x + r, y + r], fill=(240, 246, 252))


def _bg_spring(d):
    """樱花黄昏（春日包）：粉紫天空 + 樱枝 + 花瓣 + 草地。"""
    TOP, BOT = (232, 200, 216), (168, 150, 190)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=_lerp(TOP, BOT, y / H))
    d.rectangle([0, int(H * 0.62), W, H], fill=(150, 178, 140))
    d.rectangle([0, int(H * 0.62), W, int(H * 0.62) + 8 * S], fill=(170, 198, 158))
    d.arc([-40 * S, -30 * S, int(W * 0.7), int(H * 0.25)], 20, 70,
          fill=(120, 88, 70), width=10 * S)   # 樱枝
    import random
    rnd = random.Random(313)
    for _ in range(26):   # 花团
        x = rnd.randint(int(W * 0.05), int(W * 0.62))
        y = rnd.randint(int(H * 0.02), int(H * 0.14))
        r = rnd.randint(8, 20) * S
        d.ellipse([x - r, y - r, x + r, y + r],
                  fill=_lerp((252, 214, 226), (246, 180, 200), rnd.random()))
    for _ in range(30):   # 花瓣
        x, y = rnd.randint(0, W), rnd.randint(0, H)
        r = rnd.randint(3, 7) * S
        d.ellipse([x - r, y - r // 2, x + r, y + r // 2], fill=(248, 196, 214))


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


def draw_frame(state, pose=0, scene="night", palette="orange"):
    """scene: night/window/rainy/snow/spring；palette: orange/white/grey/cream。"""
    use_palette(palette)
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    if scene == "window":
        _bg_window(d)
    elif scene == "rainy":
        _bg_rain(d)
    elif scene == "snow":
        _bg_snow(d)
    elif scene == "spring":
        _bg_spring(d)
    else:
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
        # 前爪搭键盘（pose=1 时右爪抬起 6px：打字起伏的第二帧）
        lift = 6 * S if pose else 0
        for sx in (-1, 1):
            oy = lift if sx == 1 else 0
            d.ellipse([cx + sx * 24 * S - 16 * S, int(H * 0.70) - 8 * S - oy,
                       cx + sx * 24 * S + 16 * S, int(H * 0.70) + 12 * S - oy],
                      fill=FUR)
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

    if scene == "rainy":
        _overlay_rain(d)   # 雨丝画在角色之上（降水氛围）
    # 底部压暗（状态条/按钮可读性）：各场景压向自己的暗色
    if scene == "window":
        for y in range(int(H * 0.80), H):
            k = (y - H * 0.80) / (H * 0.20)
            d.line([(0, y), (W, y)],
                   fill=_lerp((168, 138, 100), (120, 94, 66), k))
    elif scene == "rainy":
        for y in range(int(H * 0.78), H):
            k = (y - H * 0.78) / (H * 0.22)
            d.line([(0, y), (W, y)], fill=_lerp((30, 40, 58), (16, 24, 38), k))
    elif scene == "snow":
        for y in range(int(H * 0.78), H):
            k = (y - H * 0.78) / (H * 0.22)
            d.line([(0, y), (W, y)], fill=_lerp((120, 132, 158), (70, 82, 108), k))
    elif scene == "spring":
        for y in range(int(H * 0.78), H):
            k = (y - H * 0.78) / (H * 0.22)
            d.line([(0, y), (W, y)], fill=_lerp((120, 150, 120), (60, 80, 78), k))
    else:
        for y in range(int(H * 0.78), H):
            k = (y - H * 0.78) / (H * 0.22)
            d.line([(0, y), (W, y)], fill=_lerp((6, 12, 28), (3, 7, 16), k))
    return img


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else OUT_DEFAULT
    scene = "night"
    if "--scene" in sys.argv:
        scene = sys.argv[sys.argv.index("--scene") + 1]
    pack = None
    if "--pack" in sys.argv:
        pack = sys.argv[sys.argv.index("--pack") + 1]
        out = os.path.join(os.path.dirname(OUT_DEFAULT), f"petpack_{pack}")
    palette = "orange"
    if "--palette" in sys.argv:
        palette = sys.argv[sys.argv.index("--palette") + 1]
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    states = ["working", "thinking", "sleeping", "greeting",
              "happy", "error", "idle", "sleepy"]
    for st in states:
        img = draw_frame(st, 0, scene, palette).resize((320, 480), Image.LANCZOS)
        path = os.path.join(out, f"pet_{st}.jpg")
        q = 88
        while True:
            img.save(path, "JPEG", quality=q)
            if os.path.getsize(path) <= 118_000 or q <= 40:
                break
            q -= 8
        print(f"{path}  {os.path.getsize(path)} bytes (q={q})")
        if st == "working":                   # 动态版：B 帧（右爪抬起）
            img_b = draw_frame(st, pose=1, scene=scene, palette=palette).resize((320, 480), Image.LANCZOS)
            path_b = os.path.join(out, "pet_working_b.jpg")
            q = 88
            while True:
                img_b.save(path_b, "JPEG", quality=q)
                if os.path.getsize(path_b) <= 118_000 or q <= 40:
                    break
                q -= 8
            print(f"{path_b}  {os.path.getsize(path_b)} bytes (q={q})")


if __name__ == "__main__":
    main()
