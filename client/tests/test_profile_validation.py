"""validate_profile 全规则回归：布局/插槽/文字/星期/应用/主题/预算。"""
import unittest

from workbuddy.profile import default_profile, validate_profile


def base(**overrides):
    """合法默认档案，按需覆盖字段。"""
    profile = default_profile()
    profile.update(overrides)
    return profile


class PageRuleTests(unittest.TestCase):
    def test_default_profile_passes(self):
        out = validate_profile(default_profile())
        self.assertEqual(len(out["pages"]), 3)

    def test_too_many_pages_rejected(self):
        pages = [{"id": f"p{i}", "title": "页", "layout": "grid_2x2",
                  "widgets": ["clock"]} for i in range(7)]
        with self.assertRaisesRegex(ValueError, "页面数量"):
            validate_profile(base(pages=pages))

    def test_duplicate_page_id_rejected(self):
        pages = [{"id": "same", "title": "A", "layout": "grid_2x2", "widgets": ["clock"]},
                 {"id": "same", "title": "B", "layout": "grid_2x2", "widgets": ["alarm"]}]
        with self.assertRaisesRegex(ValueError, "唯一"):
            validate_profile(base(pages=pages))

    def test_bad_layout_rejected(self):
        pages = [{"id": "p", "title": "页", "layout": "grid_3x3", "widgets": ["clock"]}]
        with self.assertRaisesRegex(ValueError, "布局"):
            validate_profile(base(pages=pages))

    def test_unknown_widget_rejected(self):
        pages = [{"id": "p", "title": "页", "layout": "grid_2x2", "widgets": ["news_feed"]}]
        with self.assertRaisesRegex(ValueError, "组件"):
            validate_profile(base(pages=pages))

    def test_duplicate_widget_rejected(self):
        pages = [{"id": "p", "title": "页", "layout": "grid_2x2",
                  "widgets": ["clock", "clock"]}]
        with self.assertRaisesRegex(ValueError, "重复"):
            validate_profile(base(pages=pages))

    def test_full_page_widget_must_be_alone(self):
        pages = [{"id": "p", "title": "页", "layout": "grid_2x2",
                  "widgets": ["hw_cards", "clock"]}]
        with self.assertRaisesRegex(ValueError, "独占"):
            validate_profile(base(pages=pages))

    def test_slot_overflow_rejected(self):
        pages = [{"id": "p", "title": "页", "layout": "grid_2x2",
                  "widgets": ["reminder_list", "today_calendar", "clock"]}]  # 2+2+1=5
        with self.assertRaisesRegex(ValueError, "插槽"):
            validate_profile(base(pages=pages))

    def test_grid_row_alignment_rejected(self):
        # wide 组件(2 槽)落在奇数槽位会横跨到下一行 -> 拒绝
        pages = [{"id": "p", "title": "页", "layout": "grid_2x2",
                  "widgets": ["clock", "today_calendar", "alarm"]}]
        with self.assertRaises(ValueError):
            validate_profile(base(pages=pages))

    def test_title_too_long_rejected(self):
        pages = [{"id": "p", "title": "非" * 13, "layout": "grid_2x2", "widgets": ["clock"]}]
        with self.assertRaisesRegex(ValueError, "字节"):
            validate_profile(base(pages=pages))


class ScheduleRuleTests(unittest.TestCase):
    def test_reminder_bad_time_rejected(self):
        profile = base(reminders=[{"id": "r1", "time": "9:5", "text": "开会",
                                   "weekdays": [1], "enabled": True}])
        with self.assertRaisesRegex(ValueError, "时间"):
            validate_profile(profile)

    def test_reminder_bad_weekday_rejected(self):
        profile = base(reminders=[{"id": "r1", "time": "09:30", "text": "开会",
                                   "weekdays": [8], "enabled": True}])
        with self.assertRaisesRegex(ValueError, "星期"):
            validate_profile(profile)

    def test_reminder_unsupported_char_rejected(self):
        # 韩文不在 GB2312（注意：日文假名在 GB2312 符号区，不能当反例）
        profile = base(reminders=[{"id": "r1", "time": "09:30", "text": "한국회의",
                                   "weekdays": [1], "enabled": True}])
        with self.assertRaisesRegex(ValueError, "字库"):
            validate_profile(profile)

    def test_calendar_bad_date_rejected(self):
        profile = base(calendar=[{"id": "e1", "date": "2026-13-40", "time": "10:00",
                                  "title": "评审", "enabled": True}])
        with self.assertRaisesRegex(ValueError, "日期"):
            validate_profile(profile)

    def test_valid_schedule_passes(self):
        profile = base(
            reminders=[{"id": "r1", "time": "09:30", "text": "站会",
                        "weekdays": [1, 2, 3, 4, 5], "enabled": True}],
            calendar=[{"id": "e1", "date": "2026-10-09", "time": "14:00",
                       "title": "方案评审", "enabled": True}],
            alarms=[{"id": "a1", "time": "18:00", "label": "休息",
                     "weekdays": [6, 7], "enabled": True}])
        out = validate_profile(profile)
        self.assertEqual(len(out["reminders"]), 1)
        self.assertEqual(out["calendar"][0]["date"], "2026-10-09")


