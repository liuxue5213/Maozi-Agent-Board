import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy.aiwatch import TOOL
from workbuddy.aiwatch.codex import CodexSource


def test_resumed_old_codex_session_is_still_detected(tmp_path):
    """Codex 按创建日存文件；跨天恢复时仍写旧目录，不能只扫今天/昨天。"""
    old_day = tmp_path / "2026" / "09" / "30"
    old_day.mkdir(parents=True)
    rollout = old_day / "rollout-old-session.jsonl"
    rollout.write_text(
        json.dumps({"cwd": "/Users/demo/project"}) + "\n" +
        json.dumps({"payload": {"type": "function_call", "name": "shell"}}) + "\n",
        encoding="utf-8",
    )
    now = time.time()
    os.utime(rollout, (now, now))

    acts = CodexSource(tmp_path).poll_all(now, {})

    assert len(acts) == 1
    assert acts[0].state == TOOL
    assert acts[0].project == "project"


def test_current_codex_custom_tool_call_is_detected(tmp_path):
    day = tmp_path / "2026" / "10" / "09"
    day.mkdir(parents=True)
    rollout = day / "rollout-current.jsonl"
    rollout.write_text(
        json.dumps({"cwd": "/Users/demo/current"}) + "\n" +
        json.dumps({"type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec"}}) + "\n",
        encoding="utf-8",
    )
    now = time.time()
    os.utime(rollout, (now, now))

    acts = CodexSource(tmp_path).poll_all(now, {})

    assert len(acts) == 1
    assert acts[0].state == TOOL
    assert acts[0].task == "RUN"
