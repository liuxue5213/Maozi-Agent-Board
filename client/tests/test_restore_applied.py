"""「回到设备版本」回退 + 启动安全约束的回归测试。"""
import json
from pathlib import Path
from unittest import mock

import pytest

from workbuddy import profile as profile_mod
from workbuddy.app import App


class _Health:
    def __init__(self):
        self.events = []

    def event(self, kind, **fields):
        self.events.append((kind, fields))


def _valid_profile(version):
    """过得了 validate_profile 的最小配置（restore 会重新落盘校验）。"""
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


def _app(tmp_path, monkeypatch, applied=None, current_version=99):
    """__new__ 直构最小 App：只填 restore 路径需要的属性。"""
    monkeypatch.setattr(profile_mod, "PROFILE_PATH", tmp_path / "profile.json")
    app = App.__new__(App)
    app.health = _Health()
    app.profile_lock = __import__("threading").Lock()
    app.profile = _valid_profile(current_version)
    app.profile_status = "同步失败"
    app.profile_error = "设备无响应（超时）"
    app.applied_version = 31
    app.applied_profile = applied
    app.sync_requested = False
    return app


def test_restore_writes_applied_content_keeps_version(tmp_path, monkeypatch):
    applied = _valid_profile(31)
    app = _app(tmp_path, monkeypatch, applied=applied)
    restored = app.restore_applied_profile()
    assert restored["version"] == 31          # 版本不 bump：设备已应用这一版
    assert restored["theme"]["bg"] == "060B16"
    assert app.profile_status == "设备已应用"
    assert app.profile_error == ""
    assert app.sync_requested is False
    on_disk = json.loads((tmp_path / "profile.json").read_text(encoding="utf-8"))
    assert on_disk["version"] == 31           # 落盘与设备一致
    assert any(k == "restore_applied" for k, _ in app.health.events)


def test_restore_without_snapshot_rejected(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, applied=None)
    with pytest.raises(ValueError):
        app.restore_applied_profile()


def test_restore_does_not_alias_snapshot(tmp_path, monkeypatch):
    """回退后的 profile 与快照必须隔离：改回退结果不能污染快照。"""
    applied = _valid_profile(31)
    app = _app(tmp_path, monkeypatch, applied=applied)
    restored = app.restore_applied_profile()
    restored["theme"]["bg"] = "FF0000"
    assert app.applied_profile["theme"]["bg"] == "060B16"


# ---- 启动安全约束（后台静默启动 P0 的防回归） ----

def test_config_defaults_are_silent():
    """open_settings 必须默认 False：后台/测试启动曾自动弹浏览器打断工作。"""
    from workbuddy.config import Config
    cfg = Config()
    assert cfg.open_settings is False
    loaded = Config.load()
    assert loaded.open_settings is False      # 空配置文件也不打开页面


def test_web_server_binds_loopback_only():
    """设置页只允许绑定 127.0.0.1：诊断包/配置里含启动命令，不出去本机。"""
    import inspect
    from workbuddy import web
    source = inspect.getsource(web)
    assert '("127.0.0.1", port)' in source
