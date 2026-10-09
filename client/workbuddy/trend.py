"""行为遥测趋势存储（路线图 v2 P1-4）：把每分钟一拍的系统指标落盘，
供工作台「历史记录」视图画 24h 趋势线。

设计：
- 按天一个 JSONL：~/.workbuddy/trend-YYYY-MM-DD.jsonl，每行
  {"t": epoch, "cpu": int, "mem": int, "disk": int, "tok": int|None, "tasks": int}
- 最小间隔 60s（App.tick 每 1-2.5s 来一次，由本类节流）；
  暂停采集时不喂样本（App 侧保证），避免画冻结数据。
- 保留 7 天：启动时顺手清理过期文件。
- series(hours)：把窗口内样本按 10 分钟桶聚合（均值），空桶丢弃。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

LOG_DIR = Path.home() / ".workbuddy"
MIN_INTERVAL = 60.0
RETAIN_DAYS = 7
BUCKET_SECS = 600


class TrendStore:
    def __init__(self, log_dir: Path = LOG_DIR):
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._last_at = 0.0
        self._prune()

    def _path(self, day: str) -> Path:
        return self.dir / f"trend-{day}.jsonl"

    def _prune(self):
        cutoff = time.time() - RETAIN_DAYS * 86400
        for p in self.dir.glob("trend-*.jsonl"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                continue

    def sample(self, t: dict, tasks: int = 0, now: float | None = None):
        """节流落盘一拍。t 是 tick 出的遥测字典（缺字段容忍）。"""
        now = time.time() if now is None else now
        if now - self._last_at < MIN_INTERVAL:
            return False
        self._last_at = now
        record = {
            "t": int(now),
            "cpu": t.get("cpu"),
            "mem": t.get("mem"),
            "disk": t.get("disk"),
            "tok": (int(t["ait"]) if str(t.get("ait", "")).strip().isdigit()
                    else None),
            "tasks": int(tasks or 0),
        }
        try:
            with open(self._path(time.strftime("%Y-%m-%d")), "a",
                      encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass    # 趋势数据不是关键路径
        return True

    def series(self, hours: int = 24) -> list:
        """窗口内样本按 10 分钟桶聚合（数值取均值），时间升序。"""
        cutoff = time.time() - max(1, min(hours, 24 * 7)) * 3600
        rows = []
        for p in sorted(self.dir.glob("trend-*.jsonl")):
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    try:
                        rec = json.loads(line)
                        if rec.get("t", 0) >= cutoff:
                            rows.append(rec)
                    except json.JSONDecodeError:
                        continue
            except OSError:
                continue
        buckets = {}
        for rec in rows:
            key = int(rec["t"] // BUCKET_SECS * BUCKET_SECS)
            b = buckets.setdefault(key, [])
            b.append(rec)
        out = []
        for key in sorted(buckets):
            group = buckets[key]

            def avg(field):
                vals = [g[field] for g in group if isinstance(g.get(field), (int, float))]
                return round(sum(vals) / len(vals), 1) if vals else None

            toks = [g["tok"] for g in group if isinstance(g.get("tok"), (int, float))]
            out.append({
                "t": key,
                "cpu": avg("cpu"),
                "mem": avg("mem"),
                "disk": avg("disk"),
                "tasks": avg("tasks"),
                # token 是累计量：桶内取最大（而不是均值）
                "tok": max(toks) if toks else None,
            })
        return out


class NullTrend:
    """App 未启用趋势时的空实现（FakeApp/单测兼容）。"""

    def sample(self, *a, **k):
        pass

    def series(self, hours: int = 24):
        return []
