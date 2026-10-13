"""usage scanner 的会话级 Token 累计测试。"""
import json
from datetime import datetime

from workbuddy.aiwatch.usage import (
    UsageScanner,
    _claude_record_usage,
    _codex_record_usage,
    _day_of,
    _zcode_record_usage,
)


def test_zcode_extracts_output_tokens():
    d = {"completedAt": "2026-10-12T10:00:00Z", "model": {"modelId": "glm-4"},
         "response": {"usage": {"totalTokens": 5000, "outputTokens": 800}}}
    day, total, out, model = _zcode_record_usage(d)
    assert (day, total, out, model) == ("2026-10-12", 5000, 800, "glm-4")


def test_claude_extracts_output_tokens():
    d = {"type": "assistant", "timestamp": "2026-10-12T10:00:00Z",
         "message": {"model": "claude-x",
                     "usage": {"input_tokens": 100, "output_tokens": 40,
                               "cache_read_input_tokens": 10}}}
    day, total, out, model = _claude_record_usage(d)
    assert total == 150 and out == 40


def test_zero_usage_returns_none():
    assert _zcode_record_usage({"response": {"usage": {}}}) is None
    assert _claude_record_usage({"type": "assistant", "message": {}}) is None


def test_codex_extracts_single_response_and_ignores_cumulative_event():
    record = {
        "timestamp": "2026-10-13T10:00:00+08:00",
        "type": "token_usage_record",
        "payload": {
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 60,
                "output_tokens": 20,
                "reasoning_output_tokens": 5,
                "total_tokens": 120,
            },
        },
    }
    day, total, out, model, parts = _codex_record_usage(record, "gpt-test")
    assert (day, total, out, model) == ("2026-10-13", 120, 20, "gpt-test")
    assert parts == {"input": 100, "cached": 60, "output": 20, "reasoning": 5}
    assert _codex_record_usage({
        "type": "event_msg",
        "payload": {"type": "token_count", "info": {"total_token_usage": {"total_tokens": 120}}},
    }, "gpt-test") is None


def test_iso_day_uses_explicit_local_offset():
    assert _day_of("2026-10-13T00:30:00+08:00") == "2026-10-13"


def test_codex_scanner_accumulates_without_clearing_or_double_counting(tmp_path):
    root = tmp_path / "codex"
    log = root / "2026" / "10" / "13" / "rollout-demo.jsonl"
    log.parent.mkdir(parents=True)
    now = datetime.now().astimezone().isoformat()

    def append(item):
        with log.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(item) + "\n")

    append({"timestamp": now, "type": "turn_context",
            "payload": {"model": "gpt-test"}})
    append({"timestamp": now, "type": "token_usage_record",
            "payload": {"usage": {
                "input_tokens": 100, "cached_input_tokens": 60,
                "output_tokens": 20, "reasoning_output_tokens": 5,
                "total_tokens": 120}}})
    # 同一轮的累计镜像不能再次计入。
    append({"timestamp": now, "type": "event_msg",
            "payload": {"type": "token_count",
                        "info": {"total_token_usage": {"total_tokens": 120}}}})

    scanner = UsageScanner({"codex": str(root)})
    scanner._scan_once()
    first = scanner.snapshot()
    assert first["today"]["total"] == 120
    assert first["today"]["cached"] == 60
    assert first["models"]["codex"] == "gpt-test"
    assert first["by_model"] == {"gpt-test": 120}
    assert first["sessions_by_tool"] == {"codex": 1}
    assert first["by_sid"]["rollout-demo"]["out"] == 20

    scanner._scan_once()
    assert scanner.snapshot()["today"]["total"] == 120

    append({"timestamp": now, "type": "token_usage_record",
            "payload": {"usage": {
                "input_tokens": 40, "cached_input_tokens": 0,
                "output_tokens": 10, "reasoning_output_tokens": 2,
                "total_tokens": 50}}})
    scanner._scan_once()
    final = scanner.snapshot()
    assert final["today"]["total"] == 170
    assert final["by_model"] == {"gpt-test": 170}
    assert final["by_sid"]["rollout-demo"]["out"] == 30
