"""农历日期与二十四节气（纯函数，离线计算，不上网）。

- lunar_date(y, m, d) -> "九月初三"（含闰月「闰」前缀，腊/冬传统叫法）
- solar_term(y, m, d) -> (当前节气名, 距下一节气天数)
- saver_line(y, m, d) -> 屏保日期行的完整文案（含节气倒计时，≤15 天才带）

算法：农历 = 经典压缩年表（1900-2100，基准 1900-01-31 = 正月初一）；
节气 = 通用寿星公式（21 世纪 C 值，Y=年份后两位）。
锚点测试见 tests/test_lunar.py（春节 2024-2026、今天、霜降/冬至）。
"""
from __future__ import annotations

import time

# 经典农历年表：每项 16 进制编码一年（高 4 位闰月月份，其余位大小月）
_LUNAR_INFO = (
    0x04bd8, 0x04ae0, 0x0a570, 0x054d5, 0x0d260, 0x0d950, 0x16554, 0x056a0,
    0x09ad0, 0x055d2, 0x04ae0, 0x0a5b6, 0x0a4d0, 0x0d250, 0x1d255, 0x0b540,
    0x0d6a0, 0x0ada2, 0x095b0, 0x14977, 0x04970, 0x0a4b0, 0x0b4b5, 0x06a50,
    0x06d40, 0x1ab54, 0x02b60, 0x09570, 0x052f2, 0x04970, 0x06566, 0x0d4a0,
    0x0ea50, 0x06e95, 0x05ad0, 0x02b60, 0x186e3, 0x092e0, 0x1c8d7, 0x0c950,
    0x0d4a0, 0x1d8a6, 0x0b550, 0x056a0, 0x1a5b4, 0x025d0, 0x092d0, 0x0d2b2,
    0x0a950, 0x0b557, 0x06ca0, 0x0b550, 0x15355, 0x04da0, 0x0a5b0, 0x14573,
    0x052b0, 0x0a9a8, 0x0e950, 0x06aa0, 0x0aea6, 0x0ab50, 0x04b60, 0x0aae4,
    0x0a570, 0x05260, 0x0f263, 0x0d950, 0x05b57, 0x056a0, 0x096d0, 0x04dd5,
    0x04ad0, 0x0a4d0, 0x0d4d4, 0x0d250, 0x0d558, 0x0b540, 0x0b6a0, 0x195a6,
    0x095b0, 0x049b0, 0x0a974, 0x0a4b0, 0x0b27a, 0x06a50, 0x06d40, 0x0af46,
    0x0ab60, 0x09570, 0x04af5, 0x04970, 0x064b0, 0x074a3, 0x0ea50, 0x06b58,
    0x05ac0, 0x0ab60, 0x096d5, 0x092e0, 0x0c960, 0x0d954, 0x0d4a0, 0x0da50,
    0x07552, 0x056a0, 0x0abb7, 0x025d0, 0x092d0, 0x0cab5, 0x0a950, 0x0b4a0,
    0x0baa4, 0x0ad50, 0x055d9, 0x04ba0, 0x0a5b0, 0x15176, 0x052b0, 0x0a930,
    0x07954, 0x06aa0, 0x0ad50, 0x05b52, 0x04b60, 0x0a6e6, 0x0a4e0, 0x0d260,
    0x0ea65, 0x0d530, 0x05aa0, 0x076a3, 0x096d0, 0x04afb, 0x04ad0, 0x0a4d0,
    0x1d0b6, 0x0d250, 0x0d520, 0x0dd45, 0x0b5a0, 0x056d0, 0x055b2, 0x049b0,
    0x0a577, 0x0a4b0, 0x0aa50, 0x1b255, 0x06d20, 0x0ada0, 0x0b559, 0x06cc0,
    0x0ad5a, 0x02b50, 0x0c950, 0x05455, 0x0b5a0, 0x056d0, 0x0b559, 0x0a4b0,
    0x0aa50, 0x0b255, 0x06d20, 0x0ada2, 0x0ab60, 0x09370, 0x04978, 0x04970,
    0x064b0, 0x068a6, 0x0ea50, 0x06b58, 0x05bc0, 0x0ab60, 0x096d5, 0x092e0,
    0x0c960, 0x0d954, 0x0d4a0, 0x0da50, 0x07552, 0x056a0, 0x0abb7, 0x025d0,
    0x092d0, 0x0cab5, 0x0a950, 0x0b4a0, 0x0baa4, 0x0ad50, 0x055d9, 0x04ba0,
    0x0a5b0, 0x15176, 0x052b0, 0x0a930, 0x07954, 0x06aa0, 0x0ad50, 0x05b52,
    0x04b60, 0x0a6e6, 0x0a4e0, 0x0d260, 0x0ea65, 0x0d530, 0x05aa0, 0x076a3,
    0x096d0, 0x04bd7, 0x04ad0, 0x0a4d0, 0x1d0b6, 0x0d250, 0x0d520, 0x0dd45,
    0x0b5a0, 0x056d0, 0x055b2, 0x049b0, 0x0a577, 0x0a4b0, 0x0aa50, 0x1b255,
    0x06d20, 0x0ada0, 0x0ab60, 0x09574, 0x04970, 0x0d4a0, 0x0d150, 0x0d548,
    0x0b550, 0x056a0, 0x1a5b4, 0x025d0, 0x092d0, 0x0d2b2, 0x0a950, 0x0b557,
    0x06ca0, 0x0b550, 0x15355, 0x04da0, 0x0a5b0, 0x14573, 0x052b0, 0x0a9a8,
    0x0e950, 0x06aa0, 0x0aea6, 0x0ab50, 0x04b60, 0x0aae4, 0x0a570, 0x05260,
    0x0f263, 0x0d950, 0x05b57, 0x056a0, 0x096d0, 0x04dd5, 0x04ad0, 0x0a4d0,
    0x0d4d4, 0x0d250, 0x0d558, 0x0b540, 0x0b5a0, 0x195a6, 0x095b0, 0x049b0,
)

