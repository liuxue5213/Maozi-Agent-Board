"""主循环：采集 → AI 检测 → 合并遥测 → 控制台/串口输出。"""
import json
import math
import re
import subprocess
import sys
import time
import threading
from copy import deepcopy
from pathlib import Path

from .collect import create
from .control import ControlApp
from .protocol import encode_caps, encode_hb, encode_telemetry
import base64 as _b64
import zlib as _zlib

from . import music
from .pet import Pet
from .profile import encode_profile, load_profile, save_profile, validate_profile
from .telemetry import HealthLog
from .trend import TrendStore
from .weather import WeatherFetcher
from . import syscal as syscal_mod


def _proc_report():
    """进程识别的可读报告。扫描失败时返回 ok:false，前端据此说明原因。"""
    from . import procwatch
    try:
        return procwatch.report()
    except Exception as exc:                      # 兜底：绝不因它影响遥测
        return {"ok": False, "error": str(exc), "tools": []}


# 协议槽位数：与固件 proto.h 的 tasks[8]/done[3] 对应。
AI_WIRE_SLOTS = 8
DONE_WIRE_SLOTS = 3

# 推送标题用的状态名（web 端 stateNames 的服务端子集）
_PUSH_STATE_NAMES = {"DONE": "已完成", "ERROR": "出错",
                     "WAIT_INPUT": "等你回复", "ASK": "待确认"}

# 收件箱静音状态落盘（{id: 到期 epoch}，0=永久）：Web/设备角标/推送共用
# 一个事实源。文件很小且每秒最多写一次，mtime 校验懒加载足够。
INBOX_STATE_PATH = Path.home() / ".workbuddy" / "inbox-state.json"

# 场景包昼夜自动切换的「当前已应用包」落盘：客户端重启后不重复下发，
# 部分失败时靠 at 时间戳做 ≥10 分钟的重试退避。
PACK_STATE_PATH = Path.home() / ".workbuddy" / "petpack-state.json"


