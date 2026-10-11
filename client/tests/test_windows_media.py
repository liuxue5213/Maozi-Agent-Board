"""Windows 媒体补齐的单测：pycaw 音量读写、SMTC 映射、温度来源标注。

Windows 专属 API 在 macOS 开发机上不存在，全部通过 mock 隔离在边界处——
测试的是**我们的分支逻辑与降级路径**，不是 winsdk/pycaw 本身。
"""
import sys
from unittest import mock

from workbuddy import control
from workbuddy.control import ControlApp


class _FakeEndpoint:
    """IAudioEndpointVolume 的行为替身。"""
    def __init__(self, vol=0.4, mute=False):
        self.vol = vol
        self.mute = mute
        self.set_calls = []

    def GetMasterVolumeLevelScalar(self):
        return self.vol

    def SetMasterVolumeLevelScalar(self, value, event):
        self.vol = value
        self.set_calls.append(value)

    def GetMute(self):
        return self.mute

    def SetMute(self, value, event):
        self.mute = value


def _app():
    app = ControlApp()
    app._cache_at = 0.0        # 绕过 3s 节流
    return app


def test_volume_read_via_pycaw():
    ep = _FakeEndpoint(vol=0.42, mute=True)
    with mock.patch.object(control, "_win_endpoint", return_value=ep), \
         mock.patch.object(control.sys, "platform", "win32"):
        assert _app()._read_volume() == {"vol": 42, "mut": 1}


def test_volume_read_without_pycaw_is_empty():
    with mock.patch.object(control, "_win_endpoint", return_value=None), \
         mock.patch.object(control.sys, "platform", "win32"):
        assert _app()._read_volume() == {}


def test_volume_step_uses_endpoint_set():
    ep = _FakeEndpoint(vol=0.40)
    with mock.patch.object(control, "_win_endpoint", return_value=ep), \
         mock.patch.object(control.sys, "platform", "win32"):
        _app().volume_step(10)
    assert len(ep.set_calls) == 1
    assert ep.set_calls[0] == 0.50          # 40% + 10 → 50%


def test_volume_step_clamped_at_bounds():
    ep = _FakeEndpoint(vol=0.97)
    with mock.patch.object(control, "_win_endpoint", return_value=ep), \
         mock.patch.object(control.sys, "platform", "win32"):
        _app().volume_step(10)
    assert ep.set_calls[0] == 1.0            # 顶到 100%


def test_volume_step_falls_back_to_keys_without_endpoint():
    with mock.patch.object(control, "_win_endpoint", return_value=None), \
         mock.patch.object(control.sys, "platform", "win32"), \
         mock.patch.object(control, "_win_key") as key:
        _app().volume_step(10)
    assert key.called


def test_mute_toggle_via_endpoint():
    ep = _FakeEndpoint(mute=False)
    with mock.patch.object(control, "_win_endpoint", return_value=ep), \
         mock.patch.object(control.sys, "platform", "win32"):
        _app().toggle_mute()
    assert ep.mute is True


# ---- SMTC ----

def test_smtc_method_mapping():
    from workbuddy import music
    assert music._SMTC_METHODS["play"] == "try_play_async"
    assert music._SMTC_METHODS["next"] == "try_skip_next_async"
    assert "toggle" not in music._SMTC_METHODS     # toggle 按播放状态二选一


def test_now_playing_macos_returns_none():
    from workbuddy import music
    assert music.now_playing() is None


def test_send_win32_without_loop_returns_false():
    # winsdk 缺失/无会话时 _smtc_run 归 False，绝不能抛异常打断遥测
    from workbuddy import music
    with mock.patch.object(music.sys, "platform", "win32"):
        assert music.send("toggle") is False


# ---- 温度来源标注 ----

def test_thermal_hint_records_source():
    from workbuddy.collect.windows import WindowsCollector
    collector = WindowsCollector.__new__(WindowsCollector)
    collector._thermal_cache = ({}, 0.0)
    collector.thermal_hint = ""
    with mock.patch.object(WindowsCollector, "_query_lhm_cpu_temp", return_value="57"), \
         mock.patch.object(WindowsCollector, "_query_thermal_zone", return_value=""), \
         mock.patch.object(WindowsCollector, "_query_lhm_fan", return_value=""), \
         mock.patch.object(WindowsCollector, "_query_win32_fan", return_value=""):
        out = collector._sample_thermal()
    assert out == {"cputemp": 57}
    assert collector.thermal_hint == "lhm"


def test_thermal_hint_zone_fallback():
    from workbuddy.collect.windows import WindowsCollector
    collector = WindowsCollector.__new__(WindowsCollector)
    collector._thermal_cache = ({}, 0.0)
    collector.thermal_hint = ""
    # 热区 330.15K = 57℃（开尔文×10）
    with mock.patch.object(WindowsCollector, "_query_lhm_cpu_temp", return_value=""), \
         mock.patch.object(WindowsCollector, "_query_thermal_zone", return_value="3301.5"), \
         mock.patch.object(WindowsCollector, "_query_lhm_fan", return_value=""), \
         mock.patch.object(WindowsCollector, "_query_win32_fan", return_value=""):
        out = collector._sample_thermal()
    assert out == {"cputemp": 57}
    assert collector.thermal_hint == "zone"