_MONTHS = ("正", "二", "三", "四", "五", "六", "七", "八", "九", "十",
           "冬", "腊")
_DAYS = ("初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八",
         "初九", "初十", "十一", "十二", "十三", "十四", "十五", "十六",
         "十七", "十八", "十九", "二十", "廿一", "廿二", "廿三", "廿四",
         "廿五", "廿六", "廿七", "廿八", "廿九", "三十")

# 通用寿星公式 21 世纪 C 值（小寒→冬至），Y = 年份后两位
_TERM_C = (5.4055, 20.12, 3.87, 18.73, 5.63, 20.646, 4.81, 20.1,
           5.52, 21.04, 5.678, 21.37, 7.108, 22.83, 7.5, 23.13,
           7.646, 23.042, 8.318, 23.438, 7.438, 22.36, 7.18, 21.94)
_TERM_NAMES = ("小寒", "大寒", "立春", "雨水", "惊蛰", "春分", "清明",
               "谷雨", "立夏", "小满", "芒种", "夏至", "小暑", "大暑",
               "立秋", "处暑", "白露", "秋分", "寒露", "霜降", "立冬",
               "小雪", "大雪", "冬至")
# 各节气所在月（小寒/大寒在 1 月，立春/雨水 2 月……冬至 12 月）
_TERM_MONTH = (1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6,
               7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12)


def _month_days(info: int, month: int) -> int:
    """正常农历月 m(1-12) 的天数：经典表位布局 bit(16-m)，1=大月30天。"""
    return 30 if (info >> (16 - month)) & 1 else 29


def _leap_month(info: int) -> int:
    return info & 0xF


def _leap_days(info: int) -> int:
    """闰月天数：bit16，1=30 天（闰月跟在同名月后）。"""
    return 30 if (info >> 16) & 1 else 29


