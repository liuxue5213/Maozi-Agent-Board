"""M1：配置版本端到端留痕（保存 → 下发 → 设备应用 → 断连重连同源）的回归测试。

让「同一配置版本」在 Web 自检 / 真机验收时可被一条链验证，而不是散在日志里。
"""
import threading

from workbuddy import profile as profile_mod
from workbuddy.app import App


class _Health:
    def __init__(self):
        self.events = []

    def event(self, kind, **fields):
        self.events.append((kind, fields))


def _valid_profile(version):
    return {"schema": 1, "version": version,
            "pages": [{"id": "ai", "title": "工作台", "layout": "list",
                       "widgets": ["ai_list"], "filter": "", "city": ""}],
            "reminders": [], "calendar": [], "alarms": [], "apps": [],
            "countdowns": [], "weather": {"city": "", "cities": []},
            "theme": {"bg": "060B16", "grad": "0B1730", "card": "304661",
                      "card_edge": "4B6685", "accent": "57C8F5",
                      "text": "F4F8FF", "sub": "C7D5E5",
                      "bright": 62, "idledim": 60, "saver": 0,
                      "savermsg": "", "topclock": 1, "topwx": 1,
                      "card_opa": 100, "bright_auto": 0},
            "autopage": 0}


def _app(tmp_path, monkeypatch, version=10, applied=None, status="设备已应用"):
    """最小 App：填 M1 留痕路径需要的属性。"""
    monkeypatch.setattr(profile_mod, "PROFILE_PATH", tmp_path / "profile.json")
    app = App.__new__(App)
    app.health = _Health()
    app.profile_lock = threading.Lock()
    app.profile = _valid_profile(version)
    app.profile_status = status
    app.profile_error = ""
    app.applied_version = applied
    app.applied_profile = None
    app.sync_requested = False
    app.fw_caps = {"schema": 1, "pages": 10, "apps": 12}
    app.fw_proto = (0, 3, 2)
    app.config_trace = []
    app._config_trace_cap = 24
    app._last_saved_version = None
    app._last_sent_version = None
    app.env = {}
    app.env_seen = {}
    app.weather = type("W", (), {"kick": lambda self: None})()
    return app


def test_profile_saved_emits_trace(tmp_path, monkeypatch):
    """保存必须记一条 profile_saved 留痕，且版本自增、状态进入待同步。"""
    app = _app(tmp_path, monkeypatch, version=10, applied=10)
    saved = app.save_profile(_valid_profile(10))
    assert saved["version"] == 11
    assert app._last_saved_version == 11
    assert app.profile_status == "待同步"
    assert any(e["kind"] == "profile_saved" and e["version"] == 11
               for e in app.config_trace)


def test_sync_sent_trace_records_version(tmp_path, monkeypatch):
    """push_profile 下发应记 sync_sent 留痕（版本一致，便于回执比对）。"""
    app = _app(tmp_path, monkeypatch, version=10, applied=10)
    app.link = type("L", (), {"status": "connected:cu.usbserial",
                              "send": lambda self, line: None})()
    app.sync_requested = True
    app.push_profile()
    assert app._last_sent_version == 10
    assert any(e["kind"] == "sync_sent" and e["version"] == 10
               for e in app.config_trace)


def test_sync_ack_trace_records_applied(tmp_path, monkeypatch):
    """设备回执 config_reply 应记 sync_ack 留痕，并刷新 applied_version。"""
    import time
    app = _app(tmp_path, monkeypatch, version=10, applied=None, status="待同步")
    app._on_uplink({"config_reply": {"ok": True, "version": "10", "reason": ""}})
    assert app.applied_version == 10
    assert app.profile_status == "设备已应用"
    assert any(e["kind"] == "sync_ack" and e["version"] == "10"
               for e in app.config_trace)


def test_reconnect_preserves_applied_version_when_consistent(tmp_path, monkeypatch):
    """重连且设备已有同版本：保留 applied_version，不重推、不闪待同步。"""
    app = _app(tmp_path, monkeypatch, version=10, applied=10)
    app._on_reconnect()
    assert app.applied_version == 10
    assert app.profile_status == "设备已应用"
    assert app.sync_requested is False
    assert app.fw_caps is None           # 仍需重握手以刷新能力门控
    ev = [e for e in app.config_trace if e["kind"] == "reconnect_resync"]
    assert ev and ev[-1]["changed"] is False


def test_reconnect_resyncs_when_version_mismatch(tmp_path, monkeypatch):
    """重连但客户端版本已超前设备：进入待同步、触发重推。"""
    app = _app(tmp_path, monkeypatch, version=12, applied=10)
    app._on_reconnect()
    assert app.profile_status == "待同步"
    assert app.sync_requested is True
    ev = [e for e in app.config_trace if e["kind"] == "reconnect_resync"]
    assert ev and ev[-1]["changed"] is True and ev[-1]["prev"] == 10


def test_reconnect_resyncs_when_no_applied(tmp_path, monkeypatch):
    """从未成功应用过版本（applied_version=None）：重连必须重新同步。"""
    app = _app(tmp_path, monkeypatch, version=10, applied=None, status="待同步")
    app._on_reconnect()
    assert app.profile_status == "待同步"
    assert app.sync_requested is True
    ev = [e for e in app.config_trace if e["kind"] == "reconnect_resync"]
    assert ev and ev[-1]["changed"] is True and ev[-1]["prev"] is None


def test_config_trace_snapshot_consistent_flag(tmp_path, monkeypatch):
    """_config_trace_snapshot 正确反映「客户端==设备已应用」的一致态。"""
    app = _app(tmp_path, monkeypatch, version=10, applied=10)
    snap = app._config_trace_snapshot()
    assert snap["applied_version"] == 10
    assert snap["consistent"] is True

    app.applied_version = 9            # 设备落后
    assert app._config_trace_snapshot()["consistent"] is False

    app.applied_version = 10
    app.profile_status = "待同步"      # 还没应用完
    assert app._config_trace_snapshot()["consistent"] is False
