"""Claude Code 多会话监听：~/.claude/projects/<项目>/<会话>.jsonl"""
import time
from pathlib import Path

from . import (Activity, ASK, ERROR, GENERATING, THINKING, TOOL, IDLE,
               WAIT_INPUT, tool_to_task, tail_json_lines)


def _project_name(dir_name: str) -> str:
    """'-Users-liuxue-Desktop-www-maozi-diy' -> 'maozi-diy'"""
    n = dir_name
    for marker in ("-Desktop-", "-Documents-", "-Downloads-"):
        i = n.find(marker)
        if i >= 0:
            n = n[i + len(marker):]
            break
    return n.lstrip("-")[-24:]


class ClaudeSource:
    def __init__(self, root: Path):
        self.root = Path(root)

    def poll_all(self, now, cache) -> list:
        acts = []
        try:
            dirs = [d for d in self.root.iterdir() if d.is_dir()]
        except OSError:
            return acts
        ranked = []
        for d in dirs:
            try:
                for p in d.glob("*.jsonl"):
                    m = p.stat().st_mtime
                    if now - m <= 45:
                        ranked.append((m, p, d.name))
            except OSError:
                continue
        ranked.sort(reverse=True)
        for mtime, f, dname in ranked[:4]:
            a = self._parse_session(f, mtime, _project_name(dname))
            if a:
                acts.append(a)
        return acts

    def _parse_session(self, f: Path, mtime: float, project: str):
        cwd = ""
        for line in tail_json_lines(f):
            if not cwd:
                cwd = str(line.get("cwd") or "")   # 事件自带工作目录（收件箱「打开」用）
            typ = line.get("type")
            if typ == "assistant":
                msg = line.get("message") or {}
                content = msg.get("content")
                blocks = content if isinstance(content, list) else []
                for b in blocks:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        name = b.get("name", "")
                        if name in ("AskUserQuestion", "ExitPlanMode"):
                            # AI 在等用户确认/回答
                            return Activity("claude", ASK, mtime, sid=f.stem,
                                            project=project, detail=name, path=cwd)
                        return Activity("claude", TOOL, mtime, sid=f.stem,
                                        project=project, detail=name, path=cwd,
                                        task=tool_to_task(name))
                return Activity("claude", GENERATING, mtime, sid=f.stem, project=project, path=cwd)
            if typ == "user":
                return Activity("claude", THINKING, mtime, sid=f.stem, project=project, path=cwd)
            if typ == "result":
                sub = line.get("subtype", "")
                if sub and sub != "success":
                    return Activity("claude", ERROR, mtime, sid=f.stem, project=project,
                                    err=str(line.get("result") or sub))
                # 回复完成，等用户下一句话
                return Activity("claude", WAIT_INPUT, mtime, sid=f.stem, project=project, path=cwd)
        return Activity("claude", IDLE, mtime, sid=f.stem, project=project, path=cwd)
