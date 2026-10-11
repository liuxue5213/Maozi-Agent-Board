"""ICS（iCalendar）日程文件解析 → 客户端 calendar 条目。

里程碑 C 第一步：本地日历导出成 .ics 后一键导入，替代逐条手抄。
只做「够用且诚实」的子集：
  · VEVENT 的 DTSTART（本地时间/UTC/纯日期）、SUMMARY、UID、RRULE；
  · RRULE 展开 DAILY / WEEKLY(BYDAY) 常见规则，窗口默认未来 14 天——
    月/年重复只取首次出现（文档里注明，不假装支持全量）；
  · 时区：带 TZID 的本地时间直接取墙上时间（用户的日历就是用户的时区）；
    结尾 Z 的 UTC 换算成机器本地时间。
条目 id 由 UID（或内容哈希）派生 → 同一文件重复导入天然幂等。
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

WINDOW_DAYS = 14
MAX_EVENTS = 60            # 单次导入上限（validate 的 calendar 上限是 50，留余量给合并去重）

_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _unfold(text: str) -> list[str]:
    """ICS 折行（CRLF + 空格/制表前缀）展开成逻辑行。"""
    out: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        elif raw.strip():
            out.append(raw)
    return out


def _prop(line: str) -> tuple[str, dict, str]:
    """"DTSTART;TZID=Asia/Shanghai:20261011T090000" → ("DTSTART", {TZID:...}, value)"""
    head, _, value = line.partition(":")
    parts = head.split(";", 1)
    name = parts[0].upper()
    params = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
    return name, params, value.strip()


def _parse_dt(value: str, params: dict) -> datetime | date:
    """DTSTART 值 → naive 本地 datetime 或 date（全天）。"""
    value = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date()
    if value.endswith("Z"):
        dt = datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(
            tzinfo=timezone.utc)
        return dt.astimezone().replace(tzinfo=None)   # UTC → 机器本地（naive）
    return datetime.strptime(value, "%Y%m%dT%H%M%S")   # 本地/带 TZID：取墙上时间


def _rrule_days(rule_value: str) -> list[str]:
    match = re.search(r"BYDAY=([A-Z,]+)", rule_value)
    return [d.strip().upper() for d in match.group(1).split(",")] if match else []


def _expand(start: datetime | date, rule_value: str, window_end: date) -> Iterator[date]:
    """RRULE 展开成窗口内的日期序列（DAILY/WEEKLY；其余规则只返回首日）。"""
    first = start.date() if isinstance(start, datetime) else start
    if first > window_end:
        return
    yield first
    if not isinstance(start, datetime):
        return                                # 全天事件不展开重复（保守）
    freq_m = re.search(r"FREQ=(DAILY|WEEKLY)", rule_value)
    if not freq_m:
        return
    interval_m = re.search(r"INTERVAL=(\d+)", rule_value)
    interval = max(1, int(interval_m.group(1))) if interval_m else 1
    count_m = re.search(r"COUNT=(\d+)", rule_value)
    count = int(count_m.group(1)) if count_m else 60
    until_m = re.search(r"UNTIL=(\d{8})", rule_value)
    until = (datetime.strptime(until_m.group(1), "%Y%m%d").date()
             if until_m else window_end)
    bydays = [(_WEEKDAYS[d], d) for d in _rrule_days(rule_value)] or None
    step = timedelta(days=interval)
    cur, emitted = first, 1
    while True:
        cur += step
        if cur > window_end or cur > until or emitted >= count:
            return
        if bydays:
            for wd, _ in bydays:
                day = cur + timedelta(days=(wd - cur.weekday()) % 7)
                if first <= day <= window_end and day <= until:
                    yield day
        else:
            yield cur
        emitted += 1


def parse_ics(text: str, today: date | None = None) -> list[dict]:
    """ICS 文本 → calendar 条目 [{id, date, time, title, enabled}]。

    窗口 = 今天（或传入的 today）到 +WINDOW_DAYS 天；按开始时间排序、
    去掉同 id 重复，超过 MAX_EVENTS 条截断（返回的是裁剪后的列表）。
    """
    today = today or date.today()
    window_end = today + timedelta(days=WINDOW_DAYS)
    entries: dict[str, dict] = {}
    block: list[str] = []
    for line in _unfold(text) + ["END:VCALENDAR"]:
        if line.strip().upper().startswith("BEGIN:VEVENT"):
            block = []
            continue
        if line.strip().upper().startswith("END:VEVENT"):
            entry = _event_to_entries(block, today, window_end)
            for e in entry:
                entries.setdefault(e["id"], e)
            if len(entries) >= MAX_EVENTS:
                break
            continue
        if block is not None and line.strip():
            block.append(line)
    return sorted(entries.values(), key=lambda e: (e["date"], e["time"]))[:MAX_EVENTS]


def _event_to_entries(block: list[str], today: date, window_end: date) -> list[dict]:
    props: dict[str, tuple[dict, str]] = {}
    for line in block:
        name, params, value = _prop(line)
        props.setdefault(name, (params, value))
    summary = props.get("SUMMARY", ({}, ""))[1].strip() or "日程"
    uid = props.get("UID", ({}, ""))[1].strip()
    if "DTSTART" not in props:
        return []
    try:
        start = _parse_dt(props["DTSTART"][1], props["DTSTART"][0])
    except ValueError:
        return []
    rule = props.get("RRULE", ({}, ""))[1]
    base_id = hashlib.md5((uid or f"{start}|{summary}").encode("utf-8"))
    out = []
    for day in _expand(start, rule, window_end):
        if day < today:                       # 已开始的例外不再进日历
            continue
        time_s = (start.strftime("%H:%M") if isinstance(start, datetime) else "00:00")
        eid = ("e" + base_id.hexdigest()[:11] + day.strftime("%m%d"))[:16]
        title = re.sub(r"[\r\n,=]", " ", summary)[:48]
        out.append({"id": eid, "date": day.isoformat(),
                    "time": time_s, "title": title, "enabled": True})
    return out
