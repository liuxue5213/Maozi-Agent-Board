"""P1-1：协议握手版本校验——旧固件不推送 v0.3.2 新字段。"""
import unittest

from workbuddy.protocol import (
    PROTOCOL_VERSION, PROTO_V032_DOWNLINK_ONLY,
    parse_protocol_version, negotiated_supports_v032,
    encode_telemetry, encode_caps,
)


class TestParseProtocolVersion(unittest.TestCase):
    def test_new_format_proto_field(self):
        self.assertEqual(parse_protocol_version(
            "proto:0.3.2,n16r8,schema:1,pages:10"), (0, 3, 2))

    def test_two_segment_version(self):
        self.assertEqual(parse_protocol_version("proto:3.2,foo:1"), (3, 2))

    def test_old_positional_format(self):
        # 旧固件 `FW=0.2,n16r8,...`：首段即版本
        self.assertEqual(parse_protocol_version("0.2,n16r8,schema:1"), (0, 2))

    def test_unversioned_returns_none(self):
        self.assertIsNone(parse_protocol_version("n16r8,schema:1,pages:10"))

    def test_empty_returns_none(self):
        self.assertIsNone(parse_protocol_version(""))

    def test_malformed_returns_none(self):
        self.assertIsNone(parse_protocol_version("proto:abc,foo:1"))


class TestNegotiatedSupports(unittest.TestCase):
    def test_unknown_is_supported(self):
        self.assertTrue(negotiated_supports_v032(None))

    def test_equal_version_supported(self):
        self.assertTrue(negotiated_supports_v032(PROTOCOL_VERSION))

    def test_older_version_unsupported(self):
        self.assertFalse(negotiated_supports_v032((0, 3, 1)))
        self.assertFalse(negotiated_supports_v032((0, 2, 9)))
        self.assertFalse(negotiated_supports_v032((0, 2)))

    def test_newer_version_supported(self):
        self.assertTrue(negotiated_supports_v032((0, 3, 3)))
        self.assertTrue(negotiated_supports_v032((0, 4, 0)))
        self.assertTrue(negotiated_supports_v032((1, 0, 0)))


class TestTelemetryGating(unittest.TestCase):
    def _full(self):
        t = {
            "ai0": "claude,working,50,10,proj,id0,5",
            "ai3": "codex,idle,0,0,,id3,9",
            "ai4": "zcode,working,20,30,proj,id4,2",
            "ai7": "wb,thinking,10,5,proj,id7,1",
            "np": "Song,Artist,0,100,1",
            "aiu": "claude,gpt-4,30",
            "ait": "1",                  # 基线兼容字段，始终发送
            "aitok": "1234",
            "aiout": "500",
            "aitotal": "1234",
            "aititle": "some task",
            "afm": "latest message",
            "pb": "1",
            "ast": "claude:3",
            "aib0": "gpt-4,800",
            "aib1": "claude,400",
            "aib2": "gemini,200",
            "cpu": "42",
        }
        return t

    def test_no_gate_when_fw_unknown(self):
        frame = encode_telemetry(self._full(), fw_proto=None)
        self.assertIn("AI4=", frame)
        self.assertIn("NP=", frame)
        self.assertIn("AITOK=", frame)
        self.assertIn("AIB0=", frame)

    def test_no_gate_when_fw_current(self):
        frame = encode_telemetry(self._full(), fw_proto=(0, 3, 2))
        self.assertIn("AI4=", frame)
        self.assertIn("NP=", frame)
        self.assertIn("AITOK=", frame)
        self.assertIn("AIB2=", frame)

    def test_gate_when_fw_old(self):
        frame = encode_telemetry(self._full(), fw_proto=(0, 2))
        # v0.3.2 新增下行字段全部压掉
        for key in PROTO_V032_DOWNLINK_ONLY:
            self.assertNotIn(f"{key.upper()}=", frame,
                             f"{key} 不应推送给旧固件")
        # 基线字段保留
        self.assertIn("AI0=", frame)
        self.assertIn("AI3=", frame)
        self.assertIn("AIT=", frame)
        self.assertIn("CPU=", frame)

    def test_gate_set_matches_spec(self):
        # P1-1 明确要求覆盖的字段：AI4-7 / NP / AIU / AITOK / AIOUT / AITOTAL /
        # AITITLE / AFM（传感器为上行，下行不涉及）
        for key in ("ai4", "ai5", "ai6", "ai7", "np", "aiu", "aitok",
                    "aiout", "aitotal", "aititle", "afm"):
            self.assertIn(key, PROTO_V032_DOWNLINK_ONLY)


class TestCapsProto(unittest.TestCase):
    def test_caps_carries_proto(self):
        s = encode_caps({"cpu_temp": 0, "fan": 0})
        self.assertIn("proto:0.3.2", s)
        self.assertTrue(s.startswith("CAPS="))


if __name__ == "__main__":
    unittest.main()
