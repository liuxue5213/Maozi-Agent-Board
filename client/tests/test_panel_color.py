"""屏幕色彩管线：请求色 → RGB565 量化 → 上屏值。

背景：屏幕是 RGB565。Web 里的 #RRGGBB 不是屏幕最终显示的颜色——每个通道
先被截到 5/6/5 位，深色主题里这个偏移肉眼可辨（实测 060B16 → 000810）。

因此：
  1. 对比度校验必须按量化后的值核算（否则"请求色过 AA、实机不过"）；
  2. 颜色映射文件 color-map.json 的 rgb565 字段必须与同一套位宽一致；
  3. 预览画的必须是量化后的颜色。
"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy.profile import (DEFAULT_PROFILE, PANEL_BITS, panel_rgb888,
                               validate_profile)

ASSETS = Path(__file__).resolve().parent.parent / "workbuddy" / "web_assets"
COLOR_MAP = json.loads((ASSETS / "color-map.json").read_text(encoding="utf-8"))
JS = (ASSETS / "app.js").read_text(encoding="utf-8")


def word_to_888(word: int) -> str:
    br, bg, bb = PANEL_BITS
    def expand(value, bits):
        return value if bits >= 8 else ((value << (8 - bits)) | (value >> (2 * bits - 8))) & 0xFF
    return "%02X%02X%02X" % (expand((word >> (bg + bb)) & ((1 << br) - 1), br),
                             expand((word >> bb) & ((1 << bg) - 1), bg),
                             expand(word & ((1 << bb) - 1), bb))


class PanelQuantisationTests(unittest.TestCase):
    def test_known_values(self):
        """量化 + 位复制还原的样例，人肉可核对。"""
        self.assertEqual(panel_rgb888("000000"), "000000")
        self.assertEqual(panel_rgb888("FFFFFF"), "FFFFFF")
        # 原色不因量化而变（5/6/5 各自能表示满值）
        self.assertEqual(panel_rgb888("FF0000"), "FF0000")
        self.assertEqual(panel_rgb888("00FF00"), "00FF00")
        self.assertEqual(panel_rgb888("0000FF"), "0000FF")
        # 实机主题底色：060B16 上屏后是 000810，两路各偏 6
        self.assertEqual(panel_rgb888("060B16"), "000810")

    def test_matches_the_published_mapping_file(self):
        """映射文件的 rgb565 字段与 panel_rgb888 必须互为逆运算。"""
        checked = 0
        for color in COLOR_MAP["semantic"] + COLOR_MAP["checks"] + COLOR_MAP["palette"]:
            word = int(color["rgb565"], 16)
            self.assertEqual(
                word_to_888(word), panel_rgb888(color["hex"]),
                f"{color['id']} 的 hex 与 rgb565 不自洽")
            checked += 1
        self.assertGreater(checked, 250)

    def test_flush_word_is_byte_swap_of_rgb565(self):
        for color in COLOR_MAP["checks"]:
            word = int(color["rgb565"], 16)
            self.assertEqual(color["flushWord"], f"{((word & 0xFF) << 8) | (word >> 8):04X}")

    def test_calibration_is_declared_once(self):
        """位宽在 profile.py 与映射文件里各写一次，必须一致。"""
        br, bg, bb = PANEL_BITS
        self.assertEqual(COLOR_MAP["panel"]["pixelFormat"], f"RGB{br}{bg}{bb}")
        self.assertEqual(COLOR_MAP["panel"]["calibration"]["cell"], 7)
        self.assertTrue(COLOR_MAP["panel"]["calibration"]["lvglFlushByteSwap"])

    def test_web_reads_the_same_bit_layout(self):
        """app.js 的 panelBits() 必须解析 pixelFormat，而不是写死 5/6/5。"""
        self.assertIn("panelBits()", JS)
        self.assertRegex(JS, r"panelSpec\(\)\.pixelFormat")
        # 量化与位复制展开的算法都要在（否则预览会显示未量化的色）
        self.assertIn("quantizeWord", JS)
        self.assertIn("wordToRgb", JS)
        self.assertIn("panelHex(", JS)


class QuantisedContrastTests(unittest.TestCase):
    """校验必须按上屏值算：请求色过线、量化后不过的组合要被拦住。"""

    BASE = {"bg": "040404", "grad": "040404", "card": "28384E", "card_edge": "3C5070",
            "accent": "22D3EE", "text": "767676", "sub": "A6B6C9", "bright": 90}

    def _profile(self, theme):
        return {"schema": 1, "version": 2, "pages": DEFAULT_PROFILE["pages"], "theme": theme}

    def test_text_that_only_passes_before_quantisation_is_rejected(self):
        valid = dict(self.BASE, text="777777", bg="050505")
        with self.assertRaises(ValueError) as ctx:
            validate_profile(self._profile(valid))
        self.assertIn("RGB565", str(ctx.exception))

    def test_profile_carries_the_on_screen_palette(self):
        out = validate_profile(self._profile(dict(self.BASE, text="FFFFFF")))["theme"]
        self.assertIn("panel", out)
        for key in ("bg", "grad", "card", "card_edge", "accent", "text", "sub"):
            self.assertEqual(out["panel"][key], panel_rgb888(out[key]))
            self.assertRegex(out["panel"][key], r"^[0-9A-F]{6}$")

    def test_panel_key_round_trips_without_leaking_into_validation(self):
        """导出再导入（panel 字段跟着回来）不应改变配置语义。"""
        once = validate_profile(self._profile(dict(self.BASE, text="FFFFFF")))
        twice = validate_profile(once)
        self.assertEqual(once, twice)


class PreviewUsesThePipelineTests(unittest.TestCase):
    """预览里的颜色必须经过管线，不能直接拿主题 hex 画。"""

    def test_device_theme_tokens_are_quantised(self):
        body = JS[JS.index("function applyDeviceTheme"):]
        body = body[:body.index("\n}")]
        self.assertIn("panelHex(", body,
                      "applyDeviceTheme 必须把主题色过 panelHex，否则预览比实机亮一档")

    def test_saver_preview_uses_mapping_file_semantics(self):
        """息屏预览的固定色来自映射文件，不是写死在 JS 里。"""
        for color_id in ("saver.black", "saver.frame", "saver.edge",
                         "saver.quote", "saver.time", "saver.dim", "saver.label"):
            self.assertIn(f"devSemantic('{color_id}')", JS, f"预览没用 {color_id}")
            self.assertIsNotNone(self._semantic(color_id), f"映射文件缺少 {color_id}")

    def test_robot_colors_come_from_mapping_file(self):
        """宠物三形象共用一套状态色（出错红/完成绿/正常白），
        身体底色按形象走（robot.body / cat / chick 自带色）。"""
        for color_id in ("status.err", "status.ok"):
            self.assertIn(f"devSemantic('{color_id}')", JS)
            self.assertIsNotNone(self._semantic(color_id))
        self.assertIn("devPet(", JS, "预览宠物绘制必须走 devPet（与固件同几何）")

    @staticmethod
    def _semantic(color_id):
        return next((c for c in COLOR_MAP["semantic"] if c["id"] == color_id), None)


if __name__ == "__main__":
    unittest.main()
