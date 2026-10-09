"""WorkBuddy 会话识别 + 背景图上传链路的回归测试。

背景（两个真实故障）：
1. 「屏幕识别不到 workbuddy 会话」——aiwatch 只注册了 claude/codex/zcode三个
   Source，WorkBuddy 自己的会话从未被监听。本模块锁定 Source 的存在性与
   时间戳单位（db 用毫秒，当成秒会导致所有会话被判为过期而永不显示）。
2. 「自定义背景图上传失败」——web.py 的 CSP 为 default-src 'self'，把
   URL.createObjectURL() 产生的 blob: 图片拦掉，解码失败。这里锁定
   img-src 必须放开 blob:。
"""
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy.aiwatch import AIWatcher
from workbuddy.aiwatch.workbuddy import WorkBuddySource, _STATUS_MAP, _ms_to_s
from workbuddy.config import Config, DEFAULT_ROOTS

SCHEMA = """
CREATE TABLE sessions (
  id TEXT PRIMARY KEY, cwd TEXT NOT NULL, user_id TEXT NOT NULL,
  title TEXT, status TEXT NOT NULL DEFAULT 'Pending',
  created_at INTEGER, updated_at INTEGER, last_activity_at INTEGER,
  deleted_at INTEGER, is_background_automation INTEGER
);
"""


def _make_db(root: Path, rows):
    conn = sqlite3.connect(root / "workbuddy.db")
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO sessions (id,cwd,user_id,title,status,created_at,"
        "updated_at,last_activity_at,deleted_at,is_background_automation)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


class TimestampUnitTests(unittest.TestCase):
    def test_ms_and_seconds_both_handled(self):
        """db 是毫秒；防御性地兼容秒。1e11 是分界（约等于 1973 年的毫秒）。"""
        self.assertAlmostEqual(_ms_to_s(1791538850050), 1791538850.05, places=2)
        self.assertAlmostEqual(_ms_to_s(1791538850), 1791538850.0, places=2)
        self.assertEqual(_ms_to_s(None), 0.0)
        self.assertEqual(_ms_to_s(0), 0.0)
        self.assertEqual(_ms_to_s("bad"), 0.0)

    def test_milliseconds_would_be_absurdly_stale_if_treated_as_seconds(self):
        """把毫秒当秒会让 age 变成上亿秒——这正是会话不被显示的直接成因。"""
        now_ms = int(time.time() * 1000)
        age_if_seconds = time.time() - now_ms       # 错误算法
        self.assertGreater(abs(age_if_seconds), 1e9)
        age_correct = time.time() - _ms_to_s(now_ms)  # 正确算法
        self.assertLess(abs(age_correct), 5)


class StatusMappingTests(unittest.TestCase):
    def test_known_statuses_mapped(self):
        self.assertEqual(_STATUS_MAP["working"], "GENERATING")
        self.assertEqual(_STATUS_MAP["completed"], "WAIT_INPUT")
        self.assertEqual(_STATUS_MAP["error"], "ERROR")

    def test_terminal_statuses_become_idle_and_get_dropped(self):
        for s in ("terminated", "archived"):
            self.assertEqual(_STATUS_MAP[s], "IDLE")

    def test_unknown_status_is_absent(self):
        self.assertNotIn("pending_unknown", _STATUS_MAP)


class WorkBuddySourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = WorkBuddySource(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_db_returns_empty(self):
        self.assertEqual(self.source.poll_all(time.time(), {}), [])

    def test_active_working_session_detected(self):
        now_ms = int(time.time() * 1000)
        _make_db(self.root, [
            ("sess-1", "/Users/x/Desktop/myproj", "u", "标题", "working",
             now_ms, now_ms, now_ms, None, None)])
        acts = self.source.poll_all(time.time(), {})
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0].tool, "workbuddy")
        self.assertEqual(acts[0].state, "GENERATING")
        self.assertEqual(acts[0].project, "myproj")
        self.assertLess(time.time() - acts[0].ts, 5)

    def test_background_automation_excluded(self):
        """定时任务不是用户在盯的交互会话，不应挤占屏幕的 4 个槽位。"""
        now_ms = int(time.time() * 1000)
        _make_db(self.root, [
            ("auto-1", "/Users/x/proj", "u", "自动任务", "working",
             now_ms, now_ms, now_ms, None, 1)])
        self.assertEqual(self.source.poll_all(time.time(), {}), [])

    def test_deleted_sessions_excluded(self):
        now_ms = int(time.time() * 1000)
        _make_db(self.root, [
            ("del-1", "/Users/x/proj", "u", "已删", "working",
             now_ms, now_ms, now_ms, now_ms, None)])
        self.assertEqual(self.source.poll_all(time.time(), {}), [])

    def test_heartbeat_keeps_long_running_session_alive(self):
        """db 的 updated_at 只在消息边界更新；心跳文件应能把长回合继续点亮。"""
        stale_ms = int((time.time() - 3600) * 1000)   # 一小时前
        _make_db(self.root, [
            ("live-1", "/Users/x/proj", "u", "长任务", "working",
             stale_ms, stale_ms, stale_ms, None, None)])

        # 未写心跳时，活动时间就是 db 里的旧值（1 小时前）
        before = self.source.poll_all(time.time(), {})
        self.assertEqual(len(before), 1)
        self.assertGreater(time.time() - before[0].ts, 3000)

        # 写入新鲜心跳后，活动时间必须被拉到现在
        (self.root / "sessions").mkdir(exist_ok=True)
        (self.root / "sessions" / "9999.json").write_text(
            '{"sessionId":"live-1","lastHeartbeat":%d}' % int(time.time() * 1000),
            encoding="utf-8")
        after = self.source.poll_all(time.time(), {})
        self.assertLess(time.time() - after[0].ts, 5)

    def test_tool_name_fits_device_field(self):
        """设备端 char tool[12]，workbuddy(9)+NUL 必须放得下。"""
        self.assertLess(len("workbuddy") + 1, 12)


