"""Windows WMI 温度/风扇采集回归（monkeypatch _run_ps，不依赖真 Windows）。

锁定：
- LHM 优先、热区/Win32_Fan 兜底的取值顺序；
- 十分之一开尔文 → ℃ 换算与 0℃ 死值拒绝；
- caps 与键同步（读不到 → caps False → 设备自动隐藏）；
- 5s 节流缓存。
"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy.collect.windows import WindowsCollector


def make_collector():
    # __new__ 绕过 __init__（不碰 NVML/psutil）；节流缓存手动置空
    c = WindowsCollector.__new__(WindowsCollector)
    c.caps = {"cpu_temp": False, "gpu": False, "fan": False, "bat": False}
    c._nvml = None
    c._thermal_cache = ({}, 0.0)
    return c


class ThermalTests(unittest.TestCase):
    def test_lhm_values_win(self):
        c = make_collector()
        with mock.patch.object(c, "_query_lhm_cpu_temp", return_value="57.3"), \
             mock.patch.object(c, "_query_thermal_zone", return_value=""), \
             mock.patch.object(c, "_query_lhm_fan", return_value="2662"), \
             mock.patch.object(c, "_query_win32_fan", return_value=""):
            t = c.extra(time.monotonic())
        self.assertEqual(t["cputemp"], 57.3)
        self.assertEqual(t["fan"], 2662)
        self.assertTrue(c.caps["cpu_temp"])
        self.assertTrue(c.caps["fan"])

    def test_thermal_zone_kelvin10_conversion(self):
        c = make_collector()
        # 3300（十分之一开尔文）→ 56.85 → 保留 1 位小数 56.9
        with mock.patch.object(c, "_query_lhm_cpu_temp", return_value=""), \
             mock.patch.object(c, "_query_thermal_zone", return_value="3300"), \
             mock.patch.object(c, "_query_lhm_fan", return_value=""), \
             mock.patch.object(c, "_query_win32_fan", return_value="1800"):
            t = c.extra(time.monotonic())
        self.assertAlmostEqual(t["cputemp"], 56.85, places=1)
        self.assertEqual(t["fan"], 1800)
        self.assertTrue(c.caps["cpu_temp"])

    def test_lhm_celsius_not_double_converted(self):
        """回归：LHM 的 Value 是℃，不得再当开尔文×10 换算（会变 -267℃）。"""
        c = make_collector()
        with mock.patch.object(c, "_query_lhm_cpu_temp", return_value="57.3"), \
             mock.patch.object(c, "_query_thermal_zone", return_value=""), \
             mock.patch.object(c, "_query_lhm_fan", return_value=""), \
             mock.patch.object(c, "_query_win32_fan", return_value=""):
            t = c.extra(time.monotonic())
        self.assertEqual(t["cputemp"], 57.3)
        self.assertTrue(c.caps["cpu_temp"])

    def test_zero_celsius_dead_value_rejected(self):
        c = make_collector()
        with mock.patch.object(c, "_query_lhm_cpu_temp", return_value=""), \
             mock.patch.object(c, "_query_thermal_zone", return_value="2731"), \
             mock.patch.object(c, "_query_lhm_fan", return_value=""), \
             mock.patch.object(c, "_query_win32_fan", return_value=""):
            t = c.extra(time.monotonic())
        self.assertNotIn("cputemp", t, "0℃ 死值不得当作真实温度")
        self.assertFalse(c.caps["cpu_temp"])
        self.assertFalse(c.caps["fan"])

    def test_all_sources_empty_keeps_caps_false(self):
        c = make_collector()
        for q in ("_query_lhm_cpu_temp", "_query_thermal_zone",
                  "_query_lhm_fan", "_query_win32_fan"):
            setattr(c, q, lambda: "")
        t = c.extra(time.monotonic())
        self.assertNotIn("cputemp", t)
        self.assertNotIn("fan", t)
        self.assertFalse(c.caps["cpu_temp"])
        self.assertFalse(c.caps["fan"])

    def test_throttle_within_window(self):
        c = make_collector()
        calls = {"n": 0}

        def fake_query():
            calls["n"] += 1
            return "3300"

        with mock.patch.object(c, "_query_lhm_cpu_temp", fake_query), \
             mock.patch.object(c, "_query_thermal_zone", lambda: ""), \
             mock.patch.object(c, "_query_lhm_fan", lambda: ""), \
             mock.patch.object(c, "_query_win32_fan", lambda: ""):
            c.extra(time.monotonic())
            c.extra(time.monotonic())     # 5s 窗口内：不再查询
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
