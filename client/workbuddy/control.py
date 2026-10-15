"""跨平台电脑控制：音量 / 静音 / 媒体播放 / 应用启动（触屏控制中心的后端）。

- macOS：音量优先走 CoreAudio 原生接口，失败时回退 osascript；
  媒体命令走 MediaRemote（见 music.py）。
  旧实现用 AppleScript 逐个试播放器，而只有 Apple Music/Spotify 有 AppleScript
  字典，于是无论用户装了什么，命令最终都会落到 Apple Music。
- Windows：虚拟键码（VK_VOLUME_* / VK_MEDIA_*）
- Linux：pactl + playerctl
"""
from __future__ import annotations

import subprocess
import sys
import time
import hashlib
import json
import os
import threading
from pathlib import Path

def _run(args, timeout=2.0):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _osascript(script: str):
    return _run(["osascript", "-e", script], timeout=2.5)


_volume_binary = None
_volume_binary_failed = False
_volume_binary_lock = threading.Lock()


def _mac_volume_binary():
    """Use the bundled CoreAudio helper, or compile it once for source installs."""
    global _volume_binary, _volume_binary_failed
    if _volume_binary is not None or _volume_binary_failed:
        return _volume_binary
    with _volume_binary_lock:
        if _volume_binary is not None or _volume_binary_failed:
            return _volume_binary
        source = Path(__file__).with_name("macos_volume.c")
        bundled = source.with_suffix("")
        if bundled.is_file() and os.access(bundled, os.X_OK):
            _volume_binary = bundled
            return bundled
        try:
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
            cache = Path.home() / ".workbuddy" / "bin"
            cache.mkdir(parents=True, exist_ok=True)
            binary = cache / f"macos_volume-{digest}"
            if not binary.is_file():
                temp = cache / f".{binary.name}.{os.getpid()}.tmp"
                proc = subprocess.run(
                    ["clang", "-std=c11", str(source), "-framework", "CoreAudio",
                     "-framework", "AudioToolbox", "-o", str(temp)],
                    capture_output=True, timeout=20)
                if proc.returncode != 0:
                    _volume_binary_failed = True
                    return None
                os.replace(temp, binary)
            _volume_binary = binary
            return binary
        except (OSError, subprocess.TimeoutExpired):
            _volume_binary_failed = True
            return None


def _mac_volume_command(*args):
    binary = _mac_volume_binary()
    if not binary:
        return None
    try:
        result = subprocess.run([str(binary), *map(str, args)], capture_output=True,
                                text=True, timeout=1.5)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if isinstance(data, dict) and "vol" in data:
                return {"vol": int(data["vol"]), "mut": int(data.get("mut", 0))}
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        pass
    return None


