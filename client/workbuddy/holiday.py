"""节日提醒：从预设节日里选若干个，屏保**常显**「距 X 还有 N 天」（无临近门槛）。

与生日不同——生日只在临近一个月才出现；节日只要被选中就**一直**显示倒计时。
公历/农历/节气三类规则都能表达（元旦、春节、清明……）。

- catalog()        -> [{"key","name"}]，供 Web 渲染勾选
- next_holiday(k)  -> (name, days, date)
- upcoming(keys)   -> 被选中的里面最近的一个
- saver_suffix(k)  -> "距元旦还有20天" / 当天 "元旦快乐"；无则 ''
"""
from __future__ import annotations

import datetime
import time

from .lunar import lunar_to_solar, term_date

# (key, 名称, 规则)；规则：
#   ("solar", 月, 日)  公历固定
#   ("lunar", 月, 日)  农历固定（lunar_to_solar 反推）
#   ("term", 下标)     节气当天（0=小寒…6=清明…23=冬至）
CATALOG = (
    ("newyear", "元旦", ("solar", 1, 1)),
    ("valentine", "情人节", ("solar", 2, 14)),
    ("labour", "劳动节", ("solar", 5, 1)),
    ("children", "儿童节", ("solar", 6, 1)),
    ("teacher", "教师节", ("solar", 9, 10)),
    ("national", "国庆节", ("solar", 10, 1)),
    ("christmas", "圣诞节", ("solar", 12, 25)),
    ("spring", "春节", ("lunar", 1, 1)),
    ("lantern", "元宵节", ("lunar", 1, 15)),
    ("qingming", "清明节", ("term", 6)),
    ("dragon", "端午节", ("lunar", 5, 5)),
    ("qixi", "七夕", ("lunar", 7, 7)),
    ("midautumn", "中秋节", ("lunar", 8, 15)),
    ("double9", "重阳节", ("lunar", 9, 9)),
)
_BY_KEY = {k: (name, rule) for k, name, rule in CATALOG}
MAX_HOLIDAYS = 12


def keys() -> list[str]:
    return [k for k, _, _ in CATALOG]


def catalog() -> list[dict]:
    return [{"key": k, "name": n} for k, n, _ in CATALOG]


def _holiday_date(rule, year: int):
    kind = rule[0]
    try:
        if kind == "solar":
            return datetime.date(year, rule[1], rule[2])
        if kind == "lunar":
            return lunar_to_solar(year, rule[1], rule[2])
        if kind == "term":
            return term_date(year, rule[1])
    except (ValueError, TypeError, IndexError):
        return None
    return None


def next_holiday(key: str, today: datetime.date):
    """下一个该节日 → (name, days, date)；未知 key 返回 None。"""
    entry = _BY_KEY.get(str(key))
    if not entry:
        return None
    name, rule = entry
    for year in (today.year, today.year + 1):
        d = _holiday_date(rule, year)
        if d and (d - today).days >= 0:
            return name, (d - today).days, d
    return None


def upcoming(keys_, today: datetime.date):
    """被选中的节日里最近的一个 → (name, days, date)；都没有返回 None。"""
    best = None
    for k in keys_ or []:
        nh = next_holiday(k, today)
        if nh and (best is None or nh[1] < best[1]):
            best = nh
    return best


def saver_suffix(keys_, today: datetime.date) -> str:
    """屏保后缀：'距元旦还有20天' / 当天 '元旦快乐'；无则 ''。"""
    hit = upcoming(keys_, today)
    if not hit:
        return ""
    name, days, _ = hit
    return f"{name}快乐" if days == 0 else f"距{name}还有{days}天"


def today_suffix(keys_, now: float | None = None) -> str:
    t = time.localtime(now if now is not None else time.time())
    return saver_suffix(keys_, datetime.date(t.tm_year, t.tm_mon, t.tm_mday))
