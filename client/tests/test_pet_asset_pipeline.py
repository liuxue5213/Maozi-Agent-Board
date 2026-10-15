"""宠物包资产管线（P1-4）：manifest 构建 + 上传预检（CRC 比对 / 源上限）+ 预算。"""
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory

from workbuddy.pet_render import (build_pack_manifest, PET_DECODED_FRAME_KB,
                                  PET_SKIN_SOURCE_CAP_KB, PET_ANIM_CACHE_SLOTS,
                                  PET_SKIN_PSRAM_BUDGET_KB)
from workbuddy.web import (compute_pack_info, read_pack_manifest, write_pack_manifest,
                           petpack_integrity_check, PETPACK_CANON_STATES,
                           PETPACK_OPTIONAL_STATES)


def _mk_pack(td: str, states, content=b"FRAME", big=False):
    d = Path(td) / "petpack_test"
    d.mkdir(exist_ok=True)
    for st in states:
        p = d / f"pet_{st}.jpg"
        data = content + bytes((len(content) + (st.__len__())) % 17)
        if big:
            data = data + b"\x00" * (PET_SKIN_SOURCE_CAP_KB * 1024 + 10)
        p.write_bytes(data)
    return d


def test_build_pack_manifest_crc_and_budget():
    with TemporaryDirectory() as td:
        states = list(PETPACK_CANON_STATES)            # 9 帧
        d = _mk_pack(td, states)
        m = build_pack_manifest("test", d, states, [], {})
        # 每帧 CRC32 与 zlib 实算一致
        for fr in m["frames"]:
            raw = (d / fr["file"]).read_bytes()
            assert fr["crc32"] == f"{zlib.crc32(raw) & 0xFFFFFFFF:08X}"
            assert fr["size_bytes"] == len(raw)
            assert fr["decoded_kb"] == PET_DECODED_FRAME_KB
        assert m["total_decoded_kb"] == len(states) * PET_DECODED_FRAME_KB
        assert m["psram_budget_kb"] == PET_SKIN_PSRAM_BUDGET_KB
        assert m["within_budget"] is True          # 9 帧 < 12 槽
        assert m["cache_pressure"] is False
        assert m["over_source_cap"] == []


def test_budget_and_cache_pressure_over_limit():
    with TemporaryDirectory() as td:
        states = list(PETPACK_CANON_STATES) + list(PETPACK_OPTIONAL_STATES[:4])  # 13 帧
        d = _mk_pack(td, states)
        m = build_pack_manifest("test", d, states, [], {})
        assert len(m["frames"]) == 13
        assert m["cache_pressure"] is True
        assert m["within_budget"] is False        # 13*300=3900 > 3600


def test_source_cap_over_limit():
    with TemporaryDirectory() as td:
        states = list(PETPACK_CANON_STATES[:1])   # 仅 1 帧但超大
        d = _mk_pack(td, states, big=True)
        m = build_pack_manifest("test", d, states, [], {})
        assert m["over_source_cap"] == ["idle"]
        assert m["within_budget"] is True         # 解码尺寸仍小，仅源超限


def test_compute_pack_info_superset_keeps_legacy_keys():
    with TemporaryDirectory() as td:
        present = list(PETPACK_CANON_STATES)
        d = _mk_pack(td, present)
        info = compute_pack_info(d)
        # 向后兼容键
        assert set(info["present"]) == set(present)
        assert info["missing"] == []
        assert info["fallback"] == {}
        # P1-4 新增字段
        assert len(info["frames"]) == len(present)
        assert info["total_decoded_kb"] == len(present) * PET_DECODED_FRAME_KB
        assert info["manifest"] is None           # 无 manifest.json
        assert info["over_source_cap"] == []


def test_integrity_check_no_manifest_is_ok():
    with TemporaryDirectory() as td:
        d = _mk_pack(td, list(PETPACK_CANON_STATES))
        ok, reason = petpack_integrity_check(d)
        assert ok is True and reason == ""


def test_integrity_check_passes_with_matching_manifest():
    with TemporaryDirectory() as td:
        states = list(PETPACK_CANON_STATES)
        d = _mk_pack(td, states)
        live = compute_pack_info(d)["manifest_live"]
        write_pack_manifest(d, live)              # 写一份与磁盘一致的 manifest
        ok, reason = petpack_integrity_check(d)
        assert ok is True, reason


def test_integrity_check_detects_corrupted_frame():
    with TemporaryDirectory() as td:
        states = list(PETPACK_CANON_STATES)
        d = _mk_pack(td, states)
        live = compute_pack_info(d)["manifest_live"]
        write_pack_manifest(d, live)
        # 篡改磁盘文件（manifest 未更新）→ CRC 不符
        (d / "pet_idle.jpg").write_bytes(b"CORRUPTED-DATA-X")
        ok, reason = petpack_integrity_check(d)
        assert ok is False
        assert "idle" in reason and "CRC32" in reason


def test_integrity_check_detects_over_source_cap():
    with TemporaryDirectory() as td:
        states = list(PETPACK_CANON_STATES[:1])
        d = _mk_pack(td, states, big=True)
        ok, reason = petpack_integrity_check(d)
        assert ok is False
        assert "源上限" in reason


def test_integrity_check_missing_pack_dir():
    from workbuddy.app import App
    class _Fake:
        def petpack_base(self):
            return Path("/nonexistent/petpack_base")
    ok, reason = App._preflight_petpack(_Fake(), "ghost")
    assert ok is False and "包不存在" in reason
