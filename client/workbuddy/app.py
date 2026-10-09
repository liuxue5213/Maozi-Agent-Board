"""主循环：采集 → AI 检测 → 合并遥测 → 控制台/串口输出。"""
import time
import threading

from .collect import create
from .control import ControlApp
from .protocol import encode_caps, encode_hb, encode_telemetry
import base64 as _b64
import zlib as _zlib

from .profile import encode_profile, load_profile, save_profile, validate_profile
from .telemetry import HealthLog
from .trend import TrendStore
from .weather import WeatherFetcher


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
        self.weather = WeatherFetcher(lambda: (self.profile.get('weather') or {}).get('city', ''))
        self.fw_caps = None          # 设备能力（来自 FW 握手），None = 未知
        self.profile_lock = threading.Lock()
        self.profile = load_profile()
        self.profile_status = "待同步"
        self.profile_error = ""
        self.applied_version = None
        self.sync_requested = False
        self.sync_sent_at = 0.0
        self.tasks = []
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
        self.on_quit = None   # 托盘「退出」回调：用于停掉托盘/窗口线程
        self.window_visible = True  # 主窗口是否可见（隐藏到托盘时为 False）

    def _on_uplink(self, d: dict):
        if "firmware" in d:
            self._parse_fw_caps(d["firmware"])
        if "slow_loop" in d:
            self.health.event("slow_loop", us=d["slow_loop"])
        if "launch" in d:
            self._launch_app(d["launch"])
            self.health.event("launch", app=d["launch"][:16])
        elif "key" in d:
            self._handle_key(d["key"])
            self.health.event("key", action=d["key"])
        if "touch" in d:
            self.health.event("touch", target=d["touch"][:16])
        if "img_reply" in d:
            self.health.event("img_ack" if d["img_reply"]["ok"] else "img_nak",
                              reason=d["img_reply"]["reason"])
        if "config_reply" in d:
            reply = d["config_reply"]
            with self.profile_lock:
                if str(self.profile["version"]) == reply["version"]:
                    self.profile_status = "设备已应用" if reply["ok"] else "同步失败"
                    self.profile_error = "" if reply["ok"] else reply["reason"]
                    if reply["ok"]:
                        self.applied_version = self.profile["version"]
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
        self.fw_caps = caps
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
        """status_snapshot 用：给 tasks 附上阶段事件（优化#13）。"""
        tasks = [dict(t) for t in self.tasks[:4]]
        ai = getattr(self, "ai", None)
        if ai:
            for t in tasks:
                try:
                    t["trace"] = ai.trace_for(t["id"])
                except Exception:
                    t["trace"] = []
        return tasks

    def _launch_app(self, app_id: str):
        with self.profile_lock:
            command = next((a["command"] for a in self.profile.get("apps", [])
                            if a["id"] == app_id), "")
        if command:
            self.control.launch(command)

    def _handle_key(self, action: str):
        if action == "VOL+":
            self.control.volume_step(+5)
        elif action == "VOL-":
            self.control.volume_step(-5)
        elif action == "MUTE":
            self.control.toggle_mute()
        elif action == "PLAY":
            self.control.media("play")
        elif action == "NEXT":
            self.control.media("next")
        elif action == "PREV":
            self.control.media("prev")

    def save_profile(self, draft):
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
                    "device": self.link.status if self.link else "串口关闭",
                    "tasks": self._tasks_with_trace(), "telemetry": self.last,
                    "telemetry_age": (round(time.time() - last_tick, 1)
                                      if last_tick else None),
                    "telemetry_at": last_tick,
                    "interval": self.cfg.interval,
                    "paused": getattr(self, "paused", False),
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
            self.health.event("sync_sent", version=profile["version"])
        # 限速发送：115200 下设备串口缓冲有限，突发会丢字节导致 NAK
        for line in encode_profile(profile):
            self.link.send(line)
            time.sleep(0.012)

    def send_bg_image(self, data: bytes) -> bool:
        """背景图分帧下发（IMG=BEGIN/DATA/END，同 CFG 事务风格）。"""
        if not self.link or not self.link.status.startswith("connected:"):
            return False
        crc = _zlib.crc32(data) & 0xFFFFFFFF
        self.link.send(f"IMG=BEGIN,{len(data)},{crc:08X}\n".encode())
        for seq, start in enumerate(range(0, len(data), 90)):
            chunk = _b64.b64encode(data[start:start + 90]).decode("ascii")
            self.link.send(f"IMG=DATA,{seq},{chunk}\n".encode())
            time.sleep(0.012)
        self.link.send("IMG=END,bg.jpg\n".encode())
        self.health.event("bgimg_sent", size=len(data))
        time.sleep(0.3)   # 等设备落盘/解码完成再允许后续流量
        return True

    def clear_bg_image(self) -> bool:
        if not self.link or not self.link.status.startswith("connected:"):
            return False
        self.link.send("IMG=DELETE\n".encode())
        self.health.event("bgimg_cleared")
        return True

    def send_ota(self, data: bytes) -> bool:
        """OTA 固件分帧下发（OTA=BEGIN/DATA/END）。成功后设备自动重启。"""
        if not self.link or not self.link.status.startswith("connected:"):
            return False
        import base64 as _b64
        self.link.send(f"OTA=BEGIN,{len(data)}\n".encode())
        time.sleep(0.1)
        for seq, start in enumerate(range(0, len(data), 90)):
            chunk = _b64.b64encode(data[start:start + 90]).decode("ascii")
            self.link.send(f"OTA=DATA,{seq},{chunk}\n".encode())
            time.sleep(0.012)
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
            for i, tk in enumerate(tasks[:4]):
                t[f"ai{i}"] = (f"{tk['tool']},{tk['state']},{tk['prog']},"
                               f"{tk['elapsed']},{tk['project']},{tk['id']},{tk['age']}")
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
                    if online != self._was_online:
                        if online:
                            self.health.event("device_online")
                        else:
                            secs = int(now - self._online_since) if self._online_since else 0
                            self.health.event("device_offline", seconds=secs)
                        self._was_online = online
                        self._online_since = now if online else 0.0
                    if self.link.consume_connected():
                        self.link.send(encode_caps(self.collector.caps))
                        self.push_theme()
                        self.push_profile()
                    elif self.sync_requested:
                        self.push_theme()
                        self.push_profile()
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