def _fmt_mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _in_quiet_hours(window: str, now: float | None = None) -> bool:
    """安静时段判定："HH:MM-HH:MM"，可跨零点；空串/解析失败 = 不安静。

    事件本身不丢——只是夜里不震手机，Web 收件箱照常记录。
    """
    text = (window or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", text)
    if not m:
        return False
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return False
    lt = time.localtime(time.time() if now is None else now)
    cur = lt.tm_hour * 60 + lt.tm_min
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if start == end:
        return False
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end        # 跨零点（23:00-07:30）


def weather_wire_lines(cities_cfg, data_by_name):
    """多城市天气 → W0..W2 / WF0..WF2 线级字段。

    W<i>=temp,code,hi,lo   实况（temp 一位小数）
    WF<i>=code,lo,hi;…     5 天预报（今天起，整数量纲）
    固件按页面 city（城市名）在 profile.cities 里的下标取用，
    所以这里的顺序必须与 cities 配置一致。
    """
    lines = {}
    for i, entry in enumerate(cities_cfg[:3]):
        name = entry.get("name") or ""
        d = data_by_name.get(name)
        if not d:
            continue
        lines[f"w{i}"] = (f"{d['temp']},{d['code']},{d['hi']},{d['lo']}")
        days = d.get("days") or []
        if days:
            lines[f"wf{i}"] = ";".join(f"{x['code']},{x['lo']},{x['hi']}" for x in days)
    for i in range(3):
        lines.setdefault(f"w{i}", "")
        lines.setdefault(f"wf{i}", "")
    return lines


def ai_wire_lines(tasks, done):
    """把会话/完成记录压成 AI0..AI7 与 DN0..DN2 的线级字段。

    行格式（与 docs/PROTOCOL.md 对应）：
      AI<i>=tool,state,prog,elapsed,project,id,age
      DN<i>=tool,project,age      （最近完成的会话，新→旧）
    字段里不允许出现逗号（sanitize_project 已过滤）；prog<0 表示未知进度，
    固件对未知进度不画填充条。
    """
    lines = {}
    for i, tk in enumerate(tasks[:AI_WIRE_SLOTS]):
        lines[f"ai{i}"] = (f"{tk['tool']},{tk['state']},{tk['prog']},"
                           f"{tk['elapsed']},{tk['project']},{tk['id']},{tk['age']}")
    for i, d in enumerate(done[:DONE_WIRE_SLOTS]):
        lines[f"dn{i}"] = f"{d.get('tool','')},{d.get('project','')},{int(d.get('age',0))}"
    # 槽位必须**全量下发**（空串 = 清位）：设备收到 AI<i>= 才会置 used=false，
    # 只发非空槽位会让关闭的会话在设备上永远残留（幽灵行/冻结的完成带）。
    for i in range(AI_WIRE_SLOTS):
        lines.setdefault(f"ai{i}", "")
    for i in range(DONE_WIRE_SLOTS):
        lines.setdefault(f"dn{i}", "")
    return lines


class App:
    def __init__(self, cfg):
        import psutil

        psutil.cpu_percent(interval=None)  # 预热首次读数
        self.cfg = cfg
        self.collector = create(cfg)
        self.env = {}  # ESP32 上行（temp/hum 等）
        self.env_seen = {}
        self.control = ControlApp()
        self.health = HealthLog()
        self.trend = TrendStore()    # P1-4 行为遥测：每分钟一拍趋势样本
        self.pet = Pet(lambda: self.profile.get("pet", {}))
        self._np_cache = {"supported": None, "now": {}}
        self._img_reply = None
        self._ota_reply = None
        self._ota_event = threading.Event()
        self._ota_active = False
        self.ota_result = {"ok": None, "reason": ""}
        self.weather = WeatherFetcher(lambda: (self.profile.get('weather') or {}).get('cities', []))
        # 系统日历只读接入（macOS EventKit）：默认关，设置页打开后开始拉取
        self.syscal_fetcher = None
        if getattr(cfg, "syscal", False):
            from .syscal import SysCalFetcher
            self.syscal_fetcher = SysCalFetcher()
            self.syscal_fetcher.start()
        self.fw_caps = None          # 设备能力（来自 FW 握手），None = 未知
        self.profile_lock = threading.Lock()
        self.profile = load_profile()
        # 配置/档案曾在损坏后被隔离回退过 → Web 自检面板要能提示
        from . import config as _config_module
        from . import profile as _profile_module
        self.config_warning = (_profile_module.LOAD_WARNING
                               or _config_module.LOAD_WARNING)
        # 旧档案的 weather.city 单字段迁移（地理编码，失败不打扰运行）
        if (self.profile.get("weather") or {}).get("city") \
                and not (self.profile.get("weather") or {}).get("cities"):
            threading.Thread(target=self._migrate_legacy_city,
                             daemon=True).start()
        self.profile_status = "待同步"
        self.profile_error = ""
        self._pack_next = 0.0         # 昼夜切包检查的节流闸（_pack_tick）
        self.applied_version = None
        # 设备最近一次成功应用的内容快照：「回到设备版本」的数据源。
        # 新版本应用失败时用户可以把客户端配置退回去，而不是卡在失败态。
        self.applied_profile = None
        self.sync_requested = False
        self.sync_sent_at = 0.0
        self._caps_sent_at = 0.0
        self.tasks = []
        self.snap_at = None           # 最近一次实拍完成时间（None=从未）
        self._snap_buf = None
        self._snap_meta = (160, 240)
        # 实拍回传静默窗（monotonic 时刻，0=未在实拍）：设备回传一帧约 10s 且
        # 主循环全程阻塞，期间继续下行遥测必然打断传输（tools/device_snapshot.py
        # 停客户端直读零丢帧、客户端在线永远收不到 END，实测结论）。窗口期内
        # 主循环不发任何下行；收到非 snap 上行或超时即提前结束。
        self._snap_until = 0.0
        self._was_online = False
        self._online_since = 0.0
        self._last_ai_state = ""
        self.ai = None
        self.usage_scanner = None
        if cfg.ai_watch:
            from .aiwatch import AIWatcher
            self.ai = AIWatcher(cfg)
            from .aiwatch.usage import UsageScanner
            self.usage_scanner = UsageScanner(cfg.roots)
            self.usage_scanner.start()
        if cfg.serial:
            from .serial_link import SerialLink
            self.link = SerialLink(cfg, on_uplink=self._on_uplink)
        else:
            self.link = None
        self.last = {}
        self._last_tick_at = 0.0     # 最近一次成功采集的时刻，供 Web 判定数据新鲜度
        self.paused = False          # 托盘「暂停采集」：暂停期间复播上一帧遥测
        self._kick = threading.Event()   # Hook 快通道：立即唤醒主循环（附录A）
        self.on_tick = None   # GUI 回调：每个采集周期调用一次
        self.on_ai_notice = None  # 托盘回调：焦点任务跨入 DONE/ERROR/WAIT_INPUT/ASK 时调用
        self._stop = False
        threading.Thread(target=self._np_loop, daemon=True,
                         name="now-playing").start()
        self.on_quit = None   # 托盘「退出」回调：用于停掉托盘/窗口线程
        self.window_visible = True  # 主窗口是否可见（隐藏到托盘时为 False）

    def _on_uplink(self, d: dict):
        if "snap" not in d and "snap_nak" not in d:
            # 设备能发非 snap 上行 = 主循环已恢复响应，实拍静默窗提前收尾
            self._snap_until = 0.0
        if "firmware" in d:
            self._parse_fw_caps(d["firmware"])
        if "snap" in d:
            self._handle_snap_frame(d["snap"])
        if "snap_nak" in d:
            self.health.event("snap_nak", reason=d["snap_nak"]["reason"][:24])
        if "slow_loop" in d:
            self.health.event("slow_loop", us=d["slow_loop"])
        if "launch" in d:
            self._launch_app(d["launch"])
            self.health.event("launch", app=d["launch"][:16])
        if "pet" in d:
            self.pet.interact(d["pet"])
            self.health.event("pet", action=d["pet"][:8])
        elif "key" in d:
            self._handle_key(d["key"])
            self.health.event("key", action=d["key"])
        if "touch" in d:
            self.health.event("touch", target=d["touch"][:16])
        if "img_reply" in d:
            self.health.event("img_ack" if d["img_reply"]["ok"] else "img_nak",
                              reason=d["img_reply"]["reason"])
            self._img_reply = {"ok": d["img_reply"]["ok"],
                               "reason": d["img_reply"]["reason"]}
        if "ota_reply" in d:
            self._ota_reply = d["ota_reply"]
            self._ota_event.set()
            self.health.event("ota_ack" if d["ota_reply"]["ok"] else "ota_nak",
                              reason=d["ota_reply"]["reason"][:24])
        if "config_reply" in d:
            reply = d["config_reply"]
            with self.profile_lock:
                if str(self.profile["version"]) == reply["version"]:
                    self.profile_status = "设备已应用" if reply["ok"] else "同步失败"
                    self.profile_error = "" if reply["ok"] else reply["reason"]
                    if reply["ok"]:
                        self.applied_version = self.profile["version"]
                        # 快照要在锁内、且在 version 匹配的前提下取——
                        # 晚一拍 profile 可能已经是下一个待同步版本
                        self.applied_profile = deepcopy(self.profile)
            self.health.event("sync_ack" if reply["ok"] else "sync_nak",
                              version=reply["version"], reason=reply["reason"])
        self.env.update(d)
        seen = time.monotonic()
        for key in ("temp", "hum"):
            if key in d:
                self.env_seen[key] = seen

    def _parse_fw_caps(self, raw: str):
        """FW=0.3.2,n16r8,schema:1,pages:6,widgets:10,apps:12,... → 能力字典。"""
        caps = {"raw": raw[:64]}
        for part in raw.split(","):
            if ":" in part:
                key, _, value = part.partition(":")
                caps[key.strip()] = int(value) if value.strip().isdigit() else value.strip()
        with self.profile_lock:
            first_handshake = self.fw_caps is None
            self.fw_caps = caps
            if first_handshake:
                self.sync_requested = True
                self.profile_status = "待同步"
                self.profile_error = ""
        self.health.event("fw", raw=raw[:64])

    def push_theme(self, theme: dict | None = None):
        """把配置里的主题色 + 亮度推送到屏幕。"""
        th = theme or self.profile["theme"]
        parts = []
        for k, v in th.items():
            if k == "bright":
                continue
            parts.append(f"cardopa:{v}" if k == "card_opa" else f"{k}:{v}")
        if th.get("bright") is not None:
            parts.append(f"bright:{th['bright']}")
        if self.link:
            self.link.send("CFG=" + ",".join(parts) + "\n")

    def stop(self):
        self._stop = True
        if self.usage_scanner:
            self.usage_scanner.stop()
        self.weather.stop()
        if self.syscal_fetcher:
            self.syscal_fetcher.stop()
        if self.link:
            try:
                self.link.stop()
            except Exception:
                pass

    def shutdown(self):
        """彻底退出：停采集循环 → 停串口 → 停 Web 服务（释放端口）。

        托盘「退出」走这里。必须释放 8765 端口，否则重开进程会报
        "Address already in use"。每一步独立兜底，任何一步失败都不阻断后续。
        """
        self._stop = True
        if self.usage_scanner:
            try:
                self.usage_scanner.stop()
            except Exception:
                pass
        try:
            self.weather.stop()
        except Exception:
            pass
        if self.link:
            try:
                self.link.stop()
            except Exception:
                pass
        srv = getattr(self, "_web_server", None)
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
            self._web_server = None
        if self.on_quit:
            try:
                self.on_quit()
            except Exception:
                pass

    def set_paused(self, flag: bool) -> bool:
        """托盘「暂停采集」（优化#16）。暂停期间不跑采集/AI 扫描，遥测复播
        上一帧并继续心跳——设备不会误报离线，Web 端靠 telemetry_age 判 stale。"""
        self.paused = bool(flag)
        return self.paused

    def set_device_page(self, page_1based: int) -> bool:
        """托盘「切换设备页」（优化#16）：下发 PAGE=N，固件收到后切页并回执。"""
        if not self.link or not self.link.status.startswith("connected:"):
            return False
        n = int(page_1based)
        if not 1 <= n <= len(self.profile.get("pages", [])):
            return False
        self.link.send(f"PAGE={n}\n")
        self.health.event("page_req", page=n)
        return True

    def hook_event(self, kind: str, agent: str = "") -> bool:
        """Hook Push 快通道（附录A，学 EdgeGlow/cursor-light）。

        Claude Code 等编辑器的 hooks（UserPromptSubmit/Stop…）直接打本地
        /hook/start|pulse|stop，这里立即唤醒主循环做一次采集——把「Agent 开始
        瞬间亮屏」的盲区从最长一个采集周期压到 ~0。事件本身只进健康日志。
        """
        self.health.event("hook", hook=kind[:8], agent=str(agent)[:16])
        self._kick.set()
        return True

    def _tasks_with_trace(self) -> list:
        """status_snapshot 用：给 tasks 附上阶段事件（优化#13）。

        上限取 aiwatch.MAX_TASKS：多编辑器 × 多项目并行时 Web 要能看全，
        设备侧仍只走 AI0~AI3 四条（协议未变）。
        """
        from .aiwatch import MAX_TASKS
        tasks = [dict(t) for t in self.tasks[:MAX_TASKS]]
        ai = getattr(self, "ai", None)
        if ai:
            for t in tasks:
                try:
                    t["trace"] = ai.trace_for(t["id"])
                except Exception:
                    t["trace"] = []
        return tasks

    def _done_recent(self) -> list:
        """最近完成的会话（新→旧）。AI 检测关闭时返回空列表。"""
        ai = getattr(self, "ai", None)
        if not ai:
            return []
        try:
            return ai.done_recent()
        except Exception:
            return []

    # ---- 实拍（SNAP：设备快照 → 串口回传 → PNG）----

    def pet_scene_event(self, action: str) -> None:
        """把三种宠物互动转发设备，触发对应的短反馈场景。"""
        if action in ("feed", "water", "play") and self.link and \
                self.link.status.startswith("connected:"):
            self.link.send(f"PET={action}\n")

    def request_snap(self) -> bool:
        """工作台「实拍」按钮：下发 SNAP=1，设备约 10s 内分帧回传。"""
        if not self.link or not self.link.status.startswith("connected:"):
            return False
        self._snap_buf = None
        self.snap_at = None
        self._snap_until = time.monotonic() + 25.0
        self.link.send("SNAP=1\n")
        self.health.event("snap_req")
        return True

    def _handle_snap_frame(self, frame: dict):
        if frame["kind"] == "begin":
            self._snap_meta = (frame["w"], frame["h"])
            self._snap_buf = bytearray()
        elif frame["kind"] == "data" and self._snap_buf is not None:
            try:
                self._snap_buf += _b64.b64decode(frame["b64"])
            except Exception:
                self._snap_buf = None
        elif frame["kind"] == "end" and self._snap_buf is not None:
            self._finish_snapshot()

    def _finish_snapshot(self, path: Path | None = None):
        """RGB565 小端 → PNG。像素数不符（丢帧）则整帧丢弃，不保存半图。"""
        self._snap_until = 0.0
        w, h = self._snap_meta
        expected = w * h * 2
        if not self._snap_buf or len(self._snap_buf) != expected:
            self.health.event("snap_bad",
                              size=len(self._snap_buf or b""),
                              expected=expected)
            self._snap_buf = None
            return
        pixels = bytearray(w * h * 3)
        for i in range(w * h):
            v = self._snap_buf[i * 2] | (self._snap_buf[i * 2 + 1] << 8)
            pixels[i * 3] = ((v >> 11) & 0x1F) << 3       # R
            pixels[i * 3 + 1] = ((v >> 5) & 0x3F) << 2    # G
            pixels[i * 3 + 2] = (v & 0x1F) << 3           # B
        try:
            from PIL import Image
            img = Image.frombytes("RGB", (w, h), bytes(pixels))
            # 默认落到 ~/.workbuddy/snapshot.png；测试可注入 _snap_path
            target = Path(path) if path else Path(
                getattr(self, "_snap_path", None) or
                (Path.home() / ".workbuddy" / "snapshot.png"))
            target.parent.mkdir(parents=True, exist_ok=True)
            img.save(target, "PNG")
        except Exception as exc:
            self.health.event("snap_save_fail", err=str(exc)[:32])
            self._snap_buf = None
            return
        self._snap_buf = None
        self.snap_at = time.time()
        self.health.event("snap_ok", w=w, h=h)

    def snapshot_bytes(self) -> bytes | None:
        png = Path.home() / ".workbuddy" / "snapshot.png"
        try:
            return png.read_bytes() if png.exists() else None
        except OSError:
            return None

    def _launch_app(self, app_id: str) -> dict:
        """快捷应用点击：普通条目按命令启动；@music 保留字交给默认音乐软件。"""
        with self.profile_lock:
            command = next((a["command"] for a in self.profile.get("apps", [])
                            if a["id"] == app_id), "")
        if command == music.MUSIC_COMMAND:
            return self.music_play()
        if command:
            self.control.launch(command)
        return {"ok": True, "command": command}

    def music_play(self) -> dict:
        """启动默认音乐软件并开始播放（触屏「音乐」快捷应用的落点）。"""
        result = music.play_default(self.cfg)
        health = getattr(self, "health", None)
        if health:
            health.event("music_play", app=str(result.get("app", ""))[:16],
                         launched=1 if result.get("launched") else 0,
                         ok=1 if result.get("ok") else 0)
        return result

    def _np_loop(self):
        """正在播放后台刷新：3 秒一轮，遥测与 Web 共用缓存，零阻塞。"""
        time.sleep(2)
        while not self._stop:
            try:
                info = music.now_playing(timeout=0.2)
            except Exception:
                info = None
            self._np_cache = {"supported": info is not None,
                              "now": info if isinstance(info, dict) else {}}
            time.sleep(3)

    def _music_status(self) -> dict:
        """正在播放信息（后台线程已缓存，这里只读）。"""
        return getattr(self, "_np_cache", {"supported": None, "now": {}})

    def _handle_key(self, action: str):
        # ±10：osascript 改音量不触发 macOS 音量 HUD，步长太小用户会以为没反应
        if action == "VOL+":
            self.control.volume_step(+10)
        elif action == "VOL-":
            self.control.volume_step(-10)
        elif action == "MUTE":
            self.control.toggle_mute()
        elif action == "PLAY":
            # 中间键是「播放/暂停」而不是单向播放。存在系统媒体会话时只切换
            # 当前播放器；没有会话时才启动用户在 Web 中选定的那一个软件。
            # 这样既能从冷启动开始听歌，也不会再依次弹出 Music/Spotify。
            if music.has_running_player(self.cfg):
                self.control.media("toggle")
            else:
                music.play_default(self.cfg)
        elif action == "NEXT":
            self.control.media("next")
        elif action == "PREV":
            self.control.media("prev")

    # ---- 需要处理（收件箱）：Web / 设备角标 / 推送共用的服务端事实源 ----

    def _inbox_seen(self) -> dict:
        """读静音记录；文件被外部改动（mtime 变化）时重新加载。"""
        cache = getattr(self, "_inbox_seen_cache", None)
        try:
            mtime = INBOX_STATE_PATH.stat().st_mtime
        except OSError:
            mtime = None
        if cache is None or cache[0] != mtime:
            data = {}
            try:
                data = json.loads(INBOX_STATE_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            data = data if isinstance(data, dict) else {}
            cache = self._inbox_seen_cache = (mtime, data)
        return cache[1]

    def open_task_project(self, inbox_id: str, mode: str = "finder") -> dict:
        """收件箱「打开项目」：按条目 id 找到会话的工作目录，在 Finder 或
        终端（新窗口 cd 到该目录）打开。安全边界：数组调用不走 shell。"""
        for t in self.tasks:
            if f"t:{t['tool']}:{t['id']}" != inbox_id:
                continue
            path = t.get("path") or ""
            if not path:
                return {"ok": False, "error": "该会话没有可用的项目路径"}
            from . import control as _control
            if mode == "terminal":
                return _control.open_in_terminal(path)
            return _control.open_in_finder(path)
        return {"ok": False, "error": "会话不在当前列表里"}

    def inbox_dismiss(self, ids: list, snooze_minutes: float = 0):
        """静音条目：snooze_minutes>0 = 到期重现；否则永久（条目消失自动清）。"""
        seen = self._inbox_seen()
        until = time.time() + snooze_minutes * 60 if snooze_minutes else 0
        for i in ids:
            i = str(i)
            # 硬件水位条 id 恒定：永久静音会让未来的告警也哑掉 → 最长静音 12h
            if i.startswith("hw:") and not until:
                until = time.time() + 12 * 3600
            seen[str(i)] = until
        try:
            INBOX_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            INBOX_STATE_PATH.write_text(
                json.dumps(seen, ensure_ascii=False), encoding="utf-8")
            self._inbox_seen_cache = (INBOX_STATE_PATH.stat().st_mtime, seen)
        except OSError:
            pass

    def _update_hw_alert(self, now: float):
        """CPU 高温状态机。状态存 self._hw_alert：{since, active}。"""
        if not hasattr(self, "_hw_alert"):
            self._hw_alert = {"since": None, "active": False}
        cfg = getattr(self, "cfg", None)
        threshold = float(getattr(cfg, "hw_cpu_temp", 85.0) or 85.0)
        if getattr(self, "_hw_thr_at", None) != threshold:
            # 阈值被调整：旧的防抖计时/告警状态对新阈值无意义，全部重置
            self._hw_thr_at = threshold
            self._hw_alert = {"since": None, "active": False}
        temp = self.last.get("cputemp")
        if not isinstance(temp, (int, float)):
            return                          # 无温度数据：状态保持，不误报也不恢复
        hot = temp >= threshold
        state = self._hw_alert
        if hot and state["since"] is None:
            state["since"] = now
        if hot and not state["active"] and now - state["since"] >= 60:
            state["active"] = True
            self.health.event("hw_alert", kind="cpu_temp", temp=temp)
        elif not hot and temp <= threshold - 10 and state["active"]:
            state["active"] = False
            state["since"] = None
            self.health.event("hw_recovered", kind="cpu_temp", temp=temp)
        elif not hot and not state["active"]:
            state["since"] = None

    def inbox_items(self) -> list:
        """聚合待处理事项（加锁入口）。status_snapshot 已持有 profile_lock，
        必须走 _inbox_items_locked——普通 Lock 不可重入，否则自死锁。"""
        with self.profile_lock:
            return self._inbox_items_locked()

    def _inbox_items_locked(self) -> list:
        """聚合待处理事项（等你/待确认/出错 + 24h 内日程 + 磁盘水位），
        已静音的剔除。要求调用方已持有 profile_lock。"""
        items = []
        now = time.time()
        names = {"WAIT_INPUT": ("等你回复", "warn"),
                 "ASK": ("等你确认", "warn"), "ERROR": ("出错", "bad")}
        for t in self.tasks:
            state = t.get("state")
            if state not in names:
                continue
            label, sev = names[state]
            items.append({"id": f"t:{t['tool']}:{t['id']}", "kind": "task",
                          "sev": sev,
                          "title": f"{t['tool']} · {t.get('project') or '未命名'}",
                          "detail": f"{label} · 已等 {_fmt_mmss(t.get('age', 0))}",
                          "path": t.get("path") or ""})
        calendar = list((self.profile.get("calendar") or []))
        for c in calendar:
            if c.get("enabled") is False:
                continue
            try:
                start = time.mktime(time.strptime(
                    f"{c['date']} {c['time']}", "%Y-%m-%d %H:%M"))
            except (KeyError, ValueError):
                continue
            dt = start - now
            if dt < -3600 or dt > 24 * 3600:
                continue
            items.append({"id": f"c:{c['id']}", "kind": "calendar", "sev": "info",
                          "title": c.get("title") or "日程",
                          "detail": "已开始" if dt <= 0
                          else f"{int(dt // 60)} 分钟后开始（{c['time']}）",
                          "at": start})
        # 系统日历（只读）：24h 内事件进收件箱；拉取失败静默跳过
        if getattr(self, "syscal_fetcher", None) is not None:
            state = syscal_mod.read_state()
            for e in state.get("events") or []:
                try:
                    start = time.mktime(time.strptime(
                        f"{e['date']} {e['time']}", "%Y-%m-%d %H:%M"))
                except (KeyError, ValueError):
                    continue
                dt = start - now
                if dt < -3600 or dt > 24 * 3600:
                    continue
                items.append({"id": f"s:{e['id']}", "kind": "syscal", "sev": "info",
                              "title": e.get("title") or "日程",
                              "detail": "已开始" if dt <= 0
                              else f"{int(dt // 60)} 分钟后开始（{e['time']}）",
                              "at": start})
        disk = self.last.get("disk")
        cfg = getattr(self, "cfg", None)
        disk_limit = int(getattr(cfg, "hw_disk", 90) or 90)
        if isinstance(disk, (int, float)) and disk >= disk_limit:
            free = self.last.get("diskfree")
            items.append({"id": "hw:disk", "kind": "hw", "sev": "bad",
                          "title": f"磁盘 {int(disk)}%",
                          "detail": (f"仅剩 {free} GB 可用"
                                     if free is not None else "空间不足")})
        if getattr(self, "_hw_alert", {}).get("active"):
            temp = self.last.get("cputemp")
            if isinstance(temp, (int, float)):
                limit = float(getattr(getattr(self, "cfg", None), "hw_cpu_temp", 85.0) or 85.0)
                items.append({"id": "hw:cputemp", "kind": "hw", "sev": "bad",
                              "title": f"CPU {temp:.0f}℃",
                              "detail": f"持续高于 {limit:.0f}℃（检查风扇/负载）"})
        seen = self._inbox_seen()
        now2 = time.time()
        return [x for x in items
                if x["id"] not in seen or (seen[x["id"]] and seen[x["id"]] <= now2)]

    def inbox_count(self) -> int:
        with self.profile_lock:
            return len(self._inbox_items_locked())

    def _push_notify(self, name: str, state: str):
        """手机推送（Bark / ntfy 通用）：AI 跨入需要用户知道的状态时触发。

        配置 push_url 两种写法：
          · 占位符 GET：https://api.day.app/你的Key/{title}/{body}   （Bark）
          · 直接 POST：https://ntfy.sh/你的主题                      （ntfy，
            body=消息文本，X-Title 头=标题）
        push_states 控制哪些状态推送（默认「出错 + 等你 + 待确认」，
        完成 tends 噪点多，默认不推）。
        """
        url = str(getattr(self.cfg, "push_url", "") or "").strip()
        if not url:
            return
        states = {s.strip().upper() for s in
                  str(getattr(self.cfg, "push_states", "ERROR,WAIT_INPUT,ASK")
                      or "").split(",") if s.strip()}
        if state not in states:
            return
        if _in_quiet_hours(str(getattr(self.cfg, "push_quiet", "") or "")):
            self.health.event("push_quiet", state=state)
            return
        threading.Thread(target=self._push_send,
                         args=(url, name, state), daemon=True).start()

    def _push_message(self, title: str, text: str):
        """通用文案推送（不经过 push_states 过滤；安静时段由调用方把关）。"""
        url = str(getattr(self.cfg, "push_url", "") or "").strip()
        if not url:
            return
        threading.Thread(target=self._push_post,
                         args=(url, title, text), daemon=True).start()

    def _push_send(self, url: str, name: str, state: str):
        title = f"WorkBuddy · {_PUSH_STATE_NAMES.get(state, state)}"
        text = f"{name} {title.split('·')[-1].strip()}"
        self._push_post(url, title, text)

    def _push_post(self, url: str, title: str, text: str):
        from urllib.parse import quote
        from urllib.request import Request, urlopen
        try:
            if "{title}" in url or "{body}" in url:
                req = Request(url.replace("{title}", quote(title))
                                  .replace("{body}", quote(text)))
            else:
                # HTTP 头只允许 latin-1：ntfy 约定头里用百分号编码 UTF-8
                req = Request(url, data=json.dumps(
                    {"title": title, "body": text, "message": text}).encode("utf-8"),
                    headers={"Content-Type": "application/json",
                             "X-Title": quote(title)})
            with urlopen(req, timeout=5) as resp:
                resp.read(1024)
            self.health.event("push_ok", state=state)
        except Exception as exc:
            self.health.event("push_fail", err=str(exc)[:32])

    @staticmethod
    def _is_precip_code(code) -> bool:
        """Open-Meteo 天气码是否降水（雨 51-67/80-82/95-99，雪 71-77/85-86）。"""
        try:
            c = int(code)
        except (TypeError, ValueError):
            return False
        return (51 <= c <= 67) or (71 <= c <= 77) or (80 <= c <= 82) or (c >= 95)

    def _raining_now(self) -> bool:
        """第一城市的当前天气码是否降水（无数据=False）。"""
        try:
            with self.weather._lock:
                data = dict(self.weather.data)
            cities = (self.profile.get("weather") or {}).get("cities") or []
            name = (cities[0] or {}).get("name") if cities else ""
            entry = data.get(name) or {}
            return self._is_precip_code(entry.get("code"))
        except Exception:
            return False

    def _input_idle_secs(self) -> float:
        """键鼠空闲秒数。macOS 走 ioreg HIDIdleTime（纳秒）；其他平台 -1
        （不启用提醒）。结果缓存 5s，避免每秒拉一次子进程。"""
        if not sys.platform.startswith("darwin"):
            return -1.0
        now = time.monotonic()
        cached, at = getattr(self, "_idle_cache", (-1.0, 0.0))
        if now - at < 5.0:
            return cached
        try:
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem"],
                                 capture_output=True, text=True,
                                 timeout=3).stdout
            cached = -1.0
            for line in out.splitlines():
                if "HIDIdleTime" in line and "=" in line:
                    cached = int(line.split("=")[-1].strip()) / 1e9
                    break
        except Exception:
            cached = -1.0
        self._idle_cache = (cached, now)
        return cached

    def _break_tick(self, now: float) -> int:
        """久坐提醒（竞品「偶尔提醒休息」）：键鼠空闲超过 pet.break_min
        分钟且非凌晨 → pb=1（宠物犯困姿态）+ 一次性推送；用户回来
        （空闲 <60s）复位，同一轮不重复打扰。"""
        pet = self.profile.get("pet") or {}
        try:
            limit = int(pet.get("break_min", 0) or 0)
        except (TypeError, ValueError):
            limit = 0
        idle = self._input_idle_secs() if limit > 0 else -1.0
        if 0 <= idle < 60:
            self._break_notified = False
        if limit <= 0 or idle < limit * 60:
            return 0
        hh = time.localtime(now).tm_hour
        if 1 <= hh < 6:
            return 0                        # 凌晨保持安静
        if not getattr(self, "_break_notified", False):
            self._break_notified = True
            mins = max(1, int(idle // 60))
            if not _in_quiet_hours(str(getattr(self.cfg, "push_quiet", "") or "")):
                self._push_message("WorkBuddy · 久坐提醒",
                                   f"已经 {mins} 分钟没有活动了，"
                                   "起来喝口水、活动一下吧")
            self.health.event("break_remind", mins=mins)
        return 1

    def _migrate_legacy_city(self):
        """旧档案只有 weather.city 单字段：地理编码补坐标后迁入 cities[]。

        新天气管线按 cities[] 拉取与下发，不迁移的话老城市会静默失效
        （概览/顶栏/天气组件全变「—」，用户却看不出哪里错了）。
        """
        with self.profile_lock:
            weather = dict(self.profile.get("weather") or {})
            legacy = weather.get("city", "")
            if not legacy or weather.get("cities"):
                return
        try:
            from .weather import geocode
            hits = geocode(legacy)
        except Exception as exc:
            self.health.event("city_migrate_fail", err=str(exc)[:32])
            return
        if not hits:
            self.health.event("city_migrate_fail", err=f"geocode miss:{legacy[:12]}")
            return
        hit = next((h for h in hits if h.get("name") == legacy), hits[0])
        try:
            weather["cities"] = [{
                "name": hit.get("name") or legacy,
                "lat": float(hit["lat"]), "lon": float(hit["lon"]),
                "label": hit.get("label") or legacy}]
            candidate = dict(self.profile)
            candidate["weather"] = weather
            self.save_profile(candidate)
            self.request_sync()               # 新版本号直接推给设备
            self.health.event("city_migrated",
                              city=str(hit.get("name") or legacy)[:12])
        except Exception as exc:
            self.health.event("city_migrate_fail", err=str(exc)[:32])

    def save_profile(self, draft):
        # 注意：kick 必须在 profile 赋值之后——先唤醒线程会读到旧城市表
        #（新城市 30 分钟内不会被拉取，实测：加北京后 W1 迟迟不出）
        with self.profile_lock:
            candidate = validate_profile(draft)
            candidate["version"] = self.profile["version"] + 1
            self.profile = save_profile(candidate)
            self.profile_status = "待同步"
            self.profile_error = ""
            self.sync_requested = False
        self.weather.kick()   # 城市列表可能变了：立刻按新表拉一轮
        return self.profile

    def request_sync(self):
        with self.profile_lock:
            self.sync_requested = True
            self.profile_status = "待同步"

    def restore_applied_profile(self) -> dict:
        """把客户端配置回退到设备当前已应用的版本。

        新保存的版本应用失败（NAK/超时）时，失败态会一直卡在顶栏；这个
        回退让用户一键回到「设备真实在显示的内容」，而不是对着一个上不了
        屏的配置干瞪眼。落盘保留原版本号：设备已经应用这一版，重新走
        save+sync 只是无效抖动。
        """
        with self.profile_lock:
            snap = self.applied_profile
            if not snap:
                raise ValueError("设备还没有成功应用过任何版本")
            restored = deepcopy(snap)
            self.profile = save_profile(restored)
            self.profile_status = "设备已应用"
            self.profile_error = ""
            self.applied_version = self.profile["version"]
            self.sync_requested = False
            self.health.event("restore_applied", version=self.profile["version"])
            return self.profile

    def status_snapshot(self):
        """Web 轮询的数据源。

        除遥测本身外，必须一并给出**数据年龄**：客户端主循环若卡死/崩溃，
        self.last 会停在最后一次成功采集的旧值上。前端拿到的是一份
        "看起来完全正常、实则早已冻结" 的数据（实测 CPU 一直显示 14%），
        用户无法分辨"真的是 14%"还是"早就没更新了"。
        telemetry_age / telemetry_at 让前端能据此判定陈旧并明确提示。
        """
        with self.profile_lock:
            last_tick = self._last_tick_at
            return {"profile": self.profile, "profile_status": self.profile_status,
                    "profile_error": self.profile_error,
                    "applied_version": self.applied_version,
                    "fw_caps": self.fw_caps,
                    "usage_error": self.usage_scanner.last_error if self.usage_scanner else "off",
                    # 配置/档案损坏被隔离回退的提示（Web 自检面板用）
                    "config_warning": getattr(self, "config_warning", ""),
                    # 自检面板用：手机推送是否已配置
                    "push_enabled": bool(str(getattr(self.cfg, "push_url", "") or "").strip()),
                    "push_quiet": str(getattr(self.cfg, "push_quiet", "") or ""),
                    # 需要处理：待处理数 + 明细（Web 收件箱与设备角标的事实源）。
                    # 本方法已持有 profile_lock：只能用 _locked 变体，不可重入。
                    "inbox": len(self._inbox_items_locked()),
                    "inbox_items": self._inbox_items_locked(),
                    # 系统日历只读接入的当前状态（自检面板/设置页用）
                    "syscal_enabled": bool(getattr(self.cfg, "syscal", False)),
                    "syscal": syscal_mod.read_state(),
                    # 设置页回填用；仅本机 Web 可见（服务绑定 127.0.0.1）
                    "push_url": str(getattr(self.cfg, "push_url", "") or ""),
                    # 本机采集能力与温度来源（Windows 上用于「装 LHM」引导）
                    "local_caps": dict(getattr(self.collector, "caps", {})
                                       if getattr(self, "collector", None) else {}),
                    "thermal_hint": getattr(getattr(self, "collector", None),
                                            "thermal_hint", ""),
                    "device": self.link.status if self.link else "串口关闭",
                    "tasks": self._tasks_with_trace(), "telemetry": self.last,
                    # Web 设备预览据此读取当前实机角色包的对应状态帧。
                    "petpack": self._pack_state(),
                    "telemetry_age": (round(time.time() - last_tick, 1)
                                      if last_tick else None),
                    "telemetry_at": last_tick,
                    "interval": self.cfg.interval,
                    "paused": getattr(self, "paused", False),
                    # 哪些 AI 工具真的在跑（进程识别，不是日志推断）
                    "tools": _proc_report(),
                    # 系统「正在播放」（MediaRemote；supported=False 表示读不到）
                    "music": self._music_status(),
                    # 最近完成的会话（「刚完成」条）；不靠列表重排提示完成
                    "done": self._done_recent(),
                    "usage": (self.usage_scanner.snapshot()
                              if self.usage_scanner else None),
                    # P1-2 用量预算：daily_k=0 表示未设限；exceeded 供 Web 提醒
                    "usage_budget": self._usage_budget()
                                    if self.usage_scanner else None}

    def _usage_budget(self) -> dict:
        cfg = (self.profile.get("usage") or {}) if isinstance(self.profile, dict) else {}
        try:
            daily_k = int(cfg.get("budget_daily_k") or 0)
        except (TypeError, ValueError):
            daily_k = 0
        exceeded = False
        if daily_k > 0 and self.usage_scanner:
            exceeded = ((self.usage_scanner.snapshot()
                         .get("today", {}).get("total") or 0)
                        > daily_k * 1000)
        return {"daily_k": daily_k, "exceeded": exceeded}

    def push_profile(self):
        if not self.link or not self.link.status.startswith("connected:"):
            return
        with self.profile_lock:
            profile = self.profile
            # 能力握手（PROTOCOL §5）：固件能力未知或schema不符时拒绝推送，
            # 避免"静默推送+无ACK+永久传输中"的死局。
            caps = self.fw_caps
            if caps is None:
                self.profile_status = "等待设备握手"
                return
            if caps is not None:
                if caps.get("schema") != 1:
                    self.profile_status = "同步失败"
                    self.profile_error = "固件 schema 不兼容，请升级固件"
                    self.health.event("sync_refused", reason="schema")
                    return
                if len(profile["pages"]) > caps.get("pages", 6):
                    self.profile_status = "同步失败"
                    self.profile_error = f"页面数超过固件上限（{caps.get('pages', 6)}）"
                    self.health.event("sync_refused", reason="pages")
                    return
                if len(profile.get("apps", [])) > caps.get("apps", 12):
                    self.profile_status = "同步失败"
                    self.profile_error = f"快捷应用超过固件上限（{caps.get('apps', 12)}）"
                    self.health.event("sync_refused", reason="apps")
                    return
            self.sync_sent_at = time.monotonic()
            self.profile_status = "传输中"
            self.sync_requested = False
            self.health.event("sync_sent", version=profile["version"])
        # 限速发送：115200 下设备串口缓冲有限，突发会丢字节导致 NAK
        for line in encode_profile(profile):
            self.link.send(line)
            time.sleep(0.012)

    def send_bg_image(self, data: bytes, name: str = "bg.jpg") -> bool:
        """背景图/相册图分帧下发（IMG=BEGIN/DATA/END，同 CFG 事务风格）。

        name 白名单限定 bg.jpg（页面背景）与 sv1..8.jpg（屏保相册轮播），
        防止经串口通道写任意路径。
        """
        if not re.fullmatch(r"(bg|sv[1-8]|pet_[a-z0-9_]{2,14})\.jpg", name):
            return False
        if not self.link or not self.link.status.startswith("connected:"):
            return False
        crc = _zlib.crc32(data) & 0xFFFFFFFF
        self.link.send(f"IMG=BEGIN,{len(data)},{crc:08X}\n".encode())
        for seq, start in enumerate(range(0, len(data), 90)):
            chunk = _b64.b64encode(data[start:start + 90]).decode("ascii")
            self.link.send(f"IMG=DATA,{seq},{chunk}\n".encode())
            time.sleep(0.012)
        self._img_reply = None   # 先清确认标志（防快速 ACK 被清掉的竞态）
        self.link.send(f"IMG=END,{name}\n".encode())
        self.health.event("bgimg_sent", size=len(data))
        # 等设备解码确认（ACK=IMG,APPLIED / NAK=IMG,原因）；超时如实上报
        waited = 0.0
        while waited < 5.0 and self._img_reply is None and not self._stop:
            time.sleep(0.1); waited += 0.1
        reply = self._img_reply
        if reply is None:
            self._last_bg_result = {"ok": False,
                                    "reason": "设备未确认（传输或解码超时）"}
        elif reply.get("ok"):
            self._last_bg_result = {"ok": True, "reason": "APPLIED"}
        else:
            self._last_bg_result = {"ok": False, "reason": reply.get("reason", "")}
        return self._last_bg_result["ok"]

    def clear_bg_image(self) -> bool:
        if not self.link or not self.link.status.startswith("connected:"):
            return False
        self.link.send("IMG=DELETE\n".encode())
        self.health.event("bgimg_cleared")
        return True

    # ---- 场景包（outputs/petpack_<名>/）：整套应用 + 昼夜自动切换 ----

    PACK_STATES = ("working", "thinking", "sleeping", "greeting",
                   "happy", "error", "idle", "sleepy")

    def petpack_base(self) -> Path:
        return Path(__file__).resolve().parents[2] / "outputs"

    def pack_period(self, now: float | None = None) -> str:
        """昼夜分界与固件氛围层同界（夜 22-6），体验一致。"""
        t = time.localtime(now if now is not None else time.time())
        return "night" if (t.tm_hour >= 22 or t.tm_hour < 6) else "day"

    def apply_petpack(self, name: str) -> dict:
        """整套下发 pet_<状态>.jpg。单帧 ACK 偶发超时（设备端 workanim
        解码与串口竞争），每帧最多重试 3 次并回报原因。不写状态文件——
        由调用方（手动路由/自动切换）决定语义。"""
        d = self.petpack_base() / f"petpack_{name}"
        sent, failed, reasons = 0, [], {}
        if not d.is_dir():
            return {"sent": 0,
                    "failed": list(self.PACK_STATES),
                    "reasons": {"pack": "包不存在"}}
        # 应用包 = 切换当前形象：清自定义形象（pet_custom 优先级恒高于状态帧）
        self.delete_album_image("pet_custom.jpg")
        for st in self.PACK_STATES:
            f = d / f"pet_{st}.jpg"
            if not f.is_file():
                failed.append(st)
                reasons[st] = "包内缺帧"
                continue
            ok = False
            for _ in range(3):
                if self.send_bg_image(f.read_bytes(), f"pet_{st}.jpg"):
                    ok = True
                    break
                reasons[st] = str(getattr(self, "_last_bg_result", {})
                                  .get("reason", "未知"))
                time.sleep(0.4)
            if ok:
                sent += 1
            else:
                failed.append(st)
        # 动画 B 帧：包里有就一并下发——workanim 每 900ms 交替 working/
        # working_b，只更新 A 帧会让 B 帧残留旧画风（白天包配夜空 B 帧 =
        # 用户看到的「白天夜晚不断闪烁」）
        b = d / "pet_working_b.jpg"
        if b.is_file():
            okb = False
            for _ in range(3):
                if self.send_bg_image(b.read_bytes(), "pet_working_b.jpg"):
                    okb = True
                    break
                reasons["working_b"] = str(getattr(self, "_last_bg_result", {})
                                           .get("reason", "未知"))
                time.sleep(0.4)
            if okb:
                sent += 1
            else:
                failed.append("working_b")   # B 帧失败必须计入，否则 ok=true 永不重发
        return {"sent": sent, "failed": failed, "reasons": reasons,
                "ok": not failed}

    def mark_pack_applied(self, name: str, period: str,
                          ok: bool = True) -> None:
        try:
            PACK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            PACK_STATE_PATH.write_text(json.dumps(
                {"pack": name, "period": period, "ok": bool(ok),
                 "at": time.time()}),
                encoding="utf-8")
        except OSError:
            pass

    def _pack_state(self) -> dict:
        try:
            return json.loads(PACK_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _pack_tick(self, now: float) -> None:
        """昼夜自动切包：每 60s 检查一次；同包 10 分钟内不重发（部分帧
        失败时靠这个退避自动补齐，不会每分钟轰一遍串口）。"""
        # 未开启即返回；getattr 兜底测试用 __new__ 构造的轻量实例
        pet = (getattr(self, "profile", None) or {}).get("pet") or {}
        if not pet.get("autopack"):
            return
        if now < getattr(self, "_pack_next", 0.0):
            return
        self._pack_next = now + 60
        period = self.pack_period(now)
        desired = str(pet.get("night_pack" if period == "night"
                              else "day_pack") or "")
        # 跟随天气（竞品「天气氛围」）：降水时优先用雨雪包
        if pet.get("weather_pack") and self._raining_now():
            desired = str(pet.get("weather_pack"))
        if not desired:
            return
        st = self._pack_state()
        if st.get("pack") == desired and st.get("ok"):
            return                     # 已是目标包且完整应用过：绝不再重发
        # 10 分钟退避只针对「同包补发」（部分帧失败的重试）；
        # 换到不同的目标包（昼夜翻转/雨雪起止）立即切，不等退避。
        if st.get("pack") == desired and now - st.get("at", 0) < 600:
            return
        if not (self.link and self.link.status.startswith("connected:")):
            print(f"[PACK] skip: link not connected", flush=True)
            return
        print(f"[PACK] apply {desired} (st={st.get('pack')})", flush=True)
        result = self.apply_petpack(desired)
        self.health.event("petpack_auto", pack=desired, sent=result["sent"])
        # 失败也记 ok=False：at 提供 10 分钟重试退避，成功后永不再发
        self.mark_pack_applied(desired, period, ok=result.get("ok", False))

    def delete_album_image(self, name: str) -> bool:
        """删除相册/角色图（name = sv1..sv8.jpg 或 pet_<状态>.jpg，白名单同上传）。"""
        import re as _re
        if not _re.fullmatch(r"(sv[1-8]|pet_[a-z0-9_]{2,14})\.jpg", name):
            return False
        if not self.link or not self.link.status.startswith("connected:"):
            return False
        self.link.send(f"IMG=DELETE,{name}\n".encode())
        self.health.event("album_deleted", name=name[:8])
        return True

    def send_ota(self, data: bytes) -> bool:
        """OTA 固件分帧下发（OTA=BEGIN/DATA/END）。成功后设备自动重启。

        每行 512B，与固件 proto.cpp 的解码缓冲配对。发送必须对串口队列施加
        背压：普通遥测队列满时允许丢旧帧，OTA 丢任意一帧都会触发 SEQ NAK。
        BEGIN 与 END 都等待设备 ACK，HTTP 层才能如实报告升级是否被设备接受。
        """
        if not self.link or not self.link.status.startswith("connected:"):
            return False
        self._ota_active = True
        try:
            return self._send_ota_transaction(data)
        finally:
            self._ota_active = False

    def _send_ota_transaction(self, data: bytes) -> bool:
        import base64 as _b64
        direct = getattr(self.link, "send_direct", None)
        if callable(getattr(self.link, "clear_pending", None)):
            self.link.clear_pending()

        def send_frame(frame: bytes) -> bool:
            if callable(direct):
                return bool(direct(frame))
            return bool(self.link.send(frame, block=True))

        if not hasattr(self, "_ota_event"):
            self._ota_event = threading.Event()
        self._ota_reply = None
        self._ota_event.clear()
        if not send_frame(f"OTA=BEGIN,{len(data)}\n".encode()):
            self.ota_result = {"ok": False, "reason": "串口发送队列不可用"}
            return False
        if not self._ota_event.wait(5.0) or not self._ota_reply or not self._ota_reply["ok"]:
            reason = (self._ota_reply or {}).get("reason", "设备未确认 BEGIN")
            self.ota_result = {"ok": False, "reason": reason}
            return False
        self._ota_reply = None
        self._ota_event.clear()
        window = 1
        for seq, start in enumerate(range(0, len(data), 512)):
            if seq % window == 0:
                self._ota_reply = None
                self._ota_event.clear()
            chunk = _b64.b64encode(data[start:start + 512]).decode("ascii")
            if not send_frame(f"OTA=DATA,{seq},{chunk}\n".encode()):
                self.ota_result = {"ok": False, "reason": "串口队列超时"}
                return False
            if self._ota_reply and not self._ota_reply.get("ok"):
                self.ota_result = {"ok": False, "reason": self._ota_reply.get("reason", "设备拒绝")}
                return False
            if (seq + 1) % window == 0:
                expected = f"DATA:{seq + 1}"
                if (not self._ota_event.wait(3.0) or not self._ota_reply
                        or not self._ota_reply.get("ok")
                        or self._ota_reply.get("reason") != expected):
                    reason = (self._ota_reply or {}).get(
                        "reason", f"设备未确认数据片 {seq + 1}")
                    self.ota_result = {"ok": False, "reason": reason}
                    return False
                self._ota_reply = None
                self._ota_event.clear()
        if not send_frame(b"OTA=END\n"):
            self.ota_result = {"ok": False, "reason": "END 未发送"}
            return False
        if not self._ota_event.wait(30.0) or not self._ota_reply or not self._ota_reply["ok"]:
            reason = (self._ota_reply or {}).get("reason", "设备未确认 END")
            self.ota_result = {"ok": False, "reason": reason}
            return False
        self.ota_result = {"ok": True, "reason": self._ota_reply.get("reason", "REBOOTING")}
        self.health.event("ota_sent", size=len(data))
        return True

    def check_sync_timeout(self):
        """传输中超过 10s 仍未收到 ACK/NAK → 判定失败（旧固件不支持 CFG 事务等）。

        没有这条兜底，profile_status 会永久停在「传输中」（PROTOCOL §5 违约）。
        """
        with self.profile_lock:
            if (self.profile_status == "传输中"
                    and self.sync_sent_at
                    and time.monotonic() - self.sync_sent_at > 10.0):
                self.profile_status = "同步失败"
                self.profile_error = "设备无响应（超时）"

    def _next_remind(self, now: float) -> str:
        """取最近的提醒（配置文件 reminders 字段），返回 '09:30 站会' 形式。"""
        best = None
        lt = time.localtime(now)
        now_secs = lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec
        for r in self.cfg.reminders:
            try:
                hh, mm = map(int, str(r.get("time", "")).split(":"))
                delta = (hh * 3600 + mm * 60) - now_secs
                if delta < 0:
                    delta += 86400
                if best is None or delta < best[0]:
                    best = (delta, r.get("time", ""), str(r.get("text", ""))[:32])
            except Exception:
                continue
        if best and best[0] < 86400 * 2:
            return f"{best[1]} {best[2]}".strip()
        return ""

    def tick(self, now: float) -> dict:
        self._pack_tick(now)   # 自带 60s 节流，每秒进一次无负担
        t = self.collector.sample(now)
        # 每帧显式清空空槽，避免任务结束后固件继续显示旧任务。
        for i in range(4):
            t[f"ai{i}"] = ""
        if self.ai:
            tasks = self.ai.poll_all(now)
            self.tasks = tasks
            t.update(self.ai.summary(tasks))
            t.update(ai_wire_lines(tasks, self._done_recent()))
            with self.weather._lock:
                weather_data = dict(self.weather.data)
            t.update(weather_wire_lines(
                (self.profile.get("weather") or {}).get("cities", []), weather_data))
            _pet = self.pet.snapshot()
            t["ps"] = f"{_pet['hunger']},{_pet['thirst']},{_pet['energy']}"
        state = t.get("aistate", "")
        if state and state != self._last_ai_state:
            self.health.event("ai_state", state=state)
            self._last_ai_state = state
            # 优化#10（托盘侧）：任务跨入需要用户知道的状态时发系统通知。
            # 通知不是关键路径，回调异常绝不影响采集循环。
            if state in ("DONE", "ERROR", "WAIT_INPUT", "ASK") and self.on_ai_notice:
                try:
                    focus = self.tasks[0] if self.tasks else None
                    name = (f"{focus['tool']} · {focus['project']}"
                            if focus else "AI 会话")
                    self.on_ai_notice(name, state)
                except Exception:
                    pass
                # 屏幕只在桌前有用；人走开了，「等你/出错」必须能追到手机
                self._push_notify(name, state)
        if self.usage_scanner:
            snap = self.usage_scanner.snapshot()
            day = snap["today"]
            if day.get("total"):
                t["ait"] = str(day["total"] // 1000)   # 今日总 token（K）
            # 分模型今日消耗（AIB0..2 = 模型名,token；竞品「多智能体分组指标」
            # 的本地口径——软件本身用几个模型就显示几个，按消耗降序最多 3）
            for i, (model, tok) in enumerate(
                    list((snap.get("by_model") or {}).items())[:3]):
                t[f"aib{i}"] = f"{model[:19]},{tok}"
            # 今日会话数按工具（AST 线，竞品「任务统计」）：zcode:3 · claude:1
            sby = snap.get("sessions_by_tool") or {}
            if sby:
                t["ast"] = " · ".join(f"{tool}:{n}" for tool, n in sby.items())
            # 焦点会话最近消息（AFM 线，竞品「任务补充·最近消息」）：
            # 按焦点工具取该工具的最新消息（会话 id 两源格式不同，工具级匹配
            # 语义也更对——「这个智能体最近在说什么」）
            focus_tool = (self.tasks[0] or {}).get("tool") if self.tasks else ""
            fm = (snap.get("last_msg_by_tool") or {}).get(focus_tool or "")
            if fm:
                t["afm"] = fm
            focus = self.tasks[0] if self.tasks else None
            if focus:
                model = (snap["models"].get(focus["tool"]) or "")[:15]
                if model:
                    t["aiu"] = f"{focus['tool']},{model},{focus['elapsed']}"
                # 焦点会话本轮 token（ai_cost 卡「本轮 X.Xk」，AIOUT= 下行）：
                # usage 按会话文件累计，sid 与 aiwatch 会话 id 同源
                acc = (snap.get("by_sid") or {}).get(focus.get("sid") or "")
                if acc and acc.get("out"):
                    t["aiout"] = str(acc["out"])
        t["time"] = time.strftime("%H%M%S")
        t["date"] = time.strftime("%Y-%m-%d")
        # 农历+节气倒计时（屏保日期行；计算失败不打扰主循环）
        try:
            from .lunar import today_line
            t["lunar"] = today_line(now)
        except Exception:
            pass
        # 久坐提醒：输入空闲超阈值 → pb=1（宠物犯困）+ 一次性推送
        try:
            t["pb"] = self._break_tick(now)
        except Exception:
            pass
        t["remind"] = self._next_remind(now)
        # CPU 高温告警状态机（防抖 60s + 滞回恢复）
        try:
            self._update_hw_alert(now)
        except Exception:
            pass
        # 收件箱待处理数：设备页脚角标（INBOX=）与 Web 红点共用
        try:
            t["inbox"] = self.inbox_count()
        except Exception:
            t["inbox"] = 0
        t.update(self.control.snapshot())  # VOL/MUT（不可用平台自动省略）
        with self.weather._lock:
            w = dict(self.weather.data)
        if w.get("temp") is not None:
            t["wx"] = f"{w['temp']},{w['code']},{w['hi']},{w['lo']}"
        for k in ("temp", "hum"):
            if (self.env.get(k) is not None
                    and time.monotonic() - self.env_seen.get(k, 0) < 6.0
                    and self.link and self.link.status.startswith("connected:")):
                t[k] = self.env[k]
        self.last = t
        self._last_tick_at = time.time()
        # P1-4 趋势采样：内部 60s 节流，暂停时不喂（避免冻结数据入曲线）。
        # __new__ 直构（单测）可能没有这两个属性，防御性读取。
        if not getattr(self, "paused", False):
            trend = getattr(self, "trend", None)
            if trend:
                try:
                    trend.sample(t, tasks=len(self.tasks), now=now)
                except Exception:
                    pass
        return t

    def render(self, t: dict) -> str:
        now = time.strftime("%H:%M:%S")
        cpu = f"CPU {t.get('cpu','?'):>3}%"
        # macOS 采集器会用空串表示“清除设备上的旧温度”。控制台单次
        # 采集也会经过这里，因此不能仅凭字段存在就按浮点数格式化。
        try:
            cputemp = float(t.get("cputemp"))
        except (TypeError, ValueError):
            cputemp = None
        if cputemp is not None and math.isfinite(cputemp):
            cpu += f" {cputemp:.0f}°"
        mem = f"MEM {t.get('mem','?'):>3}% {t.get('memused','?')}/{t.get('memtotal','?')}G"
        extra = []
        if "gpu" in t:
            extra.append(f"GPU {t['gpu']}%")
        if "bat" in t:
            extra.append(f"BAT {t['bat']}%")
        if "netrx" in t:
            extra.append(f"网 {t['netrx']:.1f}/{t['nettx']:.1f}Mb")
        ai = ""
        if t.get("ai0"):
            n = sum(1 for i in range(4) if t.get(f"ai{i}"))
            first = t["ai0"].split(",")
            ai = f"AI×{n} {first[0]}:{first[1]}"
        elif "aistate" in t and t["aistate"] != "IDLE":
            ai = f"AI[{t.get('aitool','')}] {t['aistate']}"
        esp = self.link.status if self.link else "串口关闭"
        if "temp" in t:
            esp += f" | 环境 {t['temp']}℃ {t.get('hum','?')}%"
        parts = [now, cpu, mem, f"DISK {t.get('disk','?')}%"] + extra + \
                ([ai] if ai else []) + [f"设备: {esp}"]
        return " | ".join(parts)

    def run(self, duration: float = 0.0):
        if self.link:
            self.link.start()
        self.weather.start()
        web_server = None
        if self.cfg.web:
            try:
                from .web import start_settings_server
                web_server = start_settings_server(self, self.cfg.web_port)
                self._web_server = web_server
                print(f"设置页：http://127.0.0.1:{self.cfg.web_port}")
                if self.cfg.open_settings:
                    import webbrowser
                    webbrowser.open(f"http://127.0.0.1:{self.cfg.web_port}")
            except OSError as exc:
                print(f"设置页启动失败：{exc}")
        start = time.time()
        hb_last = 0.0
        last_print = 0.0
        try:
            while not self._stop:
                now = time.time()
                # 暂停采集：复播上一帧遥测并保持心跳，设备不误报离线；
                # Web 端 telemetry_age 持续增长会如实提示数据陈旧。
                t = self.last if self.paused else self.tick(now)
                text = encode_telemetry(t)
                if self.link:
                    online = self.link.status.startswith("connected:")
                    snapping = time.monotonic() < self._snap_until
                    if online != self._was_online:
                        if online:
                            self.health.event("device_online")
                        else:
                            secs = int(now - self._online_since) if self._online_since else 0
                            self.health.event("device_offline", seconds=secs)
                        self._was_online = online
                        self._online_since = now if online else 0.0
                    ota_active = getattr(self, "_ota_active", False)
                    # OTA 与实拍都是独占串口事务。传输期间插入遥测、HB 或
                    # 配置帧会让设备的 DATA 序号错位，必须保持下行静默。
                    if not ota_active:
                        if self.link.consume_connected():
                            with self.profile_lock:
                                self.fw_caps = None
                                self.sync_requested = True
                                self.profile_status = "等待设备握手"
                                self.applied_version = None
                            self.link.send(encode_caps(self.collector.caps))
                            self._caps_sent_at = time.monotonic()
                        elif self.fw_caps is None and online:
                            if time.monotonic() - self._caps_sent_at >= 3:
                                self.link.send(encode_caps(self.collector.caps))
                                self._caps_sent_at = time.monotonic()
                        elif self.sync_requested and not snapping:
                            self.push_profile()
                    # 实拍静默窗：设备正阻塞在回传帧上，任何下行都会打断传输
                    if not snapping and not ota_active:
                        self.link.send(text)
                        if now - hb_last >= 5.0:
                            self.link.send(encode_hb())
                            hb_last = now
                if self.on_tick:
                    try:
                        self.on_tick(t)
                    except Exception:
                        pass
                self.check_sync_timeout()
                if self.cfg.console and now - last_print >= max(self.cfg.console_interval, 0.1):
                    print(f"\r{' ' * 110}\r{self.render(t)}", end="", flush=True)
                    last_print = now
                if duration and now - start >= duration:
                    break
                # 等一个采集周期，或被 Hook 快通道提前唤醒（附录A）
                self._kick.wait(timeout=self.cfg.interval)
                self._kick.clear()
        except KeyboardInterrupt:
            pass
        finally:
            if web_server:
                try:
                    web_server.shutdown()
                except Exception:
                    pass
            if self.link:
                self.link.stop()
            if self.cfg.console:
                print()
