"""macOS 硬件遥测的准确性回归测试。"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy.collect.macos import MacCollector
from workbuddy.app import App


class MacNetworkTests(unittest.TestCase):
    def test_reads_default_route_interface(self):
        output = """route to: default
          gateway: 192.168.1.1
        interface: en0
        """
        result = SimpleNamespace(stdout=output)
        with patch("workbuddy.collect.macos.subprocess.run", return_value=result):
            self.assertEqual(MacCollector._default_route_interface(), "en0")

    def test_rate_uses_default_interface_without_counting_vpn_twice(self):
        collector = MacCollector.__new__(MacCollector)
        collector._net_iface = ""
        collector._net_iface_at = 0.0
        collector._last_net = None
        collector._default_route_interface = lambda: "en0"
        first = {
            "en0": SimpleNamespace(bytes_recv=1_000_000, bytes_sent=2_000_000),
            "utun3": SimpleNamespace(bytes_recv=5_000_000, bytes_sent=6_000_000),
        }
        second = {
            "en0": SimpleNamespace(bytes_recv=2_000_000, bytes_sent=2_500_000),
            "utun3": SimpleNamespace(bytes_recv=9_000_000, bytes_sent=10_000_000),
        }
        with patch("psutil.net_io_counters", side_effect=[first, second]):
            self.assertEqual(collector._net_rates(10.0), (None, None))
            # en0 一秒增加 1 MB / 0.5 MB，即 8 / 4 Mbps；utun3 不应重复计入。
            self.assertEqual(collector._net_rates(11.0), (8.0, 4.0))


class ConsoleRenderTests(unittest.TestCase):
    def _render(self, cputemp):
        app = App.__new__(App)
        app.link = None
        return app.render({"cpu": 12, "mem": 34, "disk": 56,
                           "cputemp": cputemp})

    def test_empty_temperature_clear_marker_does_not_crash(self):
        text = self._render("")
        self.assertIn("CPU  12%", text)
        self.assertNotIn("°", text)

    def test_numeric_string_temperature_is_rendered(self):
        self.assertIn("56°", self._render("55.6"))


if __name__ == "__main__":
    unittest.main()
