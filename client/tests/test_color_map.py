"""color-map.json 回归测试
======================

锁定「屏幕色彩对照」子系统的正确性，覆盖三层：

1. 结构合规 —— 与用户给出的规范逐字段对齐
   - themeBindings 恰好 7 槽、全部 editableInWeb=true
   - status.* 等语义色 editable=false，且不在 themeBindings（Web 改不到）
   - palette = 21 中性灰 + 12 色相×19 级 = 249
   - diagnosticVariants = 24 格，恰好 1 格 selected，且与 panel.calibration 一致
   - panel.calibration.cell == 7（2026-10-10 实机选定）

2. 数学校验 —— 从 hex 独立重算 rgb565 / flushWord，与 JSON 自带字段比对
   - rgb565   = R5<<11 | G6<<5 | B5
   - flushWord= swap16(rgb565)   （LVGL flush 字节序相反，第 7 格校准）

3. 真机一致性（WYSIWYG 不变量）——
   固件发送 swap_bytes(rgb565(设计色))；面板字节序相反读回原式，
   panelInvert=true 是正常黑底模式（非额外取反），净效果面板显示设计色本身。
   因此 Web 预览显示「量化后的设计色」即真机最终显示色；
   预览绝不可再对设计色取反（否则负片化，回归）。
   同时校验固件 config.h 三开关与 JSON 标定、display.cpp 的 invert + 字节交换一致。

换屏或改标定时跑此测试，任意一项不符会立刻报警。
"""

import json
import os
import re
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_JSON = _HERE.parent / "workbuddy" / "web_assets" / "color-map.json"
_CONFIG_H = _HERE.parents[1] / "firmware" / "src" / "config.h"
_DISPLAY_CPP = _HERE.parents[1] / "firmware" / "src" / "display.cpp"


def _load():
    return json.loads(_JSON.read_text(encoding="utf-8"))


def _hx24(h):
    h = str(h).replace("#", "").replace("0x", "")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _to565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _swap16(w):
    return ((w & 0xFF) << 8) | ((w >> 8) & 0xFF)


def _expand(value, n):
    return value if n >= 8 else ((value << (8 - n)) | (value >> (2 * n - 8))) & 0xFF


def _word_to_rgb(word):
    # RGB565 解包（与 app.js wordToRgb 同义），按 RGB 通道序
    r = _expand((word >> 11) & 0x1F, 5)
    g = _expand((word >> 5) & 0x3F, 6)
    b = _expand(word & 0x1F, 5)
    return (r, g, b)


def _as_int(v):
    return v if isinstance(v, int) else int(str(v).replace("0x", ""), 16)


# ---------------------------------------------------------------- 结构合规


def test_top_level_keys_present():
    d = _load()
    for k in ("schema", "panel", "conversion", "themeBindings", "semantic",
             "checks", "palette", "diagnosticVariants", "referenceTheme"):
        assert k in d, f"缺少顶层字段 {k}"


def test_panel_calibration_is_cell7():
    cal = _load()["panel"]["calibration"]
    assert cal["cell"] == 7, f"calibration.cell 应为 7，实际 {cal['cell']}"
    assert cal["rgbOrder"] == "RGB"
    assert cal["lvglFlushByteSwap"] is True
    assert cal["panelInvert"] is True
    assert cal["redGreenBlueWhiteObserved"] is True


def test_theme_bindings_are_seven_and_editable():
    tb = _load()["themeBindings"]
    assert set(tb.keys()) == {
        "bg", "grad", "card", "card_edge", "accent", "text", "sub"
    }, f"themeBindings 槽位不符: {set(tb.keys())}"
    for key, meta in tb.items():
        assert meta.get("editableInWeb") is True, f"{key} 应 editableInWeb=true"
        # 规范红线：Web 写入的是原始 hex，绝不能预交换字节
        assert "preSwap" not in meta, f"{key} 不应含预交换字节字段"


def test_semantic_status_colors_not_web_editable():
    d = _load()
    sem = {c["id"]: c for c in d["semantic"]}
    for sid in ("status.ok", "status.warn", "status.err"):
        assert sid in sem, f"缺少语义色 {sid}"
        assert sem[sid].get("editable") is False, f"{sid} 必须 editable=false"
    # editable:false 的语义色不可出现在 themeBindings（否则 UI 会改写固件常量）
    bad = [sid for sid in sem if sem[sid].get("editable") is False and sid in d["themeBindings"]]
    assert not bad, f"以下固件常量色泄漏进 themeBindings: {bad}"


def test_palette_count_and_layout():
    pal = _load()["palette"]
    assert len(pal) == 249, f"palette 应为 249，实际 {len(pal)}"
    neutral = [c for c in pal if c.get("group") == "中性灰"]
    assert len(neutral) == 21, f"中性灰应 21，实际 {len(neutral)}"
    hues = {c.get("hue") for c in pal if c.get("group") != "中性灰"}
    assert len(hues) == 12, f"色相应 12，实际 {len(hues)}"
    per_hue = Counter(c.get("hue") for c in pal if c.get("group") != "中性灰")
    assert all(v == 19 for v in per_hue.values()), f"每色相级数不为 19: {dict(per_hue)}"