class WatcherRegistrationTests(unittest.TestCase):
    def test_workbuddy_registered_in_config_roots(self):
        self.assertIn("workbuddy", DEFAULT_ROOTS)

    def test_watcher_has_workbuddy_source(self):
        w = AIWatcher(Config())
        kinds = {type(s).__name__ for s in w.sources}
        self.assertIn("WorkBuddySource", kinds)

    def test_fresh_window_defined_for_workbuddy(self):
        from workbuddy.aiwatch import _FRESH
        self.assertIn("workbuddy", _FRESH)


class BackgroundImageCspTests(unittest.TestCase):
    """CSP 必须放开 blob:，否则 new Image() 解码用户选的图会被拦掉。"""

    def _csp(self):
        """取出完整 CSP。

        web.py 里 CSP 是 send_header() 的第二个参数、由多段字符串字面量拼接而成，
        正则很容易只截到一段。这里用 ast 精确取出该调用的所有字符串实参再拼接。
        """
        import ast
        path = Path(__file__).resolve().parent.parent / "workbuddy" / "web.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "send_header"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and first.value == "Content-Security-Policy"):
                continue
            parts = [a.value for a in node.args[1:]
                     if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            self.assertTrue(parts, "CSP 实参不是字符串字面量")
            return "".join(parts)
        self.fail("web.py 里找不到 send_header('Content-Security-Policy', ...) 调用")

    def test_csp_allows_blob_images(self):
        csp = self._csp()
        self.assertIn("img-src", csp)
        self.assertIn("blob:", csp)

    def test_scripts_stay_restricted_to_self(self):
        """放开 img-src 不能顺带放开 script-src。"""
        csp = self._csp()
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)


if __name__ == "__main__":
    unittest.main()