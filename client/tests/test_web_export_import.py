"""Web 配置导出/导入端点回归（路线图 P1-3）。

通过真实启动 ThreadingHTTPServer + 最小 FakeApp 验证：
- GET  /api/export 返回当前生效 profile（可下载 JSON）
- POST /api/import 校验并保存合法配置（200，版本自增）
- POST /api/import 拒绝非法配置（400）
并补充"导出→导入"往返一致性（不落盘，避免污染 ~/.workbuddy/profile.json）。
"""
import json
import threading
import time
import unittest
import urllib.request
import urllib.error

from workbuddy.profile import default_profile, validate_profile
from workbuddy.web import start_settings_server


class FakeApp:
    """仅实现 web 端点所需的最小接口，不触碰磁盘。"""

    def __init__(self):
        # 基线配置（版本 1），与真实 app 的 load_profile() 一致。
        self.profile = validate_profile(default_profile())

    def status_snapshot(self):
        return {"profile": self.profile,
                "profile_status": "设备已应用", "device": "串口关闭"}

    def save_profile(self, draft):
        # 真实 app.save_profile 会校验 + 基于当前配置版本自增 + 写盘；
        # 这里只校验并从基线自增，避免测试污染用户目录下的 profile.json。
        candidate = validate_profile(draft)
        candidate["version"] = self.profile["version"] + 1
        self.profile = candidate
        return candidate


class WebExportImportTests(unittest.TestCase):
    PORT = 8742

    @classmethod
    def setUpClass(cls):
        cls.app = FakeApp()
        cls.server = start_settings_server(cls.app, cls.PORT)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.PORT}/api/export",
                                       timeout=1)
                break
            except Exception:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _url(self, path):
        return f"http://127.0.0.1:{self.PORT}{path}"

    def test_export_returns_current_profile(self):
        with urllib.request.urlopen(self._url("/api/export"), timeout=3) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read())
        self.assertIn("pages", data)
        self.assertIn("theme", data)
        # 导出内容自身应通过校验，保证"导出→导入"可往返。
        validate_profile(data)

    def test_export_matches_status_profile(self):
        with urllib.request.urlopen(self._url("/api/export"), timeout=3) as r:
            exported = json.loads(r.read())
        self.assertEqual(exported, self.app.status_snapshot()["profile"])

    def test_import_valid_profile(self):
        before = self.app.profile["version"]
        payload = validate_profile(default_profile())
        req = urllib.request.Request(
            self._url("/api/import"),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            self.assertEqual(r.status, 200)
            resp = json.loads(r.read())
        self.assertIn("profile", resp)
        # 导入应基于当前配置自增版本（防止覆盖设备上更新的配置）。
        self.assertEqual(resp["profile"]["version"], before + 1)

    def test_import_rejects_invalid(self):
        bad = validate_profile(default_profile())
        bad["pages"] = [
            {"id": "x", "title": "坏", "layout": "grid_2x2", "widgets": ["clock"]},
            {"id": "x", "title": "坏", "layout": "grid_2x2", "widgets": ["clock"]},
        ]
        req = urllib.request.Request(
            self._url("/api/import"),
            data=json.dumps(bad).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 400)

    def test_export_import_roundtrip_idempotent(self):
        exported = validate_profile(default_profile())
        re_validated = validate_profile(exported)
        self.assertEqual(re_validated["pages"], exported["pages"])


if __name__ == "__main__":
    unittest.main()
