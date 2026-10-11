"""进程识别 + 会话门控的回归测试。

背景（用户实测反馈）：「我用的工具是 ZCode，Codex 已经关掉了，屏幕并没有
体现出来，还停留在 Codex」。日志推断只能证明工具写过日志，不能证明它还开着：
Codex 结束一轮写 task_complete → 等你回复，而这一状态的新鲜窗口是 30 分钟
（aiwatch._WAIT_FRESH），所以关掉软件后小屏会一直停在它上面。

本模块锁住三件事：
  1. 进程特征只认进程名 / 可执行路径 / 解释器脚本参数——**不认任意命令行文本**
     （ZCode agent 的 shell 里出现 codex/claude 字样是常态，子串匹配会让门控失效）；
  2. 门控只在「有特征 + 不在运行 + 日志已安静」三条同时成立时丢弃会话；
  3. 扫描不可用时绝不门控——宁可多显示，不能误删正在跑的会话。
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy import procwatch
from workbuddy.aiwatch import Activity, AIWatcher, WAIT_INPUT, THINKING
from workbuddy.config import Config


class SignatureTests(unittest.TestCase):
    """匹配规则：只在进程名 / 可执行路径 / 解释器脚本参数上认，不看任意文本。"""

    def test_desktop_app_name(self):
        self.assertTrue(procwatch._matches("zcode", "ZCode", "/Applications/ZCode.app/Contents/MacOS/ZCode", ["ZCode"]))

    def test_helper_process_name(self):
        """ZCode 的 Electron 子进程名叫 "ZCode Helper"，主进程 ZCode 一定在，
        但 zcode-cli 这类子进程也要能单独认出来。"""
        self.assertTrue(procwatch._matches("zcode", "zcode-cli", "", ["zcode-cli"]))
        self.assertTrue(procwatch._matches("zcode", "zcode-host-local", "", ["zcode-host-local-1"]))

    def test_app_bundle_path_hint(self):
        self.assertTrue(procwatch._matches(
            "cursor", "Cursor Helper", "/Applications/Cursor.app/Contents/MacOS/Cursor Helper", []))

    def test_node_wrapped_cli_via_existing_script_path(self):
        """Claude Code / Codex 常以 node 脚本运行——进程名是 node，只能看参数，
        且只认「指向真实存在的文件」的参数。"""
        import os, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "claude")
            Path(script).write_text("#!/usr/bin/env node\n")
            self.assertTrue(procwatch._matches("claude", "node", "/usr/bin/node", ["node", script]))
            # 不存在的路径不算
            self.assertFalse(procwatch._matches("claude", "node", "/usr/bin/node",
                                                ["node", os.path.join(tmp, "claude-missing")]))

    def test_python_module_invocation(self):
        self.assertTrue(procwatch._matches(
            "workbuddy", "Python", "/usr/bin/python3",
            ["/usr/bin/python3", "-m", "workbuddy", "run", "--no-console"]))

    def test_mentioned_words_are_not_a_match(self):
        """回归：ZCode agent 的 shell 命令行里出现 codex/claude 时，不能把已经
        关掉的工具判成在运行——否则进程门控直接失效（实测踩到过）。"""
        noisy = ("/bin/zsh", "-c",
                 ". ~/.zcode/cli/exec/shell-snapshots/x.sh\n"
                 "grep -r codex .\ncat docs/claude-notes.md\n"
                 'python3 - <<PY\nfrom workbuddy.aiwatch import AIWatcher\nPY')
        self.assertFalse(procwatch._matches("codex", "zsh", "/bin/zsh", list(noisy)))
        self.assertFalse(procwatch._matches("claude", "zsh", "/bin/zsh", list(noisy)))
        # grep 的参数也不算
        self.assertFalse(procwatch._matches("codex", "grep", "/usr/bin/grep", ["grep", "-r", "codex", "."]))

    def test_unrelated_process_matches_nothing(self):
        for key in procwatch.MATCH:
            self.assertFalse(procwatch._matches(
                key, "Google Chrome",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", ["Chrome"]))

    def test_every_label_has_a_match_rule(self):
        for key in list(procwatch.TOOL_LABELS) + list(procwatch.EDITOR_LABELS):
            self.assertIn(key, procwatch.MATCH, f"{key} 有标签却没有匹配规则")


class GatingRuleTests(unittest.TestCase):
    def test_tool_absent_and_log_quiet_is_gated(self):
        self.assertTrue(procwatch.should_gate("codex", set(), log_age=600))

    def test_running_tool_is_never_gated(self):
        self.assertFalse(procwatch.should_gate("codex", {"codex"}, log_age=600))

    def test_fresh_log_beats_missing_process(self):
        """刚写完日志说明它还活着——进程名没认出来也不能误删。"""
        self.assertFalse(procwatch.should_gate("claude", set(), log_age=5))

    def test_unknown_tool_is_never_gated(self):
        self.assertFalse(procwatch.should_gate("some-new-agent", set(), log_age=9999))

    def test_scan_failure_returns_none_not_empty(self):
        """None 表示「不知道」，空集合表示「一个都没有」——两者必须分开。"""
        procwatch.reset_cache()
        original = procwatch._scan
        try:
            procwatch._scan = lambda: None
            self.assertIsNone(procwatch.running_tools(now=1.0, ttl=0))
        finally:
            procwatch._scan = original
            procwatch.reset_cache()

    def test_report_marks_gating_flag(self):
        procwatch.reset_cache()
        original = procwatch._scan
        try:
            procwatch._scan = lambda: {"zcode", "cursor"}
            report = procwatch.report()
        finally:
            procwatch._scan = original
            procwatch.reset_cache()
        self.assertTrue(report["ok"])
        by_id = {t["id"]: t for t in report["tools"]}
        self.assertTrue(by_id["zcode"]["running"])
        self.assertFalse(by_id["codex"]["running"])
        self.assertTrue(by_id["codex"]["gates"])
        self.assertEqual(by_id["codex"]["capability"], "session")
        # 编辑器没有日志来源，不参与门控
        self.assertIn("cursor", by_id)
        self.assertFalse(by_id["cursor"]["gates"])
        self.assertEqual(by_id["cursor"]["capability"], "process")

    def test_report_when_scan_unavailable(self):
        procwatch.reset_cache()
        original = procwatch._scan
        try:
            procwatch._scan = lambda: None
            report = procwatch.report()
        finally:
            procwatch._scan = original
            procwatch.reset_cache()
        self.assertFalse(report["ok"])
        self.assertEqual(report["tools"], [])


class _FakeSource:
    def __init__(self, acts):
        self.acts = acts

    def poll_all(self, now, cache):
        return list(self.acts)


def _watcher(acts, processes):
    w = AIWatcher(Config(), processes=processes)
    w.sources = [_FakeSource(acts)]
    return w


class WatcherGateTests(unittest.TestCase):
    def test_closed_codex_disappears_immediately(self):
        """核心场景：Codex 关掉后 30 分钟的「等你回复」不该继续占屏。"""
        now = time.time()
        acts = [Activity("codex", WAIT_INPUT, now - 600, sid="sess-1", project="demo")]
        w = _watcher(acts, processes=lambda: {"zcode"})
        self.assertEqual(w.poll_all(now), [])
        # 不门控（进程在跑）时它仍会出现
        w2 = _watcher(acts, processes=lambda: {"codex"})
        self.assertEqual(len(w2.poll_all(now)), 1)

    def test_running_tool_keeps_waiting_task(self):
        now = time.time()
        acts = [Activity("claude", WAIT_INPUT, now - 900, sid="s2", project="demo")]
        w = _watcher(acts, processes=lambda: {"claude"})
        out = w.poll_all(now)
        self.assertEqual([t["tool"] for t in out], ["claude"])

    def test_gate_drops_only_the_closed_tool(self):
        now = time.time()
        acts = [
            Activity("codex", WAIT_INPUT, now - 600, sid="c1", project="old"),
            Activity("zcode", THINKING, now - 3, sid="z1", project="now"),
        ]
        w = _watcher(acts, processes=lambda: {"zcode"})
        out = w.poll_all(now)
        self.assertEqual([t["tool"] for t in out], ["zcode"])

    def test_no_sampler_means_no_gating(self):
        now = time.time()
        acts = [Activity("codex", WAIT_INPUT, now - 600, sid="c1", project="old")]
        w = _watcher(acts, processes=None)
        self.assertEqual(len(w.poll_all(now)), 1)

    def test_scan_failure_means_no_gating(self):
        now = time.time()
        acts = [Activity("codex", WAIT_INPUT, now - 600, sid="c1", project="old")]
        w = _watcher(acts, processes=lambda: None)
        self.assertEqual(len(w.poll_all(now)), 1)

    def test_sampler_exception_means_no_gating(self):
        now = time.time()
        acts = [Activity("codex", WAIT_INPUT, now - 600, sid="c1", project="old")]

        def boom():
            raise OSError("psutil exploded")

        self.assertEqual(len(_watcher(acts, processes=boom).poll_all(now)), 1)

    def test_legacy_new_constructed_watcher_still_works(self):
        """历史单测用 __new__ 直构：除 sources/_bursts 外没有别的属性，
        尤其没有 _processes——门控必须安静跳过，不能抛 AttributeError。"""
        now = time.time()
        w = AIWatcher.__new__(AIWatcher)
        w.sources = [_FakeSource([Activity("codex", THINKING, now - 2, sid="x")])]
        w._bursts, w._cache = {}, {}
        out = w.poll_all(now)
        self.assertEqual(len(out), 1)

    def test_config_switch_disables_the_gate(self):
        cfg = Config()
        cfg.proc_gate = False
        self.assertIsNone(AIWatcher(cfg)._processes)


if __name__ == "__main__":
    unittest.main()
