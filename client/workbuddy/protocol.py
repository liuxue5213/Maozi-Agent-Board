"""协议 v0.2 编解码。字段与 docs/PROTOCOL.md 一一对应。"""
import math
import time

# 下行遥测字段输出顺序（无值自动省略）
_ORDER = [
    "time",
    "date",
    "ai0", "ai1", "ai2", "ai3", "ai4", "ai5", "ai6", "ai7",
    "dn0", "dn1", "dn2",
    "w0", "w1", "w2", "wf0", "wf1", "wf2", "ps", "aiu", "ait", "aitok", "remind",
    "pb",   # 连续电脑使用达到活动提醒阈值（0/1）
    "ast",  # 今日会话数按工具（zcode:3 · claude:1）
    "afm",  # 焦点会话最近消息（≤80 字符单行）
    "aib0", "aib1", "aib2",   # 分模型今日消耗（模型名,token；无值自动省略）
    "cpu", "cpufreq", "cputemp", "fan",
    "mem", "memused", "memtotal",
    "gpu", "gpumem", "gputemp",
    "disk", "diskfree", "disktotal", "bat",
    "netrx", "nettx", "uptime", "wx",
    "vol", "mut", "np",
    "inbox", "aiout", "aitotal", "aititle",
    # 单任务镜像字段（aistate/aitool/aitask/aiprog/aitime/aierr）已下线：
    # 固件从未渲染它们（grep 证实），内容与 ai0 完全重复，白占约 70 字节/帧。
    "temp", "hum",
    "lunar",   # 农历+节气倒计时（屏保日期行；无值自动省略）
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


# 固定槽位：即使为空也要上线——空值对设备是「清位」信号（固件把该槽位置为
# unused）。省略它们（旧行为）会让已关闭的会话/过期的完成记录永远留在屏上。
_SLOT_KEYS = ({f"ai{i}" for i in range(8)} | {f"dn{i}" for i in range(3)}
               | {f"w{i}" for i in range(3)} | {f"wf{i}" for i in range(3)}
               | {"ps"}
               # 温度/风扇：采集能力丢失时发空串=设备端清位（不残留旧读数）
               | {"cputemp", "gputemp", "fan", "aiu", "afm", "np",
                  "aiout", "aitotal", "aititle"})


def now_playing_wire(info: dict | None) -> str:
    """NP= 的值；字段按固件固定缓冲裁剪，空值显式清除旧曲目。"""
    if not isinstance(info, dict) or not info.get("title"):
        return ""

    def field(value, size):
        text = " ".join(str(value or "").replace(",", " ").split())
        # 屏幕中文字体覆盖 GB2312；用 ? 代替未覆盖的符号和 emoji。
        text = text.encode("gb2312", "replace").decode("gb2312")
        return text.encode("utf-8")[:size].decode("utf-8", "ignore")

    def seconds(value):
        try:
            number = float(value)
            return min(32767, max(0, int(number))) if math.isfinite(number) else 0
        except (TypeError, ValueError, OverflowError):
            return 0

    return ",".join((field(info["title"], 48), field(info.get("artist"), 24),
                     str(seconds(info.get("position"))),
                     str(seconds(info.get("duration"))),
                     "1" if info.get("playing") else "0"))


def encode_telemetry(t: dict) -> str:
    """把采集/检测合并后的遥测字典编码为多行 KV 文本。"""
    lines = []
    for key in _ORDER:
        v = t.get(key)
        if v is None or (v == "" and key not in _SLOT_KEYS):
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
    if line.startswith("[PETPERF] "):
        fields = dict(part.split("=", 1) for part in line[10:].split()
                      if "=" in part)
        try:
            return {"pet_frame_perf": {"frame": fields["frame"][:32],
                                       "load_ms": int(fields["load_ms"]),
                                       "psram": int(fields["psram"])}}
        except (KeyError, ValueError):
            return None
    if not line or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip().upper()
    value = value.strip()
    if key in ("TEMP", "PRESSURE", "DISTANCE"):
        if value.upper() == "NA":
            return {key.lower(): None}
        try:
            number = float(value)
            if not math.isfinite(number):
                return None
            return {key.lower(): number}
        except ValueError:
            return None
    if key == "ENVSRC":
        return {"env_source": value if value in ("AHT20", "DHT11", "NA") else "NA"}
    if key == "SN":
        return {"sensor_bus": value[:64]}
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
    if key in ("ACK", "NAK") and value.startswith("OTA,"):
        return {"ota_reply": {"ok": key == "ACK", "reason": value[4:]}}
    if key == "FW":
        return {"firmware": value}
    if key == "SNAP":
        # 实拍回传帧：BEGIN,w,h / DATA,seq,b64 / END
        parts = value.split(",", 2)
        if parts[0] == "BEGIN" and len(parts) == 3:
            try:
                return {"snap": {"kind": "begin", "w": int(parts[1]),
                                 "h": int(parts[2])}}
            except ValueError:
                return None
        if parts[0] == "DATA" and len(parts) == 3:
            return {"snap": {"kind": "data", "seq": parts[1], "b64": parts[2]}}
        if parts[0] == "END":
            return {"snap": {"kind": "end"}}
        return None
    if key in ("ACK", "NAK") and value.startswith("SNAP,"):
        return {"snap_nak": {"ok": key == "ACK", "reason": value[5:]}}
    if key == "PET":
        return {"pet": value.lower()}
    if key == "SLOW":
        try:
            return {"slow_loop": int(value.split()[0])}
        except (ValueError, IndexError):
            return None
    return None
