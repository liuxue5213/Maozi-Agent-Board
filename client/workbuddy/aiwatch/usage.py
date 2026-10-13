"""AI 用量扫描：从本地会话日志增量提取 token 消耗与模型（不上传、只读）。

- 今日累计：ZCode / Claude / Codex 本地日志按时间戳过滤当天记录求和
- 焦点会话：最新一条记录的 usage + 模型名（给"本轮"展示）
- 增量读取：按文件偏移缓存，只解析新追加的部分
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


def _day_of(iso: str) -> str:
    """ISO 时间按电脑本地时区归日；解析失败时保留旧格式兼容。"""
    text = str(iso or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().date().isoformat()
    except (TypeError, ValueError):
        return text[:10]


def _hour_of(iso: str) -> str:
    """ISO 时间按电脑本地时区归到小时桶（YYYY-MM-DDTHH）；失败返回空串。

    与 _day_of 同口径（同一时区换算），保证「小时桶之和 = 今日累计」。"""
    text = str(iso or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%Y-%m-%dT%H")
    except (TypeError, ValueError):
        return ""


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


def _codex_record_usage(d: dict, model: str = ""):
    """读取 Codex 单次响应记录；忽略 token_count 累计事件以免重复相加。"""
    if d.get("type") != "token_usage_record":
        return None
    # Codex 正式日志把 usage 放在 payload；保留顶层兼容早期夹具/格式。
    payload = d.get("payload") or {}
    usage = payload.get("usage") or d.get("usage") or {}
    total = int(usage.get("total_tokens") or 0)
    if not total:
        return None
    out = int(usage.get("output_tokens") or 0)
    parts = {
        "input": int(usage.get("input_tokens") or 0),
        "cached": int(usage.get("cached_input_tokens") or 0),
        "output": out,
        "reasoning": int(usage.get("reasoning_output_tokens") or 0),
    }
    return _day_of(d.get("timestamp", "")), total, out, model, parts


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
    """后台线程：每 60 秒增量扫描，输出今日汇总 + 每工具最新模型。

    P1-2 用量透明度：小时桶/按日趋势（持久化，重启不清零）、高消耗会话
    排行、数据来源与最后更新时间。"""

    # 趋势持久化：小时桶保留 48 个、按日累计保留 8 天
    HOURLY_KEEP = 48
    DAILY_KEEP = 8

    def __init__(self, roots: dict, trend_path: Path | None = None):
        self.roots = roots
        self.trend_path = trend_path or Path.home() / ".workbuddy" / "usage-trend.json"
        self._zinc = _IncFile()
        self._cinc = _IncFile()
        self._codex_inc = _IncFile()
        self._codex_model_by_file: dict[str, str] = {}
        self._lock = threading.Lock()
        self._day = ""
        self.today = {"total": 0, "input": 0, "cached": 0,
                      "output": 0, "reasoning": 0}
        self.today_by_model: dict[str, int] = {}   # 今日分模型 token（降序 top6）
        self.sessions_by_tool: dict[str, int] = {}  # 今日会话数（按工具）
        self._session_ids: dict[str, set[str]] = {
            "zcode": set(), "claude": set(), "codex": set()}
        self.last_msg_by_sid: dict[str, str] = {}   # 会话最近消息（≤80 字符）
        self.last_msg_by_tool: dict[str, str] = {}  # 工具级最近消息（焦点显示用）
        self.steps_by_tool: dict[str, int] = {}     # 今日工具调用步数
        self.last_step_by_tool: dict[str, str] = {}  # 最近工具输出片段
        self.models: dict[str, str] = {}      # tool -> 最近模型名
        # 会话级今日累计（客户端启动后增量；sid -> {"total","out"}）——
        # 供「本轮 Token」显示。重启清零：语义 = 本次运行期间该会话的用量。
        self.by_sid: dict[str, dict] = {}
        # P1-2：小时桶（YYYY-MM-DDTHH -> tokens）/按日累计（YYYY-MM-DD -> tokens）
        self.hourly: dict[str, int] = {}
        self.daily: dict[str, int] = {}
        self.seen_by_tool: dict[str, float] = {}  # 每来源最近扫到用量记录的时刻
        self.updated_at = 0.0                     # 最近一次成功扫描完成时刻
        self._load_trend()
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
                    "by_sid": {k: dict(v) for k, v in self.by_sid.items()},
                    # P1-2：趋势 / 排行 / 来源与更新时间
                    "trend_hourly": dict(sorted(self.hourly.items())[-24:]),
                    "trend_daily": dict(sorted(self.daily.items())),
                    "top_sessions": [
                        {"sid": sid, **vals} for sid, vals in
                        sorted(self.by_sid.items(),
                               key=lambda kv: -kv[1].get("total", 0))[:5]],
                    "seen_by_tool": dict(self.seen_by_tool),
                    "updated_at": self.updated_at,
                    "last_error": self.last_error}

    # ---- 趋势持久化（P1-2）----

    def _load_trend(self):
        try:
            d = json.loads(self.trend_path.read_text(encoding="utf-8"))
            self.hourly = {str(k): int(v)
                           for k, v in (d.get("hourly") or {}).items()}
            self.daily = {str(k): int(v)
                          for k, v in (d.get("daily") or {}).items()}
        except Exception:
            self.hourly, self.daily = {}, {}

    def _save_trend(self):
        """原子写（tmp + replace），不占锁调用。"""
        try:
            self.trend_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.trend_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(
                {"hourly": self.hourly, "daily": self.daily},
                ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.trend_path)
        except OSError:
            pass

    @classmethod
    def _prune_trend(cls, hourly: dict, daily: dict) -> tuple[dict, dict]:
        hours = sorted(hourly)[-cls.HOURLY_KEEP:]
        days = sorted(daily)[-cls.DAILY_KEEP:]
        return ({k: hourly[k] for k in hours},
                {k: daily[k] for k in days})

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
        # 增量读取器只返回新增行，所以汇总也必须在同一天内累加；旧实现每次
        # 从 0 重算，第二轮没有新日志时会把“今日用量”错误清成 0。
        # 先在锁内复制稳定快照，扫描期间只改局部变量，最后一次性提交。
        # 首扫 70MB 日志约需 1-2 秒；若直接改 self.by_sid，Web 会看到
        # “会话已有数字但今日仍为 0”的半成品状态。
        with self._lock:
            if self._day != today:
                # 跨日：先把上一日的总量归档进按日趋势，再清今日各累计器
                if self._day and self.today.get("total"):
                    self.daily[self._day] = self.today["total"]
                self._day = today
                self.today = {"total": 0, "input": 0, "cached": 0,
                              "output": 0, "reasoning": 0}
                self.today_by_model = {}
                self.sessions_by_tool = {}
                self._session_ids = {"zcode": set(), "claude": set(), "codex": set()}
                self.steps_by_tool = {}
                self.last_step_by_tool = {}
                self.last_msg_by_sid = {}
                self.last_msg_by_tool = {}
                self.by_sid = {}
            totals = dict(self.today)
            hourly = dict(self.hourly)     # P1-2：趋势拷贝区（扫描期不改真身）
            daily = dict(self.daily)
            seen = dict(self.seen_by_tool)
            models = dict(self.models)
            by_model: dict[str, int] = dict(self.today_by_model)
            steps_by_tool: dict[str, int] = dict(self.steps_by_tool)
            last_step_by_tool: dict[str, str] = dict(self.last_step_by_tool)
            session_ids = {tool: set(ids) for tool, ids in self._session_ids.items()}
            last_msg_by_sid = dict(self.last_msg_by_sid)
            last_msg_by_tool = dict(self.last_msg_by_tool)
            by_sid = {sid: dict(values) for sid, values in self.by_sid.items()}

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
                    day, r_total, r_out, model = rec
                    seen["zcode"] = time.time()
                    if day == today:
                        hk = _hour_of(d.get("completedAt", ""))
                        if hk:
                            hourly[hk] = hourly.get(hk, 0) + r_total
                        tn, tsnip = _zcode_tool_steps(d)
                        if tn:
                            steps_by_tool["zcode"] = steps_by_tool.get("zcode", 0) + tn
                            if tsnip:
                                last_step_by_tool["zcode"] = tsnip
                        totals["total"] += r_total
                        totals["output"] += r_out
                        if model:
                            by_model[model] = by_model.get(model, 0) + r_total
                        sid = f.stem.replace("model-io-", "")
                        session_ids["zcode"].add(sid)
                        if r_total:   # 该行有用量 = 模型真实跑过一轮
                            txt = _zcode_last_text(d)
                            if txt:
                                last_msg_by_sid[sid] = txt
                                last_msg_by_tool["zcode"] = txt
                        acc = by_sid.setdefault(
                            sid, {"total": 0, "out": 0})
                        acc["total"] += r_total
                        acc["out"] += r_out
                    if model and day == today:
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
                    seen["claude"] = time.time()
                    if day == today:
                        hk = _hour_of(d.get("timestamp", ""))
                        if hk:
                            hourly[hk] = hourly.get(hk, 0) + r_total
                        totals["total"] += r_total
                        totals["output"] += r_out
                        usage = (d.get("message") or {}).get("usage") or {}
                        totals["input"] += int(usage.get("input_tokens") or 0)
                        totals["cached"] += (int(usage.get("cache_creation_input_tokens") or 0)
                                               + int(usage.get("cache_read_input_tokens") or 0))
                        if model:
                            by_model[model] = by_model.get(model, 0) + r_total
                        sid = f.stem
                        session_ids["claude"].add(sid)
                        txt = _claude_last_text(d)
                        if txt:
                            last_msg_by_sid[sid] = txt
                            last_msg_by_tool["claude"] = txt
                        acc = by_sid.setdefault(
                            sid, {"total": 0, "out": 0})
                        acc["total"] += r_total
                        acc["out"] += r_out
                    if model and day == today:
                        models["claude"] = model

        # Codex：任务状态监听早已支持；这里补齐同一 rollout 的模型与精确用量。
        # 只累计 token_usage_record（单次响应），绝不累计 token_count 中不断增长的
        # total_token_usage，否则一轮会被重复计算多次。
        xroot = Path(self.roots.get("codex", ""))
        if xroot.is_dir():
            try:
                xfiles = xroot.rglob("rollout-*.jsonl")
                for f in xfiles:
                    try:
                        if time.time() - f.stat().st_mtime > 3 * 86400:
                            continue
                    except OSError:
                        continue
                    fkey = str(f)
                    model = self._codex_model_by_file.get(fkey, "")
                    sid = f.stem
                    for line in self._codex_inc.new_lines(f):
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        payload = d.get("payload") or {}
                        if d.get("type") == "turn_context" and payload.get("model"):
                            model = str(payload["model"])
                        rec = _codex_record_usage(d, model)
                        if not rec:
                            continue
                        day, r_total, r_out, rec_model, parts = rec
                        if day != today:
                            continue
                        seen["codex"] = time.time()
                        hk = _hour_of(d.get("timestamp", ""))
                        if hk:
                            hourly[hk] = hourly.get(hk, 0) + r_total
                        totals["total"] += r_total
                        for key in ("input", "cached", "output", "reasoning"):
                            totals[key] += int(parts.get(key) or 0)
                        if rec_model:
                            by_model[rec_model] = by_model.get(rec_model, 0) + r_total
                            models["codex"] = rec_model
                        session_ids["codex"].add(sid)
                        acc = by_sid.setdefault(
                            sid, {"total": 0, "out": 0, "input": 0,
                                  "cached": 0, "reasoning": 0})
                        acc["total"] += r_total
                        acc["out"] += r_out
                        acc["input"] += parts["input"]
                        acc["cached"] += parts["cached"]
                        acc["reasoning"] += parts["reasoning"]
                    self._codex_model_by_file[fkey] = model
            except OSError:
                pass

        with self._lock:
            self.today = totals
            self.today_by_model = dict(
                sorted(by_model.items(), key=lambda kv: -kv[1])[:6])
            self.sessions_by_tool = {
                tool: len(sids) for tool, sids in session_ids.items() if sids}
            self._session_ids = session_ids
            self.steps_by_tool = dict(steps_by_tool)
            self.last_step_by_tool = dict(last_step_by_tool)
            self.last_msg_by_sid = last_msg_by_sid
            self.last_msg_by_tool = last_msg_by_tool
            self.by_sid = by_sid
            self.models.update(models)
            # P1-2：趋势裁剪提交 + 扫描完成时刻；写盘放锁外
            self.hourly, self.daily = self._prune_trend(hourly, daily)
            self.seen_by_tool = seen
            self.updated_at = time.time()
        self._save_trend()