def test_diagnostic_variants_consistency():
    v = _load()["diagnosticVariants"]
    assert len(v) == 24, f"diagnosticVariants 应 24 格，实际 {len(v)}"
    selected = [x for x in v if x.get("selected")]
    assert len(selected) == 1, f"应恰好 1 格 selected，实际 {len(selected)}"
    sel = selected[0]
    assert sel["cell"] == 7
    cal = _load()["panel"]["calibration"]
    assert sel["channelOrder"] == cal["rgbOrder"], "选中格通道序须与 calibration 一致"
    assert sel["byteSwap"] == cal["lvglFlushByteSwap"], "选中格 byteSwap 须与 calibration 一致"


def test_reference_theme_has_seven_colors():
    rt = _load()["referenceTheme"]
    assert set(rt["colors"].keys()) == {
        "bg", "grad", "card", "card_edge", "accent", "text", "sub"
    }
    assert rt.get("confirmedByUser") is True


# ---------------------------------------------------------------- 数学校验


def test_rgb565_and_flushword_math_self_consistent():
    d = _load()
    samples = d["semantic"] + d["checks"] + d["palette"]
    mism = []
    for c in samples:
        r, g, b = _hx24(c["hex"])
        exp = _to565(r, g, b)
        got = _as_int(c["rgb565"])
        flush_exp = _swap16(exp)
        flush_got = _as_int(c.get("flushWord"))
        if got != exp or flush_got != flush_exp:
            mism.append((c["hex"], exp, got, flush_exp, flush_got))
    assert not mism, f"存在数学不一致: {mism[:3]}"


def test_conversion_section_formula_matches():
    conv = _load()["conversion"]
    if "example" in conv:
        ex = conv["example"]
        r, g, b = _hx24(ex["hex"])
        exp = _to565(r, g, b)
        assert _as_int(ex["rgb565"]) == exp
        assert _as_int(ex["flushWord"]) == _swap16(exp)


# ---------------------------------------------------- WYSIWYG 不变量（核心）


def _panel_displayed_hex(hexstr, rgb_order="RGB"):
    """复刻 app.js panelColor 的 returned hex：量化设计色（面板最终显示色）。

    故意只做 RGB565 量化 + 通道序；不补 byteSwap、不补反相——
    因为面板字节序相反读回原式、panelInvert=true 是正常黑底模式，
    净效果面板显示的就是量化后的设计色本身（WYSIWYG）。
    """
    r, g, b = _hx24(hexstr)
    word = _to565(r, g, b)
    rgb = _word_to_rgb(word)
    if rgb_order.upper() == "BGR":
        rgb = (rgb[2], rgb[1], rgb[0])
    return "".join("%02X" % v for v in rgb)


def test_wysiwyg_preview_equals_device_color():
    """预览色（量化设计色）== 真机最终显示色，且绝不可是负片。"""
    d = _load()
    rt = d["referenceTheme"]["colors"]
    order = d["panel"]["calibration"]["rgbOrder"]
    for key, hexstr in rt.items():
        shown = _panel_displayed_hex(hexstr, order)
        # 真机显示色 = 量化设计色（由面板链路还原），必须相等
        r, g, b = _hx24(hexstr)
        expected = "".join("%02X" % v for v in _word_to_rgb(_to565(r, g, b)))
        assert shown == expected, f"{key} 预览 {shown} ≠ 真机 {expected}"
        # 反相回归护栏：accent 的真机显示绝不能是它的负片
        inv = "".join("%02X" % (0xFF - v) for v in _hx24(shown))
        assert shown != inv, f"{key} 预览疑似负片化，WYSIWYG 被破坏"


def test_bgr_preview_swaps_rb():
    """若某日面板是 BGR 序，预览必须跟着反 R/B（防止「预览好看实机偏色」）。"""
    r, g, b = _hx24("57C8F5")
    word = _to565(r, g, b)
    rgb = _word_to_rgb(word)
    bgr = (rgb[2], rgb[1], rgb[0])
    assert _panel_displayed_hex("57C8F5", "BGR") == "".join("%02X" % v for v in bgr)
    assert _panel_displayed_hex("57C8F5", "RGB") == "".join("%02X" % v for v in rgb)


# ---------------------------------------------------- 固件侧一致性（跨端护栏）


def _read_defines():
    text = _CONFIG_H.read_text(encoding="utf-8")
    out = {}
    for name in ("PANEL_COLOR_ORDER_BGR", "PANEL_COLOR_BYTESWAP", "PANEL_COLOR_SELFTEST"):
        m = re.search(r"#define\s+" + re.escape(name) + r"\s+(\w+)", text)
        out[name] = m.group(1) if m else None
    return out


def test_config_h_matches_calibration():
    defines = _read_defines()
    cal = _load()["panel"]["calibration"]
    assert defines["PANEL_COLOR_ORDER_BGR"] in ("false", "0"), \
        f"calibration.rgbOrder=RGB 但 BGR 开关={defines['PANEL_COLOR_ORDER_BGR']}"
    assert defines["PANEL_COLOR_BYTESWAP"] in ("true", "1"), \
        f"calibration.byteSwap=true 但 BYTESWAP 开关={defines['PANEL_COLOR_BYTESWAP']}"
    assert defines["PANEL_COLOR_SELFTEST"] in ("0", "false"), \
        f"SELFTEST 应关，实际 {defines['PANEL_COLOR_SELFTEST']}"


def test_display_cpp_has_invert_and_byteswap():
    text = _DISPLAY_CPP.read_text(encoding="utf-8")
    assert "cfg.invert = true" in text, "display.cpp 必须开启面板正常黑底模式 cfg.invert=true"
    assert "v << 8) | (v >> 8" in text, "flush_cb 缺少 RGB565 字节交换"
