"""默认音乐软件：启动 / 播放 / 上一首下一首（macOS）。

**为什么不能用 AppleScript**：macOS 上只有 Apple Music 与 Spotify 提供 AppleScript
字典。网易云音乐（com.netease.163music）、QQ音乐等没有——`tell application
"NeteaseMusic" to playpause` 直接报错。旧实现在候选列表里逐个试、失败就往下走，
于是**必然落到唯一支持 AppleScript 的 Music.app**，用户看到的就是「不管点什么都
打开苹果音乐」。

macOS 的控制走 **MediaRemote**（系统的「正在播放」中枢）：任何向
MPNowPlayingInfoCenter 注册过的播放器都能接收统一命令，与具体软件无关。
同步发送接口通过 ctypes 调用；异步曲目信息接口不在 Python 中手工桥接。

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


def now_playing(timeout: float = 1.0):
    """当前版本不读取私有框架的异步曲目信息。

    MediaRemote 的发送接口是同步的，可以稳定用于播放控制；读取接口要求一个
    Objective-C Block。用 ctypes 手工构造的 Block 在播放器真正返回数据后可能被
    系统异步持有，Python 回调释放后会令上位机退出。稳定性优先，明确返回 None，
    Web 端会显示“不支持读取”，控制按钮仍然完整可用。
    """
    return None


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
        if launched:
            time.sleep(settle)          # 等它把「正在播放」客户端注册到系统
    delivered = send("play")
    result = {"ok": bool(delivered), "app": app["name"],
              "launched": launched, "command": "play",
              "delivered": bool(delivered)}
    if not delivered:
        result["reason"] = ("播放器已启动，但系统暂时没有可控制的播放队列"
                            if launched else "系统没有可控制的播放会话")
    return result
