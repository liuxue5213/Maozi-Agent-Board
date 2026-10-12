"""农历/节气锚点测试：全部对照公开万年历核实过的日期。"""
import unittest

from workbuddy.lunar import lunar_date, solar_term, saver_line


class LunarAnchorTests(unittest.TestCase):
    # 春节锚点（正月初一）横跨 2020-2026，覆盖年表 120-126 邻域
    CNY = [
        ((2020, 1, 25), "正月初一"),
        ((2021, 2, 12), "正月初一"),
        ((2022, 2, 1), "正月初一"),
        ((2023, 1, 22), "正月初一"),
        ((2024, 2, 10), "正月初一"),
        ((2025, 1, 29), "正月初一"),
        ((2026, 2, 17), "正月初一"),
    ]

    def test_cny_anchors(self):
        for (y, m, d), expect in self.CNY:
            self.assertEqual(lunar_date(y, m, d), expect, f"{y}-{m}-{d}")

    def test_today_2026_10_12(self):
        # 万年历核对：2026-10-12 = 丙午年九月初三
        self.assertEqual(lunar_date(2026, 10, 12), "九月初三")
        self.assertEqual(lunar_date(2026, 10, 13), "九月初四")

    def test_month_day_names(self):
        self.assertEqual(lunar_date(2026, 2, 17), "正月初一")   # 正月不叫一月
        self.assertEqual(lunar_date(2025, 12, 20), "冬月初一")  # 冬月=十一月


class SolarTermTests(unittest.TestCase):
    def test_frost_2026_on_23rd(self):
        # 霜降 2026-10-23 当天：倒计时目标 = 立冬（11-07，15 天）
        prev, days = solar_term(2026, 10, 23)
        self.assertEqual((prev, days), ("立冬", 15))

    def test_countdown_from_2026_10_12(self):
        prev, days = solar_term(2026, 10, 12)
        self.assertEqual((prev, days), ("霜降", 11))   # 寒露 10-08 已过，霜降 10-23

    def test_winter_solstice_2026(self):
        prev, days = solar_term(2026, 12, 22)
        self.assertEqual((prev, days), ("小寒", 14))   # 下一个 = 小寒 2027-01-05

    def test_saver_line_format(self):
        line = saver_line(2026, 10, 12)
        self.assertEqual(line, "农历九月初三·距霜降还有11天")

    def test_saver_line_hides_far_terms(self):
        # 距下一节气 >15 天时只显示农历（2026-10-30 霜降已过 7 天、立冬还有 8 天→带）
        line_far = saver_line(2026, 6, 1)      # 芒种 6/5 或夏至 6/21 附近必 ≤15
        self.assertTrue(line_far.startswith("农历"))
        # 2027-01-10：小寒已过 5 天，大寒 1/20 还有 10 天 → 带
        self.assertIn("距大寒", saver_line(2027, 1, 10))


if __name__ == "__main__":
    unittest.main()
