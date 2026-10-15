"""电脑使用提醒不能把长时间离开误判成久坐。"""
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

import workbuddy.app as appmod
from workbuddy.healthcoach import (WorkBreakMonitor, load_break_state,
                                   save_break_state)


class WorkBreakTests(unittest.TestCase):
    def test_active_work_triggers_once_and_real_break_resets(self):
        monitor = WorkBreakMonitor()
        self.assertFalse(monitor.tick(0, 0, 5, 2))
        events = [monitor.tick(now, 0, 5, 2) for now in range(10, 310, 10)]
        self.assertEqual(events.count(True), 1)
        self.assertTrue(monitor.due)
        self.assertFalse(monitor.tick(320, 0, 5, 2))
        self.assertFalse(monitor.tick(330, 130, 5, 2))
        self.assertFalse(monitor.due)
        self.assertEqual(monitor.state, 'rested')

    def test_idle_does_not_accumulate_and_gap_preserves_work(self):
        monitor = WorkBreakMonitor()
        monitor.tick(0, 0, 5, 5)
        monitor.tick(10, 0, 5, 5)
        monitor.tick(20, 150, 5, 5)
        self.assertEqual(monitor.work_seconds, 10)
        monitor.tick(4000, 0, 5, 5)
        self.assertEqual(monitor.work_seconds, 10)
        self.assertFalse(monitor.due)

    def test_snooze_rearms_only_after_delay_and_rest_cancels_it(self):
        monitor = WorkBreakMonitor()
        monitor.tick(0, 0, 1, 2)
        for now in range(10, 70, 10):
            monitor.tick(now, 0, 1, 2)
        self.assertTrue(monitor.due)
        self.assertTrue(monitor.snooze(60, 2))
        self.assertFalse(monitor.due)
        self.assertEqual(monitor.state, 'snoozed')
        self.assertFalse(monitor.tick(170, 0, 1, 2))
        self.assertTrue(monitor.tick(180, 0, 1, 2))
        self.assertTrue(monitor.due)
        self.assertTrue(monitor.snooze(180, 2))
        monitor.tick(190, 130, 1, 2)
        self.assertEqual(monitor.state, 'rested')
        self.assertFalse(monitor.due)
        self.assertEqual(monitor.snoozed_until, 0)

    def test_unavailable_and_disabled_are_explicit(self):
        monitor = WorkBreakMonitor()
        self.assertFalse(monitor.tick(0, -1, 5, 5))
        self.assertEqual(monitor.state, 'unavailable')
        self.assertFalse(monitor.tick(10, -1, 0, 5))
        self.assertEqual(monitor.state, 'off')
        self.assertIsNone(monitor.snapshot(0, 5)['step_count'])

    def test_restart_restores_work_and_snooze_without_counting_downtime(self):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'break-state.json'
            monitor = WorkBreakMonitor()
            monitor.tick(1000, 0, 5, 2)
            monitor.tick(1060, 0, 5, 2)
            monitor.work_seconds = 301
            monitor.due = True
            monitor.snooze(1060, 10)
            save_break_state(path, monitor, now=1060)
            restored = load_break_state(path, now=1080)
            self.assertEqual(restored.work_seconds, 301)
            self.assertEqual(restored.state, 'snoozed')
            self.assertFalse(restored.tick(1080, 0, 5, 2))
            self.assertEqual(restored.work_seconds, 301)
            self.assertTrue(restored.tick(1660, 0, 5, 2))
            self.assertEqual(restored.work_seconds, 301)

    def test_old_or_corrupt_state_is_ignored(self):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'break-state.json'
            monitor = WorkBreakMonitor()
            monitor.work_seconds = 2400
            monitor.due = True
            save_break_state(path, monitor, now=1000)
            self.assertEqual(load_break_state(path, now=1901).work_seconds, 0)
            path.write_text('{bad json', encoding='utf-8')
            self.assertFalse(load_break_state(path, now=1001).due)

    def test_app_pushes_once_and_never_counts_idle_as_steps(self):
        sent, events = [], []
        app = appmod.App.__new__(appmod.App)
        app.profile = {'pet': {'break_min': 5, 'rest_min': 2}}
        app.cfg = SimpleNamespace(push_quiet='', push_url='')
        app.health = SimpleNamespace(event=lambda *a, **kw: events.append(a[0]))
        app._input_idle_secs = lambda: 0
        app._push_message = lambda title, body: sent.append((title, body))
        desktop = []
        app._desktop_break_notice = lambda work, rest: desktop.append((work, rest))
        now = time.mktime(time.strptime('2026-10-12 15:00', '%Y-%m-%d %H:%M'))
        app._break_tick(now)
        for seconds in range(10, 320, 10):
            app._break_tick(now + seconds)
        self.assertEqual(len(sent), 1)
        self.assertEqual(desktop, [(5, 2)])
        self.assertIn('休息至少 2 分钟', sent[0][1])
        self.assertEqual(events.count('break_remind'), 1)
        app._input_idle_secs = lambda: 200
        self.assertEqual(app._break_tick(now + 330), 0)

    def test_desktop_notification_respects_toggle_and_quiet_hours(self):
        for enabled, quiet, expected in ((0, False, 0), (1, True, 0),
                                         (1, False, 1)):
            with self.subTest(enabled=enabled, quiet=quiet):
                app = appmod.App.__new__(appmod.App)
                app.profile = {'pet': {'break_min': 1, 'rest_min': 2,
                                       'desktop_notify': enabled}}
                app.cfg = SimpleNamespace(push_quiet='', push_url='')
                app.health = SimpleNamespace(event=lambda *a, **kw: None)
                app._input_idle_secs = lambda: 0
                app._push_message = lambda *args: None
                desktop = []
                app._desktop_break_notice = lambda *args: desktop.append(args)
                now = time.mktime(time.strptime('2026-10-12 15:00', '%Y-%m-%d %H:%M'))
                with mock.patch.object(appmod, '_in_quiet_hours', return_value=quiet):
                    app._break_tick(now)
                    for seconds in range(10, 90, 10):
                        app._break_tick(now + seconds)
                self.assertEqual(len(desktop), expected)

    def test_app_writes_due_and_rest_reset_immediately(self):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'break-state.json'
            app = appmod.App.__new__(appmod.App)
            app.profile = {'pet': {'break_min': 1, 'rest_min': 2,
                                   'desktop_notify': 0}}
            app.cfg = SimpleNamespace(push_quiet='', push_url='')
            app.health = SimpleNamespace(event=lambda *a, **kw: None)
            app._input_idle_secs = lambda: 0
            app._push_message = lambda *args: None
            app._work_break_path = path
            now = time.mktime(time.strptime('2026-10-12 15:00', '%Y-%m-%d %H:%M'))
            app._break_tick(now)
            for seconds in range(10, 70, 10):
                app._break_tick(now + seconds)
            self.assertTrue(load_break_state(path, now=now + 60).due)
            app._input_idle_secs = lambda: 130
            app._break_tick(now + 70)
            self.assertFalse(load_break_state(path, now=now + 70).due)
            self.assertEqual(load_break_state(path, now=now + 70).work_seconds, 0)

    def test_macos_notification_uses_fixed_applescript_without_blocking(self):
        app = appmod.App.__new__(appmod.App)
        app.on_health_notice = None
        with mock.patch.object(appmod.sys, 'platform', 'darwin'), \
             mock.patch.object(appmod.subprocess, 'Popen') as popen:
            app._desktop_break_notice(45, 5)
        args = popen.call_args.args[0]
        self.assertEqual(args[:2], ['osascript', '-e'])
        self.assertIn('45', args[2])
        self.assertIn('5', args[2])

    def test_screen_snooze_action_reaches_monitor(self):
        events = []
        app = appmod.App.__new__(appmod.App)
        app.work_break = WorkBreakMonitor()
        app.work_break.due = True
        app.health = SimpleNamespace(event=lambda *a, **kw: events.append(a[0]))
        app.env, app.env_seen = {}, {}
        app._on_uplink({'touch': 'BREAK10'})
        self.assertFalse(app.work_break.due)
        self.assertEqual(app.work_break.state, 'snoozed')
        self.assertIn('break_snooze', events)


if __name__ == '__main__':
    unittest.main()
