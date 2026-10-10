"""默认音乐软件：启动 / 播放 / 上一首下一首 / 读取正在播放（macOS）。

**为什么不能用 AppleScript**：macOS 上只有 Apple Music 与 Spotify 提供 AppleScript
字典。网易云音乐（com.netease.163music）、QQ音乐等没有——`tell application
"NeteaseMusic" to playpause` 直接报错。旧实现在候选列表里逐个试、失败就往下走，
于是**必然落到唯一支持 AppleScript 的 Music.app**，用户看到的就是「不管点什么都
打开苹果音乐」。

macOS 的正解是 **MediaRemote**（系统的「正在播放」中枢）：任何向
MPNowPlayingInfoCenter 注册过的播放器都能被统一下发命令、统一读到曲目信息，
与具体软件无关。它是私有框架，所以这里用 ctypes 直接调用，读不到时明确返回
`None`/`{}`，不猜、不伪造。

Windows / Linux 各自沿用原有实现（虚拟媒体键 / playerctl），本模块只在 macOS 生效。
"""
from __future__ import annotations

import ctypes
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
_queue = None
_consts: dict = {}


def _load():
    """惰性加载 MediaRemote；不可用返回 False（非阻塞、不抛异常）。"""
    global _lib, _queue
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
            lib.MRMediaRemoteGetNowPlayingInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            libc = ctypes.CDLL(None)
            libc.dispatch_queue_create.restype = ctypes.c_void_p
            libc.dispatch_queue_create.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
            _queue = libc.dispatch_queue_create(b"workbuddy.nowplaying", None)
            _lib = lib
        except (OSError, AttributeError):
            _lib = False
    return _lib


def _sym(name: str):
    """取 MediaRemote 导出的 CFString 常量（如 kMRMediaRemoteNowPlayingInfoTitle）。"""
    if name in _consts:
        return _consts[name]
    value = None
    try:
        libc = ctypes.CDLL(None)
        libc.dlsym.restype = ctypes.c_void_p
        libc.dlsym.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        value = libc.dlsym(ctypes.c_void_p(-2), name.encode()) or None
    except (OSError, AttributeError):
        value = None
    _consts[name] = value
    return value


# ---------------------------------------------------------------- 控制

def send(action: str) -> bool:
    """下发媒体命令（play/pause/toggle/stop/next/prev）。

    命令走系统「正在播放」中枢，因此作用于**当前真正在放歌的那个软件**——
    这也是「切换歌曲只对苹果音乐生效」的修法。
    """
    lib = _load()
    code = _COMMANDS.get(action)
    if not lib or code is None:
        return False
    try:
        return bool(lib.MRMediaRemoteSendCommand(code, None))
    except Exception:
        return False


# ---------------------------------------------------------------- 读取

class _Desc(ctypes.Structure):
    _fields_ = [("reserved", ctypes.c_ulong), ("size", ctypes.c_ulong)]


_INVOKE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)


class _Block(ctypes.Structure):
    _fields_ = [("isa", ctypes.c_void_p), ("flags", ctypes.c_int),
                ("reserved", ctypes.c_int), ("invoke", _INVOKE),
                ("descriptor", ctypes.POINTER(_Desc))]


def _cfstring(ptr) -> str | None:
    try:
        buf = ctypes.create_string_buffer(1024)
        libc = ctypes.CDLL(None)
        if libc.CFStringGetCString(ctypes.c_void_p(ptr), buf, 1024, 0x08000100):
            return buf.value.decode("utf-8", "ignore")
    except Exception:
        pass
    return None


def _cfnumber(ptr):
    try:
        libc = ctypes.CDLL(None)
        out = ctypes.c_double()
        if libc.CFNumberGetValue(ctypes.c_void_p(ptr), 13, ctypes.byref(out)):  # Double
            return out.value
    except Exception:
        pass
    return None


def now_playing(timeout: float = 1.0):
    """当前的曲目信息。

    返回 None = 本平台/本机读不到（不假装支持）；
         {} = 读得到，但当前没有任何软件在放（系统里没有「正在播放」客户端）；
         字典 = {"title","artist","album","position","duration","playing"}。
    """
    lib = _load()
    if not lib or not _queue:
        return None
    isa = _sym("_NSConcreteStackBlock")
    if not isa:
        return None

    result = {}
    done = threading.Event()

    def _cb(_blk, info):
        try:
            if not info:
                result["empty"] = True
                return
            libc = ctypes.CDLL(None)
            for key in ("Title", "Artist", "Album"):
                const = _sym("kMRMediaRemoteNowPlayingInfo" + key)
                if not const:
                    continue
                value = libc.CFDictionaryGetValue(ctypes.c_void_p(info),
                                                 ctypes.c_void_p(const))
                if value:
                    text = _cfstring(value)
                    if text:
                        result[key.lower()] = text
            for key, name in (("Duration", "duration"), ("ElapsedTime", "position"),
                              ("PlaybackRate", "rate")):
                const = _sym("kMRMediaRemoteNowPlayingInfo" + key)
                if not const:
                    continue
                value = libc.CFDictionaryGetValue(ctypes.c_void_p(info),
                                                 ctypes.c_void_p(const))
                if value:
                    number = _cfnumber(value)
                    if number is not None:
                        result[name] = round(number, 1)
        except Exception:
            pass
        finally:
            done.set()

    desc = _Desc(0, ctypes.sizeof(_Block))
    block = _Block(ctypes.c_void_p(isa), 0, 0, _INVOKE(_cb), ctypes.pointer(desc))
    try:
        lib.MRMediaRemoteGetNowPlayingInfo(ctypes.c_void_p(_queue), ctypes.byref(block))
    except Exception:
        return None
    done.wait(timeout)
    if not result:
        return {}                      # 回调超时：当作没有正在播放
    if result.pop("empty", False):
        return {}
    result["playing"] = bool(result.get("rate"))
    return result


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
        if launched:
            time.sleep(settle)          # 等它把「正在播放」客户端注册到系统
    delivered = send("play")
    return {"ok": bool(delivered or launched), "app": app["name"],
            "launched": launched, "command": "play"}
