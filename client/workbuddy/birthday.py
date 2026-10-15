"""生日提醒：多身份生日 + 临近倒计时（纯函数，离线，不上网）。

- next_birthday(date, today)      -> (日期, 相隔天数)
- upcoming(birthdays, today, w)   -> 窗口内最近的一个 (name, days, date)
- saver_suffix(birthdays, today)  -> 屏保行后缀，如 "妈妈生日还有12天"

屏保日期行（app 主循环算好、下发协议 lunar 字段）在「临近一个月」时优先展示
生日倒计时，否则回落到节气倒计时——与竞品的 24 节气倒计时共用同一展示位。
"""
from __future__ import annotations

import datetime
import time

MAX_BIRTHDAYS = 8
DEFAULT_WINDOW_DAYS = 30      # 「临近一个月」


def _parse_md(date_str) -> tuple[int, int] | None:
    """'MM-DD' 或 'YYYY-MM-DD' → (月, 日)；非法返回 None。"""
    parts = str(date_str or "").strip().split("-")
    try:
        if len(parts) == 3:
            m, d = int(parts[1]), int(parts[2])
        elif len(parts) == 2:
            m, d = int(parts[0]), int(parts[1])
        else:
            return None
    except (TypeError, ValueError):
        return None
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    return m, d


def next_birthday(date_str, today: datetime.date):
    """下一个该生日的日期与相隔天数（今天=0）；非法/无解返回 None。"""
    md = _parse_md(date_str)
    if not md:
        return None
    m, d = md
    for year in (today.year, today.year + 1):
        try:
            cand = datetime.date(year, m, d)
        except ValueError:          # 例如 2-30 这类不存在的日期
            continue
        delta = (cand - today).days
        if delta >= 0:
            return cand, delta
    return None


def upcoming(birthdays, today: datetime.date, window_days: int = DEFAULT_WINDOW_DAYS):
    """窗口内最近的一个生日 → (name, days, date)；都没有返回 None。

    多个身份时取「最近的那个」；窗口默认 30 天（临近一个月）。
    """
    best = None
    for item in birthdays or []:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        nb = next_birthday(item.get("date", ""), today)
        if not nb:
            continue
        cand, delta = nb
        if delta <= window_days and (best is None or delta < best[1]):
            name = str(item.get("name") or "生日").strip() or "生日"
            best = (name, delta, cand)
    return best


def saver_suffix(birthdays, today: datetime.date,
                 window_days: int = DEFAULT_WINDOW_DAYS) -> str:
    """屏保行后缀：'妈妈生日还有12天' / 当天 '妈妈生日快乐'；无则 ''。"""
    hit = upcoming(birthdays, today, window_days)
    if not hit:
        return ""
    name, days, _ = hit
    return f"{name}生日快乐" if days == 0 else f"{name}生日还有{days}天"


def today_suffix(birthdays, now: float | None = None,
                 window_days: int = DEFAULT_WINDOW_DAYS) -> str:
    """按本地当天算屏保后缀（供 app 主循环直接调用）。"""
    t = time.localtime(now if now is not None else time.time())
    return saver_suffix(birthdays, datetime.date(t.tm_year, t.tm_mon, t.tm_mday),
                        window_days)
