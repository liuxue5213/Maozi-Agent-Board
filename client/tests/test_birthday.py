"""生日提醒：多身份生日 + 临近一个月倒计时（纯函数 + 配置校验 + 屏保行 + 线路剔除）。"""
import base64
import datetime

import pytest

from workbuddy import birthday as B
from workbuddy import lunar
from workbuddy import profile as P


# ---- 日期推算 ----

def test_next_birthday_basic():
    today = datetime.date(2026, 10, 16)
    d, days = B.next_birthday("10-20", today)
    assert d == datetime.date(2026, 10, 20) and days == 4


def test_next_birthday_today_is_zero():
    today = datetime.date(2026, 5, 12)
    assert B.next_birthday("05-12", today)[1] == 0


def test_next_birthday_wraps_to_next_year():
    today = datetime.date(2026, 12, 30)
    d, days = B.next_birthday("01-02", today)
    assert d == datetime.date(2027, 1, 2) and days == 3


def test_next_birthday_accepts_full_date():
    today = datetime.date(2026, 10, 16)
    assert B.next_birthday("1990-03-01", today)[0] == datetime.date(2027, 3, 1)


def test_next_birthday_invalid():
    today = datetime.date(2026, 10, 16)
    assert B.next_birthday("", today) is None
    assert B.next_birthday("13-40", today) is None
    assert B.next_birthday("bad", today) is None


# ---- 多个身份：窗口过滤 + 取最近 ----

def test_upcoming_window_and_nearest():
    today = datetime.date(2026, 10, 16)
    bdays = [{"name": "妈妈", "date": "11-01", "enabled": True},   # 16 天
             {"name": "爸爸", "date": "10-20", "enabled": True},   # 4 天 → 最近
             {"name": "朋友", "date": "12-20", "enabled": True}]   # 65 天，超窗
    hit = B.upcoming(bdays, today, 30)
    assert hit[0] == "爸爸" and hit[1] == 4


def test_upcoming_skips_disabled():
    today = datetime.date(2026, 10, 16)
    assert B.upcoming([{"name": "妈妈", "date": "10-20", "enabled": False}],
                      today, 30) is None


def test_upcoming_empty():
    assert B.upcoming([], datetime.date(2026, 10, 16), 30) is None
    assert B.upcoming(None, datetime.date(2026, 10, 16), 30) is None


def test_saver_suffix_texts():
    today = datetime.date(2026, 10, 16)
    assert B.saver_suffix([{"name": "妈妈", "date": "10-20", "enabled": True}],
                          today) == "妈妈生日还有4天"
    assert B.saver_suffix([{"name": "妈妈", "date": "10-16"}], today) == "妈妈生日快乐"
    assert B.saver_suffix([{"name": "妈妈", "date": "12-20"}], today) == ""


# ---- 屏保行：生日优先于节气 ----

def test_lunar_line_prioritizes_birthday():
    line = lunar.saver_line(2026, 10, 16, birthday_suffix="妈妈生日还有4天")
    assert line.startswith("农历") and line.endswith("·妈妈生日还有4天")
    # 无生日后缀时仍是「农历…」形态
    assert lunar.saver_line(2026, 10, 16).startswith("农历")


# ---- 配置校验 ----

def _valid(**birthdays_kw):
    prof = P.default_profile()
    prof["birthdays"] = birthdays_kw.get("items", [])
    return prof


def test_profile_birthdays_validation_normalizes_md():
    out = P.validate_profile(_valid(items=[
        {"id": "b1", "name": "妈妈", "date": "05-12", "enabled": True},
        {"id": "b2", "name": "爸爸", "date": "1990-03-01"}]))
    assert out["birthdays"][0]["date"] == "05-12"
    assert out["birthdays"][1]["date"] == "03-01"     # 年份被剥离


def test_profile_birthday_requires_name():
    with pytest.raises(ValueError):
        P.validate_profile(_valid(items=[{"id": "b1", "name": "", "date": "05-12"}]))


def test_profile_birthday_bad_date():
    with pytest.raises(ValueError):
        P.validate_profile(_valid(items=[{"id": "b1", "name": "妈妈", "date": "13-40"}]))


def test_profile_birthday_id_unique_and_cap():
    with pytest.raises(ValueError):
        P.validate_profile(_valid(items=[{"id": "b1", "name": "妈", "date": "01-01"},
                                         {"id": "b1", "name": "爸", "date": "02-02"}]))
    with pytest.raises(ValueError):
        P.validate_profile(_valid(items=[{"id": f"b{i}", "name": "x", "date": "01-01"}
                                         for i in range(9)]))


# ---- 生日是纯客户端：下发线路包必须剔除 ----

def test_encode_profile_excludes_birthdays():
    prof = P.default_profile()
    prof["version"] = 7
    prof["birthdays"] = [{"id": "b1", "name": "妈妈", "date": "05-12"}]
    lines = P.encode_profile(prof)
    chunks = [ln.split(",")[3].strip() for ln in lines if ln.startswith("CFG=DATA")]
    payload = base64.b64decode("".join(chunks)).decode("utf-8")
    assert "birthdays" not in payload
    assert "妈妈" not in payload          # 称呼也不应出现在串口载荷里
