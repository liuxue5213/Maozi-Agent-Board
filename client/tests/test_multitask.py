"""多编辑器 × 多项目：排序规则与「刚完成」记录的回归测试。

背景（用户实测场景）：同时开多个 AI 编辑器，每个编辑器里还有多个项目并行。
两个由此暴露的问题：

1. **排序不稳**：原来的排序是「状态优先级 + 最近更新」，于是同一个会话每次写日志
   都会跳到前面，而且一个挂了二十分钟的「等你回复」会被刚动过的新任务一直挤到
   看不见。现在：需要你/出错按**等得最久**排前，进行中按**工具→项目固定顺序**。
2. **完成没有可感知的提示**：完成靠列表重排来"让用户看到"是不可靠的——只有正好
   看着的那一刻才可见，而且重排会打乱空间记忆。改成独立的「刚完成」记录，
   顺序本身就是时间信息，再配一个未读计数解决"当时没看"。
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy.aiwatch import (Activity, AIWatcher, DONE, ERROR, GENERATING,
                               MAX_TASKS, THINKING, TOOL, WAIT_INPUT)
from workbuddy.config import Config

NOW = 1_700_000_000.0
# 夹具的 age 必须待在 aiwatch._FRESH 的新鲜窗口内，否则条目会在排序之前就被
# 当成过期丢掉（claude/codex 只有 45 秒，zcode/workbuddy 300 秒，
# 等你回复/待确认 1800 秒）——这条踩过一次，夹具 age 写大了会让用例假失败。


class _FakeSource:
    def __init__(self, acts):
        self.acts = list(acts)

    def poll_all(self, now, cache):
        return list(self.acts)


def _watcher(acts, processes=None):
    """进程门控关掉：这些用例只关心排序与完成记录。"""
    w = AIWatcher(Config(), processes=processes)
    w.sources = [_FakeSource(acts)]
    return w


def _act(tool, state, age, sid, project=""):
    return Activity(tool, state, NOW - age, sid=sid, project=project)


class OrderingTests(unittest.TestCase):
    def test_longest_waiting_surfaces_first(self):
        """两个都在等回复：等得久的在前（原来是最新写日志的在前）。"""
        acts = [_act("zcode", WAIT_INPUT, 30, "fresh", "新项目"),
                _act("claude", WAIT_INPUT, 1200, "stale", "旧项目")]
        out = _watcher(acts).poll_all(NOW)
        self.assertEqual([t["project"] for t in out], ["旧项目", "新项目"])

    def test_actionable_states_outrank_running(self):
        acts = [_act("zcode", TOOL, 5, "run", "跑着的"),
                _act("codex", ERROR, 30, "err", "报错的"),      # codex 窗口 45s
                _act("claude", WAIT_INPUT, 900, "wait", "等你的")]
        out = _watcher(acts).poll_all(NOW)
        self.assertEqual(out[0]["state"], ERROR)
        self.assertEqual(out[1]["state"], WAIT_INPUT)
        self.assertEqual(out[2]["state"], TOOL)

    def test_running_sessions_keep_a_stable_order(self):
        """进行中按固定顺序排：同一批会话，最新更新时间变了也不许换位。

        组内先沿用状态优先级（执行工具 > 生成中 > 思考中），同状态再按
        工具名 → 项目名 —— 关键是**不看时间**，否则每次写日志都会跳动。
        """
        first = [_act("zcode", TOOL, 10, "a", "alpha"),
                 _act("claude", GENERATING, 20, "b", "beta"),
                 _act("codex", THINKING, 30, "c", "gamma")]
        before = [(t["tool"], t["project"]) for t in _watcher(first).poll_all(NOW)]

        # 把 claude 那条刷成最新，其余不动——按时间排就会跳位
        shuffled = [_act("zcode", TOOL, 10, "a", "alpha"),
                    _act("claude", GENERATING, 1, "b", "beta"),
                    _act("codex", THINKING, 30, "c", "gamma")]
        after = [(t["tool"], t["project"]) for t in _watcher(shuffled).poll_all(NOW)]
        self.assertEqual(before, after, "更新时间变了，顺序不该变")
        self.assertEqual(before, [("zcode", "alpha"), ("claude", "beta"), ("codex", "gamma")])

    def test_same_state_sessions_sort_by_tool_then_project(self):
        """同一状态内按 工具名 → 项目名，与到达顺序无关。"""
        acts = [_act("zcode", TOOL, 10, "s1", "zeta"),
                _act("codex", TOOL, 20, "s2", "alpha"),
                _act("zcode", TOOL, 30, "s3", "alpha")]
        out = _watcher(acts).poll_all(NOW)
        self.assertEqual([(t["tool"], t["project"]) for t in out],
                         [("codex", "alpha"), ("zcode", "alpha"), ("zcode", "zeta")])

    def test_done_sinks_to_the_bottom(self):
        acts = [_act("zcode", DONE, 1, "finished", "刚完成的"),
                _act("claude", TOOL, 5, "running", "还在跑")]
        out = _watcher(acts).poll_all(NOW)
        self.assertEqual([t["state"] for t in out], [TOOL, DONE])

    def test_more_than_four_sessions_are_tracked(self):
        """多项目并行：以前硬截到 4 条，现在跟踪到 MAX_TASKS。"""
        acts = [_act("zcode", TOOL, 10 + i, f"s{i}", f"proj{i}") for i in range(6)]
        self.assertEqual(len(_watcher(acts).poll_all(NOW)), 6)

    def test_tracking_is_capped_at_max_tasks(self):
        acts = [_act("zcode", TOOL, 10 + i, f"s{i}", f"p{i}") for i in range(MAX_TASKS + 8)]
        self.assertEqual(len(_watcher(acts).poll_all(NOW)), MAX_TASKS)


class DoneLogTests(unittest.TestCase):
    def test_records_on_the_done_transition(self):
        w = _watcher([_act("zcode", TOOL, 5, "s1", "项目A")])
        w.poll_all(NOW)
        self.assertEqual(w.done_recent(NOW), [], "还没完成就不该有记录")
        w.sources = [_FakeSource([_act("zcode", DONE, 0, "s1", "项目A")])]
        w.poll_all(NOW + 1)
        done = w.done_recent(NOW + 1)
        self.assertEqual([d["project"] for d in done], ["项目A"])
        self.assertEqual(done[0]["tool"], "zcode")

    def test_sessions_already_done_at_first_sight_are_not_recorded(self):
        """客户端启动时就已完成的会话不算「刚完成」——那是历史，不是刚发生。"""
        w = _watcher([_act("codex", DONE, 5, "old", "早就完了")])
        w.poll_all(NOW)
        self.assertEqual(w.done_recent(NOW), [])

    def test_newest_first_and_deduplicated(self):
        w = _watcher([_act("zcode", TOOL, 5, "a", "甲"),
                      _act("claude", TOOL, 5, "b", "乙")])
        w.poll_all(NOW)
        w.sources = [_FakeSource([_act("zcode", DONE, 0, "a", "甲"),
                                  _act("claude", TOOL, 0, "b", "乙")])]
        w.poll_all(NOW + 10)
        w.sources = [_FakeSource([_act("zcode", DONE, 0, "a", "甲"),
                                  _act("claude", DONE, 0, "b", "乙")])]
        w.poll_all(NOW + 20)
        self.assertEqual([d["project"] for d in w.done_recent(NOW + 20)], ["乙", "甲"])

        # 同一个会话再完成一次：不该出现两条
        w.sources = [_FakeSource([_act("zcode", TOOL, 0, "a", "甲"),
                                  _act("claude", DONE, 0, "b", "乙")])]
        w.poll_all(NOW + 30)
        w.sources = [_FakeSource([_act("zcode", DONE, 0, "a", "甲"),
                                  _act("claude", DONE, 0, "b", "乙")])]
        w.poll_all(NOW + 40)
        keys = [d["project"] for d in w.done_recent(NOW + 40)]
        self.assertEqual(keys, ["甲", "乙"])
        self.assertEqual(len(keys), len(set(keys)))

    def test_entries_expire(self):
        w = _watcher([_act("zcode", TOOL, 5, "a", "甲")])
        w.poll_all(NOW)
        w.sources = [_FakeSource([_act("zcode", DONE, 0, "a", "甲")])]
        w.poll_all(NOW + 1)
        self.assertEqual(len(w.done_recent(NOW + 1)), 1)
        self.assertEqual(w.done_recent(NOW + 60 * 40), [], "超过 30 分钟应过期")

    def test_carries_absolute_time_for_unread_tracking(self):
        """未读判定要按绝对时间比：只用相对 age 会随轮询间隔抖动。"""
        w = _watcher([_act("zcode", TOOL, 5, "a", "甲")])
        w.poll_all(NOW)
        w.sources = [_FakeSource([_act("zcode", DONE, 0, "a", "甲")])]
        w.poll_all(NOW + 1)
        entry = w.done_recent(NOW + 1)[0]
        self.assertAlmostEqual(entry["at"], NOW + 1, places=1)
        self.assertIn("age", entry)
        self.assertIn("id", entry)

    def test_legacy_watcher_without_done_log_still_works(self):
        w = AIWatcher.__new__(AIWatcher)
        w.sources = [_FakeSource([_act("zcode", THINKING, 2, "x")])]
        w._bursts, w._cache = {}, {}
        self.assertEqual(len(w.poll_all(NOW)), 1)
        self.assertEqual(w.done_recent(NOW), [])


class StatusExposureTests(unittest.TestCase):
    def test_done_is_exposed_in_status(self):
        from pathlib import Path as _P
        src = (_P(__file__).resolve().parent.parent / "workbuddy" / "app.py").read_text()
        self.assertIn('"done": self._done_recent()', src,
                      "status_snapshot 必须带上完成记录，Web 的「刚完成」条才有数据")

    def test_done_recent_degrades_without_watcher(self):
        from workbuddy.app import App
        app = App.__new__(App)
        self.assertEqual(app._done_recent(), [])

    def test_task_limit_matches_watcher_cap(self):
        src = (Path(__file__).resolve().parent.parent / "workbuddy" / "app.py").read_text()
        self.assertIn("MAX_TASKS", src, "Web 侧会话上限应与 aiwatch 一致，别写死 4")


if __name__ == "__main__":
    unittest.main()
