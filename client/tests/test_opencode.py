"""OpenCode 终端 agent 会话监听的回归测试。

用户要求：主流 AI agent 编辑器——包括纯终端使用方式的（Claude Code、Codex、
OpenCode 这类 CLI 工具）——都要能识别和支持，不局限于 IDE。

OpenCode 把会话存在 SQLite（~/.local/share/opencode/opencode.db）：
  session(id, directory, title, time_updated ms)
  message(id, session_id, time_updated ms, data JSON 含 role)
  part(id, session_id, time_updated ms, data JSON 含 type)
本测试用临时库复刻该 schema，锁住状态推断与只读访问两条底线。
"""
import os
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy.aiwatch import AIWatcher, THINKING, TOOL, WAIT_INPUT
from workbuddy.aiwatch.opencode import OpenCodeSource
from workbuddy.config import Config, DEFAULT_ROOTS

SCHEMA = """
CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT NOT NULL,
                      title TEXT NOT NULL, time_updated INTEGER NOT NULL);
CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                      time_updated INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE part (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                   time_updated INTEGER NOT NULL, data TEXT NOT NULL);
"""

NOW_MS = 1_800_000_000_000      # 任意毫秒时刻

def _make_db_into(root, rows):
    import sqlite3
    con = sqlite3.connect(root / "opencode.db")
    con.executescript(SCHEMA)
    for i, (kind, sid, directory, payload, age_ms) in enumerate(rows):
        ts = int((NOW_MS - age_ms) * 1000)
        if kind == "session":
            con.execute("INSERT INTO session VALUES (?,?,?,?)",
                        (sid, directory, directory, ts))
        elif kind == "message":
            con.execute("INSERT INTO message VALUES (?,?,?,?)",
                        (f"m{i}", sid, ts, f'{{"role": "{payload}"}}'))
    con.commit()
    con.close()


def _make_db(root: Path, rows):
    """rows: (kind, session_id, directory, role_or_type, age_ms)"""
    con = sqlite3.connect(root / "opencode.db")
    con.executescript(SCHEMA)
    for i, (kind, sid, directory, payload, age_ms) in enumerate(rows):
        ts = NOW_MS - age_ms
        if kind == "session":
            con.execute("INSERT INTO session VALUES (?,?,?,?)",
                        (sid, directory, directory, ts))
        elif kind == "message":
            con.execute("INSERT INTO message VALUES (?,?,?,?)",
                        (f"m{i}", sid, ts, f'{{"role": "{payload}"}}'))
        elif kind == "part":
            con.execute("INSERT INTO part VALUES (?,?,?,?)",
                        (f"p{i}", sid, ts, f'{{"type": "{payload}"}}'))
    con.commit()
    con.close()


class OpenCodeSourceTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.root = Path(tempfile.mkdtemp())
        self.source = OpenCodeSource(self.root)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _seed(self, rows):
        _make_db(self.root, rows)

    def test_missing_db_returns_empty(self):
        self.assertEqual(self.source.poll_all(NOW_SEC := __import__("time").time(), {}), [])

    def test_user_last_message_means_thinking(self):
        """最后一条是 user → agent 正在生成回复（THINKING）。"""
        self._seed([
            ("session", "ses1", "/Users/x/demo", None, 0),
            ("message", "ses1", "/Users/x/demo", "user", 5_000),
            ("message", "ses1", "/Users/x/demo", "assistant", 60_000),
        ])
        now = NOW_MS / 1000
        acts = self.source.poll_all(now, {})
        self.assertEqual([a.state for a in acts], [THINKING])
        self.assertEqual(acts[0].project, "demo")

    def test_assistant_last_means_waiting(self):
        """最后一条是 assistant → 回复完毕，等用户下一句。"""
        self._seed([
            ("session", "ses1", "/Users/x/demo", None, 0),
            ("message", "ses1", "/Users/x/demo", "assistant", 10_000),
            ("message", "ses1", "/Users/x/demo", "user", 60_000),
        ])
        acts = self.source.poll_all(NOW_MS / 1000, {})
        self.assertEqual([a.state for a in acts], [WAIT_INPUT])

    def test_fresh_tool_part_means_tool(self):
        """assistant 消息之后 20 秒内出现 tool 部件 → 正在执行工具。"""
        self._seed([
            ("session", "ses1", "/Users/x/demo", None, 0),
            ("message", "ses1", "/Users/x/demo", "assistant", 8_000),
            ("part", "ses1", "/Users/x/demo", "tool", 2_000),
        ])
        acts = self.source.poll_all(NOW_MS / 1000, {})
        self.assertEqual([a.state for a in acts], [TOOL])

    def test_stale_activity_not_reported(self):
        """超过 90 秒活跃窗口的会话不上报（与其它源同窗口）。"""
        self._seed([
            ("session", "ses1", "/Users/x/demo", None, 0),
            ("message", "ses1", "/Users/x/demo", "assistant", 200_000),
        ])
        self.assertEqual(self.source.poll_all(NOW_MS / 1000, {}), [])

    def test_multiple_sessions_all_reported(self):
        self._seed([
            ("session", "ses1", "/Users/x/demo", None, 0),
            ("message", "ses1", "/Users/x/demo", "user", 5_000),
            ("session", "ses2", "/Users/x/other", None, 0),
            ("message", "ses2", "/Users/x/other", "assistant", 9_000),
        ])
        self.assertEqual(len(self.source.poll_all(NOW_MS / 1000, {})), 2)

    def test_readonly_database_still_works(self):
        """只读 URI 打开：把库文件设为 0444 也能读——绝不写 OpenCode 的库。"""
        import os
        self._seed([
            ("session", "ses1", "/Users/x/demo", None, 0),
            ("message", "ses1", "/Users/x/demo", "assistant", 5_000),
        ])
        db = self.root / "opencode.db"
        os.chmod(db, 0o444)
        try:
            self.assertEqual(len(self.source.poll_all(NOW_MS / 1000, {})), 1)
        finally:
            os.chmod(db, 0o644)

    def test_no_json1_returns_empty_not_crash(self):
        """库缺 JSON1 扩展等 sqlite 错误：安静返回空，不影响其它源。"""
        original = OpenCodeSource._poll_open
        try:
            def boom(self_con, now):
                raise sqlite3.OperationalError("no such function: json_extract")
            OpenCodeSource._poll_open = boom
            self.assertEqual(self.source.poll_all(NOW_MS / 1000, {}), [])
        finally:
            OpenCodeSource._poll_open = original


class RegistrationTests(unittest.TestCase):
    def test_source_registered_in_watcher(self):
        w = AIWatcher(Config())
        names = [type(x).__name__ for x in w.sources]
        self.assertIn("OpenCodeSource", names)

    def test_fresh_window_defined(self):
        from workbuddy.aiwatch import _FRESH
        self.assertEqual(_FRESH.get("opencode"), 45)

    def test_default_root_configured(self):
        self.assertEqual(str(DEFAULT_ROOTS["opencode"]),
                         str(Path.home() / ".local/share/opencode"))

    def test_procwatch_recognises_opencode(self):
        from workbuddy import procwatch
        self.assertIn("opencode", procwatch.MATCH)
        self.assertEqual(procwatch.TOOL_LABELS.get("opencode"), "OpenCode")
        self.assertTrue(procwatch.should_gate("opencode", set(), log_age=600))


class IntegrationTests(unittest.TestCase):
    def test_watcher_with_real_opencode_source(self):
        """AIWatcher 全链路：OpenCode 会话进 poll_all 并带项目名/工具名。"""
        import tempfile
        root = Path(tempfile.mkdtemp())
        _make_db_into(root, [
            ("session", "ses9", "/Users/x/my-project", None, 0),
            ("message", "ses9", "/Users/x/my-project", "assistant", 3_000),
        ])
        now = NOW_MS / 1000
        cfg = Config()
        cfg.roots["opencode"] = str(root)
        for k in ("claude", "codex", "zcode", "workbuddy"):
            cfg.roots[k] = str(root / "empty")
        w = AIWatcher(cfg, processes=None)
        out = w.poll_all(now)
        match = [t for t in out if t["tool"] == "opencode"]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["project"], "my-project")
        self.assertEqual(match[0]["state"], WAIT_INPUT)

if __name__ == "__main__":
    unittest.main()
