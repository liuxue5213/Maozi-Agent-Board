"""托盘常驻与监控数据新鲜度的回归测试。

两个被测故障：
1. 「关闭 Web 页面窗口后程序退出」——托盘缺失导致窗口一关就没了。
   托盘的 GUI 部分无法在无头环境断言，因此把菜单/状态机/退出流程抽到
   `TrayController`（纯逻辑），这里直接单测它。
2. 「监控数据不显示」——实测后端断开时卡片会**冻结在旧值**且毫无提示
   （CPU 一直显示 14%），用户无法分辨"实时值"与"早就没更新的值"。
   这里锁定 /api/status 必须给出数据年龄，以及陈旧阈值与退避计算。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy.tray import (APP_ID, APP_NAME, TrayController,
                            _make_image, tray_available)


class TrayControllerTests(unittest.TestCase):
    def _ctrl(self, **kw):
        calls = {"show": 0, "quit": 0, "refresh": 0}
        c = TrayController(
            on_show=lambda: calls.__setitem__("show", calls["show"] + 1),
            on_quit=lambda: calls.__setitem__("quit", calls["quit"] + 1),
            on_refresh=lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
            port=8765, **kw)
        return c, calls

    # ---- 左键单击：显示/恢复窗口 ----
    def test_show_window_invokes_callback_and_marks_visible(self):
        c, calls = self._ctrl()
        self.assertTrue(c.show_window())
        self.assertTrue(c.visible)
        self.assertEqual(calls["show"], 1)

    def test_show_window_survives_callback_error(self):
        """恢复窗口失败不能把托盘带崩——托盘还得留着让用户能退出。"""
        c = TrayController(on_show=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                           on_quit=lambda: None)
        self.assertTrue(c.show_window())      # 不抛异常即为通过
        self.assertTrue(c.visible)

    def test_toggle_round_trip(self):
        c, calls = self._ctrl()
        self.assertTrue(c.visible)
        c.toggle_window()
        self.assertFalse(c.visible, "再点一次应隐藏")
        c.toggle_window()
        self.assertTrue(c.visible)
        self.assertEqual(calls["show"], 1, "恢复时必须真的调起窗口")

    def test_restore_works_from_minimized_state(self):
        """最小化与隐藏是两种状态，但都应恢复成功。"""
        c, calls = self._ctrl()
        c.visible = False
        c.show_window()
        self.assertTrue(c.visible)
        self.assertEqual(calls["show"], 1)

    # ---- 关闭按钮：只隐藏，不退出 ----
    def test_hide_window_does_not_quit(self):
        c, calls = self._ctrl()
        c.hide_window()
        self.assertFalse(c.visible)
        self.assertEqual(calls["quit"], 0, "点关闭按钮绝不能触发退出")
        self.assertFalse(c.quitting)

    # ---- 首次最小化提示 ----
    def test_tip_shown_only_once(self):
        c, _ = self._ctrl()
        self.assertTrue(c.show_tip_once(), "首次应提示")
        self.assertFalse(c.show_tip_once(), "不应反复打扰")
        self.assertFalse(c.show_tip_once())

    # ---- 菜单项 ----
    def test_refresh_calls_callback(self):
        c, calls = self._ctrl()
        self.assertTrue(c.refresh())
        self.assertEqual(calls["refresh"], 1)

    def test_refresh_without_callback_opens_settings(self):
        c = TrayController(on_show=lambda: None, on_quit=lambda: None, port=8766)
        # 无回调时退化为重开设置页，不应抛异常
        self.assertIn(c.refresh(), (True, False))

    def test_autostart_toggle_returns_new_state(self):
        state = {"on": False}

        def toggle():
            state["on"] = not state["on"]
            return state["on"]

        c = TrayController(on_show=lambda: None, on_quit=lambda: None,
                           on_autostart=toggle)
        self.assertTrue(c.toggle_autostart())
        self.assertFalse(c.toggle_autostart())

    def test_autostart_returns_none_when_unsupported(self):
        c = TrayController(on_show=lambda: None, on_quit=lambda: None,
                           on_autostart=None)
        self.assertIsNone(c.toggle_autostart(), "不支持时应为 None，不能假装成功")

    def test_about_mentions_settings_url(self):
        c = TrayController(on_show=lambda: None, on_quit=lambda: None, port=8788)
        self.assertIn("8788", c.about())
        self.assertIn(APP_NAME, c.about())

    def test_settings_url_shape(self):
        c = TrayController(on_show=lambda: None, on_quit=lambda: None, port=1234)
        self.assertEqual(c.settings_url(), "http://127.0.0.1:1234/")

    # ---- 退出：真正结束进程 ----
    def test_quit_invokes_callback(self):
        c, calls = self._ctrl()
        self.assertTrue(c.quit())
        self.assertEqual(calls["quit"], 1)
        self.assertTrue(c.quitting)

    def test_quit_is_idempotent(self):
        """重复点「退出」不能重复释放资源（串口/端口会二次 shutdown）。"""
        c, calls = self._ctrl()
        self.assertTrue(c.quit())
        self.assertFalse(c.quit())
        self.assertEqual(calls["quit"], 1)

    def test_quit_survives_callback_error(self):
        c = TrayController(on_show=lambda: None,
                           on_quit=lambda: (_ for _ in ()).throw(RuntimeError("x")))
        self.assertTrue(c.quit())
        self.assertTrue(c.quitting)


class TrayAssetsTests(unittest.TestCase):
    def test_tray_available_is_boolean(self):
        self.assertIsInstance(tray_available(), bool)

    def test_icon_image_is_programmatic(self):
        """图标程序化生成，不依赖外部图片文件（打包时不会漏资源）。"""
        img = _make_image()
        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, "RGBA")

    def test_app_identity_constants(self):
        self.assertTrue(APP_ID)
        self.assertIn("WorkBuddy", APP_NAME)


class ShutdownTests(unittest.TestCase):
    """退出必须释放端口，否则重开进程会 Address already in use。"""

    def test_app_exposes_shutdown_and_quit_hook(self):
        from workbuddy.app import App
        self.assertTrue(hasattr(App, "shutdown"))
        self.assertTrue(hasattr(App, "stop"))

    def test_shutdown_releases_web_server(self):
        """构造一个最小假对象，验证 shutdown 会关掉 _web_server。"""
        from workbuddy.app import App

        class FakeServer:
            def __init__(self):
                self.closed = False

            def shutdown(self):
                pass

            def server_close(self):
                self.closed = True

        app = App.__new__(App)          # 绕过 __init__ 的重依赖
        app._stop = False
        app.usage_scanner = None
        app.weather = type("W", (), {"stop": lambda self: None})()
        app.link = None
        app.on_quit = None
        srv = FakeServer()
        app._web_server = srv
        app.shutdown()
        self.assertTrue(srv.closed, "shutdown 必须 server_close 释放端口")
        self.assertIsNone(app._web_server)
        self.assertTrue(app._stop)

    def test_shutdown_survives_failing_subsystems(self):
        """串口/天气任一抛异常都不能中断清理流程。"""
        from workbuddy.app import App

        class Boom:
            def stop(self):
                raise RuntimeError("serial dead")

        app = App.__new__(App)
        app._stop = False
        app.usage_scanner = Boom()
        app.weather = Boom()
        app.link = Boom()
        app._web_server = None
        called = []
        app.on_quit = lambda: called.append(1)
        app.shutdown()                  # 不应抛异常
        self.assertTrue(called, "即使子系统异常也要走到 on_quit")
        self.assertTrue(app._stop)


class TelemetryFreshnessTests(unittest.TestCase):
    """监控数据必须能区分「实时值」与「冻结的旧值」。"""

    def test_snapshot_exposes_age_fields(self):
        from workbuddy.app import App
        import inspect
        src = inspect.getsource(App.status_snapshot)
        for key in ("telemetry_age", "telemetry_at"):
            self.assertIn(key, src, f"/api/status 必须返回 {key}")

    def test_snapshot_reports_none_age_before_first_tick(self):
        from workbuddy.app import App
        app = App.__new__(App)
        app.profile_lock = __import__("threading").Lock()
        app.profile = {"version": 1}
        app.profile_status = "待同步"
        app.profile_error = ""
        app.applied_version = None
        app.fw_caps = None
        app.usage_scanner = None
        app.link = None
        app.tasks = []
        app.last = {}
        app._last_tick_at = 0.0
        app.cfg = type("C", (), {"interval": 1.0})()
        snap = app.status_snapshot()
        self.assertIsNone(snap["telemetry_age"],
                          "还没采到数据时 age 应为 None，而不是假装 0")

    def test_snapshot_age_grows_when_collection_stalls(self):
        """采集停摆时 age 必须随之变大，前端据此提示陈旧。"""
        import threading, time
        from workbuddy.app import App
        app = App.__new__(App)
        app.profile_lock = threading.Lock()
        app.profile = {"version": 1}
        app.profile_status = "待同步"
        app.profile_error = ""
        app.applied_version = None
        app.fw_caps = None
        app.usage_scanner = None
        app.link = None
        app.tasks = []
        app.last = {"cpu": 14}
        app.cfg = type("C", (), {"interval": 1.0})()
        app._last_tick_at = time.time() - 30
        snap = app.status_snapshot()
        self.assertGreater(snap["telemetry_age"], 20,
                           "停摆 30 秒后 age 必须显著大于陈旧阈值 8 秒")
        self.assertEqual(snap["telemetry"]["cpu"], 14, "旧值仍保留，不清空")


class PollResilienceSourceTests(unittest.TestCase):
    """前端轮询韧性：防止请求堆积、后台标签页降频后能立刻恢复。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (Path(__file__).resolve().parent.parent / "workbuddy" /
                  "web_assets" / "app.js").read_text(encoding="utf-8")

    def test_has_inflight_guard(self):
        self.assertIn("if(polling)return", self.js,
                      "缺少 in-flight 守卫，慢响应时请求会堆积")

    def test_has_visibility_recovery(self):
        self.assertIn("visibilitychange", self.js,
                      "后台标签页会被降频到 1 分钟一次，回前台必须立即补拉")
        self.assertIn("focus", self.js)

    def test_has_exponential_backoff(self):
        self.assertIn("failStreak", self.js, "连续失败应退避，避免疯狂重试")
        self.assertIn("Math.pow", self.js)

    def test_stale_is_marked_not_silently_frozen(self):
        self.assertIn("is-stale", self.js, "陈旧状态必须显式标记")
        self.assertIn("telemetry_age", self.js, "需依据后端数据年龄判定陈旧")

    def test_no_naive_setinterval_for_polling(self):
        """轮询不能用裸 setInterval，否则无法取消/重排（退避与前台恢复依赖它）。"""
        self.assertNotIn("setInterval(poll", self.js)


if __name__ == "__main__":
    unittest.main()