"""本地健康日志：只记状态与计数，不含任务名、会话内容、路径（PRD 隐私约束）。

按天写 ~/.workbuddy/usage-YYYY-MM-DD.jsonl，单日超过上限后静默丢弃（环形保护）。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

LOG_DIR = Path.home() / ".workbuddy"
DAILY_CAP = 3000  # 单日事件上限，防止异常风暴写爆磁盘


class HealthLog:
    def __init__(self):
        self.day = ""
        self.path: Path | None = None
        self.count = 0
        self._lock = threading.Lock()   # 看门狗线程与采集循环会并发写日志

    def _rotate(self):
        today = time.strftime("%Y-%m-%d")
        if today != self.day:
            self.day = today
            self.path = LOG_DIR / f"usage-{today}.jsonl"
            self.count = 0

    def event(self, kind: str, **fields):
        with self._lock:
            try:
                self._rotate()
                if self.count >= DAILY_CAP or self.path is None:
                    return
                record = {"ts": time.strftime("%H:%M:%S"), "kind": kind}
                record.update(fields)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                self.count += 1
            except OSError:
                pass  # 日志失败绝不能影响主功能

    def recent_lines(self, days: int = 7) -> list[str]:
        """导出诊断包用：最近 N 天的日志行。"""
        out = []
        now = time.time()
        for offset in range(days):
            day = time.strftime("%Y-%m-%d", time.localtime(now - offset * 86400))
            path = LOG_DIR / f"usage-{day}.jsonl"
            if path.exists():
                try:
                    out.append(path.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue
        return out
