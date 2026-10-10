"""ZCode 多会话监听：应用日志事件按 sessionId 分组 + exec/artifacts 实时文件。

~/.zcode/cli/log/zcode-YYYY-MM-DD.jsonl 每条事件带 sessionId/turnId，
可同时区分多个会话、多个项目的并行任务。
"""
import json
import time
from datetime import datetime
from pathlib import Path

from . import (Activity, ASK, DONE, ERROR, GENERATING, THINKING, TOOL, IDLE,
               WAIT_INPUT, newest_file, _PRIO)

_INFLIGHT_CAP = 600.0


def _iso_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


class ZCodeSource:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _log_file(self, cache):
        for off in range(2):
            name = time.strftime("zcode-%Y-%m-%d.jsonl", time.localtime(time.time() - off * 86400))
            p = self.root / "log" / name
            if p.exists():
                return p
        return newest_file(self.root / "log", "zcode-*.jsonl", cache, "zc-log")

    def _log_events(self, path):
        """尾部相关事件 [(epoch, event, sessionId)]，新→旧。"""
        try:
            size = path.stat().st_size
            with open(path, "rb") as f:
                if size > 65536:
                    f.seek(-65536, 2)
                data = f.read()
        except OSError:
            return []
        interesting = {
            "model.request.started", "model.request.completed",
            "tool.call.started", "tool.call.completed",
            "turn.started", "turn.completed",
            # 失败也是状态：turn/model 失败若不识别，屏幕会永远停在
            # 最后一次「生成中」（用户实测：对话结束后屏幕卡生成中不动）
            "turn.failed", "model.request.failed",
        }
        out = []
        for line in reversed(data.splitlines()):
            try:
                d = json.loads(line)
            except Exception:
                continue
            ev = d.get("event", "")
            if ev in interesting:
                out.append((_iso_ts(d.get("timestamp", "")), ev, d.get("sessionId", "")))
        return out

    def poll_all(self, now, cache) -> list:
        """返回多会话活动：per-session 最新事件分类，exec/artifacts 信号覆盖。"""
        base = self.root
        per_sid = {}   # sid -> Activity（保留最新的）

        # 1) exec：每个会话目录独立判断（命令执行中）
        try:
            for d in (base / "exec").glob("sess_*"):
                dm = d.stat().st_mtime
                if now - dm > 40:
                    continue
                log = newest_file(d, "*", cache, f"zc-ex-{d.name}")
                if log is None:
                    continue
                m = log.stat().st_mtime
                if now - m < 15:
                    per_sid[d.name] = Activity("zcode", TOOL, m, sid=d.name,
                                               detail="Bash", task="RUN")
        except OSError:
            pass

        # 2) artifacts：工具结果刚落盘 → 思考中
        try:
            for d in (base / "artifacts").glob("sess_*"):
                if d.name in per_sid and per_sid[d.name].ts > now - 10:
                    continue
                art = newest_file(d, "call_*-tool-result-*.json", cache, f"zc-ar-{d.name}")
                if art is None:
                    continue
                m = art.stat().st_mtime
                if now - m < 10:
                    a = Activity("zcode", THINKING, m, sid=d.name)
                    cur = per_sid.get(d.name)
                    if cur is None or a.ts > cur.ts:
                        per_sid[d.name] = a
        except OSError:
            pass

        # 3) 日志生命周期事件 per sessionId
        lf = self._log_file(cache)
        if lf is not None:
            for ts, ev, sid in self._log_events(lf):
                age = now - ts
                if age < 0 or age > _INFLIGHT_CAP or not sid:
                    continue
                state = None
                if ev == "turn.completed":
                    # 30 秒内算刚完成，之后长期保持"等你回复"
                    state = DONE if age < 30 else WAIT_INPUT
                elif ev == "turn.failed" or ev == "model.request.failed":
                    state = ERROR
                elif ev == "model.request.started":
                    state = GENERATING
                elif ev in ("model.request.completed", "turn.started"):
                    state = THINKING
                if state is None:
                    continue
                if sid in per_sid and per_sid[sid].ts >= ts:
                    continue
                per_sid[sid] = Activity("zcode", state, ts, sid=sid)

        # 4) 等待用户确认：最新模型回复里带 ask/confirm 类工具调用
        ask_sid = self._detect_ask(now, cache)
        if ask_sid:
            cur = per_sid.get(ask_sid)
            if cur is None or _PRIO.get(ASK, 0) > _PRIO.get(cur.state, 0):
                per_sid[ask_sid] = Activity("zcode", ASK, now, sid=ask_sid)

        return list(per_sid.values())

    def _detect_ask(self, now, cache):
        """最新 rollout 记录的 toolCalls 里含 ask/confirm/plan 类工具 → 该会话在等确认。"""
        rollout = newest_file(self.root / "rollout", "model-io-sess_*.jsonl",
                              cache, "zc-rollout")
        if rollout is None:
            return None
        try:
            if now - rollout.stat().st_mtime > 600:
                return None
            with open(rollout, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 8192))
                data = f.read()
            for line in reversed(data.splitlines()):
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                resp = d.get("response") or {}
                calls = resp.get("toolCalls") or []
                for c in calls:
                    name = ""
                    if isinstance(c, dict):
                        fn = c.get("functionName") or c.get("name") or ""
                        name = str(fn).lower()
                    if any(k in name for k in ("ask", "question", "confirm", "plan")):
                        return d.get("sessionId", "")
                return None  # 只看最新一条
        except Exception:
            return None
        return None