class ControlApp:
    """触屏动作执行器 + 音量状态采样（带节流缓存）。"""

    def __init__(self):
        self._cache: dict = {}
        self._cache_at = 0.0

    # ---- 音量状态（1s 节流；不可用平台返回空）----

    def snapshot(self) -> dict:
        now = time.monotonic()
        if now - self._cache_at < 1.0:
            return dict(self._cache)
        self._cache_at = now
        self._cache = self._read_volume() or {}
        return dict(self._cache)

    def _read_volume(self):
        if sys.platform == "darwin":
            native = _mac_volume_command()
            if native is not None:
                return native
            result = _osascript("get volume settings")
            if result and result.returncode == 0:
                text = result.stdout
                vol = mut = None
                for part in text.split(","):
                    key, _, value = part.partition(":")
                    value = value.strip()
                    if key.strip() == "output volume":
                        try:
                            vol = int(value)
                        except ValueError:
                            pass
                    elif key.strip() == "output muted":
                        mut = 1 if value == "true" else 0
                out = {}
                if vol is not None:
                    out["vol"] = vol
                if mut is not None:
                    out["mut"] = mut
                return out
            return {}
        if sys.platform.startswith("linux"):
            result = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            muted = _run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
            out = {}
            if result and result.returncode == 0 and "/" in result.stdout:
                try:
                    out["vol"] = int(round(int(result.stdout.split("/")[1].strip().rstrip("%")) /
                                           max(1, int(result.stdout.split("/")[0].strip().rstrip("%")) or 1)))
                except (ValueError, IndexError, ZeroDivisionError):
                    pass
            if muted and muted.returncode == 0:
                out["mut"] = 1 if "yes" in muted.stdout.lower() else 0
            return out
        if sys.platform == "win32":
            return _win_volume_read()
        return {}

    # ---- 动作 ----

    def volume_step(self, delta: int):
        if sys.platform == "darwin":
            native = _mac_volume_command("step", delta)
            if native is not None:
                self._cache = native
                self._cache_at = time.monotonic()
                return
            sign = "+" if delta >= 0 else "-"
            _osascript(f"set volume output volume ((output volume of (get volume settings)) "
                       f"{sign} {abs(delta)})")
            if delta > 0:
                _osascript("set volume output muted false")
        elif sys.platform.startswith("linux"):
            _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@",
                  f"{delta:+d}%".replace("+", "+")])
        elif sys.platform == "win32":
            # pycaw 可用时直接写端点音量：精确到步长，不依赖按键换算，
            # 也避免 VK_MEDIA 键只动「默认设备」以外的歧义
            ep = _win_endpoint()
            if ep is not None:
                try:
                    current = ep.GetMasterVolumeLevelScalar()
                    target = max(0.0, min(1.0, current + delta / 100.0))
                    ep.SetMasterVolumeLevelScalar(target, None)
                except Exception:
                    _win_key(0xAF if delta > 0 else 0xAE, count=abs(delta) // 2 or 1)
            else:
                _win_key(0xAF if delta > 0 else 0xAE, count=abs(delta) // 2 or 1)
        else:
            _win_key(0xAF if delta > 0 else 0xAE, count=abs(delta) // 5 or 1)
        self._cache_at = 0.0  # 立即刷新缓存

    def toggle_mute(self):
        if sys.platform == "darwin":
            native = _mac_volume_command("toggle-mute")
            if native is not None:
                self._cache = native
                self._cache_at = time.monotonic()
                return
            _osascript("set volume output muted (not (output muted of (get volume settings)))")
        elif sys.platform.startswith("linux"):
            _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
        elif sys.platform == "win32":
            ep = _win_endpoint()
            if ep is not None:
                try:
                    ep.SetMute(not ep.GetMute(), None)
                except Exception:
                    _win_key(0xAD)
            else:
                _win_key(0xAD)
        else:
            _win_key(0xAD)
        self._cache_at = 0.0

    def media(self, action: str) -> bool:
        """向当前系统媒体会话发送命令；绝不为了兜底而启动播放器。

        返回值只表示系统是否接收了命令。macOS 的 MediaRemote 在没有活动会话时
        也可能返回 True，因此是否需要启动默认播放器由上层检查播放器进程。
        """
        if sys.platform == "darwin":
            from . import music
            # AppleScript 的 `tell application` 会启动 Music/Spotify。这里必须只走
            # 系统媒体中枢，避免一次触摸弹出多个播放器。
            return music.send(action)
        elif sys.platform.startswith("linux"):
            commands = {"play": "play", "pause": "pause", "toggle": "play-pause",
                        "next": "next", "prev": "previous"}
            if action in commands:
                result = _run(["playerctl", commands[action]])
                return bool(result and result.returncode == 0)
        else:
            # 首选 SMTC 会话命令（作用于真实媒体会话，见 music.py）；
            # 无 winsdk 或当前没有媒体会话时回退虚拟键——播放键本身就是切换键
            from . import music
            if music.send(action):
                return True
            codes = {"play": 0xB3, "pause": 0xB3, "toggle": 0xB3,
                     "next": 0xB0, "prev": 0xB1}
            if action in codes:
                _win_key(codes[action])
                return True
        return False

    def launch(self, command: str):
        try:
            if sys.platform == "win32":
                subprocess.Popen(command, shell=True)
            else:
                subprocess.Popen(command, shell=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


def open_in_terminal(path: str) -> dict:
    """在 Terminal.app 新窗口 cd 到目录（收件箱「终端打开」）。

    open -a Terminal <dir> 是 Terminal 原生支持的行为，全机必有；
    iTerm2/Warp 各有授权差异，不做自动猜测。"""
    target = Path(path)
    if not target.is_dir():
        return {"ok": False, "error": "目录不存在"}
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Terminal", str(target)])
        elif sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "cmd", "/K",
                              f"cd /d {target}"])
        else:
            subprocess.Popen(["x-terminal-emulator", "--working-directory",
                              str(target)])
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:64]}


def open_in_finder(path: str) -> dict:
    """在 Finder 里显示目录（收件箱「打开项目」）。数组调用不走 shell，无注入面。"""
    target = Path(path)
    if not target.is_dir():
        return {"ok": False, "error": "目录不存在"}
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:64]}


def _win_key(code: int, count: int = 1):
    """Windows 虚拟键（音量/媒体键）。"""
    try:
        import ctypes

        for _ in range(max(1, count)):
            ctypes.windll.user32.keybd_event(code, 0, 0, 0)
            ctypes.windll.user32.keybd_event(code, 0, 2, 0)  # KEYEVENTF_KEYUP
    except Exception:
        pass


def _win_endpoint():
    """默认输出设备的 IAudioEndpointVolume（pycaw）。不可用返回 None。

    调用可能来自 Web 线程：COM 单元按线程初始化，每次进来都 CoInitialize
    （幂等、开销可忽略）。pycaw 缺失或设备枚举失败都归 None，调用方回退
    虚拟键——音量「能调」不依赖 pycaw，「看得见」才依赖。
    """
    if sys.platform != "win32":
        return None
    try:
        from ctypes import POINTER, cast
        import comtypes
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        device = AudioUtilities.GetSpeakers()
        interface = device.Activate(IAudioEndpointVolume._iid_,
                                    comtypes.CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception:
        return None


def _win_volume_read():
    """pycaw 读默认输出音量/静音。拿不到就空字典（屏上显示 --，诚实降级）。"""
    ep = _win_endpoint()
    if ep is None:
        return {}
    try:
        return {"vol": int(round(ep.GetMasterVolumeLevelScalar() * 100)),
                "mut": 1 if ep.GetMute() else 0}
    except Exception:
        return {}
