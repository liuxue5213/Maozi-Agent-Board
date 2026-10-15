"""ZCode 多会话监听：应用日志事件按 sessionId 分组 + exec/artifacts 实时文件。

~/.zcode/cli/log/zcode-YYYY-MM-DD.jsonl 每条事件带 sessionId/turnId，
可同时区分多个会话、多个项目的并行任务。

项目名提取（之前完全没有 → 设备/Web 全显示「未命名项目」）：
  1. agents/<sid>/agent_*/metadata.json —— 主会话派生过子代理时，元数据里带
     父会话的 cwd/workspaceRoot；
  2. agents/*/agent_*/metadata.json 的 childSessionId —— 子代理会话
     （sess_subagent_*）按此归属父工作区，拿 description 当标签；
  3. rollout/model-io-<sid>.jsonl 转录里的路径频次 —— 兜底，取最常见的
     用户目录下二级目录名当项目名（排除 .zcode 等系统目录）。
结果按 sid 缓存：成功 1 小时、失败 90 秒后重试。
"""
import json
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import (Activity, ASK, DONE, ERROR, GENERATING, THINKING, TOOL, IDLE,
               WAIT_INPUT, newest_file, _PRIO)

_INFLIGHT_CAP = 600.0
_PROJ_HIT_TTL = 3600.0
_PROJ_MISS_TTL = 90.0
# 路径频次统计要排除的目录级：用户主目录下的「容器目录」（Desktop/www/maozi-diy
# 里的项目是 maozi-diy 不是 Desktop），以及系统目录
_CONTAINERS = {"Desktop", "Documents", "Downloads", "Projects", "projects",
               "code", "dev", "work", "src", "repos", "www", "go", "git"}
_NON_PROJECT = {".zcode", ".claude", ".codex", "usr", "opt", "private",
                "tmp", "var", "etc", "Applications", "Library", "System",
                "Users"}
_FULLPATH_RE = re.compile(r"/Users/[^/\s\"\\,。()]*(?:/[^/\s\"\\,。()]+)+")
_PATH_RE = re.compile(r"/Users/[^/\"\\,\s。()]+(?:/([^/\"\\,\s。()]+))?(?:/([^/\"\\,\s。()]+))?(?:/([^/\"\\,\s。()]+))?(?:/([^/\"\\,\s。()]+))?")
# 组件名合法性：转录里会出现 regex 源码等非路径文本，肉眼可辨的目录名
# 只会是「字母数字开头、不含括号引号」的短名——过滤掉解析噪音
_COMP_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,40}")


def _iso_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


class ZCodeSource:
    NAME = "zcode"
    KIND = "session"   # 读取真实会话状态

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

        # 5) 项目名：日志源不给 project，逐会话从 agent 元数据/转录推导
        for a in per_sid.values():
            if not a.project:
                a.project, a.path = self._sid_project(a.sid, cache)

        return list(per_sid.values())

    # ---- 项目名推导（带缓存，结果进共享 cache 字典） ----

    def _sid_project(self, sid: str, cache) -> tuple[str, str]:
        """返回 (项目名, 项目绝对路径)。路径用于收件箱「打开项目」。"""
        key = f"zc-proj-{sid}"
        now = time.time()
        hit = cache.get(key)
        if hit and now - hit[0] < (_PROJ_HIT_TTL if hit[1] else _PROJ_MISS_TTL):
            return hit[1], hit[2]
        name = path = ""
        name, path = self._project_from_agents(sid)
        if not name:
            name, path = self._project_from_rollout(sid)
        cache[key] = (now, name, path)
        return name, path

    def _project_from_agents(self, sid: str) -> tuple[str, str]:
        """从 agent 元数据拿工作区名与绝对路径。子代理会话走 childSessionId 反查。"""
        agents_root = self.root / "agents"
        paths = []
        if sid.startswith("sess_subagent_"):
            paths = sorted(agents_root.glob("*/agent_*/metadata.json"))
        else:
            paths = sorted(agents_root.glob(f"{sid}/agent_*/metadata.json"))
        newest, found = (None, 0.0), ("", "")
        for path in paths:
            try:
                mtime = path.stat().st_mtime
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if sid.startswith("sess_subagent_") \
                    and data.get("childSessionId") != sid:
                continue
            if mtime < newest[1]:
                continue
            workspace = str(data.get("cwd") or data.get("workspaceRoot") or "")
            if workspace:
                newest = (data, mtime)
                # 子代理优先用任务描述当标签（比目录名更可读），
                # 但描述可能是长句，只取前 16 字
                desc = str(data.get("description") or "").strip()
                name = (desc[:16] if desc and sid.startswith("sess_subagent_")
                        else Path(workspace).name)
                found = (name, workspace.rstrip("/"))
        return found

    @staticmethod
    def _project_from_blob(blob: str) -> tuple[str, str]:
        """路径频次按**项目根**归一（深层文件路径归到其项目目录），否则
        反复操作的单个深层文件（如 apk 产物）会以最高频胜出。"""
        counts = Counter()
        for full in _FULLPATH_RE.findall(blob):
            comps = [c for c in full.split("/") if c]
            root_idx = None
            for i, c in enumerate(comps):
                if i <= 1:
                    continue                   # comps[0]='Users'，comps[1]=用户名
                if c in _NON_PROJECT or c in _CONTAINERS:
                    continue
                if not _COMP_RE.fullmatch(c):
                    break                      # 非法组件 = 转录噪音，整条丢弃
                root_idx = i
                break
            if root_idx is None:
                continue
            root = "/" + "/".join(comps[:root_idx + 1])
            counts[root] += 1
        if not counts:
            return "", ""
        best = counts.most_common(1)[0][0]
        return best.rstrip("/").split("/")[-1], best.rstrip("/")

    def _project_from_rollout(self, sid: str) -> tuple[str, str]:
        """转录里路径频次兜底：最常见的工作区路径 = (项目名, 绝对路径)。"""
        path = self.root / "rollout" / f"model-io-{sid}.jsonl"
        try:
            if time.time() - path.stat().st_mtime > 86400 * 3:
                return "", ""                  # 3 天内的会话才值得找
            with open(path, "rb") as f:
                size = path.stat().st_size
                f.seek(max(0, size - 262144))
                blob = f.read().decode("utf-8", "ignore")
        except OSError:
            return "", ""
        return self._project_from_blob(blob)

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
