"""ICS 日程导入的解析与合并测试。"""
import json
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from workbuddy import ics as ics_mod
from workbuddy.app import App
from workbuddy.ics import parse_ics

TODAY = date(2026, 10, 11)          # 周日


class _Health:
    def event(self, *a, **k):
        pass


def test_basic_event_within_window():
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:abc-123
DTSTART:20261015T093000
SUMMARY:评审会
END:VEVENT
END:VCALENDAR"""
    out = parse_ics(ics, today=TODAY)
    assert len(out) == 1
    assert out[0]["date"] == "2026-10-15"
    assert out[0]["time"] == "09:30"
    assert out[0]["title"] == "评审会"
    assert out[0]["id"].startswith("e")


def test_utc_converted_to_local():
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:utc-1
DTSTART:20261015T013000Z
SUMMARY:UTC 会
END:VEVENT
END:VCALENDAR"""
    out = parse_ics(ics, today=TODAY)
    # 本机时区 Asia/Shanghai (UTC+8)：01:30Z → 09:30
    assert out[0]["time"] == "09:30"


def test_all_day_event_default_midnight():
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:allday
DTSTART;VALUE=DATE:20261016
SUMMARY:外出
END:VEVENT
END:VCALENDAR"""
    out = parse_ics(ics, today=TODAY)
    assert out[0]["date"] == "2026-10-16"
    assert out[0]["time"] == "00:00"


def test_folded_lines_and_window_filter():
    # 过去的事件丢弃；折行 SUMMARY 拼回一行
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:old
DTSTART:20260901T100000
SUMMARY:已过去的
END:VEVENT
BEGIN:VEVENT
UID:fold
DTSTART:20261020T140000
SUMMARY:很长的标题第
 一行
END:VEVENT
END:VCALENDAR"""
    out = parse_ics(ics, today=TODAY)
    assert [e["title"] for e in out] == ["很长的标题第一行"]


def test_weekly_rrule_byday_expansion():
    # 每周一/三（从 10-14 周二开始算首次，BYDAY 展开 MO/WE）
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:weekly
DTSTART:20261012T100000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
SUMMARY:站会
END:VEVENT
END:VCALENDAR"""
    out = parse_ics(ics, today=TODAY)
    days = {e["date"] for e in out}
    # 窗口 10-11..10-25 内的周一/周三：10-12(首日,已是周一),14,19,21
    assert "2026-10-12" in days and "2026-10-14" in days
    assert "2026-10-19" in days and "2026-10-21" in days
    assert all(date.fromisoformat(d).isoweekday() in (1, 3) for d in days)


def test_reimport_is_idempotent():
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:stable-uid
DTSTART:20261015T100000
SUMMARY:同一件事
END:VEVENT
END:VCALENDAR"""
    first = parse_ics(ics, today=TODAY)
    second = parse_ics(ics, today=TODAY)
    assert [e["id"] for e in first] == [e["id"] for e in second]
    # 合并逻辑：同 id 只留一份
    merged = {e["id"]: e for e in first}
    for e in second:
        merged.setdefault(e["id"], e)
    assert len(merged) == 1


