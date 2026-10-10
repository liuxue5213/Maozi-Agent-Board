"""ZCode 失败事件识别的回归测试。

背景（用户实测）：对话失败结束（turn.failed）后，屏幕永远停在「生成中」——
状态监听器的事件表里没有 turn.failed / model.request.failed，失败事件被
直接忽略，状态卡在上一次识别到的生成中。
"""
import json
import time
import unittest
from pathlib import Path

from workbuddy.aiwatch.zcode import ZCodeSource


class ZCodeFailureTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "log").mkdir()
        self.log = self.dir / "log" / "zcode-test.jsonl"
        self.src = ZCodeSource(self.dir)
        self.now = time.time()

    def _write(self, events):
        with open(self.log, "w", encoding="utf-8") as f:
            for age, ev, sid in events:
                # 真实日志时间戳是 UTC（Z 后缀）；夹具必须用 gmtime，
                # 用 localtime 会超前 8 小时被「未来事件」过滤掉
                ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(self.now - age))
                f.write(json.dumps({"timestamp": ts + ".000Z",
                                    "event": ev, "sessionId": sid}) + "\n")

    def test_turn_failed_maps_to_error(self):
        self._write([(12, "turn.started", "s1"),
                     (10, "model.request.started", "s1"),
                     (4, "turn.failed", "s1")])
        acts = self.src.poll_all(self.now, {})
        self.assertEqual([a.state for a in acts], ["ERROR"])

    def test_request_failed_maps_to_error(self):
        self._write([(6, "model.request.started", "s1"),
                     (2, "model.request.failed", "s1")])
        self.assertEqual([a.state for a in self.src.poll_all(self.now, {})],
                         ["ERROR"])

    def test_failure_then_recovery_shows_running(self):
        """失败后重试成功（新的 model.request.started）→ 回到生成中。"""
        self._write([(8, "model.request.started", "s1"),
                     (6, "model.request.failed", "s1"),
                     (2, "model.request.started", "s1")])
        self.assertEqual([a.state for a in self.src.poll_all(self.now, {})],
                         ["GENERATING"])

    def test_completed_beats_older_failure(self):
        """失败后重试完成 → 已完成（30 秒内），最新事件优先。"""
        self._write([(10, "model.request.started", "s1"),
                     (8, "model.request.failed", "s1"),
                     (2, "turn.completed", "s1")])
        self.assertEqual([a.state for a in self.src.poll_all(self.now, {})],
                         ["DONE"])


if __name__ == "__main__":
    unittest.main()
