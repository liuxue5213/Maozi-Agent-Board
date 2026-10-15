"""节日提醒：预设节日（公历/农历/节气）+ 屏保常显倒计时 + 配置与线路剔除。"""
import base64
import datetime

import pytest

from workbuddy import holiday as H
from workbuddy import lunar
from workbuddy import profile as P


# ---- 农历 → 公历反推（农历节日基础） ----

def test_lunar_to_solar_cny_anchors():
    assert lunar.lunar_to_solar(2024, 1, 1) == datetime.date(2024, 2, 10)
    assert lunar.lunar_to_solar(2025, 1, 1) == datetime.date(2025, 1, 29)
    assert lunar.lunar_to_solar(2026, 1, 1) == datetime.date(2026, 2, 17)


def test_lunar_to_solar_roundtrip():
    d = lunar.lunar_to_solar(2026, 8, 15)
    assert lunar.lunar_date(d.year, d.month, d.day) == "八月十五"


# ---- 单个节日 ----

def test_next_holiday_solar_wraps_next_year():
    name, days, d = H.next_holiday("newyear", datetime.date(2026, 10, 16))
    assert name == "元旦" and d == datetime.date(2027, 1, 1) and days == 77


def test_next_holiday_solar_this_year():
    name, days, d = H.next_holiday("national", datetime.date(2026, 9, 20))
    assert d == datetime.date(2026, 10, 1) and days == 11


def test_next_holiday_on_the_day_is_zero():
    assert H.next_holiday("national", datetime.date(2026, 10, 1))[1] == 0


def test_next_holiday_lunar_spring():
    # 2026 春节 2-17 已过 → 下一年 2027-02-06
    assert H.next_holiday("spring", datetime.date(2026, 3, 1))[2] == datetime.date(2027, 2, 6)


def test_next_holiday_term_qingming():
    name, days, d = H.next_holiday("qingming", datetime.date(2026, 10, 16))
    assert name == "清明节" and d == datetime.date(2027, 4, 5)


def test_unknown_key_returns_none():
    assert H.next_holiday("nope", datetime.date(2026, 10, 16)) is None
    assert H.upcoming([], datetime.date(2026, 10, 16)) is None


# ---- 多个节日：取最近 + 文案 ----

def test_upcoming_picks_nearest():
    # 2026-12-20：元旦 12 天最近（春节 48、国庆 285）
    hit = H.upcoming(["spring", "newyear", "national"], datetime.date(2026, 12, 20))
    assert hit[0] == "元旦"


def test_saver_suffix_day_and_countdown():
    assert H.saver_suffix(["newyear"], datetime.date(2027, 1, 1)) == "元旦快乐"
    assert H.saver_suffix(["newyear"], datetime.date(2026, 12, 31)) == "距元旦还有1天"


def test_saver_suffix_empty_when_none_selected():
    assert H.saver_suffix([], datetime.date(2026, 10, 16)) == ""


def test_catalog_shape():
    cat = H.catalog()
    assert cat and all(set(h) == {"key", "name"} for h in cat)
    assert {"key": "spring", "name": "春节"} in cat


# ---- 屏保行：event_suffix 优先于节气 ----

def test_lunar_line_uses_event_suffix():
    line = lunar.saver_line(2026, 10, 16, event_suffix="距元旦还有77天")
    assert line.startswith("农历") and line.endswith("·距元旦还有77天")


# ---- 配置校验 ----

def test_profile_holidays_validation():
    prof = P.default_profile()
    prof["holidays"] = ["newyear", "spring"]
    assert P.validate_profile(prof)["holidays"] == ["newyear", "spring"]


def test_profile_holidays_reject_unknown_and_dup():
    prof = P.default_profile()
    prof["holidays"] = ["nope"]
    with pytest.raises(ValueError):
        P.validate_profile(prof)
    prof = P.default_profile()
    prof["holidays"] = ["newyear", "newyear"]
    with pytest.raises(ValueError):
        P.validate_profile(prof)


# ---- 纯客户端：线路包剔除 ----

def test_encode_profile_excludes_holidays():
    prof = P.default_profile()
    prof["version"] = 9
    prof["holidays"] = ["newyear"]
    lines = P.encode_profile(prof)
    chunks = [ln.split(",")[3].strip() for ln in lines if ln.startswith("CFG=DATA")]
    payload = base64.b64decode("".join(chunks)).decode("utf-8")
    assert "holidays" not in payload
