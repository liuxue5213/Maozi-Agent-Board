"""协议 v0.2 编解码。字段与 docs/PROTOCOL.md 一一对应。"""
import time

# 下行遥测字段输出顺序（无值自动省略）
_ORDER = [
    "time",
    "date",
    "ai0", "ai1", "ai2", "ai3", "remind",
    "cpu", "cpufreq", "cputemp",
    "mem", "memused", "memtotal",
    "gpu", "gpumem", "gputemp",
    "disk", "diskfree", "disktotal", "bat",
    "netrx", "nettx", "uptime",
    "vol", "mut",
    "aistate", "aitool", "aitask", "aiprog", "aitime", "aierr",
    "temp", "hum",
]


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def sanitize_err(text: str, limit: int = 48) -> str:
    """错误消息单行化 + 截断，保证不破坏 KV 协议。"""
    if not text:
        return ""
    s = " ".join(str(text).split())
    return s[:limit]


def encode_telemetry(t: dict) -> str:
    """把采集/检测合并后的遥测字典编码为多行 KV 文本。"""
    lines = []
    for key in _ORDER:
        v = t.get(key)
        if v is None or (v == "" and key not in ("ai0", "ai1", "ai2", "ai3")):
            continue
        lines.append(f"{key.upper()}={_fmt(v)}")
    return "\n".join(lines) + "\n"


def encode_caps(caps: dict) -> str:
    kv = ",".join(f"{k}:{1 if v else 0}" for k, v in caps.items())
    return f"CAPS={kv}\n"


def encode_hb() -> str:
    return f"HB={int(time.time())}\n"


def parse_uplink(line: str):
    """解析 ESP32 上行一行 KV，返回 dict 或 None。"""
    line = line.strip()
    if not line or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip().upper()
    value = value.strip()
    if key in ("TEMP",):
        if value.upper() == "NA":
            return {"temp": None}
        try:
            return {"temp": float(value)}
        except ValueError:
            return None
    if key in ("HUM", "BRIGHT"):
        if key == "HUM" and value.upper() == "NA":
            return {"hum": None}
        try:
            return {key.lower(): int(float(value))}
        except ValueError:
            return None
    if key == "TOUCH":
        return {"touch": value}
    if key == "KEY":
        return {"key": value.upper()}
    if key == "LAUNCH":
        return {"launch": value}
    if key == "HB":
        return {"dev_hb": value}
    if key in ("ACK", "NAK") and value.startswith("IMG,"):
        return {"img_reply": {"ok": key == "ACK", "reason": value[4:]}}
    if key in ("ACK", "NAK") and value.startswith("CFG,"):
        fields = value.split(",", 2)
        if len(fields) == 3:
            return {"config_reply": {"ok": key == "ACK", "version": fields[1],
                                     "reason": fields[2]}}
    if key == "FW":
        return {"firmware": value}
    if key == "SLOW":
        try:
            return {"slow_loop": int(value.split()[0])}
        except (ValueError, IndexError):
            return None
    return None
