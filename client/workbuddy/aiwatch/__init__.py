"""AI 任务状态检测引擎 v3：多工具 × 多会话并行监控。

每个来源（Claude Code / Codex / ZCode / WorkBuddy）返回其所有活跃会话的活动列表，
引擎按活跃度排序输出最多 4 条并行任务（协议 AI0~AI3）。
"""
import json
import hashlib
import time
from pathlib import Path

IDLE, THINKING, GENERATING, TOOL, BUILD, DONE, ERROR, WAIT_INPUT, ASK = (
    "IDLE", "THINKING", "GENERATING", "TOOL", "BUILD", "DONE", "ERROR",
    "WAIT_INPUT", "ASK")

_FRESH = {"claude": 45, "codex": 45, "zcode": 300, "workbuddy": 300}
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
        from .workbuddy import WorkBuddySource

        self._cache = {}
        self._bursts = {}   # (tool, sid) -> burst 起始时间
        # 阶段事件（优化#13 活动时间线）：(tool, sid) -> [(t, state), ...]。
        # 只记状态切换边沿，用来回答「瓶颈在 想 还是 在 等」。
        self._trace = {}
        self._last_state = {}
        self.sources = [
            ClaudeSource(cfg.root("claude")),
            CodexSource(cfg.root("codex")),
            ZCodeSource(cfg.root("zcode")),
            WorkBuddySource(cfg.root("workbuddy")),
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

        # 阶段事件：对全部活跃会话（不只 top4）记录状态切换边沿。
        # __new__ 直构（单测）不会有这三个属性，惰性补齐。
        if not hasattr(self, "_trace"):
            self._trace, self._last_state = {}, {}
        for k, a in fresh.items():
            if self._last_state.get(k) != a.state:
                self._last_state[k] = a.state
                self._trace.setdefault(k, []).append((now, a.state))

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
        # 清理过期的 burst / trace 记录（_last_state 随会话消失自然失活）
        for k in [k for k, v in self._bursts.items() if now - v > 3600]:
            del self._bursts[k]
        for k in [k for k, v in self._trace.items() if not v or now - v[-1][0] > 3600]:
            del self._trace[k]
        return out

    def trace_for(self, task_id: str, limit: int = 24) -> list:
        """按任务 id 取阶段事件 [{t, s}...]（旧→新，最多 limit 条）。

        id 是 tool:sid 的 sha1 前 8 位——碰撞概率可忽略（4 个活跃会话）。
        找不到（会话已结束超 1h / 新客户端）返回空列表，前端显示占位。
        """
        for (tool, sid), events in self._trace.items():
            if hashlib.sha1(f"{tool}:{sid}".encode()).hexdigest()[:8] == task_id:
                return [{"t": round(t), "s": s} for t, s in events[-limit:]]
        return []

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
