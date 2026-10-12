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
                name = (f"{target}.jpg" if re.fullmatch(r"(bg|sv[1-8]|pet_[a-z0-9_]{2,14})", target)
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
                if not re.fullmatch(r"(sv[1-8]|pet_[a-z0-9_]{2,14})", target):
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
            if path == "/api/petpacks":
                # 场景包：列出 outputs/petpack_*/ 目录（8 状态图集，GET）
                base = Path(__file__).resolve().parents[2] / "outputs"
                packs = []
                if base.is_dir():
                    for d in sorted(base.glob("petpack_*")):
                        if d.is_dir():
                            frames = [f.name for f in sorted(d.glob("pet_*.jpg"))
                                      if not f.name.endswith("_b.jpg")]
                            packs.append({"name": d.name.replace("petpack_", ""),
                                          "dir": str(d), "frames": len(frames)})
                self._json(200, {"packs": packs})
                return
            if path == "/api/petpack-thumb":
                # 场景包缩略图（取包内 pet_idle.jpg）；name 白名单防路径穿越
                try:
                    name = urlsplit(self.path).query.split("name=")[1].split("&")[0]
                except IndexError:
                    name = ""
                if not re.fullmatch(r"[a-z0-9_]{1,24}", name):
                    self._json(400, {"error": "包名非法"})
                    return
                f = (Path(__file__).resolve().parents[2]
                     / "outputs" / f"petpack_{name}" / "pet_idle.jpg")
                if not f.is_file():
                    self.send_error(404)
                    return
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
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
            if path == "/api/inbox":
                # 需要处理清单（收件箱）：Web 收件箱与设备角标的事实源
                self._json(200, {"items": app.inbox_items(),
                                 "count": app.inbox_count()})
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
            if path == "/api/ota":
                # 固件升级是二进制 POST：必须放行在 JSON 门槛之前。
                # 旧版这条路由只在 do_GET 里，前端 POST 永远 415——升级按钮
                # 从上线起就没通过。
                # 上限 = default_16MB.csv 的 app0 槽 0x640000（6.25MB）；
                # 旧 3MB 是拍脑袋值，当前固件 4.3MB 根本发不出去。
                # 0xE9 是 ESP32 镜像 magic，明显不是固件的文件直接拒掉。
                length = int(self.headers.get("Content-Length", "0"))
                if not (0 < length <= 0x640000):
                    self._json(413, {"error": "固件需在 6.25MB 内（设备 OTA 槽大小）"})
                    return
                if not app.link or not app.link.status.startswith("connected:"):
                    self._json(503, {"error": "设备未连接"})
                    return
                data = self.rfile.read(length)
                if not data or data[0] != 0xE9:
                    self._json(400, {"error": "不是 ESP32 固件文件（magic 校验失败）"})
                    return
                if not app.send_ota(data):
                    result = getattr(app, "ota_result", {}) or {}
                    self._json(502, {"error": "设备未接受固件：" +
                                     str(result.get("reason") or "未知原因")})
                    return
                self._json(200, {"status": "设备已确认固件，正在重启并切分区"})
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
                if not re.fullmatch(r"(sv[1-8]|pet_[a-z0-9_]{2,14})", target):
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
                name = (f"{target}.jpg" if re.fullmatch(r"(bg|sv[1-8]|pet_[a-z0-9_]{2,14})", target)
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
                    action = str(data.get("action", ""))
                    result = app.pet.interact(action)
                    app.pet_scene_event(action)   # 运动 → 设备 PLAYING 场景
                    self._json(200, result)
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
                    old_pet = (app.profile.get("pet") or {})
                    candidate["pet"] = {
                        "type": str(data.get("type", "robot")),
                        "name": str(data.get("name", "帽子")),
                        "sleep": 1 if data.get("sleep", old_pet.get("sleep", 1)) else 0,
                        "greet": 1 if data.get("greet", old_pet.get("greet", 0)) else 0,
                        "autopack": 1 if data.get("autopack", old_pet.get("autopack", 0)) else 0,
                        "day_pack": str(data.get("day_pack", old_pet.get("day_pack", "")) or ""),
                        "night_pack": str(data.get("night_pack", old_pet.get("night_pack", "")) or "")}
                    result = app.save_profile(candidate)
                    self._json(200, {"status": "已保存，待同步",
                                     "version": result["version"]})
                    return
                if path == "/api/smc-interval":
                    # 温度/风扇采样间隔（秒）：存 profile；已安装的采样器需要
                    # 重写 launchd plist 才生效（launchd 只在 bootstrap 时读
                    # StartInterval）——重装会弹一次管理员密码框，阻塞本线程属预期。
                    from . import smcpriv
                    try:
                        seconds = max(10, min(3600, int(data.get("seconds", 20))))
                    except (TypeError, ValueError):
                        seconds = 20
                    candidate = dict(app.profile)
                    candidate["smc"] = {"interval": seconds}
                    result = app.save_profile(candidate)
                    reinstalled = None
                    if smcpriv.PLIST_PATH.exists():
                        reinstalled = smcpriv.install(seconds)
                    self._json(200, {"interval": seconds,
                                     "version": result["version"],
                                     "reinstalled": reinstalled})
                    return
                if path == "/api/validate":
                    self._json(200, {"profile": validate_profile(data)})
                elif path == "/api/syscal":
                    # 系统日历只读接入：{enable:bool, refresh:bool}。
                    # 打开时启动后台拉取（首次会弹 TCC 日历授权）；refresh 立即拉一次。
                    enable = bool(data.get("enable"))
                    app.cfg.syscal = enable
                    try:
                        app.cfg.save()
                    except OSError:
                        pass
                    from . import syscal as syscal_mod
                    fetcher = app.syscal_fetcher
                    if enable and fetcher is None:
                        app.syscal_fetcher = syscal_mod.SysCalFetcher()
                        app.syscal_fetcher.start()
                    if not enable and app.syscal_fetcher is not None:
                        app.syscal_fetcher.stop()
                        app.syscal_fetcher = None
                    refreshed = None
                    if data.get("refresh") or enable:
                        refreshed = syscal_mod.refresh()
                    self._json(200, {"enabled": enable,
                                     "state": syscal_mod.read_state(),
                                     "refreshed": refreshed})
                elif path == "/api/petpack-apply":
                    # 应用场景包：整套下发（重试/原因在 app.apply_petpack）。
                    # 手动应用记为当前时段的选择——昼夜自动切换开启时，
                    # 同步进 profile 对应时段的包位，10 分钟后不会被自动切走。
                    name = str(data.get("name", ""))
                    if not re.fullmatch(r"[a-z0-9_]{1,24}", name):
                        self._json(400, {"error": "包名非法"})
                        return
                    if not (app.petpack_base() / f"petpack_{name}").is_dir():
                        self._json(404, {"error": "包不存在"})
                        return
                    result = app.apply_petpack(name)
                    period = app.pack_period()
                    app.mark_pack_applied(name, period)
                    pet = dict(app.profile.get("pet") or {})
                    if pet.get("autopack"):
                        candidate = dict(app.profile)
                        candidate["pet"] = {**pet,
                                            "night_pack" if period == "night"
                                            else "day_pack": name}
                        app.save_profile(candidate)
                    self._json(200, {"sent": result["sent"],
                                     "failed": result["failed"],
                                     "reasons": result["reasons"],
                                     "status": f"已下发 {result['sent']} 张（{name} 包）"})
                    return
                if path == "/api/inbox-open":
                    # 收件箱「打开项目」：mode=finder(默认) / terminal
                    result = app.open_task_project(str(data.get("id", "")),
                                                   str(data.get("mode", "finder")))
                    if result.get("ok"):
                        self._json(200, result)
                    else:
                        self._json(400, result)
                elif path == "/api/inbox-dismiss":
                    ids = data.get("ids") or []
                    if not isinstance(ids, list) or not ids:
                        self._json(400, {"error": "缺少 ids"})
                        return
                    try:
                        snooze = float(data.get("snooze_minutes", 0) or 0)
                    except (TypeError, ValueError):
                        snooze = 0
                    app.inbox_dismiss([str(i) for i in ids], snooze)
                    self._json(200, {"count": app.inbox_count()})
                elif path == "/api/calendar-ics":
                    # ICS 日程导入：解析 → 与现有 calendar 按 id 合并 → 走标准
                    # save（校验 + 版本自增）。同 id = 同一来源事件，重复导入幂等。
                    from . import ics as ics_mod
                    text = str(data.get("text", "") or "")
                    if not text.strip():
                        self._json(400, {"error": "ICS 内容为空"})
                        return
                    imported = ics_mod.parse_ics(text)
                    if not imported:
                        self._json(200, {"added": 0, "skipped": 0,
                                         "status": "没有解析到窗口内（未来 14 天）的日程"})
                        return
                    candidate = dict(app.profile)
                    existing = {c["id"]: c for c in candidate.get("calendar", [])}
                    added = skipped = 0
                    for e in imported:
                        if e["id"] in existing:
                            skipped += 1
                            continue
                        if len(existing) >= 50:
                            skipped += 1
                            continue
                        existing[e["id"]] = e
                        added += 1
                    candidate["calendar"] = sorted(
                        existing.values(), key=lambda c: (c["date"], c["time"]))
                    if not added:
                        # 零新增不落盘不 bump 版本：重复导入不该白让设备重同步
                        self._json(200, {"added": 0, "skipped": skipped,
                                         "status": f"没有新日程（跳过重复 {skipped} 条）"})
                        return
                    result = app.save_profile(candidate)
                    self._json(200, {"added": added, "skipped": skipped,
                                     "version": result["version"],
                                     "status": f"已导入 {added} 条（跳过重复 {skipped} 条），保存并待同步"})
                elif path == "/api/push-config":
                    # 手机推送配置（设置→系统）：URL + 安静时段，落 config.json。
                    # 安静时段格式校验在这里做一次，坏值直接 400 不落盘。
                    quiet = str(data.get("quiet", "") or "").strip()
                    if quiet and not re.fullmatch(
                            r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}", quiet):
                        self._json(400, {"error": "安静时段格式应为 HH:MM-HH:MM"})
                        return
                    app.cfg.push_url = str(data.get("url", "") or "").strip()
                    app.cfg.push_quiet = quiet
                    try:
                        app.cfg.save()
                    except OSError:
                        pass
                    self._json(200, {"status": "已保存",
                                     "enabled": bool(app.cfg.push_url)})
                elif path == "/api/page":
                    # 切设备页（1 基）：多窗格预览点击直达小屏对应页。
                    # 页号超范围由 set_device_page 拒绝（400 语义此处简化为 ok:false）。
                    ok = app.set_device_page(int(data.get("page", 1)))
                    self._json(200, {"ok": bool(ok)})
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
                elif path == "/api/restore-applied":
                    # 回到设备当前已应用的版本（新版本应用失败时的退路）
                    try:
                        self._json(200, {"profile": app.restore_applied_profile(),
                                         "status": "已回退到设备版本"})
                    except ValueError as exc:
                        self._json(400, {"error": str(exc)})
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
