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


def _zcode_record_usage(d: dict):
    """从一条 rollout 记录提取 (date, total_tokens, model) 或 None。"""
    resp = d.get("response") or {}
    usage = resp.get("usage") or {}
    total = int(usage.get("totalTokens") or 0)
    if not total:
        return None
    model = ((d.get("model") or {}).get("modelId")) or ""
    return _day_of(d.get("completedAt", "")), total, model


def _claude_record_usage(d: dict):
    if d.get("type") != "assistant":
        return None
    msg = d.get("message") or {}
    usage = msg.get("usage") or {}
    total = (int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
             + int(usage.get("cache_read_input_tokens") or 0) + int(usage.get("output_tokens") or 0))
    if not total:
        return None
    return _day_of(d.get("timestamp", "")), total, msg.get("model") or ""


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
        if size < pos:
            pos, rem = 0, ""       # 文件被轮转/截断
        if size - pos > max_bytes:
            pos = max(0, size - max_bytes)   # 首次扫描大文件只看尾部
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
        self.models: dict[str, str] = {}      # tool -> 最近模型名
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
            return {"today": dict(self.today), "models": dict(self.models)}

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

        # ZCode：rollout 增量
        zroot = Path(self.roots.get("zcode", "")) / "rollout"
        if zroot.is_dir():
            for f in zroot.glob("model-io-sess_*.jsonl"):
                if time.time() - f.stat().st_mtime > 3 * 86400:
                    continue
                for line in self._zinc.new_lines(f):
                    try:
                        rec = _zcode_record_usage(json.loads(line))
                    except Exception:
                        continue
                    if not rec:
                        continue
                    day, r_total, model = rec
                    if day == today:
                        total += r_total
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
                        rec = _claude_record_usage(json.loads(line))
                    except Exception:
                        continue
                    if not rec:
                        continue
                    day, r_total, model = rec
                    if day == today:
                        total += r_total
                    if model:
                        models["claude"] = model

        with self._lock:
            self.today = {"total": total}
            self.models.update(models)
