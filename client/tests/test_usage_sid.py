"""usage scanner 的会话级 Token 累计测试。"""
import json
from workbuddy.aiwatch.usage import _zcode_record_usage, _claude_record_usage


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
