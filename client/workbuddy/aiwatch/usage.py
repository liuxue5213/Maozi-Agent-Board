"""AI 用量扫描：从本地会话日志增量提取 token 消耗与模型（不上传、只读）。

- 今日累计：ZCode rollout / Claude JSONL 按 completedAt/timestamp 过滤当天记录求和
- 焦点会话：最新一条记录的 usage + 模型名（给"本轮"展示）
- 增量读取：按文件偏移缓存，只解析新追加的部分
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path


def _day_of(iso: str) -> str:
    return (iso or "")[:10]


def _zcode_tool_steps(d: dict):
    """rollout 记录里的工具步数与最近输出片段：(n, snippet≤40)。"""
    n = 0
    snip = ""
    for m in (d.get("request") or {}).get("messages") or []:
        if not isinstance(m, dict) or m.get("role") != "tool":
            continue
        n += 1
        c = m.get("content")
        if isinstance(c, str) and c.strip() and not snip:
            snip = " ".join(c.split())[:40]
    return n, snip


def _zcode_last_text(d: dict) -> str:
    """rollout 记录里 AI 的最近回复文本（≤80 字符单行）；无则空串。"""
    msgs = ((d.get("request") or {}).get("messages")) or []
    for m in reversed(msgs):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, list):
            c = " ".join(b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text")
        if isinstance(c, str) and c.strip():
            return " ".join(c.split())[:80]
    return ""


def _claude_last_text(d: dict) -> str:
    """claude assistant 记录的文本块拼接（≤80 字符单行）。"""
    if d.get("type") != "assistant":
        return ""
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list):
        c = " ".join(b.get("text", "") for b in c
                     if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str):
        return ""
    return " ".join(c.split())[:80]


def _zcode_record_usage(d: dict):
    """从一条 rollout 记录提取 (date, total_tokens, out_tokens, model) 或 None。"""
    resp = d.get("response") or {}
    usage = resp.get("usage") or {}
    total = int(usage.get("totalTokens") or 0)
    if not total:
        return None
    out = int(usage.get("outputTokens") or 0)
    model = ((d.get("model") or {}).get("modelId")) or ""
    return _day_of(d.get("completedAt", "")), total, out, model


def _claude_record_usage(d: dict):
    if d.get("type") != "assistant":
        return None
    msg = d.get("message") or {}
    usage = msg.get("usage") or {}
    total = (int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
             + int(usage.get("cache_read_input_tokens") or 0) + int(usage.get("output_tokens") or 0))
    if not total:
        return None
    out = int(usage.get("output_tokens") or 0)
    return _day_of(d.get("timestamp", "")), total, out, msg.get("model") or ""


class _IncFile:
    """增量读取器：记住每个文件的偏移和半行残留。"""

    def __init__(self):
        self.state: dict[str, tuple[int, str]] = {}

    def new_lines(self, path: Path, max_bytes: int = 4 * 1024 * 1024) -> list[str]:
        try:
            size = path.stat().st_size
        except OSError:
            return []
        pos, rem = self.state.get(str(path), (0, ""))
        first_scan = str(path) not in self.state
        if size < pos:
            pos, rem = 0, ""       # 文件被轮转/截断
        # 4MB 尾部截断只用于增量防御；首扫不限——否则大文件前半段的今日
        # 记录永久漏计（实测偏差 -65%：真值 63.4M 只读到 22.1M）
        if not first_scan and size - pos > max_bytes:
            pos = max(0, size - max_bytes)
            rem = ""
        lines = []
        try:
            with open(path, "rb") as f:
                f.seek(pos)
                chunk = f.read()
        except OSError:
            return []
        data = rem.encode("utf-8", "ignore") + chunk
        *complete, tail = data.split(b"\n")
        for raw in complete:
            text = raw.decode("utf-8", "ignore").strip()
            if text:
                lines.append(text)
        self.state[str(path)] = (size, tail.decode("utf-8", "ignore"))
        return lines


class UsageScanner:
    """后台线程：每 60 秒增量扫描，输出今日汇总 + 每工具最新模型。"""

    def __init__(self, roots: dict):
        self.roots = roots
        self._zinc = _IncFile()
        self._cinc = _IncFile()
        self._lock = threading.Lock()
        self.today = {"total": 0}
        self.today_by_model: dict[str, int] = {}   # 今日分模型 token（降序 top6）
        self.sessions_by_tool: dict[str, int] = {}  # 今日会话数（按工具）
        self.last_msg_by_sid: dict[str, str] = {}   # 会话最近消息（≤80 字符）
        self.last_msg_by_tool: dict[str, str] = {}  # 工具级最近消息（焦点显示用）
        self.models: dict[str, str] = {}      # tool -> 最近模型名
        # 会话级今日累计（客户端启动后增量；sid -> {"total","out"}）——
        # 供「本轮 Token」显示。重启清零：语义 = 本次运行期间该会话的用量。
        self.by_sid: dict[str, dict] = {}
        self._thread = None
        self._stop = False
        self.last_error = ""

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def snapshot(self) -> dict:
        with self._lock:
            return {"today": dict(self.today), "models": dict(self.models),
                    "by_model": dict(self.today_by_model),
                    "sessions_by_tool": dict(self.sessions_by_tool),
                    "steps_by_tool": dict(self.steps_by_tool),
                    "last_step_by_tool": dict(self.last_step_by_tool),
                    "last_msg_by_tool": dict(self.last_msg_by_tool),
                    "last_msg_by_sid": dict(self.last_msg_by_sid),
                    "by_sid": {k: dict(v) for k, v in self.by_sid.items()}}

    def _loop(self):
        time.sleep(3)
        while not self._stop:
            try:
                self._scan_once()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:120]
            time.sleep(60)

    def _scan_once(self):
        today = time.strftime("%Y-%m-%d")
        total = 0
        models = {}
        by_model: dict[str, int] = {}
        sids_by_tool: dict[str, set] = {"zcode": set(), "claude": set()}
        steps_by_tool: dict[str, int] = {}
        last_step_by_tool: dict[str, str] = {}

        # ZCode：rollout 增量
        zroot = Path(self.roots.get("zcode", "")) / "rollout"
        if zroot.is_dir():
            for f in zroot.glob("model-io-sess_*.jsonl"):
                if time.time() - f.stat().st_mtime > 3 * 86400:
                    continue
                for line in self._zinc.new_lines(f):
                    try:
                        d = json.loads(line)
                        rec = _zcode_record_usage(d)
                    except Exception:
                        continue
                    if not rec:
                        continue
                    tn, tsnip = _zcode_tool_steps(d)
                    if tn:
                        steps_by_tool["zcode"] = steps_by_tool.get("zcode", 0) + tn
                        if tsnip:
                            last_step_by_tool["zcode"] = tsnip
                    day, r_total, r_out, model = rec
                    if day == today:
                        total += r_total
                        if model:
                            by_model[model] = by_model.get(model, 0) + r_total
                        sid = f.stem.replace("model-io-", "")
                        sids_by_tool["zcode"].add(sid)
                        if r_total:   # 该行有用量 = 模型真实跑过一轮
                            txt = _zcode_last_text(d)
                            if txt:
                                self.last_msg_by_sid[sid] = txt
                                self.last_msg_by_tool["zcode"] = txt
                        acc = self.by_sid.setdefault(
                            sid, {"total": 0, "out": 0})
                        acc["total"] += r_total
                        acc["out"] += r_out
                    if model:
                        models["zcode"] = model

        # Claude：今天的 JSONL 增量
        croot = Path(self.roots.get("claude", ""))
        if croot.is_dir():
            cutoff = time.time() - 86400
            for f in croot.glob("*/*.jsonl"):
                try:
                    if f.stat().st_mtime < cutoff:
                        continue
                except OSError:
                    continue
                for line in self._cinc.new_lines(f):
                    try:
                        d = json.loads(line)
                        rec = _claude_record_usage(d)
                    except Exception:
                        continue
                    if not rec:
                        continue
                    day, r_total, r_out, model = rec
                    if day == today:
                        total += r_total
                        if model:
                            by_model[model] = by_model.get(model, 0) + r_total
                        sid = f.stem
                        sids_by_tool["claude"].add(sid)
                        txt = _claude_last_text(d)
                        if txt:
                            self.last_msg_by_sid[sid] = txt
                            self.last_msg_by_tool["claude"] = txt
                        acc = self.by_sid.setdefault(
                            sid, {"total": 0, "out": 0})
                        acc["total"] += r_total
                        acc["out"] += r_out
                    if model:
                        models["claude"] = model

        with self._lock:
            self.today = {"total": total}
            self.today_by_model = dict(
                sorted(by_model.items(), key=lambda kv: -kv[1])[:6])
            self.sessions_by_tool = {
                tool: len(sids) for tool, sids in sids_by_tool.items() if sids}
            self.steps_by_tool = dict(steps_by_tool)
            self.last_step_by_tool = dict(last_step_by_tool)
            self.last_msg_by_sid = dict(self.last_msg_by_sid)
            self.last_msg_by_tool = dict(self.last_msg_by_tool)
            self.models.update(models)
