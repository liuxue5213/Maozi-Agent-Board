"""P1-1 活动舞台：固件与 Web 预览同源几何/行为静态校验。

迷你角色几何（24×30 @ (7,14)、工具标签 x=34 宽 92）在 firmware/ui.cpp 与
web_assets/app.js 两端必须一致（预览与真机同源约定）；等待/待确认的「!」
徽标与运行眨眼为两端共有的行为标记。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = (ROOT / "firmware" / "src" / "ui.cpp").read_text(encoding="utf-8")
JS = (ROOT / "client" / "workbuddy" / "web_assets" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "client" / "workbuddy" / "web_assets" / "app.css").read_text(encoding="utf-8")


def test_firmware_has_stage_parts():
    assert "struct MiniPet" in UI
    assert "pet_mini[10][3]" in UI  # AI 页面各自持有 3 只，不跨页借用控件
    assert "static void draw_pet_mini(MiniPet& p, lv_obj_t* parent, int x, int y);" in UI
    assert "pet_mini_apply(MiniPet& p, const AITask& task, bool blink_on)" in UI
    # AI 页 3 行：状态点已被迷你角色取代
    assert "draw_pet_mini(pet_mini[widget_page_slot(&view)][i], row, 7, 14);" in UI
    assert "pet_mini_apply(pet_mini[widget_page_slot(&view)][row], task," in UI


def test_firmware_tool_page_keeps_dot():
    # 工具过滤页（make_ai_tool_page）5 行仍是状态点，只有「全部」页换舞台
    assert "view.row_dot[i] = dot_obj(row, 8, 10, 8);" in UI


def test_geometry_parity():
    # 迷你角色 24×30；行内原点 (7,14)；工具标签 x=34 宽 92 —— 两端一致
    assert "pet_rect(parent, x, y, 24, 30, 0, bg);" in UI
    assert "devAbs(el('div','dev-el'), 7, 14, 24, 30)" in JS
    assert re.search(r'label\(row, "", 20, text_color, 34,\s+4, 92, 26\)', UI)
    assert re.search(r"devLabel\(row, 34, 4, 92, task\.tool", JS)
    # 头/眼/嘴/身几何两端一致（固件 pet_rect ↔ web devAbs）
    for fw, web in [("pet_rect(p.root, 2, 0, 20, 13, 5,", "devAbs(el('div','dev-el'), 2, 0, 20, 13"),
                    ("pet_rect(p.root, 6, 5, 3, 4, 1,", "devAbs(el('div','dev-el'), ex, ey, 3, eyeH"),
                    ("pet_rect(p.root, 4, 15, 16, 12, 4,", "devAbs(el('div','dev-el'), 4, 15, 16, 12")]:
        assert fw in UI, fw
        assert web in JS, web


def test_wait_input_badge_and_done_parity():
    # 等待输入/待确认 → 「!」徽标；完成 → ok 色身体 + 眯眼（两端同义）
    assert re.search(r'set_text\(p\.badge, wait \? "!" : ""\);', UI)
    assert re.search(r"bool wait = !strcmp\(task\.state, \"WAIT_INPUT\"\) \|\| !strcmp\(task\.state, \"ASK\"\);", UI)
    assert "devSemantic('status.warn')" in JS
    assert "b.textContent='!'" in JS
    assert "mix_hex(COL_OK, bg, 130)" in UI          # 完成身体色（固件）
    assert "rgbHex(ok,130/255)" in JS                # 完成身体色（Web 同权重）


def test_web_blink_animation():
    assert "dev-mini-eye" in CSS and "dev-mini-blink" in CSS
    assert "dev-mini-eye" in JS
