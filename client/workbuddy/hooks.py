"""Hook 快通道自动注入（AI 采集第 2 层）：把 /hook/start|pulse|stop 写进
Claude Code 的 ~/.claude/settings.json，Agent 一开秒级亮屏（附录A）。

设计约束：
- **加法合并**：只追加带 `workbuddy-hook` 标记的条目，用户已有 hooks 一律不动；
  卸载只删自己的条目。
- **首次安装前备份** settings.json → settings.json.bak（已存在则不覆盖，
  那是用户最早的原始版本）。
- hook 命令自带 `-m 1`（本地服务应答毫秒级；客户端不在时 1 秒超时）与
  `|| true`，绝不能卡住编辑器。
- 幂等：重复安装不产生重复条目。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

MARKER = "workbuddy-hook"

# 事件 → (agent 标记, hook 动作)。UserPromptSubmit=用户发话（开始），
# PostToolUse=每次工具调用（心跳），Stop=回合结束。
_PLAN = [
    ("UserPromptSubmit", "claude", "start"),
    ("PostToolUse", "claude", "pulse"),
    ("Stop", "claude", "stop"),
]


def _command(port: int, agent: str, action: str) -> str:
    return (f'curl -s -m 1 "http://127.0.0.1:{port}/hook/{action}'
            f'?agent={agent}" >/dev/null 2>&1 # {MARKER}')


def _has_marker(entry: dict) -> bool:
    for h in (entry.get("hooks") or []):
        if MARKER in str(h.get("command", "")):
            return True
    return False


def install(settings_path: Path, port: int = 8765) -> dict:
    """追加 hooks 并写回。返回 {installed: [...], skipped: [...], backup}。"""
    settings_path = Path(settings_path)
    data = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raise ValueError(
                f"{settings_path} 不是合法 JSON——请先手工检查，注入已中止")
        if not isinstance(data, dict):
            raise ValueError(f"{settings_path} 顶层不是对象，注入已中止")
    backup = None
    backup_path = settings_path.with_name(settings_path.name + ".bak")
    if settings_path.exists() and not backup_path.exists():
        shutil.copy2(settings_path, backup_path)
        backup = str(backup_path)
    hooks = data.setdefault("hooks", {})
    installed, skipped = [], []
    for event, agent, action in _PLAN:
        entries = hooks.setdefault(event, [])
        if any(_has_marker(e) for e in entries):
            skipped.append(event)
            continue
        entries.append({"hooks": [{"type": "command",
                                   "command": _command(port, agent, action)}]})
        installed.append(event)
    if installed:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        # 写后回读校验：损坏用户的编辑器配置是事故级
        json.loads(settings_path.read_text(encoding="utf-8"))
    return {"installed": installed, "skipped": skipped, "backup": backup}


def uninstall(settings_path: Path) -> dict:
    """只移除带标记的条目；用户自己的 hooks 原样保留。"""
    settings_path = Path(settings_path)
    if not settings_path.exists():
        return {"removed": [], "missing": True}
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise ValueError(f"{settings_path} 不是合法 JSON，卸载已中止")
    hooks = data.get("hooks") or {}
    removed = []
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not _has_marker(e)]
        if len(kept) != len(entries):
            removed.append(event)
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if removed:
        settings_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    return {"removed": removed, "missing": False}


def status(settings_path: Path) -> dict:
    """{wired: bool, events: [...], settings_found, backup_exists}"""
    settings_path = Path(settings_path)
    if not settings_path.exists():
        return {"wired": False, "events": [], "settings_found": False,
                "backup_exists": False}
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"wired": False, "events": [], "settings_found": True,
                "backup_exists": False, "error": "settings.json 解析失败"}
    events = []
    for event, entries in (data.get("hooks") or {}).items():
        if isinstance(entries, list) and any(_has_marker(e) for e in entries):
            events.append(event)
    return {"wired": len(events) == len(_PLAN), "events": sorted(events),
            "settings_found": True,
            "backup_exists": Path(str(settings_path) + ".bak").exists()}
