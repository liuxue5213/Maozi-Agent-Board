"""Regressions for task clearing, sensor invalidation and AI progress semantics."""
import unittest
import time

from workbuddy.app import App
from workbuddy.aiwatch import AIWatcher, Activity, DONE, THINKING, WAIT_INPUT
from workbuddy.protocol import encode_telemetry, parse_uplink


class _Source:
    def __init__(self, state):
        self.state = state

    def poll_all(self, now, cache):
        return [Activity("codex", self.state, now, sid="test")]


class _Collector:
    def sample(self, now):
        return {}


class _Link:
    status = "connected:test"


class _Control:
    def snapshot(self):
        return {}


class _Cfg:
    reminders = []


class StateProtocolTests(unittest.TestCase):
    def test_empty_ai_slots_are_sent_as_clear_commands(self):
        frame = encode_telemetry({f"ai{i}": "" for i in range(4)})
        self.assertEqual(frame, "AI0=\nAI1=\nAI2=\nAI3=\n")

    def test_sensor_failure_invalidates_last_value(self):
        self.assertEqual(parse_uplink("TEMP=NA"), {"temp": None})
        self.assertEqual(parse_uplink("HUM=NA"), {"hum": None})

    def test_inferred_ai_states_have_no_percentage(self):
        watcher = AIWatcher.__new__(AIWatcher)
        watcher._cache = {}
        watcher._bursts = {}
        for state, expected in ((THINKING, -1), (WAIT_INPUT, -1), (DONE, 100)):
            watcher.sources = [_Source(state)]
            self.assertEqual(watcher.poll_all(1000)[0]["prog"], expected)

    def test_stale_environment_reading_is_not_sent(self):
        app = App.__new__(App)
        app.collector = _Collector()
        app.ai = None
        app.cfg = _Cfg()
        app.link = _Link()
        app.control = _Control()
        app.env = {"temp": 25.0, "hum": 40}
        app.env_seen = {"temp": time.monotonic() - 7, "hum": time.monotonic() - 7}
        self.assertNotIn("temp", app.tick(time.time()))
        self.assertNotIn("hum", app.last)
        app.env_seen = {"temp": time.monotonic(), "hum": time.monotonic()}
        self.assertEqual(app.tick(time.time())["temp"], 25.0)


if __name__ == "__main__":
    unittest.main()
