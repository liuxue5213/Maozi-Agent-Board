"""主题清单回归：界面上能点的每一套主题都必须真的能存下来。

背景（本次修复）：app.js 的 themePresets / galleryThemes 是界面上可直接
点击应用的配色。它们从不经过 validate_profile 之外的校验，而主题画廊里
7 套（极光/赛博霓虹/山峦黄昏/星空/像素花园/深海波浪/墨韵）的 card 填充对 bg
的对比度只有 1.13–1.34:1，低于 1.5:1 底线 —— 也就是说**主题画廊从来没存下来过**：
点一下只会弹「卡片与背景亮度过近」。

这里把「界面上可点的主题」全部拉出来跑一遍校验，防止再次漂移。
"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy.profile import blend888, panel_rgb888, validate_profile

ASSETS = Path(__file__).resolve().parent.parent / "workbuddy" / "web_assets"
JS = (ASSETS / "app.js").read_text(encoding="utf-8")
COLOR_MAP = json.loads((ASSETS / "color-map.json").read_text(encoding="utf-8"))

COLOR_KEYS = ("bg", "grad", "card", "card_edge", "accent", "text", "sub")

# 预设条目形如 '名字':{card_opa:NN,bg:'..',grad:'..',card:'..',...}
_PRESET_RE = re.compile(
    r"^  '([^']+)':\{(?:card_opa:(\d+),)?bg:'(\w+)',grad:'(\w+)',card:'(\w+)',"
    r"card_edge:'(\w+)',accent:'(\w+)',text:'(\w+)',sub:'(\w+)'\},?", re.M)
_GALLERY_RE = re.compile(
    r"^  (\w+):\{name:'([^']+)',card_opa:(\d+),colors:\{(bg:'\w+',grad:'\w+',"
    r"card:'(\w+)',card_edge:'\w+',accent:'\w+',text:'(\w+)',sub:'(\w+)')\}\},", re.M)


def ui_themes():
    """界面上可点的主题：设置页预设色板 + 画廊。

    每套都必须带 card_opa —— 校验算的是**合成后**的卡片填充色，不带不透明度
    会用默认 100% 去校验，把「半透明卡片边界看不见」这类问题整片掩盖掉。
    """
    out = {}
    for m in _PRESET_RE.finditer(JS):
        colors = dict(zip(COLOR_KEYS, m.groups()[2:]))
        colors["card_opa"] = int(m.group(2) or 100)
        out[m.group(1)] = colors
    for m in _GALLERY_RE.finditer(JS):
        name, opa, all_colors = m.group(2), int(m.group(3)), m.group(4)
        colors = dict(re.findall(r"(\w+):'(\w+)'", all_colors))
        colors["card_opa"] = opa
        out[name] = colors
    return out


def _luminance(hex_color):
    values = [int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
    return sum(a * b for a, b in zip(linear, (0.2126, 0.7152, 0.0722)))


class CompositedCardTests(unittest.TestCase):
    """半透明卡片的边界判定必须走合成色，而不是 card 原色。"""

    _PAGE = [{"id": "ai", "title": "T", "layout": "list", "widgets": ["ai_list"]}]

    def _theme(self, **over):
        base = {"bg": "0B1220", "grad": "0E1B33", "card": "2E4C76", "card_edge": "3E506E",
                "accent": "22D3EE", "text": "F3F7FF", "sub": "A8BDD9", "card_opa": 70}
        base.update(over)
        return base

    def test_raw_card_contrast_alone_is_not_enough(self):
        """card 原色对背景 1.8:1，但 60% 不透明度合成后掉到 1.3:1 —— 必须拒绝。"""
        theme = self._theme(card_opa=60)
        back = panel_rgb888(theme["bg"])
        fill = panel_rgb888(blend888(panel_rgb888(theme["card"]), back, 0.6))
        light, dark = sorted((_luminance(panel_rgb888(theme["card"])), _luminance(back)),
                             reverse=True)
        self.assertGreaterEqual((light + 0.05) / (dark + 0.05), 1.5, "前置条件：raw 达标")
        with self.assertRaises(ValueError) as ctx:
            validate_profile({"schema": 1, "version": 1, "pages": self._PAGE, "theme": theme})
        self.assertIn("合成", str(ctx.exception))

    def test_low_opacity_is_acceptable_when_card_is_bright_enough(self):
        """同样的 60% 不透明度，把 card 提亮就该通过。"""
        validate_profile({"schema": 1, "version": 1, "pages": self._PAGE,
                          "theme": self._theme(card="4A74AE", card_opa=60)})

    def test_panel_fill_is_reported_for_diagnostics(self):
        out = validate_profile({"schema": 1, "version": 1, "pages": self._PAGE,
                                "theme": self._theme()})["theme"]
        self.assertIn("panel_fill", out)
        self.assertEqual(set(out["panel_fill"]), {"bg", "grad"})


class UiThemeTests(unittest.TestCase):
    def test_every_clickable_theme_can_be_saved(self):
        themes = ui_themes()
        self.assertGreaterEqual(len(themes), 14, "主题清单解析异常，检查 app.js 结构")
        for name, colors in themes.items():
            with self.subTest(theme=name):
                try:
                    validate_profile({
                        "schema": 1, "version": 1,
                        "pages": [{"id": "ai", "title": "T", "layout": "list",
                                   "widgets": ["ai_list"]}],
                        "theme": dict(colors),
                    })
                except ValueError as exc:
                    self.fail(f"主题「{name}」在界面上可点但存不下来：{exc}")

    def test_card_fill_is_distinguishable_from_background(self):
        """锁住让整个画廊失效过的门槛——按**合成后**的填充色算。

        固件用 box() 的 bg_opa=card_opa 把 card 叠在渐变背景上，所以屏幕上
        真正的填充色是合成结果：不透明度越低越贴近背景。按 card 原色算会
        系统性高估（实测 15 套主题里 11 套的实际边界低于 1.5:1）。
        """
        for name, colors in ui_themes().items():
            with self.subTest(theme=name):
                alpha = colors["card_opa"] / 100.0
                for side in ("bg", "grad"):
                    back = panel_rgb888(colors[side])
                    fill = panel_rgb888(blend888(panel_rgb888(colors["card"]), back, alpha))
                    light, dark = sorted((_luminance(fill), _luminance(back)), reverse=True)
                    ratio = (light + 0.05) / (dark + 0.05)
                    self.assertGreaterEqual(
                        ratio, 1.5,
                        f"「{name}」@{colors['card_opa']}%：合成后的卡片填充对 {side} "
                        f"只有 {ratio:.2f}:1，实机上边界看不见")

    def test_reference_theme_is_the_calibrated_one(self):
        ref = COLOR_MAP["referenceTheme"]
        self.assertTrue(ref["confirmedByUser"])
        self.assertEqual(ref["colors"]["bg"], "060B16")
        validate_profile({
            "schema": 1, "version": 1,
            "pages": [{"id": "ai", "title": "T", "layout": "list", "widgets": ["ai_list"]}],
            "theme": dict(ref["colors"]),
        })

    def test_layout_presets_only_use_device_supported_widgets(self):
        """四种布局预设写进 draft.pages 的组件必须是固件能渲染的。

        以前这四种"布局"是一组只喂浏览器效果图的参数（tv.*），从不进 profile；
        现在它们就是页面组合，所以内容必须落在固件的组件白名单内。
        """
        supported = {"ai_list", "hw_cards", "clock", "environment", "reminder_list",
                     "today_calendar", "alarm", "weather", "countdown", "ai_cost"}
        block = JS[JS.index("const LAYOUT_PRESETS"):]
        block = block[:block.index("\n};")]

        keys = re.findall(r"\n  (\w+):\s*\{desc:", block)
        self.assertEqual(keys, ["focus", "timeline", "companion", "dashboard"])

        pages = re.findall(r"\{id:'\w+', title:'[^']*', layout:'(\w+)', widgets:\[([^\]]*)\]\}",
                           block)
        self.assertEqual(len(pages), 7, "预期 1+2+2+2 页")
        for layout, group in pages:
            self.assertIn(layout, ("list", "grid_2x2"), f"固件不认的布局：{layout}")
            for name in re.findall(r"'(\w+)'", group):
                self.assertIn(name, supported, f"预设使用了固件不支持的组件：{name}")
        # 预设里不应再出现只影响浏览器预览的私有参数
        for leftover in ("fv:", "cp:", "dv:", "crown:", "scene"):
            self.assertNotIn(leftover, block, f"预设里残留了 web 私有参数：{leftover}")


if __name__ == "__main__":
    unittest.main()
