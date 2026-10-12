"""AI 任务状态检测引擎 v3：多工具 × 多会话并行监控。

每个来源（Claude Code / Codex / ZCode / WorkBuddy）返回其所有活跃会话的活动列表，
引擎按活跃度排序输出最多 4 条并行任务（协议 AI0~AI3）。
"""
import json
import hashlib
import time
from pathlib import Path

from .. import procwatch

_UNSET = object()

IDLE, THINKING, GENERATING, TOOL, BUILD, DONE, ERROR, WAIT_INPUT, ASK = (
    "IDLE", "THINKING", "GENERATING", "TOOL", "BUILD", "DONE", "ERROR",
    "WAIT_INPUT", "ASK")

_FRESH = {"claude": 45, "codex": 45, "opencode": 45, "zcode": 300, "workbuddy": 300}
# 同时跟踪的会话上限。多编辑器 × 多项目并行是常态（用户实测场景），
# 4 条远远不够看；协议侧仍只下发前 4 条（AI0~AI3），提高上限主要给 Web 用。
MAX_TASKS = 12
# 「刚完成」条的保留窗口与条数：完成靠这条带子表达，而不是靠主列表重排。
_DONE_RETAIN = 1800.0
_DONE_MAX = 8
_WAIT_FRESH = 1800   # 等你回复/等待确认 可保持 30 分钟
_PRIO = {IDLE: 0, DONE: 1, THINKING: 2, GENERATING: 3,
         BUILD: 3, TOOL: 4, WAIT_INPUT: 5, ASK: 6, ERROR: 7}


def _order_key(activity):
    """排序：组间沿用既有的状态优先级（出错 > 待确认 > 等你回复 > 进行中 > 完成），
    组内规则变了两处——

    1. 需要你 / 出错：**等得最久的排最前**。原来是最近更新的排最前，结果一个
       挂了二十分钟的「等你回复」会被刚动过的新任务一直挤下去，永远看不见。
    2. 进行中：按 工具名 → 项目名 **固定顺序**，不再按时间。按时间排意味着
       每次状态变化都会引起跳动，多工具多项目时用户根本定位不到某个项目。

    已完成放在最后：它出现在这里只是为了兼容旧行为（协议仍会下发），
    Web 会把它从主列表拿掉、改由「刚完成」条展示。
    """
    prio = _PRIO.get(activity.state, 0)
    if activity.state in (WAIT_INPUT, ASK, ERROR):
        return (-prio, activity.ts, activity.tool, activity.sid)
    if activity.state == DONE:
        return (-prio, -activity.ts, activity.tool, activity.sid)
    return (-prio, 0.0, activity.tool, activity.project or "", activity.sid)


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
    __slots__ = ("tool", "state", "ts", "sid", "project", "detail", "task", "err",
                 "path")

    def __init__(self, tool, state, ts, sid="", project="", detail="", task="", err="",
                 path=""):
        self.tool, self.state, self.ts = tool, state, ts
        self.sid, self.project = sid, project
        self.detail, self.task, self.err = detail, task, err
        self.path = path            # 项目目录绝对路径（收件箱「打开」用，可为空）


class AIWatcher:
    """聚合三个来源的全部活跃会话。"""

    def __init__(self, cfg, processes=_UNSET):
        from .claude import ClaudeSource
        from .codex import CodexSource
        from .opencode import OpenCodeSource
        from .zcode import ZCodeSource
        from .workbuddy import WorkBuddySource

        # 进程采样器（返回在运行的工具集合；None = 不可用 → 不门控）。
        # 可注入以便测试；cfg.proc_gate=False 可整体关掉。
        if processes is _UNSET:
            self._processes = (procwatch.running_tools
                               if getattr(cfg, "proc_gate", True) else None)
        else:
            self._processes = processes
        self._cache = {}
        self._bursts = {}   # (tool, sid) -> burst 起始时间
        # 阶段事件（优化#13 活动时间线）：(tool, sid) -> [(t, state), ...]。
        # 只记状态切换边沿，用来回答「瓶颈在 想 还是 在 等」。
        self._trace = {}
        self._last_state = {}
        self.sources = [
            ClaudeSource(cfg.root("claude")),
            CodexSource(cfg.root("codex")),
            OpenCodeSource(cfg.root("opencode")),
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
        fresh = self._gate_inactive(fresh, now)
        ranked = sorted(fresh.values(), key=_order_key)[:MAX_TASKS]

        # 阶段事件：对全部活跃会话（不只前几条）记录状态切换边沿。
        # __new__ 直构（单测）不会有这些属性，惰性补齐。
        if not hasattr(self, "_trace"):
            self._trace, self._last_state = {}, {}
        if not hasattr(self, "_done"):
            self._done = []
        for k, a in fresh.items():
            prev = self._last_state.get(k)
            if prev != a.state:
                self._last_state[k] = a.state
                self._trace.setdefault(k, []).append((now, a.state))
                # 完成记录：「刚完成」条的数据源。只在**跨到** DONE 的那一次记，
                # 客户端启动时就已经完成的会话不算（prev is None）。
                if a.state == DONE and prev is not None:
                    self._done = [d for d in self._done if d["key"] != k]
                    self._done.insert(0, {
                        "key": k, "tool": a.tool,
                        "project": sanitize_project(a.project),
                        "id": hashlib.sha1(f"{a.tool}:{a.sid}".encode()).hexdigest()[:8],
                        "at": now,
                    })
        self._done = [d for d in self._done
                      if now - d["at"] <= _DONE_RETAIN][:_DONE_MAX]

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
                "path": getattr(a, "path", "") or "",
                "sid": a.sid,
            })
        # 清理过期的 burst / trace 记录（_last_state 随会话消失自然失活）
        for k in [k for k, v in self._bursts.items() if now - v > 3600]:
            del self._bursts[k]
        for k in [k for k, v in self._trace.items() if not v or now - v[-1][0] > 3600]:
            del self._trace[k]
        return out

    def _gate_inactive(self, fresh: dict, now: float) -> dict:
        """丢掉「进程已不在」的工具的会话。

        日志只说明这个工具写过日志，不说明它还开着。Codex 结束一轮会写
        task_complete → 等你回复，而这一状态的新鲜窗口是 30 分钟：用户关掉
        Codex 之后小屏会一直停在它上面（用户实测反馈，因此加了这一层）。

        采样器不可用（未装 psutil / 扫描失败 / __new__ 直构的单测）时一律
        不门控——宁可多显示一个已关闭的工具，也不能误删正在跑的会话。
        """
        sampler = getattr(self, "_processes", None)
        if sampler is None:
            return fresh
        try:
            running = sampler()
        except Exception:
            return fresh
        if running is None:
            return fresh
        for key in [k for k, a in fresh.items()
                    if procwatch.should_gate(a.tool, running, now - a.ts)]:
            del fresh[key]
        return fresh

    def done_recent(self, now: float | None = None) -> list:
        """最近的完成记录（新→旧），给「刚完成」条用。

        完成提示不靠主列表重排：重排只在用户正好看着的那一刻才可见，而且会
        打乱空间记忆。改成固定位置的一条带子，顺序本身就是时间信息。
        """
        now = time.time() if now is None else now
        # 同时给 age（相对）与 at（绝对）：Web 的「未读」要按绝对时间比，
        # 只靠 age 会随轮询间隔抖动，刚标记已读的条目会又变回未读。
        return [{"id": d["id"], "tool": d["tool"], "project": d["project"],
                 "age": max(0, int(now - d["at"])), "at": round(d["at"], 1)}
                for d in getattr(self, "_done", [])
                if now - d["at"] <= _DONE_RETAIN]

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
