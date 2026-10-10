#!/usr/bin/env python3
"""Regenerate color-map.json 的派生字段，并补齐固件语义色。

单一来源是「hex」——rgb565 与 flushWord 都是它按 ST7796 的 RGB565 像素格式
推导出来的。手改 hex 后跑一次本脚本，两个派生字段就不会漂。

  python3 tools/gen_color_map.py            # 校验并原地重写
  python3 tools/gen_color_map.py --check    # 只校验，不写盘（CI / 测试用）

换屏或换控制器时改 panel.pixelFormat / panel.calibration，再跑本脚本。
注意：本脚本只做颜色数学，不会也不该修驱动层的通道顺序错误——
PANEL_COLOR_ORDER_BGR / PANEL_COLOR_BYTESWAP / cfg.invert 的测定见
docs/COLOR_MAP.md。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MAP_PATH = Path(__file__).resolve().parents[1] / "workbuddy" / "web_assets" / "color-map.json"

# 固件里写死的颜色（firmware/src/config.h 与 ui.cpp 的字面值）。
# 这些颜色不受 profile 主题影响，但屏幕显示它们同样要过 RGB565，
# 所以必须在映射文件里有条目——预览取的就是这里的值。
REQUIRED_SEMANTIC = {
    "saver.time":  ("saver_time",  "固件固定色", "22D3EE", "息屏「方寸仪表」时间，等同默认主题强调色"),
    "saver.dim":   ("saver_dim",   "固件固定色", "6B7A8F", "息屏日期/温湿度等次要文字"),
    "saver.label": ("saver_label", "固件固定色", "3A4A5F", "息屏左上角样式名，最低对比装饰文字"),
}

BITS_RE = re.compile(r"^RGB(\d)(\d)(\d)$")


def bits(pixel_format: str) -> tuple[int, int, int]:
    match = BITS_RE.match(pixel_format)
    if not match:
        raise SystemExit(f"无法解析像素格式：{pixel_format!r}（需要形如 RGB565）")
    return tuple(int(g) for g in match.groups())  # type: ignore[return-value]


def word(rgb_hex: str, bit_widths: tuple[int, int, int]) -> int:
    r, g, b = (int(rgb_hex[i:i + 2], 16) for i in (0, 2, 4))
    br, bg, bb = bit_widths
    return ((r >> (8 - br)) << (bg + bb)) | ((g >> (8 - bg)) << bb) | (b >> (8 - bb))


def swap_bytes(value: int) -> int:
    return ((value & 0xFF) << 8) | (value >> 8)


def derive(color: dict, bit_widths: tuple[int, int, int]) -> None:
    rgb = str(color["hex"]).upper().lstrip("#")
    if not re.fullmatch(r"[0-9A-F]{6}", rgb):
        raise SystemExit(f"颜色 {color.get('id') or color.get('name')} 的 hex 无效：{color['hex']!r}")
    color["hex"] = rgb
    raw = word(rgb, bit_widths)
    color["rgb565"] = f"{raw:04X}"
    color["flushWord"] = f"{swap_bytes(raw):04X}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只校验，不写盘")
    args = parser.parse_args()

    mapping = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    bit_widths = bits(mapping["panel"]["pixelFormat"])

    groups = [mapping["semantic"], mapping["checks"], mapping["palette"]]
    for group in groups:
        for color in group:
            derive(color, bit_widths)

    # 补齐固件里写死、但映射文件还缺的语义色（幂等：已存在则只校验 hex 一致）
    by_id = {c["id"]: c for c in mapping["semantic"]}
    added = []
    for color_id, (name, group, rgb, purpose) in REQUIRED_SEMANTIC.items():
        existing = by_id.get(color_id)
        if existing is None:
            entry = {"id": color_id, "name": name, "group": group, "hex": rgb,
                     "purpose": purpose, "editable": False}
            derive(entry, bit_widths)
            mapping["semantic"].append(entry)
            added.append(color_id)
        elif existing["hex"].upper() != rgb:
            raise SystemExit(
                f"{color_id} 的 hex 是 {existing['hex']}，固件用的是 {rgb}；"
                "改固件常量或改这里，不要放着不一致")

    if added:
        print("补齐语义色：" + "、".join(added))

    payload = json.dumps(mapping, ensure_ascii=False, indent=1)
    current = MAP_PATH.read_text(encoding="utf-8")
    if args.check:
        if payload != current and not added:
            print("color-map.json 的派生字段与 hex 不一致，请运行 tools/gen_color_map.py")
            return 1
        print("color-map.json 一致性校验通过")
        return 0
    MAP_PATH.write_text(payload, encoding="utf-8")
    print(f"已写入 {MAP_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
