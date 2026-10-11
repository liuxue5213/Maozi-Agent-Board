"""主循环：采集 → AI 检测 → 合并遥测 → 控制台/串口输出。"""
import json
import re
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
        self.weather = WeatherFetcher(lambda: (self.profile.get('weather') or {}).get('cities', []))
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
        """FW=0.3.1,n16r8,schema:1,pages:6,widgets:7,apps:12,... → 能力字典。"""
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
        """宠物互动转发设备：运动触发屏端 PLAYING 场景 2.5s（固件三层仲裁的场景层）。"""
        if action == "play" and self.link and \
                self.link.status.startswith("connected:"):
            self.link.send("PET=play\n")

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

    def _push_send(self, url: str, name: str, state: str):
        from urllib.parse import quote
        from urllib.request import Request, urlopen
        title = f"WorkBuddy · {_PUSH_STATE_NAMES.get(state, state)}"
        text = f"{name} {title.split('·')[-1].strip()}"
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
        self.weather.kick()   # 城市列表可能变了：立刻拉一轮
        with self.profile_lock:
            candidate = validate_profile(draft)
            candidate["version"] = self.profile["version"] + 1
            self.profile = save_profile(candidate)
            self.profile_status = "待同步"
            self.profile_error = ""
            self.sync_requested = False
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
                    # 设置页回填用；仅本机 Web 可见（服务绑定 127.0.0.1）
                    "push_url": str(getattr(self.cfg, "push_url", "") or ""),
                    # 本机采集能力与温度来源（Windows 上用于「装 LHM」引导）
                    "local_caps": dict(getattr(self.collector, "caps", {})
                                       if getattr(self, "collector", None) else {}),
                    "thermal_hint": getattr(getattr(self, "collector", None),
                                            "thermal_hint", ""),
                    "device": self.link.status if self.link else "串口关闭",
                    "tasks": self._tasks_with_trace(), "telemetry": self.last,
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
                              if self.usage_scanner else None)}

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
        if not re.fullmatch(r"(bg|sv[1-8])\.jpg", name):
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

    def delete_album_image(self, name: str) -> bool:
        """删除屏保相册中的一张（name = sv1.jpg..sv8.jpg，白名单同上传）。"""
        import re as _re
        if not _re.fullmatch(r"sv[1-8]\.jpg", name):
            return False
        if not self.link or not self.link.status.startswith("connected:"):
            return False
        self.link.send(f"IMG=DELETE,{name}\n".encode())
        self.health.event("album_deleted", name=name[:8])
        return True

    def send_ota(self, data: bytes) -> bool:
        """OTA 固件分帧下发（OTA=BEGIN/DATA/END）。成功后设备自动重启。

        每行 512B + 2ms 间隔：与固件 proto.cpp 的解码缓冲配对。921600 波特率
        下 4.3MB 固件约 1 分钟（旧 90B+12ms 组合要 10 分钟，等于不可用）。
        设备主循环边收边写 flash，2ms 是给 flash 写入留的余量；遥测行按序
        交织处理，互不影响。
        """
        if not self.link or not self.link.status.startswith("connected:"):
            return False
        import base64 as _b64
        self.link.send(f"OTA=BEGIN,{len(data)}\n".encode())
        time.sleep(0.1)
        for seq, start in enumerate(range(0, len(data), 512)):
            chunk = _b64.b64encode(data[start:start + 512]).decode("ascii")
            self.link.send(f"OTA=DATA,{seq},{chunk}\n".encode())
            time.sleep(0.002)
        time.sleep(0.3)
        self.link.send("OTA=END\n".encode())
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
            focus = self.tasks[0] if self.tasks else None
            if focus:
                model = (snap["models"].get(focus["tool"]) or "")[:15]
                if model:
                    t["aiu"] = f"{focus['tool']},{model},{focus['elapsed']}"
        t["time"] = time.strftime("%H%M%S")
        t["date"] = time.strftime("%Y-%m-%d")
        t["remind"] = self._next_remind(now)
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
        if "cputemp" in t:
            cpu += f" {t['cputemp']:.0f}°"
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
                    if not snapping:
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
