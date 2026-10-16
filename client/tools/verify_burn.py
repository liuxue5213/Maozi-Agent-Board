#!/usr/bin/env python3
"""烧录后握手校验：确认固件已烧入 v0.3.2 且传感器 I2C 探测正常。

配合 docs/P0-3_固件v0.3.2_真机验收清单.md 使用。烧录 `pio run -t upload` 后，
先停掉客户端释放串口（否则读不到完整开机握手），跑本脚本监听设备开机握手，
确认三件事：

  1. 协议版本 >= 0.3.2 —— 否则烧录没生效 / 烧错分区 / 仍是旧基线
  2. pages 能力 == 10 —— v0.3.2 相对旧版的 6→10（旧固件这里会是 6）
  3. 传感器 I2C 扫描命中 AHT20/BMP280/VL6180X —— 固件 config.h 已对齐
     SDA=GPIO2 / SCL=GPIO1（与旧文档的 GPIO1/2 相反；写反也能用，但首选要对齐）

灯珠 RGB 不在握手里暴露能力位，烧录含 a8ca483 即支持，烧后到网页「灯珠」面板
开关+取色验证即可（详见 P0-3 清单）。

用法：
  # 真机：监听开机握手（默认约 8s 超时，够设备重启后发 FW= 与 SN=）
  python3 tools/verify_burn.py -p /dev/cu.usbserial-XXXX

  # 离线演练：从文件读握手样本（CI / 无硬件测试用）
  python3 tools/verify_burn.py --sample tools/samples/burn_ok.txt

  # 从管道读一行 FW=（或 SN=）
  echo 'FW=proto:0.3.2,n16r8,schema:1,pages:10,widgets:10,apps:12' \\
    | python3 tools/verify_burn.py --stdin
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

# 允许以独立脚本运行：把 client/ 加进 sys.path，使 `import workbuddy.protocol` 可用。
_CLIENT = Path(__file__).resolve().parents[1]
if str(_CLIENT) not in sys.path:
    sys.path.insert(0, str(_CLIENT))

from workbuddy.protocol import parse_protocol_version, PROTOCOL_VERSION  # noqa: E402

# 已知传感器 I2C 地址（十六进制，不带 0x）。固件 sensors.h 扫到后回 SN=...:38 76 29
SENSOR_ADDRS = {
    "38": "AHT20（温湿度）",
    "76": "BMP280（气压，地址 0x76）",
    "77": "BMP280（气压，地址 0x77）",
    "29": "VL6180X（ToF 距离）",
}
REQUIRED_SENSORS = ("AHT20", "BMP280", "VL6180X")


def parse_fw_line(line: str) -> dict:
    """从 `FW=proto:0.3.2,n16r8,schema:1,pages:10,...` 提取键值。"""
    line = line.strip()
    if line.upper().startswith("FW="):
        line = line[3:]
    out: dict = {}
    for part in line.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
        else:
            out[part.strip()] = True
    return out


def parse_sn_line(line: str) -> list[str]:
    """从 `SN=SDA=2,SCL=1:38 76 29` 提取设备地址列表（十六进制串，如 ['38','76','29']）。"""
    line = line.strip()
    if line.upper().startswith("SN="):
        line = line[3:]
    m = re.search(r":([0-9A-Fa-f ]+)$", line)
    if not m:
        return []
    return [a for a in re.split(r"\s+", m.group(1).strip()) if a]


def evaluate(fw_line: str, sn_line: str | None = None) -> dict:
    """对一段握手文本做版本/能力/传感器三项校验，返回结构化结果。

    纯函数，无 IO —— 便于离线单元测试（见 tests/test_verify_burn.py）。
    """
    fw = parse_fw_line(fw_line)
    proto_raw = fw.get("proto") or fw.get("FW") or ""
    version = parse_protocol_version("proto:" + proto_raw) if proto_raw else None
    pages = fw.get("pages")
    try:
        pages_n = int(pages) if pages is not None else None
    except ValueError:
        pages_n = None

    found_sensors: list[str] = []
    if sn_line:
        for addr in parse_sn_line(sn_line):
            name = SENSOR_ADDRS.get(addr.lower())
            if name:
                found_sensors.append(name)

    checks = []

    # 1) 协议版本 >= 0.3.2
    if version is None:
        checks.append(("协议版本", False,
                       "握手未含 proto: 字段，无法判定版本（旧基线或握手格式异常）"))
    elif version >= PROTOCOL_VERSION:
        checks.append(("协议版本", True,
                       f"proto:{'.'.join(map(str, version))} ≥ {'.'.join(map(str, PROTOCOL_VERSION))}"))
    else:
        checks.append(("协议版本", False,
                       f"proto:{'.'.join(map(str, version))} < {'.'.join(map(str, PROTOCOL_VERSION))}：烧录未生效或烧错分区"))

    # 2) pages 能力 == 10（v0.3.2 标志）
    if pages_n is None:
        checks.append(("页面能力", False, "握手未含 pages: 字段"))
    elif pages_n >= 10:
        checks.append(("页面能力", True, f"pages:{pages_n}（v0.3.2 应为 10）"))
    else:
        checks.append(("页面能力", False,
                       f"pages:{pages_n}：旧基线（v0.3.2 应为 10），烧录未生效"))

    # 3) 传感器 I2C 命中三件套
    if not sn_line:
        checks.append(("传感器 I2C", None,
                       "未收到 SN= 行（超时或设备未探测）；可单独看串口日志确认 [SN] 行"))
    else:
        missing = [s for s in REQUIRED_SENSORS if s not in " ".join(found_sensors)]
        if not missing:
            checks.append(("传感器 I2C", True,
                           "命中 " + " / ".join(dict.fromkeys(found_sensors))))
        else:
            detail = " / ".join(found_sensors) or "未命中任何已知地址"
            checks.append(("传感器 I2C", False,
                           f"缺失 {', '.join(missing)}（已探测：{detail}）"))

    status = all(c[1] is not False for c in checks)
    return {"version": version, "pages": pages_n,
            "sensors": found_sensors, "checks": checks, "pass": status}


def _render(report: dict) -> str:
    lines = ["=== 固件烧录校验 ==="]
    icon = {True: "✓", False: "✗", None: "·"}
    for name, ok, msg in report["checks"]:
        lines.append(f"  {icon.get(ok,'·')} {name}：{msg}")
    verdict = "PASS —— 固件 v0.3.2 已正确烧录，可进行后续屏显/用量/灯珠验收" \
        if report["pass"] else "FAIL —— 上述未通过项需先解决（多半是烧错分区或串口被占用）"
    lines.append(verdict)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="烧录后固件握手校验（只读）")
    ap.add_argument("-p", "--port", default="/dev/cu.usbserial-A5069RR4")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--timeout", type=float, default=8.0,
                    help="等待开机握手的秒数（设备重启后发 FW= 与 SN=）")
    ap.add_argument("--sample", help="从文件读握手样本（离线演练，不连串口）")
    ap.add_argument("--stdin", action="store_true",
                    help="从 stdin 读一行 FW=（或 SN=）")
    args = ap.parse_args()

    fw_line: str | None = None
    sn_line: str | None = None

    if args.sample:
        text = Path(args.sample).read_text(encoding="utf-8")
        for ln in text.splitlines():
            if ln.strip().upper().startswith("FW="):
                fw_line = ln
            elif ln.strip().upper().startswith("SN="):
                sn_line = ln
    elif args.stdin:
        for ln in sys.stdin:
            if ln.strip().upper().startswith("FW="):
                fw_line = ln
            elif ln.strip().upper().startswith("SN="):
                sn_line = ln
    else:
        try:
            import serial
        except ImportError:
            sys.exit("需要 pyserial：pip3 install pyserial")
        try:
            with serial.Serial(args.port, args.baud, timeout=0.5) as ser:
                time.sleep(0.4)
                ser.reset_input_buffer()
                deadline = time.time() + args.timeout
                while time.time() < deadline:
                    line = ser.readline().decode("ascii", "ignore").strip()
                    if line.upper().startswith("FW="):
                        fw_line = line
                    elif line.upper().startswith("SN="):
                        sn_line = line
                    if fw_line and sn_line:
                        break
        except OSError as exc:
            sys.exit(f"无法打开串口 {args.port}：{exc}")

    if not fw_line:
        print("未在超时内收到 FW= 握手行（确认设备已上电、串口未被客户端占用）")
        return 1

    report = evaluate(fw_line, sn_line)
    print(_render(report))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
