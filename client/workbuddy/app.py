"""主循环：采集 → AI 检测 → 合并遥测 → 控制台/串口输出。"""
import time
import threading

from .collect import create
from .control import ControlApp
from .protocol import encode_caps, encode_hb, encode_telemetry
from .profile import encode_profile, load_profile, save_profile, validate_profile


class App:
    def __init__(self, cfg):
        import psutil

        psutil.cpu_percent(interval=None)  # 预热首次读数
        self.cfg = cfg
        self.collector = create(cfg)
        self.env = {}  # ESP32 上行（temp/hum 等）
        self.env_seen = {}
        self.control = ControlApp()
        self.profile_lock = threading.Lock()
        self.profile = load_profile()
        self.profile_status = "待同步"
        self.profile_error = ""
        self.applied_version = None
        self.sync_requested = False
        self.sync_sent_at = 0.0
        self.tasks = []
        self.ai = None
        if cfg.ai_watch:
            from .aiwatch import AIWatcher
            self.ai = AIWatcher(cfg)
        if cfg.serial:
            from .serial_link import SerialLink
            self.link = SerialLink(cfg, on_uplink=self._on_uplink)
        else:
            self.link = None
        self.last = {}
        self.on_tick = None   # GUI 回调：每个采集周期调用一次
        self._stop = False

    def _on_uplink(self, d: dict):
        if "launch" in d:
            self._launch_app(d["launch"])
        elif "key" in d:
            self._handle_key(d["key"])
        if "config_reply" in d:
            reply = d["config_reply"]
            with self.profile_lock:
                if str(self.profile["version"]) == reply["version"]:
                    self.profile_status = "设备已应用" if reply["ok"] else "同步失败"
                    self.profile_error = "" if reply["ok"] else reply["reason"]
                    if reply["ok"]:
                        self.applied_version = self.profile["version"]
        self.env.update(d)
        seen = time.monotonic()
        for key in ("temp", "hum"):
            if key in d:
                self.env_seen[key] = seen

    def push_theme(self, theme: dict | None = None):
        """把配置里的主题色 + 亮度推送到屏幕。"""
        th = theme or self.profile["theme"]
        parts = [f"{k}:{v}" for k, v in th.items() if k != "bright"]
        if th.get("bright") is not None:
            parts.append(f"bright:{th['bright']}")
        if self.link:
            self.link.send("CFG=" + ",".join(parts) + "\n")

    def stop(self):
        self._stop = True

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
        with self.profile_lock:
            return {"profile": self.profile, "profile_status": self.profile_status,
                    "profile_error": self.profile_error,
                    "applied_version": self.applied_version,
                    "device": self.link.status if self.link else "串口关闭",
                    "tasks": self.tasks[:4], "telemetry": self.last}

    def push_profile(self):
        if not self.link or not self.link.status.startswith("connected:"):
            return
        with self.profile_lock:
            profile = self.profile
            self.sync_requested = False
            self.profile_status = "传输中"
            self.sync_sent_at = time.monotonic()
        for line in encode_profile(profile):
            self.link.send(line)

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
        t["time"] = time.strftime("%H%M%S")
        t["date"] = time.strftime("%Y-%m-%d")
        t["remind"] = self._next_remind(now)
        t.update(self.control.snapshot())  # VOL/MUT（不可用平台自动省略）
        for k in ("temp", "hum"):
            if (self.env.get(k) is not None
                    and time.monotonic() - self.env_seen.get(k, 0) < 6.0
                    and self.link and self.link.status.startswith("connected:")):
                t[k] = self.env[k]
        self.last = t
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
        web_server = None
        if self.cfg.web:
            try:
                from .web import start_settings_server
                web_server = start_settings_server(self, self.cfg.web_port)
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
                t = self.tick(now)
                text = encode_telemetry(t)
                if self.link:
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
                time.sleep(self.cfg.interval)
        except KeyboardInterrupt:
            pass
        finally:
            if web_server:
                web_server.shutdown()
            if self.link:
                self.link.stop()
            if self.cfg.console:
                print()
