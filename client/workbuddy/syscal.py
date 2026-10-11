"""系统日历只读接入（macOS EventKit / PyObjC）。

设计约束（里程碑 C 第二步）：
  · **只读**：系统日历事件不写进 profile（与手工/ICS 条目的删除语义分离），
    落盘到 ~/.workbuddy/syscal.json 供收件箱聚合与 Web 只读展示；
  · 默认关闭，用户在设置里打开时才首次访问日历（TCC 授权弹窗只出现一次）；
  · 授权被拒 / 未装 pyobjc-framework-EventKit / 超时 → 诚实降级为
    {ok:false, error:...}，绝不影响手工与 ICS 日程；
  · 后台线程 10 分钟一拉（kick 可立即刷新），拉取异常只记状态不抛出。

依赖（可选）：pip install pyobjc-framework-EventKit ; sys_platform == "darwin"
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

STATE_PATH = Path.home() / ".workbuddy" / "syscal.json"
FETCH_INTERVAL = 600.0        # 10 分钟
WINDOW_DAYS = 14
FETCH_TIMEOUT = 25.0          # 含首次 TCC 授权弹窗的用户等待


def read_state() -> dict:
    """读最近一次拉取结果（无文件/损坏返回中性空状态）。"""
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("ok", False)
            data.setdefault("error", "")
            data.setdefault("events", [])
            data.setdefault("fetched_at", 0)
            return data
    except (OSError, ValueError):
        pass
    return {"ok": False, "error": "", "events": [], "fetched_at": 0}


def _write_state(state: dict):
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False),
                              encoding="utf-8")
    except OSError:
        pass


def _ek_datetime(dt: datetime):
    import EventKit
    return EventKit.NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


def fetch_events(window_days: int = WINDOW_DAYS) -> dict:
    """EventKit 拉取窗口内事件 → {ok, error, events:[{id,date,time,title}]}。

    纯函数式入口（无线程），失败返回 ok:false 与人类可读原因。
    """
    try:
        import EventKit
    except ImportError:
        return {"ok": False, "error": "未安装 pyobjc-framework-EventKit",
                "events": [], "fetched_at": time.time()}

    store = EventKit.EKEventStore.alloc().init()
    granted = {"ok": None}
    done = threading.Semaphore(0)

    def handler(ok, error):     # EventKit 在内部队列回调，信号量等它
        granted["ok"] = bool(ok)
        if not ok and error:
            granted["error"] = str(error.localizedDescription() or "")
        done.release()

    store.requestAccessToEntityType_completion_(0, handler)   # 0 = EKEntityTypeEvent
    if not done.acquire(timeout=FETCH_TIMEOUT):
        return {"ok": False, "error": "日历授权等待超时", "events": [],
                "fetched_at": time.time()}
    if not granted["ok"]:
        return {"ok": False,
                "error": granted.get("error") or "日历访问被拒绝（系统设置 → 隐私与安全性 → 日历）",
                "events": [], "fetched_at": time.time()}

    now = datetime.now().replace(second=0, microsecond=0)
    end = now + timedelta(days=window_days)
    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
        _ek_datetime(now), _ek_datetime(end), None)
    found = store.eventsMatchingPredicate(predicate) or []
    events = []
    for e in found:
        try:
            epoch = e.startDate().timeIntervalSince1970()
            local = datetime.fromtimestamp(epoch)
            title = str(e.title() or "")
            ext = str(e.calendarItemExternalIdentifier() or "")
            key = ext or f"{epoch}|{title}"
            eid = "s" + hashlib.md5(key.encode("utf-8")).hexdigest()[:11]
            events.append({"id": eid, "date": local.strftime("%Y-%m-%d"),
                           "time": local.strftime("%H:%M"), "title": title[:48]})
        except Exception:
            continue                    # 单条坏事件不拖垮整批
    events.sort(key=lambda e: (e["date"], e["time"]))
    return {"ok": True, "error": "", "events": events, "fetched_at": time.time()}


def refresh() -> dict:
    state = fetch_events()
    _write_state(state)
    return state


class SysCalFetcher:
    """后台定时拉取线程（10 分钟；kick 可立即触发，供设置页「立即刷新」）。"""

    def __init__(self):
        self._stop = False
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="workbuddy-syscal")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True
        self._wake.set()

    def kick(self):
        self._wake.set()

    def _loop(self):
        time.sleep(3)               # 等主循环起完（遥测/串口优先）
        while not self._stop:
            try:
                refresh()
            except Exception:
                pass                # 拉取失败只影响 freshness，不抛出
            self._wake.wait(FETCH_INTERVAL)
            self._wake.clear()
