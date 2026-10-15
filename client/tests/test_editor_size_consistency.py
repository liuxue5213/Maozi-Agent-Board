"""「预览 = 真机」守卫：Web 编辑器的组件尺寸/大字档必须与下发用的 profile 尺寸一致。

历史问题：app.js 有两套大字尺寸（渲染用 +40/+12、拖拽 resize 用只含 clock/weather
的 BIG 映射），且 music 常规高度写成 64（真机 76）——导致「大字」后拖拽钳位算错、
音乐卡预览比真机矮。此测试把两边锁死。
"""
import re
from pathlib import Path

from workbuddy import profile as P

APP_JS = Path(__file__).resolve().parents[1] / "workbuddy" / "web_assets" / "app.js"
SRC = APP_JS.read_text(encoding="utf-8")


def _js_map(name):
    """从 app.js 解析 `const <name>={k:[w,h], ...};` 这类映射。"""
    m = re.search(r"const " + name + r"=\{(.*?)\};", SRC, re.S)
    assert m, f"app.js 里找不到 {name} 映射"
    return {k: (int(w), int(h))
            for k, w, h in re.findall(r"(\w+):\[(\d+),(\d+)\]", m.group(1))}


def test_web_size_map_matches_profile():
    """常规尺寸表逐项一致（任一组件尺寸漂移即失败）。"""
    assert _js_map("SIZE") == {k: tuple(v) for k, v in P.FREE_SIZES.items()}


def test_web_big_fixed_matches_profile():
    """clock/weather 专属大字档一致。"""
    big = _js_map("BIG_FIXED")
    for k in ("clock", "weather"):
        assert big[k] == P.FREE_BIG_SIZES[k], k


def test_no_style_set_matches_profile():
    """不支持大字的组件集合与 profile 的排除项一致。"""
    m = re.search(r"const NO_STYLE=new Set\(\[([^\]]*)\]\)", SRC)
    assert m
    assert set(re.findall(r"'(\w+)'", m.group(1))) == {"ai_cost", "pet", "music"}
    # profile 侧同样排除这三者
    for k in ("ai_cost", "pet", "music"):
        assert k not in P.FREE_BIG_SIZES


def test_dims_helper_is_single_source():
    """渲染/拖拽/切换都应调用 dimsOf，不应再各自内联尺寸算术。"""
    assert SRC.count("dimsOf(") >= 3          # 渲染 / 拖拽 / 大字切换 三处共用
    assert "BIG[page.components" not in SRC   # 旧的错位来源已移除


def test_big_style_button_is_wired_into_toolbar():
    """回归：『大字 / 常规』按钮曾被创建却漏 append，导致大/小卡不可达。"""
    assert "bar.append(del,dup,style," in SRC
