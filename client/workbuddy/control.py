"""跨平台电脑控制：音量 / 静音 / 媒体播放 / 应用启动（触屏控制中心的后端）。

- macOS：音量用 osascript 精确读写；媒体命令走 MediaRemote（见 music.py）。
  旧实现用 AppleScript 逐个试播放器，而只有 Apple Music/Spotify 有 AppleScript
  字典，于是无论用户装了什么，命令最终都会落到 Apple Music。
- Windows：虚拟键码（VK_VOLUME_* / VK_MEDIA_*）
- Linux：pactl + playerctl
"""
from __future__ import annotations

import subprocess
import sys
import time

def _run(args, timeout=2.0):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _osascript(script: str):
    return _run(["osascript", "-e", script], timeout=2.5)


class ControlApp:
    """触屏动作执行器 + 音量状态采样（带节流缓存）。"""

    def __init__(self):
        self._cache: dict = {}
        self._cache_at = 0.0

    # ---- 音量状态（3s 节流；不可用平台返回空）----

    def snapshot(self) -> dict:
        now = time.monotonic()
        if now - self._cache_at < 3.0:
            return dict(self._cache)
        self._cache_at = now
        self._cache = self._read_volume() or {}
        return dict(self._cache)

    def _read_volume(self):
        if sys.platform == "darwin":
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
        return {}  # Windows 音量读取需要额外 API，控制中心显示 --

    # ---- 动作 ----

    def volume_step(self, delta: int):
        if sys.platform == "darwin":
            sign = "+" if delta >= 0 else "-"
            _osascript(f"set volume output volume ((output volume of (get volume settings)) "
                       f"{sign} {abs(delta)})")
        elif sys.platform.startswith("linux"):
            _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@",
                  f"{delta:+d}%".replace("+", "+")])
        else:
            _win_key(0xAF if delta > 0 else 0xAE, count=abs(delta) // 5 or 1)
        self._cache_at = 0.0  # 立即刷新缓存

    def toggle_mute(self):
        if sys.platform == "darwin":
            _osascript("set volume output muted (not (output muted of (get volume settings)))")
        elif sys.platform.startswith("linux"):
            _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
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
            # Windows 的播放键本身就是播放/暂停切换；没有独立 play/pause 键。
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


def _win_key(code: int, count: int = 1):
    """Windows 虚拟键（音量/媒体键）。"""
    try:
        import ctypes

        for _ in range(max(1, count)):
            ctypes.windll.user32.keybd_event(code, 0, 0, 0)
            ctypes.windll.user32.keybd_event(code, 0, 2, 0)  # KEYEVENTF_KEYUP
    except Exception:
        pass
