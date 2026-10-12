"""久坐提醒闸门逻辑：注入空闲秒数，验证阈值/一轮一次/复位/凌晨安静。"""
import time
import unittest
from types import SimpleNamespace

import workbuddy.app as appmod


def _make(break_min, idle, hour=15):
    events, sent = [], []
    app = appmod.App.__new__(appmod.App)   # 跳过 __init__ 的重装配
    app.profile = {"pet": {"break_min": break_min}}
    app.cfg = SimpleNamespace(push_quiet="", push_url="")
    app.health = SimpleNamespace(
        event=lambda *a, **kw: events.append({**kw, "kind": a[0] if a else None}))
    app._input_idle_secs = lambda: idle
    app._push_message = lambda t, x: sent.append((t, x))
    now = time.mktime(time.strptime(
        f"2026-10-12 {hour:02d}:00", "%Y-%m-%d %H:%M"))
    return app, sent, now


class BreakTickTests(unittest.TestCase):
    def test_triggers_once_per_episode_then_resets(self):
        app, sent, now = _make(45, 50 * 60)
        self.assertEqual(app._break_tick(now), 1)
        self.assertEqual(len(sent), 1)
        self.assertIn("50 分钟", sent[0][1])
        self.assertEqual(app._break_tick(now), 1)      # 同一轮不重复打扰
        self.assertEqual(len(sent), 1)
        app._input_idle_secs = lambda: 5.0             # 用户回来了
        self.assertEqual(app._break_tick(now), 0)
        self.assertFalse(app._break_notified)
        app._input_idle_secs = lambda: 50 * 60
        self.assertEqual(app._break_tick(now), 1)      # 新一轮可再次提醒
        self.assertEqual(len(sent), 2)

    def test_disabled_below_threshold_and_night(self):
        app, sent, now = _make(0, 50 * 60)             # 关闭
        self.assertEqual(app._break_tick(now), 0)
        self.assertEqual(sent, [])
        app, sent, now = _make(45, 10)                 # 未达阈值
        self.assertEqual(app._break_tick(now), 0)
        app, sent, night = _make(45, 50 * 60, hour=3)  # 凌晨安静
        self.assertEqual(app._break_tick(night), 0)
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
