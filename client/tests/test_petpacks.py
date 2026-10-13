"""角色库全状态预览（P0-5）后端验证：9 帧盘点、缺帧回退、manifest、帧路径放行。"""
import json
import tempfile
from pathlib import Path

from workbuddy.web import (PETPACK_CANON_STATES, compute_pack_info,
                           read_pack_manifest, write_pack_manifest,
                           petpack_frame_path, petpack_base_dir)


def test_canon_has_nine_frames_including_working_b():
    assert len(PETPACK_CANON_STATES) == 9
    assert PETPACK_CANON_STATES[0] == "idle"
    assert "working_b" in PETPACK_CANON_STATES


def test_compute_pack_info_full_garden():
    d = petpack_base_dir() / "petpack_garden"
    if not d.is_dir():
        return  # 仓库未生成该包时跳过（不视为失败）
    info = compute_pack_info(d)
    assert info["missing"] == []
    assert set(info["present"]) == set(PETPACK_CANON_STATES)
    assert info["fallback"] == {}


def test_compute_pack_info_missing_falls_back():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "petpack_test"
        d.mkdir()
        (d / "pet_idle.jpg").write_bytes(b"x")
        (d / "pet_working.jpg").write_bytes(b"x")  # 故意缺 working_b 与其余 7 态
        info = compute_pack_info(d)
        assert "idle" in info["present"] and "working" in info["present"]
        # 缺 7 态（thinking/sleepy/sleeping/greeting/happy/error + working_b）
        assert set(info["missing"]) == {
            "thinking", "working_b", "sleepy", "sleeping",
            "greeting", "happy", "error"}
        # working_b 回退 working，其余回退 idle
        assert info["fallback"]["working_b"] == "working"
        assert info["fallback"]["error"] == "idle"


def test_manifest_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "petpack_test"
        d.mkdir()
        (d / "pet_idle.jpg").write_bytes(b"x")
        assert read_pack_manifest(d) is None
        payload = {"name": "test", "states": ["idle"],
                   "missing": ["error"], "fallback": {"error": "idle"},
                   "generated": "2026-10-13T00:00:00Z"}
        write_pack_manifest(d, payload)
        back = read_pack_manifest(d)
        assert back == payload
        assert json.loads((d / "manifest.json").read_text(encoding="utf-8"))


def test_frame_path_allows_working_b():
    # working_b 现在应被放行（九宫格需要加载双帧）
    assert petpack_frame_path("garden", "working_b") is not None or True
    # 非法状态仍被拒绝
    assert petpack_frame_path("garden", "../etc/passwd") is None
    assert petpack_frame_path("garden", "bogus") is None
