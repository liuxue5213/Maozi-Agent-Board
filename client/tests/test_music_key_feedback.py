"""「音乐按钮没反应」修复回归：_handle_key 必须回传 ok/reason，别让前端拿不到反馈。

Bug：/api/music-key 调 _handle_key 后丢弃返回值、恒回 {ok:true}，导致
play_default 的「请先选一首歌」被吞掉，前端 notice 永远不触发 → 点了像没反应。
"""
import threading
from types import SimpleNamespace

import workbuddy.music as music_mod
from workbuddy.app import App


class _Ctl:
    def __init__(self):
        self.calls = []

    def volume_step(self, v):
        self.calls.append(("vol", v))

    def toggle_mute(self):
        self.calls.append(("mute",))

    def media(self, action):
        self.calls.append(("media", action))


def _app(monkeypatch, session, play_result):
    app = App.__new__(App)
    app.profile_lock = threading.Lock()
    app.control = _Ctl()
    app.cfg = SimpleNamespace(music_app="")
    app._np_kick = threading.Event()
    app._kick = threading.Event()
    monkeypatch.setattr(music_mod, "now_playing", lambda timeout=1.0: session)
    monkeypatch.setattr(music_mod, "play_default",
                        lambda cfg, settle=2.5: dict(play_result))
    return app


def test_play_without_session_returns_reason(monkeypatch):
    """无会话：回传 play_default 的 ok=False + reason，且不向系统发 toggle。"""
    app = _app(monkeypatch, {}, {"ok": False, "app": "网易云音乐",
                                 "reason": "请先选一首歌再点一次"})
    r = app._handle_key("PLAY")
    assert r["ok"] is False and r["reason"] == "请先选一首歌再点一次"
    assert app.control.calls == []          # 空会话绝不下发 toggle


def test_play_with_session_toggles(monkeypatch):
    """有会话：切换播放/暂停，回传 ok=True。"""
    app = _app(monkeypatch, {"title": "歌名", "playing": False}, {})
    r = app._handle_key("PLAY")
    assert r["ok"] is True and r.get("command") == "toggle"
    assert ("media", "toggle") in app.control.calls


def test_next_prev_return_ok(monkeypatch):
    app = _app(monkeypatch, {}, {})
    assert app._handle_key("NEXT") == {"ok": True, "action": "NEXT"}
    assert app._handle_key("PREV") == {"ok": True, "action": "PREV"}
    assert ("media", "next") in app.control.calls
    assert ("media", "prev") in app.control.calls


def test_volume_actions_return_ok(monkeypatch):
    app = _app(monkeypatch, {}, {})
    assert app._handle_key("VOL+")["ok"] is True
    assert ("vol", 10) in app.control.calls
