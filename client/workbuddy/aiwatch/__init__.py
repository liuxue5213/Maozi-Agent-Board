"""AI 任务状态检测引擎 v3：多工具 × 多会话并行监控。

每个来源（Claude Code / Codex / ZCode）返回其所有活跃会话的活动列表，
引擎按活跃度排序输出最多 4 条并行任务（协议 AI0~AI3）。
"""
import json
import hashlib
import time
from pathlib import Path

IDLE, THINKING, GENERATING, TOOL, BUILD, DONE, ERROR, WAIT_INPUT, ASK = (
    "IDLE", "THINKING", "GENERATING", "TOOL", "BUILD", "DONE", "ERROR",
    "WAIT_INPUT", "ASK")

_FRESH = {"claude": 45, "codex": 45, "zcode": 300}
_WAIT_FRESH = 1800   # 等你回复/等待确认 可保持 30 分钟
_PRIO = {IDLE: 0, DONE: 1, THINKING: 2, GENERATING: 3,
         BUILD: 3, TOOL: 4, WAIT_INPUT: 5, ASK: 6, ERROR: 7}


def sanitize_project(name: str) -> str:
    """Keep a short UTF-8 project label without wire delimiters."""
    clean = "".join(c for c in (name or "") if c not in ",=\r\n" and ord(c) >= 32)
    return clean.encode("utf-8")[:24].decode("utf-8", "ignore")


def tool_to_task(tool_name: str) -> str:
    m = {"bash": "RUN", "shell": "RUN", "exec": "RUN",
         "edit": "CODE", "write": "CODE", "multiedit": "CODE", "notebookedit": "CODE",
         "apply_patch": "CODE", "task": "CODE", "agent": "CODE",
         "webfetch": "RESEARCH", "websearch": "RESEARCH",
         "grep": "EXPLORE", "glob": "EXPLORE", "read": "EXPLORE", "ls": "EXPLORE",
         "todowrite": "DOC"}
    return m.get((tool_name or "").lower(), "CODE")


def newest_file(directory: Path, pattern: str, cache: dict, key: str, ttl: float = 2.0):
    now = time.time()
    hit = cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    best = None
    try:
        for p in directory.glob(pattern):
            m = p.stat().st_mtime
            if best is None or m > best[1]:
                best = (p, m)
    except OSError:
        best = None
    cache[key] = (now, best[0] if best else None)
    return best[0] if best else None


def tail_json_lines(path: Path, nbytes: int = 16384, limit: int = 12):
    """读文件末尾，返回按时间倒序的有效 JSON 行（新→旧）。"""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > nbytes:
                f.seek(-nbytes, 2)
            data = f.read()
    except OSError:
        return []
    out = []
    for line in reversed(data.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


class Activity:
    __slots__ = ("tool", "state", "ts", "sid", "project", "detail", "task", "err")

    def __init__(self, tool, state, ts, sid="", project="", detail="", task="", err=""):
        self.tool, self.state, self.ts = tool, state, ts
        self.sid, self.project = sid, project
        self.detail, self.task, self.err = detail, task, err


class AIWatcher:
    """聚合三个来源的全部活跃会话。"""

    def __init__(self, cfg):
        from .claude import ClaudeSource
        from .codex import CodexSource
        from .zcode import ZCodeSource

        self._cache = {}
        self._bursts = {}   # (tool, sid) -> burst 起始时间
        self.sources = [
            ClaudeSource(cfg.root("claude")),
            CodexSource(cfg.root("codex")),
            ZCodeSource(cfg.root("zcode")),
        ]

    # ---- 多任务输出（协议 AI0~AI3）----

    def poll_all(self, now: float) -> list:
        acts = []
        for src in self.sources:
            try:
                acts.extend(src.poll_all(now, self._cache) or [])
            except Exception:
                continue
        # 仅保留活跃任务，同会话去重（高优先级状态优先），按活跃度排序
        fresh = {}
        for a in acts:
            if a.state == IDLE:
                continue
            limit = (_WAIT_FRESH if a.state in (WAIT_INPUT, ASK)
                     else _FRESH.get(a.tool, 45))
            if now - a.ts > limit:
                continue
            k = (a.tool, a.sid)
            cur = fresh.get(k)
            if cur is None or _PRIO.get(a.state, 0) >= _PRIO.get(cur.state, 0):
                if cur is None or a.ts >= cur.ts or _PRIO.get(a.state, 0) > _PRIO.get(cur.state, 0):
                    fresh[k] = a
        ranked = sorted(fresh.values(), key=lambda a: (_PRIO.get(a.state, 0), a.ts),
                        reverse=True)[:4]

        out = []
        for a in ranked:
            key = (a.tool, a.sid)
            start = self._bursts.setdefault(key, now)
            elapsed = max(0, now - start)
            # 日志只提供阶段信号，不能推算真实完成百分比。
            prog = 100 if a.state == DONE else -1
            out.append({
                "id": hashlib.sha1(f"{a.tool}:{a.sid}".encode()).hexdigest()[:8],
                "tool": a.tool,
                "state": a.state,
                "task": a.task or tool_to_task(a.detail) or "TASK",
                "prog": prog,
                "elapsed": int(elapsed),
                "age": max(0, int(now - a.ts)),
                "project": sanitize_project(a.project),
                "err": a.err or "",
            })
        # 清理过期的 burst 记录
        for k in [k for k, v in self._bursts.items() if now - v > 3600]:
            del self._bursts[k]
        return out

    # ---- 单任务摘要（控制台渲染 / CLI once 用）----

    def summary(self, tasks: list) -> dict:
        if not tasks:
            return {"aistate": IDLE, "aitool": "", "aitask": "", "aiprog": 0, "aitime": 0}
        t = tasks[0]
        return {
            "aistate": t["state"],
            "aitool": t["tool"],
            "aitask": t["task"],
            "aiprog": t["prog"],
            "aitime": t["elapsed"],
            "aierr": t["err"],
        }

    def poll(self, now: float) -> dict:
        return self.summary(self.poll_all(now))
