"""默认音乐软件：启动 / 播放 / 上一首下一首 + 系统媒体会话读写。

**macOS 为什么不能用 AppleScript**：只有 Apple Music 与 Spotify 提供 AppleScript
字典。网易云音乐（com.netease.163music）、QQ音乐等没有——`tell application
"NeteaseMusic" to playpause` 直接报错。旧实现在候选列表里逐个试、失败就往下走，
于是**必然落到唯一支持 AppleScript 的 Music.app**，用户看到的就是「不管点什么都
打开苹果音乐」。

macOS 的控制走 **MediaRemote**（系统的「正在播放」中枢）：任何向
MPNowPlayingInfoCenter 注册过的播放器都能接收统一命令，与具体软件无关。
同步发送接口通过 ctypes 调用。异步曲目信息由独立的小进程读取，避免系统
回调越过 Python 对象生命周期时把常驻上位机带崩。

Windows 走 **SMTC**（GlobalSystemMediaTransportControlsSessionManager，winsdk 包）：
与 MediaRemote 同级的中枢，读写当前媒体会话。曲目信息用常驻事件循环线程承接
winsdk 的异步接口——asyncio.run 每次新建 loop 与 winsdk 的互操作桥不兼容，实测会
随机挂起，常驻 loop 是稳妥解。

Linux 沿用 playerctl，本模块不参与（control.py 直接调命令行）。
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

_FW = "/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote"

# MRMediaRemoteCommand（与官方枚举一致；0-5 稳定且够用）
_COMMANDS = {"play": 0, "pause": 1, "toggle": 2, "stop": 3, "next": 4, "prev": 5}

# 已知音乐软件的 bundle id → 显示名。自动发现时也用它做匹配。
KNOWN_APPS = {
    "com.netease.163music": "网易云音乐",
    "com.tencent.QQMusicMac": "QQ音乐",
    "com.tencent.QQMusic": "QQ音乐",
    "com.apple.Music": "音乐（Apple）",
    "com.spotify.client": "Spotify",
    "com.kugou.mac": "酷狗音乐",
    "cn.kuwo.player": "酷我音乐",
    "com.migu.music": "咪咕音乐",
    "com.tencent.QQMusicHD": "QQ音乐 HD",
}
_BUNDLE_HINT = re.compile(r"music|netease|kugou|kuwo|migu|spotify|163music", re.I)
# 名字里带这些的通常不是播放器（遥控器 / 更新器 / 助手），实测踩到过
# 网易的 UURemote（com.netease.uuremote）被 "netease" 关键字误收。
_BUNDLE_SKIP = re.compile(r"remote|helper|updater|installer|daemon|driver", re.I)
_APP_DIRS = (Path("/Applications"), Path.home() / "Applications")

# 快捷应用里用这个命令值表示「交给默认音乐软件」：点一下 = 启动它 + 开始播放。
# 比让用户手写 `open -a NeteaseMusic` 好——后者只能打开应用，不会开始播放。
MUSIC_COMMAND = "@music"

_lock = threading.Lock()
_lib = None            # CDLL 或 False（不可用）
_reader_lock = threading.Lock()
_reader_failed = False
_reader_path = None


def _load():
    """惰性加载 MediaRemote；不可用返回 False（非阻塞、不抛异常）。"""
    global _lib
    if _lib is not None:
        return _lib
    with _lock:
        if _lib is not None:
            return _lib
        if sys.platform != "darwin":
            _lib = False
            return _lib
        try:
            lib = ctypes.CDLL(_FW)
            lib.MRMediaRemoteSendCommand.restype = ctypes.c_bool
            lib.MRMediaRemoteSendCommand.argtypes = [ctypes.c_int, ctypes.c_void_p]
            _lib = lib
        except (OSError, AttributeError):
            _lib = False
    return _lib


# ---------------------------------------------------------------- 控制

def send(action: str) -> bool:
    """下发媒体命令（play/pause/toggle/next/prev）。

    命令走系统「正在播放」中枢，因此作用于**当前真正在放歌的那个软件**——
    这也是「切换歌曲只对苹果音乐生效」的修法。Windows 走 SMTC 会话命令。
    """
    if sys.platform == "win32":
        return _smtc_send(action)
    lib = _load()
    code = _COMMANDS.get(action)
    if not lib or code is None:
        return False
    # 没有活动曲目时，macOS 会把 PLAY/TOGGLE 路由给 Music.app 并启动它。
    # 必须先确认系统存在真实的当前媒体会话。
    session = now_playing()
    if not session or not session.get("title"):
        return False
    try:
        return bool(lib.MRMediaRemoteSendCommand(code, None))
    except Exception:
        return False


def now_playing(timeout: float = 1.0):
    """当前媒体会话的曲目信息。

    Windows：SMTC 读取（歌名/歌手/播放状态/进度），供 Web「正在播放」块与
    设备 NP 线级字段使用。
    macOS：通过隔离的 Objective-C 进程读取 MediaRemote。空字典表示当前没有
    媒体会话；None 表示读取能力不可用，Web 与屏幕据此诚实降级。
    """
    if sys.platform == "win32":
        return _smtc_now_playing(timeout)
    if sys.platform == "darwin":
        binary = _mac_reader_binary()
        if not binary:
            return None
        try:
            proc = subprocess.run([str(binary)], capture_output=True, text=True,
                                  timeout=max(2.5, timeout))
            if proc.returncode != 0:
                return None
            data = json.loads(proc.stdout)
            return data if isinstance(data, dict) else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None
    return None


def _mac_reader_binary() -> Path | None:
    """优先用打包时生成的 helper；源码运行时编译一次并缓存。"""
    global _reader_failed, _reader_path
    if _reader_path is not None:
        return _reader_path
    if _reader_failed:
        return None
    with _reader_lock:
        if _reader_path is not None:
            return _reader_path
        if _reader_failed:
            return None
        source = Path(__file__).with_name("macos_now_playing.m")
        bundled = source.with_suffix("")
        if bundled.is_file() and os.access(bundled, os.X_OK):
            _reader_path = bundled
            return bundled
        try:
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
            cache = Path.home() / ".workbuddy" / "bin"
            cache.mkdir(parents=True, exist_ok=True)
            binary = cache / f"macos_now_playing-{digest}"
            if not binary.is_file():
                temp = cache / f".{binary.name}.{os.getpid()}.tmp"
                proc = subprocess.run(
                    ["clang", "-fobjc-arc", "-fblocks", str(source),
                     "-framework", "Foundation", "-o", str(temp)],
                    capture_output=True, timeout=20)
                if proc.returncode != 0:
                    _reader_failed = True
                    return None
                os.replace(temp, binary)
            _reader_path = binary
            return binary
        except (OSError, subprocess.TimeoutExpired):
            _reader_failed = True
            return None


# ---------------------------------------------------------------- Windows SMTC

# SMTC 会话命令 → 方法名。toggle 在拿到会话后按播放状态二选一。
_SMTC_METHODS = {
    "play": "try_play_async",
    "pause": "try_pause_async",
    "next": "try_skip_next_async",
    "prev": "try_skip_previous_async",
}

_smtc_state = {"loop": None}


def _smtc_loop():
    """SMTC 专用事件循环线程：winsdk 的异步桥要求稳定的 loop。"""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _smtc_state["loop"] = loop
    loop.run_forever()


def _smtc_run(coro, timeout: float):
    """把 winsdk 协程投递到常驻 loop 并限时取结果；任何失败归 None/False。"""
    import asyncio
    if _smtc_state["loop"] is None:
        threading.Thread(target=_smtc_loop, daemon=True,
                         name="workbuddy-smtc").start()
        for _ in range(100):
            if _smtc_state["loop"] is not None:
                break
            time.sleep(0.02)
    loop = _smtc_state["loop"]
    if loop is None:
        return None
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout)
    except Exception:
        return None


def _smtc_now_playing(timeout: float = 1.0):
    """读 SMTC 当前会话：{title, artist, album, playing, position, duration}。"""
    import asyncio

    async def _read():
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as SMTC,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
        )
        manager = await SMTC.request_async()
        session = manager.get_current_session()
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        playback = session.get_playback_info()
        timeline = session.get_timeline_properties()
        position = getattr(timeline.position, "total_seconds", lambda: None)()
        duration = getattr(timeline.end_time, "total_seconds", lambda: None)()
        return {
            "title": props.title or "",
            "artist": props.artist or "",
            "album": props.album_title or "",
            "playing": playback.playback_status == Status.PLAYING,
            "position": position if isinstance(position, (int, float)) else None,
            "duration": duration if isinstance(duration, (int, float)) else None,
        }

    return _smtc_run(_read(), timeout)


def _smtc_send(action: str) -> bool:
    """向 SMTC 当前会话发命令；没有会话或 winsdk 缺失返回 False（上层回退按键）。"""
    import asyncio

    async def _send():
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as SMTC,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
        )
        manager = await SMTC.request_async()
        session = manager.get_current_session()
        if session is None:
            return False
        if action == "toggle":
            method = ("try_pause_async"
                      if session.get_playback_info().playback_status == Status.PLAYING
                      else "try_play_async")
        else:
            method = _SMTC_METHODS.get(action)
        if not method:
            return False
        await getattr(session, method)()
        return True

    return bool(_smtc_run(_send(), 1.5))


# ---------------------------------------------------------------- 应用发现 / 启动

def _info_plist(app: Path) -> tuple[str, str]:
    """读 .app 的 bundle id 与显示名（不启动应用）。"""
    plist = app / "Contents" / "Info.plist"
    if not plist.exists():
        return "", ""
    try:
        import plistlib
        with plist.open("rb") as handle:
            data = plistlib.load(handle)
    except Exception:
        return "", ""
    bundle = str(data.get("CFBundleIdentifier") or "")
    name = str(data.get("CFBundleDisplayName") or data.get("CFBundleName")
               or app.stem)
    return bundle, name


def installed_apps() -> list[dict]:
    """本机装了的音乐软件（按已知清单 + bundle id 关键字自动发现）。"""
    found: dict[str, dict] = {}
    for root in _APP_DIRS:
        try:
            entries = list(root.glob("*.app"))
        except OSError:
            continue
        for app in entries:
            bundle, name = _info_plist(app)
            if not bundle:
                continue
            if bundle in KNOWN_APPS:
                name = KNOWN_APPS[bundle]
            elif not _BUNDLE_HINT.search(bundle) or _BUNDLE_SKIP.search(bundle):
                continue
            found.setdefault(bundle, {"id": bundle, "name": name, "path": str(app)})
    return sorted(found.values(), key=lambda item: item["name"])


def default_app(cfg) -> dict | None:
    """当前的默认音乐软件：配置值优先，未配置时取第一个装了的（偏好网易云）。"""
    apps = installed_apps()
    want = str(getattr(cfg, "music_app", "") or "")
    if want and want != "auto":
        for app in apps:
            if app["id"] == want:
                return app
        return {"id": want, "name": KNOWN_APPS.get(want, want), "path": ""}
    for preferred in ("com.netease.163music", "com.tencent.QQMusicMac",
                      "com.apple.Music", "com.spotify.client"):
        for app in apps:
            if app["id"] == preferred:
                return app
    return apps[0] if apps else None


def is_running(app: dict | None) -> bool:
    if not app:
        return False
    path = app.get("path") or ""
    target = path if path else (app.get("id") or "")
    if not target:
        return False
    try:
        done = subprocess.run(["pgrep", "-f", target], capture_output=True,
                              timeout=2.0)
        return done.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def has_running_player(cfg) -> bool:
    """默认播放器或其他已发现播放器是否已运行（不启动任何软件）。"""
    preferred = default_app(cfg)
    if preferred and is_running(preferred):
        return True
    preferred_id = (preferred or {}).get("id", "")
    return any(app.get("id") != preferred_id and is_running(app)
               for app in installed_apps())


def open_app(app: dict | None) -> bool:
    """启动（或切到前台）该应用；已在运行时不重复启动。"""
    if not app:
        return False
    args = ["open", "-b", app["id"]] if app.get("id") else ["open", "-a", app.get("name", "")]
    try:
        return subprocess.run(args, capture_output=True, timeout=5.0).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def play_default(cfg, settle: float = 1.2) -> dict:
    """「点一下就开始放歌」：确保默认软件在跑 → 下发播放命令。

    返回 {"ok","app","launched","command"}，便于前端/日志说明到底做了什么。
    """
    app = default_app(cfg)
    if not app:
        return {"ok": False, "reason": "没有找到可用的音乐软件"}
    launched = False
    if not is_running(app):
        launched = open_app(app)
        if not launched:
            return {"ok": False, "app": app["name"], "launched": False,
                    "reason": "未能打开所选音乐软件"}
    if sys.platform == "darwin":
        # 只控制所选播放器已经注册的媒体会话。新打开的播放器若还没有歌曲，
        # 发送通用 PLAY 反而会弹出 Apple Music。
        deadline = time.monotonic() + max(0, settle)
        session = now_playing()
        while launched and not (session or {}).get("title") and time.monotonic() < deadline:
            time.sleep(0.2)
            session = now_playing()
        if not session or not session.get("title"):
            # 软件可能已在后台运行，但尚未选择歌曲；切到所选播放器让用户
            # 直接选歌。只打开这一个应用，绝不向空会话发送系统 PLAY。
            activated = open_app(app) if not launched else True
            return {"ok": False, "app": app["name"], "launched": launched,
                    "activated": activated,
                    "reason": "已打开播放器；请先在软件中选择一首歌曲"}
        if session.get("bundle") != app["id"]:
            return {"ok": False, "app": app["name"], "launched": launched,
                    "reason": "当前媒体会话属于其他软件；请先在所选播放器中选择歌曲"}
    delivered = send("play")
    confirmed = bool(delivered)
    if delivered and sys.platform == "darwin":
        # MediaRemote 的返回值只表示命令已投递，不表示播放器真的开始放歌。
        # 尤其会员/离线曲目可能仍停在暂停态。
        deadline = time.monotonic() + 1.5
        current = now_playing()
        while (current or {}).get("bundle") == app["id"] and \
                not current.get("playing") and time.monotonic() < deadline:
            time.sleep(0.2)
            current = now_playing()
        confirmed = bool(current and current.get("bundle") == app["id"]
                         and current.get("playing"))
    result = {"ok": confirmed, "app": app["name"],
              "launched": launched, "command": "play",
              "delivered": bool(delivered), "confirmed": confirmed}
    if delivered and not confirmed:
        result["reason"] = "已发送播放命令，但播放器未进入播放；请检查歌曲是否可播放"
    elif not delivered:
        result["reason"] = ("播放器已启动，但系统暂时没有可控制的播放队列"
                            if launched else "系统没有可控制的播放会话")
    return result
