"""设备实拍（SNAP）链路回归：协议帧解析 / RGB565→PNG 组装 / 端点形状。

不依赖真设备：帧用合成数据，PNG 组装用 __new__ App + 临时路径。
"""
import base64
import json
import tempfile
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path

from workbuddy.protocol import parse_uplink
from workbuddy.web import start_settings_server


class SnapProtocolTests(unittest.TestCase):
    def test_begin_data_end_frames(self):
        self.assertEqual(parse_uplink("SNAP=BEGIN,160,240"),
                         {"snap": {"kind": "begin", "w": 160, "h": 240}})
        d = parse_uplink("SNAP=DATA,3,QUJD")
        self.assertEqual(d["snap"]["kind"], "data")
        self.assertEqual(d["snap"]["seq"], "3")
        self.assertEqual(d["snap"]["b64"], "QUJD")
        self.assertEqual(parse_uplink("SNAP=END"), {"snap": {"kind": "end"}})

    def test_begin_bad_dims_rejected(self):
        self.assertIsNone(parse_uplink("SNAP=BEGIN,x,y"))

    def test_nak_frame(self):
        self.assertEqual(parse_uplink("NAK=SNAP,MEM"),
                         {"snap_nak": {"ok": False, "reason": "MEM"}})


class SnapAssemblyTests(unittest.TestCase):
    def _app(self):
        from workbuddy.app import App

        class H:
            def event(self, *a, **k):
                pass

        app = App.__new__(App)
        app.health = H()
        app.snap_at = None
        app._snap_buf = None
        app._snap_meta = (160, 240)
        return app

    def test_assembles_rgb565_to_png(self):
        import base64 as b64
        app = self._app()
        # 2×1 像素：纯红 (0xF800) + 纯蓝 (0x001F)，小端
        raw = bytes([0x00, 0xF8, 0x1F, 0x00])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "snap.png"
            app._snap_path = out            # end 帧自动落盘到注入路径
            app._handle_snap_frame({"kind": "begin", "w": 2, "h": 1})
            app._handle_snap_frame({"kind": "data", "seq": "0",
                                    "b64": b64.b64encode(raw).decode()})
            app._handle_snap_frame({"kind": "end"})
            self.assertTrue(out.exists())
            self.assertIsNotNone(app.snap_at)
            from PIL import Image
            img = Image.open(out).convert("RGB")
            self.assertEqual(img.getpixel((0, 0)), (248, 0, 0))    # R5→R8
            self.assertEqual(img.getpixel((1, 0)), (0, 0, 248))    # B5→B8

    def test_short_frame_discarded_not_saved(self):
        app = self._app()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "snap.png"
            app._snap_path = out
            app._handle_snap_frame({"kind": "begin", "w": 160, "h": 240})
            app._handle_snap_frame({"kind": "data", "seq": "0", "b64": "QUJD"})
            app._handle_snap_frame({"kind": "end"})
            self.assertFalse(out.exists(), "像素数不符必须整帧丢弃")
            self.assertIsNone(app.snap_at)


class SnapEndpointTests(unittest.TestCase):
    PORT = 8755

    @classmethod
    def setUpClass(cls):
        class App:
            class cfg:
                web_port = cls.PORT
            snap_at = None
            class health:
                @staticmethod
                def event(*a, **k):
                    pass
            link = None
            def status_snapshot(self):
                return {"profile": {}}
            def request_snap(self):
                return False          # 无设备 → 503
            def snapshot_bytes(self):
                return None

        cls.server = start_settings_server(App(), cls.PORT)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_snapshot_without_image_is_404(self):
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{self.PORT}/api/snapshot", timeout=2)
            self.fail("应 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_snap_request_without_device_is_503(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.PORT}/api/snap-request",
            data=b"{}", headers={"Content-Type": "application/json"},
            method="POST")
        try:
            urllib.request.urlopen(req, timeout=2)
            self.fail("应 503")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 503)

    def test_meta_shape(self):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.PORT}/api/snapshot-meta",
                timeout=2) as r:
            self.assertIn("at", json.loads(r.read()))


if __name__ == "__main__":
    unittest.main()
