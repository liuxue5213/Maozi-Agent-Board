"""M1：AI 来源读取能力快照（读取方式 / 数据时间戳 / 失败原因）的回归测试。

目标：屏幕/自检面板能逐来源说明「读到了什么、为什么没有数据」，而不是只给一个总开关。
"""
import time
from pathlib import Path

from workbuddy.aiwatch import AIWatcher, Activity, GENERATING
from workbuddy.app import App


class _FakeSource:
    NAME = "fake"
    KIND = "session"

    def __init__(self, root, acts=None, exc=None):
        self.root = Path(root)
        self._acts = acts or []
        self._exc = exc

    def poll_all(self, now, cache):
        if self._exc:
            raise self._exc
        return list(self._acts)


def _watcher(sources):
    w = AIWatcher.__new__(AIWatcher)
    w.sources = sources
    w._cache = {}
    w._processes = None      # 不门控
    w._bursts = {}
    w._src_status = []
    return w


def test_source_status_one_entry_per_source(tmp_path):
    now = time.time()
    w = _watcher([_FakeSource(tmp_path,
                              acts=[Activity("fake", GENERATING, now - 12)])])
    w.poll_all(now)
    st = w.source_status()
    assert len(st) == 1
    e = st[0]
    assert e["name"] == "fake" and e["kind"] == "session"
    assert e["readable"] is True and e["sessions"] == 1
    assert e["last_age"] == 12          # 数据时间戳：12 秒前
    assert e["root_exists"] is True and e["error"] == ""


def test_source_status_records_failure(tmp_path):
    w = _watcher([_FakeSource(tmp_path, exc=RuntimeError("boom"))])
    w.poll_all(time.time())
    e = w.source_status()[0]
    assert e["readable"] is False
    assert e["sessions"] == 0
    assert "boom" in e["error"]         # 失败原因可读


def test_source_status_distinguishes_missing_root(tmp_path):
    """根目录不存在 = 未安装/未使用，与「读取报错」是两回事。"""
    w = _watcher([_FakeSource(tmp_path / "nope", acts=[])])
    w.poll_all(time.time())
    e = w.source_status()[0]
    assert e["root_exists"] is False
    assert e["readable"] is True and e["error"] == ""   # 能读，只是没有会话
    assert e["sessions"] == 0 and e["last_age"] is None


def test_source_status_empty_before_first_poll():
    w = _watcher([_FakeSource("/tmp", acts=[])])
    assert w.source_status() == []       # 尚未轮询过


def test_all_builtin_sources_declare_name_and_kind():
    from workbuddy.aiwatch.claude import ClaudeSource
    from workbuddy.aiwatch.codex import CodexSource
    from workbuddy.aiwatch.opencode import OpenCodeSource
    from workbuddy.aiwatch.zcode import ZCodeSource
    from workbuddy.aiwatch.workbuddy import WorkBuddySource
    for cls in (ClaudeSource, CodexSource, OpenCodeSource, ZCodeSource, WorkBuddySource):
        assert isinstance(cls.NAME, str) and cls.NAME
        assert cls.KIND == "session"


def test_app_ai_sources_helper_handles_none():
    app = App.__new__(App)
    app.ai = None
    assert app._ai_sources() == []

    class _AI:
        def source_status(self):
            return [{"name": "claude", "readable": True}]

    app.ai = _AI()
    assert app._ai_sources() == [{"name": "claude", "readable": True}]
