"""verify_burn.py 的离线单元测试：握手解析 + 三项校验判定。

不连串口、不依赖硬件；只验证纯函数 evaluate / parse_fw_line / parse_sn_line。
真机路径（main 里的 serial 分支）由 docs/P0-3 清单的人工验收覆盖。
"""
import importlib.util
import os
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "verify_burn.py"
_spec = importlib.util.spec_from_file_location("verify_burn", _TOOL)
VB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(VB)

SAMPLE_OK = Path(__file__).resolve().parents[1] / "tools" / "samples" / "burn_ok.txt"
SAMPLE_OLD = Path(__file__).resolve().parents[1] / "tools" / "samples" / "burn_old.txt"


def test_parse_fw_line_extracts_keys():
    fw = VB.parse_fw_line("FW=proto:0.3.2,n16r8,schema:1,pages:10,apps:12")
    assert fw["proto"] == "0.3.2"
    assert fw["schema"] == "1"
    assert fw["pages"] == "10"
    assert fw["apps"] == "12"
    # 无冒号的部分按 True 处理（兼容 FW=0.2,n16r8 旧格式）
    assert fw["n16r8"] is True


def test_parse_fw_line_without_prefix():
    fw = VB.parse_fw_line("proto:0.3.2,pages:6")
    assert fw["proto"] == "0.3.2"
    assert fw["pages"] == "6"


def test_parse_sn_line_pulls_addresses():
    addrs = VB.parse_sn_line("SN=SDA=2,SCL=1:38 76 29")
    assert addrs == ["38", "76", "29"]


def test_parse_sn_line_none():
    assert VB.parse_sn_line("SN=none") == []


def test_evaluate_ok_sample():
    text = SAMPLE_OK.read_text(encoding="utf-8")
    fw = next(l for l in text.splitlines() if l.startswith("FW="))
    sn = next(l for l in text.splitlines() if l.startswith("SN="))
    rep = VB.evaluate(fw, sn)
    assert rep["version"] == (0, 3, 2)
    assert rep["pages"] == 10
    joined = " ".join(rep["sensors"])
    assert "AHT20" in joined
    assert "BMP280" in joined
    assert "VL6180X" in joined
    # 新字段：led:1 / slot:ota_0
    assert rep["led"] == "1"
    assert rep["slot"] == "ota_0"
    assert rep["pass"] is True
    verdicts = {c[0]: c[1] for c in rep["checks"]}
    # 版本/页面/传感器/灯珠 四项硬校验都应通过
    assert verdicts["协议版本"] is True
    assert verdicts["页面能力"] is True
    assert verdicts["传感器 I2C"] is True
    assert verdicts["灯珠能力"] is True
    # 运行分区是信息级（None），不阻断 pass
    assert verdicts["运行分区"] is None


def test_evaluate_old_firmware_fails():
    text = SAMPLE_OLD.read_text(encoding="utf-8")
    fw = next(l for l in text.splitlines() if l.startswith("FW="))
    sn = next(l for l in text.splitlines() if l.startswith("SN="))
    rep = VB.evaluate(fw, sn)
    # 旧基线：版本 < 0.3.2、pages==6 → 两关 False
    assert rep["version"] == (0, 2)
    assert rep["pages"] == 6
    verdicts = {c[0]: c[1] for c in rep["checks"]}
    assert verdicts["协议版本"] is False
    assert verdicts["页面能力"] is False
    # SN=none 是非空行但没有地址 → 传感器项 False（已探测但零命中）
    assert verdicts["传感器 I2C"] is False
    assert rep["pass"] is False


def test_evaluate_missing_proto_field():
    rep = VB.evaluate("FW=n16r8,schema:1,pages:6")
    verdicts = {c[0]: c[1] for c in rep["checks"]}
    assert verdicts["协议版本"] is False
    assert rep["version"] is None


def test_evaluate_partial_sensors():
    # 只命中 AHT20，缺 BMP280 / VL6180X
    rep = VB.evaluate("FW=proto:0.3.2,pages:10", "SN=SDA=2,SCL=1:38")
    verdicts = {c[0]: c[1] for c in rep["checks"]}
    assert verdicts["协议版本"] is True
    assert verdicts["页面能力"] is True
    assert verdicts["传感器 I2C"] is False
    assert rep["pass"] is False


def test_evaluate_no_sn_line():
    rep = VB.evaluate("FW=proto:0.3.2,pages:10")
    verdicts = {c[0]: c[1] for c in rep["checks"]}
    assert verdicts["传感器 I2C"] is None
    # 版本与页面通过 → 整体仍视为 pass（传感器需人工看日志）
    assert rep["pass"] is True


def test_led_capability_present():
    rep = VB.evaluate("FW=proto:0.3.2,pages:10,led:1,slot:ota_0")
    verdicts = {c[0]: c[1] for c in rep["checks"]}
    assert verdicts["灯珠能力"] is True
    assert rep["led"] == "1"
    assert rep["slot"] == "ota_0"


def test_led_capability_absent_graceful():
    # 旧固件不发出 led/slot → 降级为 None，不误判 FAIL
    rep = VB.evaluate("FW=proto:0.3.2,pages:10")
    verdicts = {c[0]: c[1] for c in rep["checks"]}
    assert verdicts["灯珠能力"] is None
    assert verdicts["运行分区"] is None
    assert rep["pass"] is True


def test_led_capability_disabled():
    # led:0 表示该构建未启用板载 RGB（PIN_RGB 未定义）
    rep = VB.evaluate("FW=proto:0.3.2,pages:10,led:0")
    verdicts = {c[0]: c[1] for c in rep["checks"]}
    assert verdicts["灯珠能力"] is False
    assert rep["pass"] is False


def test_slot_factory_informational():
    # slot:factory 是信息级（首次 USB 烧录/回退），永不阻断 PASS
    rep = VB.evaluate("FW=proto:0.3.2,pages:10,slot:factory")
    verdicts = {c[0]: c[1] for c in rep["checks"]}
    assert verdicts["运行分区"] is None
    assert rep["pass"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
