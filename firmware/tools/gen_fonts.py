#!/usr/bin/env python3
"""生成 LVGL 中文字库（两层策略，用户自定义文字永不缺字）：

- cn_16 / cn_20 / cn_24：覆盖完整 GB2312（简体全部 6763 字）→ 用户配置的
  应用名、提醒、日历文字都能渲染。
- cn_32 / cn_48：只含源码字符串字面量里用到的字（扫描 src/*.cpp|h 自动
  提取），未覆盖的字符通过 LVGL fallback 回退到 cn_24 显示。

字号档位说明（320×480 竖屏实测）：
16px 是"能显示但读不清"的下限——中文笔画在 16px 下笔画间距仅 1~2px，
4bpp 抗锯齿的灰边会吃掉笔画间隙，实机观感为糊成一团。因此新增 20px 档，
所有需要在1~2m 外读的文字一律不得用 16px。

用法：python3 tools/gen_fonts.py [-v]   （改了任何 UI 文案或字号后重跑）
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
# 必须用 TTF（TrueType 轮廓）；OTF/CFF 会让 lv_font_conv 产出碎点字库
FONT = ROOT / "tools" / ".font-cache" / "NotoSansSC-Medium.ttf"
FONT_CONV = "lv_font_conv@1.4.0"
EXTRA = "℃·…°，。：！？（）【】"

# 全量字库覆盖的字号。16/20/24 均含GB2312 全字。
FULL_SIZES = (16, 20, 24)
# 仅界面文案字库的字号（体积换清晰度，只用于超大标题）。
UI_ONLY_SIZES = (32, 48)


def gb2312_all() -> str:
    chars = set()
    for lead in range(0xB0, 0xF8):
        for trail in range(0xA1, 0xFF):
            try:
                chars.add(bytes((lead, trail)).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    return "".join(sorted(chars))


def ui_literals() -> str:
    """全部源码字符串字面量中的 CJK 字符（注释天然跳过）。"""
    chars = set(EXTRA)
    for path in sorted(SRC.glob("*.cpp")) + sorted(SRC.glob("*.h")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for literal in re.findall(r'"((?:[^"\\]|\\.)*)"', text):
            for ch in literal:
                if "\u3400" <= ch <= "\u9fff" or ch in EXTRA:
                    chars.add(ch)
    return "".join(sorted(chars))


common = gb2312_all() + ui_literals()
ui_only = ui_literals()
print(f"GB2312 全量 {len(gb2312_all())} 字 · 界面文案 {len(ui_only)} 字")
if "-v" in sys.argv:
    print("界面文案字符：", ui_only)

out_dir = SRC / "fonts"
out_dir.mkdir(parents=True, exist_ok=True)
for size in FULL_SIZES + UI_ONLY_SIZES:
    if size in FULL_SIZES:
        symbols, fallback = common, []
    else:
        symbols, fallback = ui_only, []
    result = subprocess.run(
        ["npx", "--yes", FONT_CONV, "--font", str(FONT), "--size", str(size),
         "--bpp", "4", "--format", "lvgl", "--no-compress",
         "-r", "0x20-0x7F", "--symbols", symbols,
         *fallback,
         "-o", str(out_dir / f"cn_{size}.c")],
        capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-800:], result.stderr[-800:])
        sys.exit(f"cn_{size} 生成失败")
    print(f"✓ cn_{size}.c  ({(out_dir / f'cn_{size}.c').stat().st_size // 1024}K)")
