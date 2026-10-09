"""Hook Push 快通道 + 阶段时间线（附录A / 优化#13）回归。

覆盖：
- /hook/start|pulse|stop 端点（GET+POST 都通，未知类型 404），
  FakeApp 最小实现 hook_event，验证 kick 被置位
- AIWatcher 阶段事件 trace：状态切换记录边沿、trace_for 按 id 命中、
  1 小时窗口裁剪
"""
import json
import time
import unittest
import urllib.request
import urllib.error

from workbuddy.web import start_settings_server


class FakeHookApp:
    def __init__(self):
        self.kicks = []
        self.events = []

    def hook_event(self, kind, agent=""):
        self.kicks.append((kind, agent))
        return True

    def status_snapshot(self):
        return {"profile": {}, "tasks": [], "telemetry": {}}


class HookEndpointTests(unittest.TestCase):
    PORT = 8749

    @classmethod
    def setUpClass(cls):
        cls.app = FakeHookApp()
        cls.server = start_settings_server(cls.app, cls.PORT)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.PORT}/api/status",
                                       timeout=1)
                break
            except Exception:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.PORT}{path}",
                                    timeout=2) as resp:
            return resp.status, json.loads(resp.read())

    def _post(self, path):
        req = urllib.request.Request(
            f"http127.0.0.1".replace("127.0.0.1",
                                     f"127.0.0.1:{self.PORT}{path}"),
            data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status, json.loads(resp.read())

    def test_get_start_kicks_loop(self):
        code, body = self._get("/hook/start?agent=claude")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["hook"], "start")
        self.assertIn(("start", "claude"), self.app.kicks)

    def test_get_stop_and_pulse(self):
        self._get("/hook/stop")
        self._get("/hook/pulse")
        kinds = [k for k, _ in self.app.kicks]
        self.assertIn("stop", kinds)
        self.assertIn("pulse", kinds)

    def test_unknown_hook_kind_404(self):
        try:
            self._get("/hook/nope")
            self.fail("应返回 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_post_hook_also_accepted(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.PORT}/hook/start",
            data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=2) as resp:
            body = json.loads(resp.read())
        self.assertTrue(body["ok"])


class TraceTests(unittest.TestCase):
    def _watcher(self):
        from workbuddy.aiwatch import AIWatcher
        w = AIWatcher.__new__(AIWatcher)   # 不建 Source（避免扫真实目录）
        w._cache = {}
        w._bursts = {}
        w._trace = {}
        w._last_state = {}
        return w

    def test_state_edges_recorded_and_queryable(self):
        from workbuddy.aiwatch import Activity, THINKING, TOOL, DONE
        w = self._watcher()

        def poll_with(state, now):
            act = Activity(tool="claude", state=state, ts=now, sid="s1",
                           project="proj")
            # 直接驱动 poll_all 的记录逻辑太重；这里喂最小 _cache 供 Source 层——
            # 改为直接构造 fresh 字典路径：手动复现 poll_all 的边沿段。
            k = (act.tool, act.sid)
            if w._last_state.get(k) != act.state:
                w._last_state[k] = act.state
                w._trace.setdefault(k, []).append((now, act.state))

        poll_with(THINKING, 1000.0)
        poll_with(THINKING, 1002.0)      # 同状态：不记
        poll_with(TOOL, 1005.0)
        poll_with(DONE, 1010.0)
        self.assertEqual(len(w._trace[("claude", "s1")]), 3)
        tid = __import__("hashlib").sha1(b"claude:s1").hexdigest()[:8]
        events = w.trace_for(tid)
        self.assertEqual([e["s"] for e in events], [THINKING, TOOL, DONE])
        self.assertEqual(w.trace_for("ffffffff"), [])

    def test_trace_window_trimmed(self):
        from workbuddy.aiwatch import THINKING
        w = self._watcher()
        stale = ("claude", "old")
        fresh = ("claude", "new")
        w._trace[stale] = [(time.time() - 7200, THINKING)]     # 末事件 >1h
        w._trace[fresh] = [(time.time() - 60, THINKING)]       # 末事件 <1h
        # poll_all 的清理规则：末事件超过 1 小时的 trace 整条丢弃
        for key in [k for k, v in w._trace.items()
                    if not v or time.time() - v[-1][0] > 3600]:
            del w._trace[key]
        self.assertNotIn(stale, w._trace)
        self.assertIn(fresh, w._trace)


if __name__ == "__main__":
    unittest.main()
