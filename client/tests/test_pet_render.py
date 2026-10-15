"""P1-2 收口：canonical 矢量 vs 可选皮肤 单一事实源（pet_render.render_model）。

验证：内置矢量永远可用且零 PSRAM；皮肤受激活/完整/缺帧约束；回落语义正确；
PSRAM 预算常量自洽。
"""
from workbuddy.pet_render import (
    BUILTIN_STATES, PET_SKIN_PSRAM_BUDGET_KB, PET_DECODED_FRAME_KB,
    builtin_descriptor, pack_descriptor, active_pack_roles, render_model,
)

_FULL = {  # 完整皮肤包 info（9 规范帧齐全）
    "present": list(BUILTIN_STATES), "missing": [], "fallback": {},
}
_MISSING = {  # 缺帧皮肤包 info（只有 idle/working）
    "present": ["idle", "working"], "missing": ["thinking", "error"],
    "fallback": {"working_b": "working"},
}


def _profile(day="", night="", weather=""):
    return {"day_pack": day, "night_pack": night, "weather_pack": weather}


def test_builtin_is_canonical_and_free():
    b = builtin_descriptor()
    assert b["canonical"] is True
    assert b["kind"] == "builtin"
    assert b["frames"] == 9
    assert b["psram_kb"] == 0
    assert b["complete"] is True


def test_active_pack_roles_maps_periods():
    roles = active_pack_roles(_profile(day="shiba", night="xingli", weather="xuebai"))
    assert roles.get("shiba") == ["白天"]
    assert set(roles.get("xingli")) == {"夜晚"}
    assert set(roles.get("xuebai")) == {"雨雪"}


def test_pack_descriptor_marks_skin_and_psram():
    p = pack_descriptor("shiba", _FULL, active=False)
    assert p["canonical"] is False
    assert p["kind"] == "skin"
    assert p["complete"] is True
    # 9 帧 × 300KB/帧
    assert p["psram_kb"] == 9 * PET_DECODED_FRAME_KB


def test_render_model_no_active_uses_builtin():
    m = render_model(_profile(), [("shiba", _FULL)])
    assert m["active_kind"] == "builtin"
    assert m["builtin"]["active"] is True
    assert m["skin"] is None
    assert m["fallback_to_builtin"] is False
    assert m["psram_kb"] == 0


def test_render_model_active_complete_skin():
    m = render_model(_profile(day="shiba"), [("shiba", _FULL)])
    assert m["active_kind"] == "skin"
    assert m["skin"]["name"] == "shiba"
    assert m["skin"]["active"] is True
    assert m["builtin"]["active"] is False
    assert m["fallback_to_builtin"] is False
    assert m["psram_kb"] == 9 * PET_DECODED_FRAME_KB


def test_render_model_active_pack_missing_falls_back():
    # 激活的包根本不在列表中（未导入/被删）→ 矢量回落
    m = render_model(_profile(day="ghost"), [("shiba", _FULL)])
    assert m["active_kind"] == "builtin"
    assert m["skin"] is None
    assert m["fallback_to_builtin"] is True


def test_render_model_active_pack_incomplete_falls_back():
    # 激活的包缺帧（不完整）→ 矢量回落
    m = render_model(_profile(night="shiba"), [("shiba", _MISSING)])
    assert m["active_kind"] == "builtin"
    assert m["skin"] is None
    assert m["fallback_to_builtin"] is True


def test_render_model_psram_budget_constant():
    # 12 槽 × 300KB = 3600KB 最坏预算
    assert PET_SKIN_PSRAM_BUDGET_KB == 12 * PET_DECODED_FRAME_KB
    m = render_model(_profile(), [])
    assert m["psram_budget_kb"] == PET_SKIN_PSRAM_BUDGET_KB