class AppRuleTests(unittest.TestCase):
    def test_app_bad_id_rejected(self):
        profile = base(apps=[{"id": "含中文", "name": "浏览器", "command": "open -a Safari"}])
        with self.assertRaisesRegex(ValueError, "应用 ID"):
            validate_profile(profile)

    def test_app_empty_command_rejected(self):
        profile = base(apps=[{"id": "safari", "name": "浏览器", "command": "  "}])
        with self.assertRaisesRegex(ValueError, "启动命令"):
            validate_profile(profile)

    def test_app_newline_in_command_rejected(self):
        profile = base(apps=[{"id": "evil", "name": "X", "command": "open -a Safari\nrm -rf /"}])
        with self.assertRaisesRegex(ValueError, "换行"):
            validate_profile(profile)

    def test_too_many_apps_rejected(self):
        apps = [{"id": f"a{i}", "name": f"应用{i}", "command": f"open {i}"} for i in range(13)]
        with self.assertRaisesRegex(ValueError, "12 个"):
            validate_profile(base(apps=apps))


class ThemeRuleTests(unittest.TestCase):
    def test_bad_color_rejected(self):
        profile = base(theme={"bg": "ZZZZZZ", "card": "141E32", "accent": "22D3EE",
                              "text": "F3F7FF", "sub": "8CA3C7", "bright": 90})
        with self.assertRaisesRegex(ValueError, "主题颜色"):
            validate_profile(profile)

    def test_low_text_contrast_rejected(self):
        # 深灰文字配深蓝背景：4.5:1 不达标
        profile = base(theme={"bg": "0B1220", "card": "141E32", "accent": "22D3EE",
                              "text": "333344", "sub": "8CA3C7", "bright": 90})
        with self.assertRaisesRegex(ValueError, "对比度"):
            validate_profile(profile)

    def test_brightness_out_of_range_rejected(self):
        profile = base(theme={"bg": "0B1220", "card": "141E32", "accent": "22D3EE",
                              "text": "F3F7FF", "sub": "8CA3C7", "bright": 101})
        with self.assertRaisesRegex(ValueError, "亮度"):
            validate_profile(profile)

    def test_new_card_color_theme_passes(self):
        # 第1批修复后的新卡片色 274064 + 正常文字必须通过
        profile = base(theme={"bg": "0B1220", "grad": "0E1B33", "card": "274064",
                              "accent": "22D3EE", "text": "F3F7FF", "sub": "A8BDD9",
                              "bright": 90})
        out = validate_profile(profile)
        self.assertEqual(out["theme"]["card"], "274064")
        self.assertEqual(out["theme"]["grad"], "0E1B33")


class BudgetTests(unittest.TestCase):
    def test_oversize_profile_rejected(self):
        # 各类别顶格（全部通过单条校验）堆出 >8KB 预算拒绝
        calendar = [{"id": f"e{i:02d}", "date": "2026-10-09", "time": "10:00",
                     "title": "事" * 16, "enabled": True} for i in range(50)]
        reminders = [{"id": f"r{i:02d}", "time": "09:30", "text": "事" * 16,
                      "weekdays": [1, 2, 3, 4, 5], "enabled": True} for i in range(20)]
        alarms = [{"id": f"a{i:02d}", "time": "18:00", "label": "事" * 16,
                   "weekdays": [1, 2, 3, 4, 5, 6, 7], "enabled": True} for i in range(16)]
        apps = [{"id": f"app{i:02d}", "name": "应" * 5, "command": "x" * 160}
                for i in range(12)]
        profile = base(calendar=calendar, reminders=reminders,
                       alarms=alarms, apps=apps)
        try:
            validate_profile(profile)
        except ValueError as exc:
            self.assertIn("8 KB", str(exc))
        else:
            self.fail("超预算档案应被拒绝")


if __name__ == "__main__":
    unittest.main()
