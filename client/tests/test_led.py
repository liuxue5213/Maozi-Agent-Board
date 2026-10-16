"""板载 RGB 灯珠：配置校验 / 十六进制→rgb 换算 / 下行字段 / CFG 剔除。"""
import base64
import json

import pytest

from workbuddy import profile as P
from workbuddy.protocol import led_wire, encode_telemetry


def test_led_wire_on_off_and_colors():
    assert led_wire({"on": False, "color": "4CC9F0"}) == "0,0,0"
    assert led_wire({"on": True, "color": "4CC9F0"}) == "76,201,240"
    assert led_wire({"on": True, "color": "#FF0000"}) == "255,0,0"
    assert led_wire({"on": True, "color": "00ff88"}) == "0,255,136"


def test_led_wire_handles_garbage():
    assert led_wire(None) == "0,0,0"
    assert led_wire({}) == "0,0,0"
    assert led_wire({"on": True, "color": "xyz"}) == "0,0,0"     # 非法色 → 熄灭，不发脏值
    assert led_wire({"on": True, "color": ""}) == "0,0,0"


def test_profile_led_default():
    assert P.validate_profile(P.default_profile())["led"] == {"on": False, "color": "4CC9F0"}


def test_profile_led_color_normalized_and_fallback():
    prof = P.default_profile()
    prof["led"] = {"on": True, "color": "#00ff88"}
    assert P.validate_profile(prof)["led"] == {"on": True, "color": "00FF88"}
    prof["led"] = {"on": True, "color": "bad"}
    assert P.validate_profile(prof)["led"]["color"] == "4CC9F0"


def test_encode_profile_strips_led():
    """灯珠走下行 RGB=，不进 CFG 载荷（避免撑大配置包）。"""
    prof = P.default_profile()
    prof["version"] = 11
    prof["led"] = {"on": True, "color": "FF0000"}
    lines = P.encode_profile(prof)
    chunks = [ln.split(",")[3].strip() for ln in lines if ln.startswith("CFG=DATA")]
    payload = base64.b64decode("".join(chunks)).decode("utf-8")
    data = json.loads(payload)
    assert "led" not in data              # 注意别用子串判断：idledim 里含 "led"
    assert "FF0000" not in payload


def test_encode_telemetry_carries_rgb():
    assert "RGB=76,201,240" in encode_telemetry({"time": "120000", "rgb": "76,201,240"})
    assert "RGB=0,0,0" in encode_telemetry({"time": "120000", "rgb": "0,0,0"})
