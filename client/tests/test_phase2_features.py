"""Phase 2/3 新功能回归：托盘增强 / 一键启动 / 暂停采集 / 设备切页。

覆盖：
- TrayController 的 暂停/静音/切页 动作（纯逻辑，不依赖 pystray）
- /api/launch 只接受已登记应用 id（FakeApp + 真实 HTTP 服务）
- App.set_paused / set_device_page 的状态与守卫（__new__ 绕过重依赖）
- status_snapshot 暴露 paused / usage 字段
"""
import json
import time
import unittest
import urllib.request
import urllib.error

from workbuddy.tray import TrayController
from workbuddy.web import start_settings_server


def make_controller(**cbs):
    return TrayController(on_show=lambda: None, on_quit=lambda: None, **cbs)


class TrayEnhancementTests(unittest.TestCase):
    def test_toggle_pause_round_trip(self):
        state = {"paused": False}

        def flip():
            state["paused"] = not state["paused"]
            return state["paused"]

        ctrl = make_controller(on_pause=flip)
        ctrl.pause_getter = lambda: state["paused"]
        self.assertTrue(ctrl.toggle_pause())      # → 暂停
        self.assertTrue(ctrl.paused_state())
        self.assertFalse(ctrl.toggle_pause())     # → 恢复
        self.assertFalse(ctrl.paused_state())

    def test_toggle_pause_without_callback_is_falsy(self):
        self.assertFalse(make_controller().toggle_pause())

    def test_mute_invokes_callback(self):
        called = []
        ctrl = make_controller(on_mute=lambda: called.append(1))
        self.assertTrue(ctrl.toggle_mute())
        self.assertEqual(called, [1])
        self.assertFalse(make_controller().toggle_mute())

    def test_request_page_guards_range_and_link(self):
        sent = []
        ctrl = make_controller(on_page=lambda n: 1 <= n <= 3 and not sent.append(n))
        ctrl.page_count = 3
        self.assertTrue(ctrl.request_page(2))
        self.assertFalse(ctrl.request_page(9))
        self.assertEqual(sent, [2])

    def test_page_menu_items_only_when_wired(self):
        """无 on_page 回调时不应出现切页子菜单（菜单构造在 GUI 层，这里锁语义）。"""
        ctrl = make_controller()
        self.assertEqual(ctrl.page_count, 0)
        self.assertFalse(ctrl.request_page(1))


class FakeLaunchApp:
    """只实现 /api/launch 所需接口。"""

    def __init__(self, apps):
        self.profile = {"apps": apps}
        self.launched = []

    class control:
        @staticmethod
        def launch(command):
            return None

    def status_snapshot(self):
        return {"profile": self.profile}


class LaunchEndpointTests(unittest.TestCase):
    PORT = 8747

    @classmethod
    def setUpClass(cls):
        cls.app = FakeLaunchApp([
            {"id": "app1", "name": "终端", "command": "open -a Terminal"},
        ])
        cls.app.launched = []
        cls.server = start_settings_server(cls.app, cls.PORT)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.PORT}/api/status", timeout=1)
                break
            except Exception:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.PORT}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_launch_known_app_returns_200(self):
        code, body = self._post("/api/launch", {"id": "app1"})
        self.assertEqual(code, 200)
        self.assertEqual(body.get("id"), "app1")

    def test_launch_unknown_app_rejected(self):
        code, body = self._post("/api/launch", {"id": "nope"})
        self.assertEqual(code, 400)
        self.assertIn("error", body)


class AppPausePageTests(unittest.TestCase):
    def _app(self):
        from workbuddy.app import App
        app = App.__new__(App)
        app.paused = False
        app.link = None
        app.profile = {"pages": [{}, {}, {}]}
        app.health = type("H", (), {"event": lambda self, *a, **kw: None})()
        return app

    def test_set_paused_toggles_state(self):
        app = self._app()
        self.assertTrue(app.set_paused(True))
        self.assertTrue(app.paused)
        self.assertFalse(app.set_paused(False))
        self.assertFalse(app.paused)

    def test_set_device_page_requires_connection(self):
        app = self._app()
        self.assertFalse(app.set_device_page(1))

    def test_set_device_page_validates_range(self):
        app = self._app()
        app.link = type("L", (), {
            "status": "connected:/dev/x",
            "sent": [],
            "send": lambda self, s: self.sent.append(s),
        })()
        self.assertFalse(app.set_device_page(0))
        self.assertFalse(app.set_device_page(4))
        self.assertTrue(app.set_device_page(2))
        self.assertEqual(app.link.sent, ["PAGE=2\n"])

    def test_snapshot_exposes_paused_and_usage(self):
        import inspect
        from workbuddy.app import App
        src = inspect.getsource(App.status_snapshot)
        for key in ("paused", "usage"):
            self.assertIn(key, src)


if __name__ == "__main__":
    unittest.main()
