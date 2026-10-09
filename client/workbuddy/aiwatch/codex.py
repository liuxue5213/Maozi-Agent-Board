"""Codex 多会话监听：~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl"""
import datetime as dt
import re
import time
from pathlib import Path

from . import (Activity, ERROR, GENERATING, THINKING, TOOL, IDLE,
               WAIT_INPUT, tool_to_task, sanitize_project, tail_json_lines)

_CWD_RE = re.compile(rb'"cwd":"([^"]+)"')


def _project_from_head(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        m = _CWD_RE.search(head)
        if m:
            cwd = m.group(1).decode("utf-8", "ignore").replace("\\\\", "\\")
            return cwd.replace("\\", "/").rstrip("/").split("/")[-1]
    except Exception:
        pass
    return ""


class CodexSource:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _day_dirs(self):
        today = dt.date.today()
        for offset in range(2):
            day = today - dt.timedelta(days=offset)
            d = self.root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
            if d.is_dir():
                yield d

    def poll_all(self, now, cache) -> list:
        acts = []
        files = []
        for d in self._day_dirs():
            try:
                for p in d.glob("rollout-*.jsonl"):
                    m = p.stat().st_mtime
                    if now - m <= 45:
                        files.append((m, p))
            except OSError:
                continue
        files.sort(reverse=True)
        for mtime, f in files[:4]:
            act = self._parse(f, mtime)
            if act:
                acts.append(act)
        return acts

    def _parse(self, f: Path, mtime: float):
        for line in tail_json_lines(f):
            payload = line.get("payload") or {}
            ptype = payload.get("type", "")
            if ptype == "function_call":
                name = payload.get("name", "")
                if name.lower() in ("shell", "exec", "local_shell", "bash"):
                    return Activity("codex", TOOL, mtime, sid=f.stem,
                                    project=_project_from_head(f),
                                    detail="Bash", task="RUN")
                return Activity("codex", TOOL, mtime, sid=f.stem,
                                project=_project_from_head(f),
                                detail=name, task=tool_to_task(name))
            if ptype == "function_call_output":
                return Activity("codex", THINKING, mtime, sid=f.stem,
                                project=_project_from_head(f))
            if ptype == "task_complete":
                # 回复完成，等用户下一句话
                return Activity("codex", WAIT_INPUT, mtime, sid=f.stem,
                                project=_project_from_head(f))
            if ptype in ("task_started", "turn_context", "task_note"):
                return Activity("codex", GENERATING, mtime, sid=f.stem,
                                project=_project_from_head(f))
            if ptype in ("error", "stream_error"):
                return Activity("codex", ERROR, mtime, sid=f.stem,
                                project=_project_from_head(f),
                                err=str(payload.get("message") or ptype))
            if ptype == "response_item":
                inner = payload.get("payload") or {}
                if inner.get("role") == "assistant":
                    return Activity("codex", GENERATING, mtime, sid=f.stem,
                                    project=_project_from_head(f))
        return None