def lunar_date(year: int, month: int, day: int) -> str:
    """公历日期 → 农历「九月初三」样式串（闰月带「闰」字）。超出年表抛 ValueError。"""
    if not 1900 <= year <= 2100:
        raise ValueError("农历年表只覆盖 1900-2100")
    import datetime
    target = datetime.date(year, month, day)
    base = datetime.date(1900, 1, 31)       # 1900 年正月初一
    offset = (target - base).days
    if offset < 0:
        raise ValueError("早于 1900-01-31")
    y = 1900
    while y <= 2100:
        info = _LUNAR_INFO[y - 1900]
        leap = _leap_month(info)
        days = sum(_month_days(info, m) for m in range(1, 13))
        if leap:
            days += _leap_days(info)
        if offset < days:
            break
        offset -= days
        y += 1
    else:
        raise ValueError("超出农历年表")
    info = _LUNAR_INFO[y - 1900]
    leap = _leap_month(info)
    m = 1
    while m <= 12:
        mdays = _month_days(info, m)
        if offset < mdays:
            return f"{_MONTHS[m - 1]}月" + _DAYS[offset]
        offset -= mdays
        if leap and m == leap:
            ldays = _leap_days(info)
            if offset < ldays:
                return f"闰{_MONTHS[leap - 1]}月" + _DAYS[offset]
            offset -= ldays
        m += 1
    raise ValueError(f"农历推算越界：{year}-{month}-{day}")


def _term_day(year: int, index: int) -> tuple[int, int]:
    """第 index 个节气（0=小寒）在某年的 (月, 日)。寿星公式，21 世纪。"""
    y = year % 100
    day = int(y * 0.2422 + _TERM_C[index]) - (y - 1) // 4
    return _TERM_MONTH[index], day


def solar_term(year: int, month: int, day: int) -> tuple[str, int]:
    """公历日期 → (下一节气名, 距今天数)——倒计时目标（当日节气不算在内）。"""
    import datetime
    target = datetime.date(year, month, day)
    # 收集上一年小寒到下一年冬至的全部节气日，找 target 之后第一个
    terms: list[tuple[datetime.date, str]] = []
    for y in (year - 1, year, year + 1):
        for i in range(24):
            tm, td = _term_day(y, i)
            terms.append((datetime.date(y, tm, td), _TERM_NAMES[i]))
    terms.sort(key=lambda x: x[0])
    for d, name in terms:
        if d > target:
            return name, (d - target).days
    raise ValueError("节气推算失败")


def _lunar_year_days(info: int) -> int:
    days = sum(_month_days(info, m) for m in range(1, 13))
    leap = _leap_month(info)
    if leap:
        days += _leap_days(info)
    return days


def lunar_to_solar(year: int, month: int, day: int):
    """农历 (年, 月, 日) → 公历 datetime.date（不含闰月；与 lunar_date 互逆）。

    用于按农历固定的节日（春节/元宵/端午/七夕/中秋/重阳…）。
    """
    import datetime
    if not 1900 <= year <= 2100:
        raise ValueError("农历年表只覆盖 1900-2100")
    offset = sum(_lunar_year_days(_LUNAR_INFO[y - 1900])
                 for y in range(1900, year))
    info = _LUNAR_INFO[year - 1900]
    leap = _leap_month(info)
    for m in range(1, month):
        offset += _month_days(info, m)
        if leap and m == leap:
            offset += _leap_days(info)
    offset += day - 1
    return datetime.date(1900, 1, 31) + datetime.timedelta(days=offset)


def term_date(year: int, index: int):
    """第 index 个节气（0=小寒…6=清明…23=冬至）在 year 年的公历日期。"""
    import datetime
    m, d = _term_day(year, index)
    return datetime.date(year, m, d)


def saver_line(y: int, m: int, d: int, now: float | None = None,
               event_suffix: str = "") -> str:
    """屏保日期行文案：农历必带。

    有 `event_suffix`（调用方算好的生日/节日倒计时，如「妈妈生日还有4天」/
    「距元旦还有20天」）时**优先**用它；否则下一节气 ≤15 天才带节气倒计时。
    """
    line = f"农历{lunar_date(y, m, d)}"
    if event_suffix:
        return f"{line}·{event_suffix}"
    term, days = solar_term(y, m, d)
    if days <= 15:
        line += f"·距{term}还有{days}天"
    return line


def today_line(now: float | None = None, event_suffix: str = "") -> str:
    t = time.localtime(now if now is not None else time.time())
    return saver_line(t.tm_year, t.tm_mon, t.tm_mday, now, event_suffix)
