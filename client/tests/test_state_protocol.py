"""Regressions for task clearing, sensor invalidation and AI progress semantics."""
import unittest
import time
import threading
import base64
import re
import queue
from pathlib import Path

from workbuddy.app import App
from workbuddy.aiwatch import AIWatcher, Activity, DONE, THINKING, WAIT_INPUT
from workbuddy.protocol import encode_telemetry, parse_uplink
from workbuddy.serial_link import SerialLink


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


class _Weather:
    _lock = __import__("threading").Lock()
    data = {}


class _Cfg:
    reminders = []


class StateProtocolTests(unittest.TestCase):
    def test_empty_ai_slots_are_sent_as_clear_commands(self):
        frame = encode_telemetry({f"ai{i}": "" for i in range(4)})
        self.assertEqual(frame, "AI0=\nAI1=\nAI2=\nAI3=\n")

    def test_temp_fan_empty_is_clear_sent(self):
        # 温度/风扇丢能力时发空串——设备端按清位处理，不残留旧读数
        frame = encode_telemetry({"cputemp": "", "gputemp": "", "fan": ""})
        self.assertIn("CPUTEMP=\n", frame)
        self.assertIn("GPUTEMP=\n", frame)
        self.assertIn("FAN=\n", frame)

    def test_fan_value_encoded(self):
        self.assertIn("FAN=5301", encode_telemetry({"fan": 5301}))

    def test_pet_actions_forward_to_device(self):
        # 三种互动都必须下行，数值变化与视觉反馈才形成闭环。
        class RecLink:
            status = "connected:test"
            def __init__(self):
                self.sent = []
            def send(self, line):
                self.sent.append(line)

        app = App.__new__(App)
        app.link = RecLink()
        app.pet_scene_event("feed")
        app.pet_scene_event("water")
        app.pet_scene_event("play")
        app.pet_scene_event("unknown")
        self.assertEqual(app.link.sent,
                         ["PET=feed\n", "PET=water\n", "PET=play\n"])

    def test_pet_play_event_silent_without_link(self):
        app = App.__new__(App)
        app.link = None
        app.pet_scene_event("play")  # 不抛异常即通过

    def test_sensor_failure_invalidates_last_value(self):
        self.assertEqual(parse_uplink("TEMP=NA"), {"temp": None})
        self.assertEqual(parse_uplink("HUM=NA"), {"hum": None})

    def test_ota_ack_and_nak_are_parsed(self):
        self.assertEqual(parse_uplink("ACK=OTA,BEGIN"),
                         {"ota_reply": {"ok": True, "reason": "BEGIN"}})
        self.assertEqual(parse_uplink("NAK=OTA,SEQ"),
                         {"ota_reply": {"ok": False, "reason": "SEQ"}})

    def test_ota_uses_lossless_queue_and_waits_for_device_ack(self):
        class Health:
            def event(self, *args, **kwargs):
                pass

        class Link:
            status = "connected:test"
            def __init__(self, owner):
                self.owner = owner
                self.calls = []
            def send(self, data, *, block=False, timeout=5.0):
                self.calls.append((data, block))
                if data.startswith(b"OTA=BEGIN"):
                    self.owner._ota_reply = {"ok": True, "reason": "BEGIN"}
                    self.owner._ota_event.set()
                elif data.startswith(b"OTA=DATA,"):
                    seq = int(data.split(b",", 2)[1])
                    self.owner._ota_reply = {"ok": True, "reason": f"DATA:{seq + 1}"}
                    self.owner._ota_event.set()
                elif data == b"OTA=END\n":
                    self.owner._ota_reply = {"ok": True, "reason": "REBOOTING"}
                    self.owner._ota_event.set()
                return True

        app = App.__new__(App)
        app._ota_event = threading.Event()
        app._ota_reply = None
        app.ota_result = {"ok": None, "reason": ""}
        app.health = Health()
        app.link = Link(app)
        self.assertTrue(app.send_ota(b"\xe9" + b"x" * 4096))
        self.assertTrue(all(block for _, block in app.link.calls))
        self.assertEqual(app.ota_result["reason"], "REBOOTING")

    def test_ota_frame_fits_firmware_line_buffer(self):
        encoded = base64.b64encode(b"x" * 512).decode("ascii")
        longest = len(f"OTA=DATA,99999,{encoded}\n")
        header = (Path(__file__).resolve().parents[2] /
                  "firmware" / "src" / "proto.h").read_text(encoding="utf-8")
        match = re.search(r"char\s+_line\[(\d+)\]", header)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)), longest)

    def test_serial_nonblocking_send_still_queues(self):
        link = SerialLink.__new__(SerialLink)
        link._port = object()
        link._q = queue.Queue(maxsize=2)
        self.assertTrue(link.send(b"HB=1\n"))
        self.assertEqual(link._q.get_nowait(), b"HB=1\n")

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
        app.weather = _Weather()
        app.usage_scanner = None
        app.env = {"temp": 25.0, "hum": 40}
        app.env_seen = {"temp": time.monotonic() - 7, "hum": time.monotonic() - 7}
        self.assertNotIn("temp", app.tick(time.time()))
        self.assertNotIn("hum", app.last)
        app.env_seen = {"temp": time.monotonic(), "hum": time.monotonic()}
        self.assertEqual(app.tick(time.time())["temp"], 25.0)


if __name__ == "__main__":
    unittest.main()
