"""Local only settings and live preview server (stdlib HTTP)."""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from .profile import validate_profile

WEB_ROOT = Path(__file__).with_name("web_assets")
FILES = {"/": ("index.html", "text/html; charset=utf-8"),
         "/app.css": ("app.css", "text/css; charset=utf-8"),
         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/color-map.json": ("color-map.json", "application/json; charset=utf-8"),
         "/mini": ("mini.html", "text/html; charset=utf-8"),
         "/mini.css": ("mini.css", "text/css; charset=utf-8"),
         "/mini.js": ("mini.js", "text/javascript; charset=utf-8")}


def hooks_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def hooks_port(app) -> int:
    cfg = getattr(app, "cfg", None)
    return getattr(cfg, "web_port", 8765) if cfg else 8765


def start_settings_server(app, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _write(self, status, body, mime="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            # img-src 必须放开 blob: —— 自定义背景图走
            # URL.createObjectURL(file) → new Image() 解码，缺了 blob: 会被
            # default-src 'self' 拦掉，表现为「背景图失败」且 onerror 收到的是
            # Event 而非 Error，前端连错误原因都拿不到。data: 供内联缩略图。
            # 其余保持 'self'：脚本/样式仍禁止内联，页面只能加载本机资源。
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self'; script-src 'self'; "
                "img-src 'self' blob: data:; connect-src 'self'; object-src 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, data):
            self._write(status, json.dumps(data, ensure_ascii=False).encode("utf-8"))

        def _stream(self):
            """SSE 实时推送（优化#3）：状态内容变化即推，亚秒级刷新前端。

            保留 /api/status 轮询作为兜底。ThreadingHTTPServer 每连接一线程，
            本机单用户场景开销可忽略；前端关闭页面表现为写失败，捕获即结束。
            快照里 telemetry_age 每次都在变，签名只取内容字段，避免空推。
            """
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            last_sig = None
            last_beat = time.time()
            try:
                while not app._stop:
                    snap = app.status_snapshot()
                    sig = json.dumps(
                        {"telemetry": snap["telemetry"], "tasks": snap["tasks"],
                         "device": snap["device"], "profile_status": snap["profile_status"],
                         "profile_error": snap["profile_error"],
                         "applied_version": snap["applied_version"],
                         "profile_version": (snap["profile"] or {}).get("version"),
                         "tools": snap.get("tools"),
                         "done": snap.get("done")},
                        sort_keys=True, default=str)
                    now = time.time()
                    if sig != last_sig:
                        last_sig = sig
                        last_beat = now
                        payload = json.dumps(snap, ensure_ascii=False, default=str)
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    elif now - last_beat >= 15.0:
                        # 注释行心跳：保活连接，前端不渲染
                        last_beat = now
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    time.sleep(0.6)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/status":
                self._json(200, app.status_snapshot())
                return
            if path.startswith("/hook/"):
                self._hook(path)
                return
            if path == "/api/stream":
                self._stream()
                return
            if path == "/api/diagnose":
                from .diagnose import build_bundle
                body = build_bundle(app)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition",
                                 f'attachment; filename="workbuddy-diagnose-{time.strftime("%m%d-%H%M")}.zip"')
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/ota":
                length = int(self.headers.get("Content-Length", "0"))
                if not (0 < length <= 3 * 1024 * 1024):
                    self._json(413, {"error": "固件需在 3MB 内"})
                    return
                if not app.link or not app.link.status.startswith("connected:"):
                    self._json(503, {"error": "设备未连接"})
                    return
                data = self.rfile.read(length)
                app.send_ota(data)
                self._json(200, {"status": "固件已发送，设备将自动重启并切分区"})
                return
            if path == "/api/bgimg":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 120 * 1024:
                    self._json(413, {"error": "图片需在 120KB 内（工作台会自动压缩）"})
                    return
                if not app.link or not app.link.status.startswith("connected:"):
                    self._json(503, {"error": "设备未连接"})
                    return
                data = self.rfile.read(length)
                # ?target=svN → 屏保相册第 N 张；缺省 bg.jpg（页面背景）
                target = parse_qs(urlsplit(self.path).query).get("target", ["bg"])[0]
                name = (f"{target}.jpg" if re.fullmatch(r"(bg|sv[1-8])", target)
                        else "bg.jpg")
                if app.send_bg_image(data, name):
                    self._json(200, {"status": f"已应用（{name}）"})
                else:
                    reason = getattr(app, "_last_bg_result", {}).get("reason", "未知原因")
                    self._json(503, {"error": f"背景图失败：{reason}"})
                return
            if path == "/api/bgimg-clear":
                app.clear_bg_image()
                self._json(200, {"status": "已请求恢复纯色"})
                return
            if path == "/api/bgimg-delete":
                # ?target=svN → 删除屏保相册第 N 张（无 body 的 POST，须在 JSON 门槛前放行）
                target = parse_qs(urlsplit(self.path).query).get("target", [""])[0]
                if not re.fullmatch(r"sv[1-8]", target):
                    self._json(400, {"error": "相册槽位无效"})
                    return
                app.delete_album_image(f"{target}.jpg")
                self._json(200, {"status": f"已删除（{target}）"})
                return
            if path == "/api/pet":
                self._json(200, app.pet.snapshot())
                return
            if path == "/api/music-apps":
                # 本机装了哪些音乐软件 + 当前默认 + 是否在跑（设置页用）
                from . import music as music_module
                default = music_module.default_app(app.cfg)
                self._json(200, {
                    "apps": music_module.installed_apps(),
                    "default": default,
                    "running": music_module.is_running(default),
                    "configured": str(getattr(app.cfg, "music_app", "auto") or "auto"),
                    "supported": sys.platform == "darwin",
                })
                return
            if path == "/api/autostart-status":
                try:
                    from . import autostart
                    self._json(200, {"enabled": autostart.status()})
                except Exception:
                    self._json(200, {"enabled": False, "unsupported": True})
                return
            if path == "/api/hooks-status":
                # Hook 快通道接入状态（AI 采集第 2 层）
                from . import hooks
                self._json(200, hooks.status(hooks_settings_path()))
                return
            if path == "/api/smc-status":
                # 温度/风扇特权采样状态（未安装≠错误，前端按中性态显示）
                from . import smcpriv
                self._json(200, smcpriv.status())
                return
            if path == "/api/snapshot":
                # 设备实拍 PNG（无图或设备不支持时 404）
                data = app.snapshot_bytes()
                if not data:
                    self._json(404, {"error": "还没有实拍图，请先点击「实拍」"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/snapshot-meta":
                self._json(200, {"at": getattr(app, "snap_at", None)})
                return
            if path == "/api/trend":
                # P1-4 行为遥测趋势：?hours=24（上限 7 天）
                trend = getattr(app, "trend", None)
                try:
                    hours = int(urlsplit(self.path).query.split("hours=")[1]
                                .split("&")[0])
                except (IndexError, ValueError):
                    hours = 24
                self._json(200, {"hours": hours,
                                 "series": trend.series(hours) if trend else []})
                return
            if path == "/api/export":
                # 导出当前生效配置为 JSON（前端触发浏览器下载）。
                # 与 /api/save 同源校验结果，保证"导出→导入"可往返。
                profile = app.status_snapshot().get("profile", {})
                body = json.dumps(profile, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition",
                                 f'attachment; filename="workbuddy-profile-'
                                 f'{time.strftime("%m%d-%H%M")}.json"')
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if path not in FILES:
                self._json(404, {"error": "未找到页面"})
                return
            filename, mime = FILES[path]
            self._write(200, (WEB_ROOT / filename).read_bytes(), mime)

        def _hook(self, path):
            """Hook Push 快通道（附录A）：/hook/start|pulse|stop[?agent=claude]。

            给编辑器 hooks 配的极简端点——GET 也收（curl 一行即用），
            非本机调试场景不存在；返回 JSON 便于 hook 脚本静默忽略失败。
            """
            kind = path.split("/", 2)[2].split("?")[0]
            if kind not in ("start", "pulse", "stop"):
                self._json(404, {"error": f"未知 hook 类型：{kind}"})
                return
            query = parse_qs(urlsplit(self.path).query)
            agent = (query.get("agent") or [""])[0]
            try:
                kicked = bool(app.hook_event(kind, agent))
            except Exception:
                kicked = False
            self._json(200, {"ok": kicked, "hook": kind})

        def do_POST(self):
            path = urlsplit(self.path).path
            if path == "/api/snap-request":
                # 实拍：立即返回，前端轮询 /api/snapshot-meta 等 snap_at 翻新
                if not app.request_snap():
                    self._json(503, {"error": "设备未连接"})
                    return
                self._json(200, {"status": "已请求，设备回传约需 10 秒"})
                return
            if path.startswith("/hook/"):
                self._hook(path)
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
            if path == "/api/bgimg-clear":
                # 无 body 的 POST：必须在 JSON 门槛之前放行，否则「恢复纯色」
                # 按钮永远 415（fetch 不带 Content-Type，实测踩过）。
                app.clear_bg_image()
                self._json(200, {"status": "已请求恢复纯色"})
                return
            if path == "/api/bgimg-delete":
                # ?target=svN → 删除屏保相册第 N 张（同上须在 JSON 门槛前放行）
                target = parse_qs(urlsplit(self.path).query).get("target", [""])[0]
                if not re.fullmatch(r"sv[1-8]", target):
                    self._json(400, {"error": "相册槽位无效"})
                    return
                app.delete_album_image(f"{target}.jpg")
                self._json(200, {"status": f"已删除（{target}）"})
                return
            if path == "/api/bgimg":
                # 背景图走二进制通道（客户端已压成 ≤120KB 的 JPEG）
                length = int(self.headers.get("Content-Length", "0"))
                if not (0 < length <= 120 * 1024):
                    self._json(413, {"error": "图片需在 120KB 内"})
                    return
                if not app.link or not app.link.status.startswith("connected:"):
                    self._json(503, {"error": "设备未连接"})
                    return
                data = self.rfile.read(length)
                target = parse_qs(urlsplit(self.path).query).get("target", ["bg"])[0]
                name = (f"{target}.jpg" if re.fullmatch(r"(bg|sv[1-8])", target)
                        else "bg.jpg")
                if app.send_bg_image(data, name):
                    self._json(200, {"status": f"已应用（{name}）"})
                else:
                    reason = getattr(app, "_last_bg_result", {}).get("reason", "未知原因")
                    self._json(503, {"error": f"背景图失败：{reason}"})
                return
            if path in ("/api/smc-install", "/api/smc-uninstall"):
                # 温度/风扇特权采样：弹系统管理员密码框，可能等待用户输密码
                # （ThreadingHTTPServer 每连接一线程，不会拖住 SSE/轮询）。
                # 无 body 语义的路由，同样必须在 JSON 门槛之前。
                from . import smcpriv
                self._json(200, smcpriv.install() if path.endswith("install")
                           else smcpriv.uninstall())
                return
            if content_type != "application/json":
                self._json(415, {"error": "需要 application/json"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 16384:
                self._json(413, {"error": "请求过大"})
                return
            try:
                data = json.loads(self.rfile.read(length))
                if path == "/api/pet-do":
                    # 触屏/网页的宠物交互：喂食/喝水/运动
                    self._json(200, app.pet.interact(str(data.get("action", ""))))
                    return
                if path == "/api/weather-search":
                    # 省市区地理编码（Open-Meteo，中文，含省市标签）。
                    # 网络错误必须兜住：处理线程抛异常会直接断连。
                    from .weather import geocode
                    try:
                        results = geocode(str(data.get("q", "")))
                    except Exception as exc:
                        self._json(200, {"results": [], "error": f"搜索失败：{exc}"})
                        return
                    self._json(200, {"results": results})
                    return
                if path == "/api/pet-config":
                    # 类型/名字改动 → 走标准保存（校验+版本递增），设备重建宠物形象
                    candidate = dict(app.profile)
                    candidate["pet"] = {"type": str(data.get("type", "robot")),
                                        "name": str(data.get("name", "帽子"))}
                    result = app.save_profile(candidate)
                    self._json(200, {"status": "已保存，待同步",
                                     "version": result["version"]})
                    return
                if path == "/api/validate":
                    self._json(200, {"profile": validate_profile(data)})
                elif path == "/api/autostart":
                    from . import autostart
                    self._json(200, {"enabled": autostart.set_enabled(bool(data.get("enable")))})
                elif path == "/api/save":
                    self._json(200, {"profile": app.save_profile(data), "status": "已保存，待同步"})
                elif path == "/api/sync":
                    app.request_sync()
                    self._json(200, {"status": "待同步"})
                elif path == "/api/import":
                    # 导入外部配置：复用 save_profile 的严格校验 + 版本自增，
                    # 非法配置会被 validate_profile 拒绝（400），与 /api/save 一致。
                    self._json(200, {"profile": app.save_profile(data),
                                     "status": "已导入，待同步"})
                elif path == "/api/launch":
                    # 一键启动（优化#15）：只接受 profile 里已登记的应用 id，
                    # 不接受任意命令——该端点等价于小屏 LAUNCH 上行，不是 shell。
                    app_id = str(data.get("id", ""))
                    command = next(
                        (a.get("command", "") for a in app.profile.get("apps", [])
                         if a.get("id") == app_id), "")
                    if not command:
                        self._json(400, {"error": "未找到该应用，请先在其他视图登记"})
                        return
                    launcher = getattr(app, "_launch_app", None)
                    result = (launcher(app_id) or {}) if callable(launcher) else {}
                    if not callable(launcher):
                        app.control.launch(command)
                    # @music 保留字：启动默认音乐软件并开始播放，回执里带上软件名
                    status = (f"已启动：{result['app']}" if result.get("app")
                              else "已启动")
                    self._json(200, {"status": status, "id": app_id})
                elif path == "/api/music-app":
                    # 设定默认音乐软件（bundle id；"auto" = 自动挑）
                    choice = str(data.get("id", "auto")) or "auto"
                    app.cfg.music_app = choice
                    try:
                        app.cfg.save()
                    except OSError:
                        pass
                    from . import music as music_module
                    picked = music_module.default_app(app.cfg)
                    self._json(200, {"status": "已保存", "default": picked})
                elif path == "/api/music-play":
                    # 试一下：启动默认软件并开始播放（设置页「试一下」按钮）
                    self._json(200, app.music_play())
                elif path == "/api/hooks-install":
                    from . import hooks
                    result = hooks.install(hooks_settings_path(),
                                           hooks_port(app))
                    health = getattr(app, "health", None)
                    if health:
                        health.event("hooks_install",
                                     n=len(result.get("installed", [])))
                    self._json(200, result)
                elif path == "/api/hooks-uninstall":
                    from . import hooks
                    result = hooks.uninstall(hooks_settings_path())
                    health = getattr(app, "health", None)
                    if health:
                        health.event("hooks_uninstall",
                                     n=len(result.get("removed", [])))
                    self._json(200, result)
                else:
                    self._json(404, {"error": "未找到接口"})
            except (ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
