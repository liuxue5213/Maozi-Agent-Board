"""协议 v0.2 编解码。字段与 docs/PROTOCOL.md 一一对应。"""
import math
import time

# --- 协议版本协商（P1-1：握手版本校验） ---
# 客户端与固件通过握手交换协议版本，旧固件不推送 v0.3.2 新增字段。
PROTOCOL_VERSION = (0, 3, 2)       # 本客户端遵循的线协议版本（与 FW 握手串一致）
PROTOCOL_VERSION_STR = "0.3.2"

# v0.3.2 才引入的下行字段（旧固件解析不了/无对应渲染）。握手版本 < (3,2) 时压掉。
# AI0~AI3 为基线保留；NP/用量/焦点/久坐统计/分模型消耗均为 v0.3.2 新增。
PROTO_V032_DOWNLINK_ONLY = frozenset({
    "ai4", "ai5", "ai6", "ai7",      # AI 槽 4→7（0-3 为基线）
    "np",                             # 正在播放
    "aiu", "aitok", "aiout", "aitotal", "aititle", "afm",  # 用量 / 焦点会话
    "pb", "ast",                      # 久坐阈值 / 会话统计
    "aib0", "aib1", "aib2",           # 分模型消耗
})


def _split_version(token: str):
    """'0.3.2' / '3.2' → (3, 2) / (0, 3, 2)；非法返回 None。"""
    parts = token.split(".")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def parse_protocol_version(raw: str):
    """从 FW 握手串提取固件协议版本。优先 `proto:X.Y`，否则取首个版本形态 token。"""
    if not raw:
        return None
    for part in raw.split(","):           # 1) 显式 proto: 字段（新固件）
        if part.startswith("proto:"):
            v = _split_version(part[len("proto:"):].strip())
            if v:
                return v
    head = raw.split(",")[0].strip()      # 2) 兼容旧固件 `FW=0.2,n16r8,...`
    if "." in head:
        v = _split_version(head)
        if v:
            return v
    return None


def negotiated_supports_v032(fw_proto) -> bool:
    """固件是否支持 v0.3.2 全部新字段。fw_proto=None（未知）按支持处理，避免误伤。"""
    if fw_proto is None:
        return True
    return fw_proto >= PROTOCOL_VERSION


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


def encode_telemetry(t: dict, fw_proto=None) -> str:
    """把采集/检测合并后的遥测字典编码为多行 KV 文本。
    fw_proto 为固件握手版本；若 < (3,2) 则压掉 v0.3.2 才引入的下行字段（旧固件兼容）。
    fw_proto=None（未知）按全量发送，保持历史行为。"""
    skip = (PROTO_V032_DOWNLINK_ONLY
            if fw_proto is not None and not negotiated_supports_v032(fw_proto)
            else frozenset())
    lines = []
    for key in _ORDER:
        if key in skip:
            continue
        v = t.get(key)
        if v is None or (v == "" and key not in _SLOT_KEYS):
            continue
        lines.append(f"{key.upper()}={_fmt(v)}")
    return "\n".join(lines) + "\n"


def encode_caps(caps: dict, proto: str = PROTOCOL_VERSION_STR) -> str:
    kv = ",".join(f"{k}:{1 if v else 0}" for k, v in caps.items())
    return f"CAPS={kv},proto:{proto}\n"


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
        # 已知来源白名单：AHT20(主) / DHT11(回退) / BMP280(温度兜底，见 firmware sensors.h) / NA(无)。
        # 未知值一律回落 "NA"，避免未来固件新增来源时把脏字符串透传给前端。
        return {"env_source": value if value in ("AHT20", "DHT11", "BMP280", "NA") else "NA"}
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