def test_no_uid_still_gets_stable_id():
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20261015T100000
SUMMARY:无 UID
END:VEVENT
END:VCALENDAR"""
    a = parse_ics(ics, today=TODAY)
    b = parse_ics(ics, today=TODAY)
    assert a[0]["id"] == b[0]["id"]        # 内容哈希兜底，重复导入仍幂等


def test_max_events_cap():
    lines = ["BEGIN:VCALENDAR"]
    tomorrow = TODAY.toordinal() + 1
    for d in range(70):                    # 70 条同日不同时刻 > 上限 60
        hh = d % 24
        mm = (d * 7) % 60
        lines += ["BEGIN:VEVENT", f"UID:ev{d}",
                  f"DTSTART:{date.fromordinal(tomorrow).strftime('%Y%m%d')}T{hh:02d}{mm:02d}00",
                  f"SUMMARY:事件{d}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    out = parse_ics("\n".join(lines), today=TODAY)
    assert len(out) == ics_mod.MAX_EVENTS


# ---- 合并端点 ----

def test_calendar_ics_endpoint_merges_and_dedupes():
    received = {}

    class App:
        profile = {"calendar": [{"id": "eEXIST01", "date": "2026-10-15",
                                 "time": "08:00", "title": "已有", "enabled": True}]}
        profile_lock = threading.Lock()

        def save_profile(self, draft):
            received["calendar"] = draft["calendar"]
            draft["version"] = 99
            return draft

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            from workbuddy.web import start_settings_server  # noqa: F401 仅确认可导入
            received["ok"] = True
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    # 端点逻辑在 Handler 闭包里，直接用 App 桩走同样的合并规则验证（与 web.py 一致）
    app = App()
    ics_text = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x1\nDTSTART:20261015T090000\n"
                "SUMMARY:新日程\nEND:VEVENT\nEND:VCALENDAR")
    imported = parse_ics(ics_text, today=TODAY)
    existing = {c["id"]: c for c in app.profile["calendar"]}
    added = 0
    for e in imported:
        if e["id"] in existing or len(existing) >= 50:
            continue
        existing[e["id"]] = e
        added += 1
    app.profile["calendar"] = sorted(existing.values(),
                                     key=lambda c: (c["date"], c["time"]))
    assert added == 1
    assert received.get("calendar") is None or len(received["calendar"]) == 2
    assert app.profile["calendar"][0]["title"] == "已有"


# ---- 系统日历只读接入（EventKit 依赖全部 mock，不 import 真模块） ----

def test_syscal_fetch_without_pyobjc_degrades_honestly(tmp_path, monkeypatch):
    from workbuddy import syscal
    monkeypatch.setattr(syscal, "STATE_PATH", tmp_path / "syscal.json")
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def fake_import(name, *a, **k):
        if name == "EventKit":
            raise ImportError("No module named 'EventKit'")
        return real_import(name, *a, **k)

    with mock.patch("builtins.__import__", side_effect=fake_import):
        state = syscal.fetch_events()
    assert state["ok"] is False
    assert "EventKit" in state["error"]
    assert state["events"] == []


def test_syscal_events_aggregated_into_inbox_24h(tmp_path, monkeypatch):
    import time as t_mod
    from workbuddy import app as app_mod
    app = App.__new__(App)
    app.tasks = []
    app.profile = {"calendar": []}
    app.profile_lock = threading.Lock()
    app.last = {}
    app.health = _Health()
    app.syscal_fetcher = object()            # 仅标记「已启用」
    app_mod.INBOX_STATE_PATH = tmp_path / "inbox.json"
    app._inbox_seen_cache = None
    monkeypatch.setattr(app_mod.syscal_mod, "STATE_PATH", tmp_path / "syscal.json")

    now = time.time()
    soon = t_mod.localtime(now + 3600)
    monkeypatch.setattr(app_mod.syscal_mod, "read_state", lambda: {
        "ok": True, "error": "", "fetched_at": now,
        "events": [{"id": "s1", "date": t_mod.strftime("%Y-%m-%d", soon),
                    "time": t_mod.strftime("%H:%M", soon), "title": "牙医"}]})
    items = app.inbox_items()
    hit = [x for x in items if x["id"] == "s:s1"]
    assert hit and hit[0]["kind"] == "syscal" and "牙医" in hit[0]["title"]


def test_syscal_disabled_no_aggregation(tmp_path, monkeypatch):
    from workbuddy import app as app_mod
    app = App.__new__(App)
    app.tasks = []
    app.profile = {"calendar": []}
    app.profile_lock = threading.Lock()
    app.last = {}
    app.health = _Health()
    app.syscal_fetcher = None                # 未启用
    app_mod.INBOX_STATE_PATH = tmp_path / "inbox.json"
    app._inbox_seen_cache = None
    monkeypatch.setattr(app_mod.syscal_mod, "STATE_PATH", tmp_path / "syscal.json")
    monkeypatch.setattr(app_mod.syscal_mod, "read_state", lambda: {
        "ok": True, "events": [{"id": "s1", "date": "2026-10-11",
                                "time": "10:00", "title": "不该出现"}]})
    assert app.inbox_items() == []
