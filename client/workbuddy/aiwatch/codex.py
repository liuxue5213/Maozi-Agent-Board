"""Codex 多会话监听：~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl。

会话目录按“创建日期”归档，不会在跨天后搬到当天目录。因此监听最近修改的
rollout 文件，不能只看今天和昨天；长对话或恢复旧对话仍会继续写原文件。
"""
import re
import time
from pathlib import Path

from . import (Activity, ERROR, GENERATING, THINKING, TOOL, IDLE,
               WAIT_INPUT, tool_to_task, sanitize_project, tail_json_lines)

# JSON 序列化器可以在冒号两侧插入空格；不能依赖 Codex 当前恰好输出紧凑 JSON。
_CWD_RE = re.compile(rb'"cwd"\s*:\s*"([^"]+)"')
_HEAD_CACHE = {}          # (path, mtime) -> (project_name, cwd)


def _head_meta(path: Path, mtime: float) -> tuple[str, str]:
    """rollout 头部的 cwd → (项目名, 绝对路径)。带 mtime 缓存（每秒轮询）。"""
    key = (str(path), mtime)
    hit = _HEAD_CACHE.get(key)
    if hit:
        return hit
    name = cwd = ""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        m = _CWD_RE.search(head)
        if m:
            cwd = (m.group(1).decode("utf-8", "ignore")
                   .replace("\\\\", "\\").replace("\\", "/").rstrip("/"))
            name = cwd.split("/")[-1]
    except Exception:
        pass
    _HEAD_CACHE[key] = (name, cwd)
    if len(_HEAD_CACHE) > 256:
        _HEAD_CACHE.pop(next(iter(_HEAD_CACHE)))
    return name, cwd


def _project_from_head(path: Path) -> str:
    return _head_meta(path, path.stat().st_mtime)[0]


class CodexSource:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _rollouts(self, now, cache):
        """返回所有会话文件路径，短暂缓存目录遍历结果。

        只缓存“有哪些文件”，每轮仍重新读取 mtime；这样已有长会话持续写入时
        能在一秒内刷新，同时避免每秒递归扫描整个 sessions 目录。
        """
        key = "codex-rollout-paths"
        hit = cache.get(key)
        if hit and now - hit[0] < 5:
            return hit[1]
        try:
            paths = list(self.root.rglob("rollout-*.jsonl"))
        except OSError:
            paths = []
        cache[key] = (now, paths)
        return paths

    def poll_all(self, now, cache) -> list:
        acts = []
        files = []
        for p in self._rollouts(now, cache):
            try:
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
        proj, pcwd = _head_meta(f, mtime)   # 项目名 + 绝对路径（收件箱「打开」用）
        for line in tail_json_lines(f):
            payload = line.get("payload") or {}
            ptype = payload.get("type", "")
            if ptype in ("function_call", "custom_tool_call"):
                name = payload.get("name", "")
                if name.lower() in ("shell", "exec", "local_shell", "bash"):
                    return Activity("codex", TOOL, mtime, sid=f.stem,
                                    project=proj, path=pcwd,
                                    detail="Bash", task="RUN")
                return Activity("codex", TOOL, mtime, sid=f.stem,
                                project=proj, path=pcwd,
                                detail=name, task=tool_to_task(name))
            if ptype in ("function_call_output", "custom_tool_call_output"):
                return Activity("codex", THINKING, mtime, sid=f.stem,
                                project=proj, path=pcwd)
            if ptype == "task_complete":
                # 回复完成，等用户下一句话
                return Activity("codex", WAIT_INPUT, mtime, sid=f.stem,
                                project=proj, path=pcwd)
            if ptype in ("task_started", "turn_context", "task_note", "reasoning"):
                return Activity("codex", GENERATING, mtime, sid=f.stem,
                                project=proj, path=pcwd)
            if ptype in ("error", "stream_error"):
                return Activity("codex", ERROR, mtime, sid=f.stem,
                                project=proj, path=pcwd,
                                err=str(payload.get("message") or ptype))
            if ptype in ("response_item", "message"):
                inner = payload.get("payload") or payload
                if inner.get("role") == "assistant":
                    return Activity("codex", GENERATING, mtime, sid=f.stem,
                                    project=proj, path=pcwd)
                if inner.get("role") == "user":
                    return Activity("codex", THINKING, mtime, sid=f.stem,
                                    project=proj, path=pcwd)
        return None
