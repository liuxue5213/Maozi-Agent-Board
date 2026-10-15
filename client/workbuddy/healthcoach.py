"""Computer-use break reminder. HID activity is not a pedometer."""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path


STATE_MAX_AGE_SECONDS = 15 * 60


class WorkBreakMonitor:
    def __init__(self):
        self.work_seconds = 0.0
        self.last_tick = None
        self.due = False
        self.completed_at = None
        self.state = "off"
        self.idle_seconds = None
        self.snoozed_until = 0.0
        self.last_target = 0

    def snooze(self, now: float, minutes: int = 10) -> bool:
        """Defer an active reminder without claiming the user has rested."""
        if not self.due or minutes < 1 or minutes > 60:
            return False
        self.due = False
        self.snoozed_until = now + minutes * 60
        self.state = "snoozed"
        return True

    def tick(self, now: float, idle_seconds: float, work_min: int,
             rest_min: int) -> bool:
        """Return True only when a new move/rest reminder becomes due.

        Work time accrues only while recent keyboard/mouse use is observed.
        A sufficiently long absence completes a break. Time gaps caused by
        sleep or a stalled client never count as continuous work.
        """
        dt = 0 if self.last_tick is None else max(0, now - self.last_tick)
        self.last_tick = now
        self.idle_seconds = idle_seconds if idle_seconds >= 0 else None
        if work_min != self.last_target:
            if work_min > 0 and self.work_seconds < work_min * 60:
                self.due = False
                self.snoozed_until = 0
            self.last_target = work_min
        if work_min <= 0:
            self.work_seconds = 0
            self.due = False
            self.snoozed_until = 0
            self.state = "off"
            return False
        if idle_seconds < 0:
            self.state = "unavailable"
            return False
        if dt > 30:
            # Serial transfers, debugger pauses and sleep must not add work
            # time. The current HID idle value decides whether a real break
            # happened; a stalled client alone is not evidence of rest.
            dt = 0
        if idle_seconds >= max(1, rest_min) * 60:
            if self.due or self.work_seconds:
                self.completed_at = now
            self.work_seconds = 0
            self.due = False
            self.snoozed_until = 0
            self.state = "rested"
            return False
        if idle_seconds >= 120:
            self.state = "snoozed" if now < self.snoozed_until else ("break" if not self.due else "due")
            return False
        self.work_seconds += min(dt, 30)
        if now < self.snoozed_until:
            self.state = "snoozed"
            return False
        self.snoozed_until = 0
        self.state = "due" if self.due else "working"
        if not self.due and self.work_seconds >= work_min * 60:
            self.due = True
            self.state = "due"
            return True
        return False

    def snapshot(self, work_min: int, rest_min: int) -> dict:
        return {"state": self.state, "due": self.due,
                "work_minutes": int(self.work_seconds // 60),
                "work_seconds": int(self.work_seconds),
                "rest_seconds": int(self.idle_seconds or 0) if self.state in ("break", "due", "rested") else 0,
                "work_target_minutes": work_min, "rest_target_minutes": rest_min,
                "snoozed_until": self.snoozed_until,
                "step_count": None, "step_source": "unavailable"}


def load_break_state(path: Path, now: float | None = None) -> WorkBreakMonitor:
    """Restore a recent session, never counting time while the client was off."""
    monitor = WorkBreakMonitor()
    now = time.time() if now is None else now
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        saved_at = float(data["saved_at"])
        work_seconds = float(data["work_seconds"])
        snoozed_until = float(data.get("snoozed_until", 0))
        last_target = int(data["last_target"])
        if (data.get("schema") != 1 or not all(map(math.isfinite,
                (saved_at, work_seconds, snoozed_until)))
                or not 0 <= now - saved_at <= STATE_MAX_AGE_SECONDS
                or not 0 <= work_seconds <= 24 * 3600
                or not 0 <= last_target <= 240
                or not 0 <= snoozed_until <= now + 3600):
            return monitor
        monitor.work_seconds = work_seconds
        monitor.due = data.get("due") is True
        monitor.snoozed_until = snoozed_until if snoozed_until > now else 0
        monitor.last_target = last_target
        monitor.last_tick = now
        monitor.state = ("snoozed" if monitor.snoozed_until else
                         "due" if monitor.due else "working")
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        pass
    return monitor


def save_break_state(path: Path, monitor: WorkBreakMonitor, now: float | None = None):
    """Atomically save the small reminder state for quick client restarts."""
    now = time.time() if now is None else now
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps({
            "schema": 1, "saved_at": now,
            "work_seconds": monitor.work_seconds,
            "due": monitor.due,
            "snoozed_until": monitor.snoozed_until,
            "last_target": monitor.last_target,
        }), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
