"""P1-4 行为遥测趋势存储回归（临时目录，不碰真实 ~/.workbuddy）。

锁定：60s 节流、按天落盘可回读、10 分钟桶聚合（均值/累计取最大）、
hours 窗口裁剪、过期文件清理、/api/trend 端点往返。
"""
import json
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from workbuddy.trend import TrendStore


class TrendStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TrendStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_sample_throttled_to_60s(self):
        now = 1000.0
        self.assertTrue(self.store.sample({"cpu": 10}, tasks=1, now=now))
        self.assertFalse(self.store.sample({"cpu": 20}, tasks=1, now=now + 30))
        self.assertTrue(self.store.sample({"cpu": 30}, tasks=2, now=now + 61))

    def test_series_buckets_and_aggregation(self):
        # 对齐到当前 10 分钟桶内第 2 分钟：两拍必然同桶（不受墙钟位置影响）
        base = int(time.time() // 600) * 600 + 120
        t = {"cpu": 10, "mem": 50, "disk": 80, "ait": "120"}
        self.store.sample(t, tasks=1, now=base)
        self.store.sample({"cpu": 30, "mem": 70, "disk": 82, "ait": "150"},
                          tasks=3, now=base + 60)
        series = self.store.series(hours=1)
        self.assertEqual(len(series), 1)
        b = series[0]
        self.assertEqual(b["cpu"], 20.0)          # 均值
        self.assertEqual(b["mem"], 60.0)
        self.assertEqual(b["disk"], 81.0)
        self.assertEqual(b["tok"], 150, "token 累计量桶内取最大")
        self.assertEqual(b["tasks"], 2.0)

    def test_series_window_prunes_old(self):
        old = time.time() - 2 * 86400          # 2 天前
        self.store.sample({"cpu": 5}, tasks=0, now=old)
        self.store.sample({"cpu": 42}, tasks=0, now=time.time() - 120)
        series = self.store.series(hours=24)
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["cpu"], 42)

    def test_prune_removes_expired_files(self):
        stale = self.store._path("2099-01-01")
        stale.write_text('{"t":1}\n')
        import os
        ancient = time.time() - 9 * 86400
        os.utime(stale, (ancient, ancient))
        TrendStore(Path(self.tmp.name))        # 重新实例化触发清理
        self.assertFalse(stale.exists())

    def test_missing_fields_tolerated(self):
        self.store.sample({}, tasks=0, now=time.time())
        series = self.store.series(hours=1)
        self.assertEqual(len(series), 1)
        self.assertIsNone(series[0]["cpu"])


class TrendEndpointTests(unittest.TestCase):
    PORT = 8753

    @classmethod
    def setUpClass(cls):
        from workbuddy.web import start_settings_server

        class App:
            class cfg:
                web_port = cls.PORT
            trend = None       # FakeApp 无 trend → 端点应返回空而非 500
            def status_snapshot(self):
                return {"profile": {}}

        cls.server = start_settings_server(App(), cls.PORT)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_trend_endpoint_shape_without_store(self):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.PORT}/api/trend?hours=24",
                timeout=2) as r:
            data = json.loads(r.read())
        self.assertEqual(data["hours"], 24)
        self.assertEqual(data["series"], [])


if __name__ == "__main__":
    unittest.main()
