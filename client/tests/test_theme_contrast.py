"""Regressions for theme contrast validation.

背景：深色主题下 card 与 bg 亮度过近时，卡片边界在实机上等于不存在
（旧默认 card #141E32 对 bg #0B1220 仅 1.12:1，底部渐变处 1.03:1）。
这些用例锁住 validate_profile 的三条对比度规则，防止回退。
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy.profile import DEFAULT_PROFILE, default_profile, validate_profile


def _profile(theme):
    return {"schema": 1, "version": 2, "pages": DEFAULT_PROFILE["pages"], "theme": theme}


# 夹具的 card 从 28384E 提到 2E4C76：校验改成按「合成后的卡片填充色」并同时看
# 渐变两端后，28384E 在渐变**亮端**（卡片下沿所在处）只有 1.49:1，低于 1.5 底线。
# 这不是夹具写错，而是旧规则只看 bg 端、把渐变下方的边界漏掉了。
BASE = {"bg": "0B1220", "grad": "0E1B33", "card": "2E4C76", "card_edge": "3C5070",
        "accent": "22D3EE", "text": "F3F7FF", "sub": "A8BDD9", "bright": 90}


class ThemeContrastTests(unittest.TestCase):
    def test_default_theme_is_self_consistent(self):
        theme = validate_profile(default_profile())["theme"]
        self.assertEqual(theme["card"], "28384E")
        self.assertEqual(theme["card_edge"], "3C5070")

    def test_legacy_low_contrast_card_is_rejected(self):
        """旧默认卡片色必须被拦住——它就是"边界看不见"的根因。"""
        theme = dict(BASE, card="141E32")
        with self.assertRaises(ValueError) as ctx:
            validate_profile(_profile(theme))
        self.assertIn("卡片与背景", str(ctx.exception))

    def test_unreadable_secondary_text_is_rejected(self):
        theme = dict(BASE, sub="1A2436")
        with self.assertRaises(ValueError) as ctx:
            validate_profile(_profile(theme))
        self.assertIn("次要文字", str(ctx.exception))

    def test_unreadable_primary_text_is_rejected(self):
        theme = dict(BASE, text="1E2A3A")
        with self.assertRaises(ValueError) as ctx:
            validate_profile(_profile(theme))
        self.assertIn("对比度不足", str(ctx.exception))

    def test_missing_card_edge_backfills_default(self):
        """旧用户档案没有 card_edge 键，应补默认值而不是回退成 bg（同色即无边界）。"""
        theme = {k: v for k, v in BASE.items() if k != "card_edge"}
        out = validate_profile(_profile(theme))["theme"]
        self.assertEqual(out["card_edge"], "3C5070")

    def test_malformed_card_edge_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_profile(_profile(dict(BASE, card_edge="ZZZ")))

    def test_card_edge_may_equal_card(self):
        """描边与填充同色应被接受（用户主动选择无描边），不能误报。"""
        out = validate_profile(_profile(dict(BASE, card_edge="2E4C76")))["theme"]
        self.assertEqual(out["card_edge"], "2E4C76")


if __name__ == "__main__":
    unittest.main()