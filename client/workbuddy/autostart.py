"""开机自启：macOS LaunchAgent / Windows 注册表 Run / Linux XDG autostart。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PLIST_LABEL = "com.maozi.workbuddy"


def _agent_command() -> list:
    """自启时执行的命令：打包版直接跑二进制；脚本版用当前解释器。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "run", "--no-console", "--open-settings"]
    return [sys.executable, "-m", "workbuddy", "run",
            "--no-console", "--open-settings"]


def _macos_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def _macos_status() -> bool:
    return _macos_path().exists()


def _macos_enable():
    path = _macos_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    args = "</string>".join(f"<string>{a}</string>" for a in _agent_command())
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f"  <key>Label</key><string>{PLIST_LABEL}</string>\n"
        f"  <key>ProgramArguments</key><array>{args}</array>\n"
        "  <key>RunAtLoad</key><true/>\n"
        "  <key>KeepAlive</key><false/>\n"
        "</dict></plist>\n", encoding="utf-8")


def _macos_disable():
    path = _macos_path()
    if path.exists():
        path.unlink()


def _windows_status() -> bool:
    result = subprocess.run(
        ["reg", "query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
         "/v", PLIST_LABEL], capture_output=True, text=True)
    return result.returncode == 0


def _windows_enable():
    command = " ".join(f'"{a}"' if " " in a else a for a in _agent_command())
    subprocess.run(["reg", "add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                    "/v", PLIST_LABEL, "/t", "REG_SZ", "/d", command, "/f"],
                   capture_output=True)


def _windows_disable():
    subprocess.run(["reg", "delete", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                    "/v", PLIST_LABEL, "/f"], capture_output=True)


def _linux_path() -> Path:
    return Path.home() / ".config" / "autostart" / "workbuddy.desktop"


def _linux_status() -> bool:
    return _linux_path().exists()


def _linux_enable():
    path = _linux_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    command = " ".join(_agent_command())
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=WorkBuddy\n"
        f"Exec={command}\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")


def _linux_disable():
    path = _linux_path()
    if path.exists():
        path.unlink()


def status() -> bool:
    if sys.platform == "darwin":
        return _macos_status()
    if sys.platform == "win32":
        return _windows_status()
    return _linux_status()


def set_enabled(enable: bool) -> bool:
    if sys.platform == "darwin":
        (_macos_enable if enable else _macos_disable)()
    elif sys.platform == "win32":
        (_windows_enable if enable else _windows_disable)()
    else:
        (_linux_enable if enable else _linux_disable)()
    return status()
