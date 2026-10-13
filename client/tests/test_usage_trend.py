"""P1-2 用量透明度：小时桶趋势、持久化、跨日归档、裁剪、排行与预算校验。"""
import json
import time
from pathlib import Path

from workbuddy.aiwatch.usage import UsageScanner, _day_of, _hour_of
from workbuddy.profile import default_profile, validate_profile

TODAY = time.strftime("%Y-%m-%d")


def test_hour_of_consistent_with_day():
    """小时桶与日归属同一时区口径：桶前缀必须等于 _day_of 的结果。"""
    for iso in ("2026-10-13T22:00:00Z", "2026-10-13T22:00:00+08:00",
                "2026-10-13T22:00:00", ""):
        hk = _hour_of(iso)
        assert hk == "" or hk[:10] == _day_of(iso)
        assert hk == "" or (len(hk) == 13 and hk[10] == "T")


def _write_claude(root: Path, sid: str, recs: list[dict]):
    d = root / "proj"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{sid}.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


def _claude_rec(ts: str, inp: int, out: int) -> dict:
    return {"type": "assistant", "timestamp": ts,
            "message": {"model": "claude-x",
                        "usage": {"input_tokens": inp, "output_tokens": out}}}


def test_scan_buckets_hourly_top_sessions_and_meta(tmp_path):
    croot = tmp_path / "claude"
    _write_claude(croot, "sess1", [
        _claude_rec(f"{TODAY}T01:10:00+08:00", 1000, 500),
        _claude_rec(f"{TODAY}T01:40:00+08:00", 1000, 500)])
    _write_claude(croot, "sess2", [
        _claude_rec(f"{TODAY}T02:15:00+08:00", 2000, 1000)])
    s = UsageScanner({"claude": str(croot)}, trend_path=tmp_path / "trend.json")
    s._scan_once()
    snap = s.snapshot()
    # 小时桶：01 点两记录共 3000，02 点 3000；之和 = 今日累计
    assert snap["trend_hourly"] == {f"{TODAY}T01": 3000, f"{TODAY}T02": 3000}
    assert snap["today"]["total"] == 6000
    # 排行：两会话 total 相同，断言集合与数值而非顺序细节
    tops = {t["sid"]: t["total"] for t in snap["top_sessions"]}
    assert tops == {"sess1": 3000, "sess2": 3000}
    assert snap["updated_at"] > 0
    assert snap["seen_by_tool"].get("claude", 0) > 0
    assert snap["last_error"] == ""
    # 持久化：文件里能读到同一份小时桶
    saved = json.loads((tmp_path / "trend.json").read_text(encoding="utf-8"))
    assert saved["hourly"][f"{TODAY}T01"] == 3000


def test_day_rollover_archives_daily_and_survives_restart(tmp_path):
    croot = tmp_path / "claude"
    _write_claude(croot, "sessA", [_claude_rec(f"{TODAY}T03:00:00+08:00", 3000, 1000)])
    s = UsageScanner({"claude": str(croot)}, trend_path=tmp_path / "trend.json")
    s._scan_once()
    # 模拟跨日：上一日总量 7777 待归档
    s._day = "2020-01-01"
    s.today = {"total": 7777, "input": 0, "cached": 0, "output": 0, "reasoning": 0}
    s._scan_once()
    assert s.daily.get("2020-01-01") == 7777
    # 重启后从趋势文件恢复按日历史
    s2 = UsageScanner({"claude": str(croot)}, trend_path=tmp_path / "trend.json")
    assert s2.daily.get("2020-01-01") == 7777
    assert s2.snapshot()["trend_daily"]["2020-01-01"] == 7777


def test_prune_trend_keeps_recent_buckets():
    hourly = {f"2026-01-{i // 24:02d}T{i % 24:02d}": i for i in range(60)}
    daily = {f"2026-01-{d:02d}": d for d in range(1, 15)}
    h2, d2 = UsageScanner._prune_trend(hourly, daily)
    assert len(h2) == UsageScanner.HOURLY_KEEP == 48
    assert len(d2) == UsageScanner.DAILY_KEEP == 8
    # 保留的是最新（key 序最大）的桶
    assert max(hourly) in h2 and min(hourly) not in h2
    assert "2026-01-14" in d2 and "2026-01-01" not in d2


def test_codex_breakdown_into_by_sid_and_hourly(tmp_path):
    xroot = tmp_path / "codex" / "2026" / "10" / "13"
    xroot.mkdir(parents=True)
    recs = [
        {"type": "turn_context", "payload": {"model": "codex-x"}},
        {"type": "token_usage_record", "timestamp": f"{TODAY}T05:00:00+08:00",
         "payload": {"usage": {"total_tokens": 1000, "input_tokens": 400,
                               "cached_input_tokens": 300, "output_tokens": 200,
                               "reasoning_output_tokens": 100}}},
    ]
    with open(xroot / "rollout-test.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    s = UsageScanner({"codex": str(tmp_path / "codex")},
                     trend_path=tmp_path / "trend.json")
    s._scan_once()
    snap = s.snapshot()
    assert snap["today"]["input"] == 400
    assert snap["today"]["cached"] == 300
    assert snap["today"]["reasoning"] == 100
    assert snap["trend_hourly"][f"{TODAY}T05"] == 1000
    sid = "rollout-test"
    assert snap["by_sid"][sid]["input"] == 400
    assert snap["models"]["codex"] == "codex-x"


def test_profile_usage_budget_validation():
    def raw(**kw):
        p = default_profile()
        p.update(kw)
        return p

    assert validate_profile(raw(usage={"budget_daily_k": 500}))["usage"]["budget_daily_k"] == 500
    assert validate_profile(raw())["usage"]["budget_daily_k"] == 0          # 默认不设限
    assert validate_profile(raw(usage={"budget_daily_k": -5}))["usage"]["budget_daily_k"] == 0
    assert validate_profile(raw(usage={"budget_daily_k": "abc"}))["usage"]["budget_daily_k"] == 0
    assert validate_profile(raw(usage="bad"))["usage"]["budget_daily_k"] == 0
