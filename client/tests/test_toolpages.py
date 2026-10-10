"""P2 协议扩展与「按工具分页」的回归测试。

v0.4（2026-10-10）：
- 会话槽位 AI0..AI3 → AI0..AI7（多编辑器 × 多项目并行时 Web 与固件都按 8 条走）；
- 新增 DN0..DN2（最近完成的会话，新→旧，工具页「完成带」的数据源）；
- 下线单任务镜像字段 AISTATE/AITOOL/AITASK/AIPROG/AITIME/AIERR
  （固件从未渲染它们——grep 证实——内容与 AI0 完全重复）；
- 页面配置新增 filter（小写工具名，空 = 全部），固件按它过滤会话实现工具页。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy.aiwatch import MAX_TASKS

CLIENT = Path(__file__).resolve().parents[1]
REPO = CLIENT.parent
from workbuddy.app import DONE_WIRE_SLOTS, AI_WIRE_SLOTS, ai_wire_lines
from workbuddy.profile import validate_profile


def _task(tool="zcode", state="TOOL", i=0):
    return {"id": f"s{i}", "tool": tool, "state": state, "task": "RUN",
            "prog": -1, "elapsed": 60 + i, "age": 5 + i,
            "project": f"项目{i}", "err": "", "trace": []}


class WireLineTests(unittest.TestCase):
    def test_eight_sessions_encoded(self):
        lines = ai_wire_lines([_task(i=i) for i in range(8)], [])
        self.assertEqual([lines[f"ai{i}"] for i in range(8)],
                         [lines[f"ai{i}"] for i in range(8)])  # 槽位齐全
        self.assertIn("zcode", lines["ai7"])
        self.assertEqual(sorted(k for k in lines if k.startswith("ai")), [f"ai{i}" for i in range(8)])

    def test_more_than_wire_slots_is_truncated(self):
        lines = ai_wire_lines([_task(i=i) for i in range(AI_WIRE_SLOTS + 4)], [])
        self.assertNotIn(f"ai{AI_WIRE_SLOTS}", lines)

    def test_done_entries_newest_first(self):
        done = [{"tool": "zcode", "project": "甲", "age": 9, "at": 2},
                {"tool": "claude", "project": "乙", "age": 99, "at": 1}]
        lines = ai_wire_lines([], done)
        self.assertEqual(lines["dn0"], "zcode,甲,9")
        self.assertEqual(lines["dn1"], "claude,乙,99")

    def test_empty_slots_are_sent_explicitly(self):
        """会话减少时必须发空槽位清位，否则设备上残留幽灵行（实测踩过）。"""
        lines = ai_wire_lines([], [])
        self.assertEqual(lines.get("ai0"), "")
        self.assertEqual(lines.get("ai7"), "")
        self.assertEqual(lines.get("dn0"), "")

    def test_empty_slots_survive_encoding(self):
        from workbuddy.protocol import encode_telemetry
        frame = encode_telemetry(ai_wire_lines([_task()], []) | {"time": "120000"})
        self.assertIn("AI7=\n", frame, "空槽位必须在帧里（清位信号）")
        self.assertIn("DN2=\n", frame)

    def test_done_cap_three(self):
        done = [{"tool": "t", "project": f"p{i}", "age": i, "at": i}
                for i in range(DONE_WIRE_SLOTS + 3)]
        lines = ai_wire_lines([], done)
        self.assertEqual(sorted(k for k in lines if k.startswith("dn")), ["dn0","dn1","dn2"])
        self.assertIn("p2", lines["dn2"])

    def test_slots_match_firmware_capacity(self):
        """与固件 proto.h 的 tasks[8]/done[3] 对齐——改任何一侧都要同步另一侧。"""
        firmware = (REPO / "firmware" / "src" / "proto.h")
        header = firmware.read_text(encoding="utf-8")
        self.assertIn("AITask tasks[8];", header)
        self.assertIn("DoneTask done[3];", header)
        self.assertEqual(AI_WIRE_SLOTS, 8)
        self.assertEqual(DONE_WIRE_SLOTS, 3)

    def test_mirror_fields_off_the_wire(self):
        """镜像字段下线：协议字段表里不应再有 AISTATE 等键（固件从未渲染）。"""
        proto = (CLIENT / "workbuddy" / "protocol.py").read_text()
        for key in ("aistate", "aitool", "aitask", "aiprog", "aitime", "aierr"):
            self.assertNotIn(f'"{key}"', proto, f"镜像字段 {key} 应从线上移除")
        proto_src = (REPO / "firmware" / "src" / "proto.cpp").read_text()
        self.assertNotIn('strcmp(key, "AISTATE")', proto_src)


class PageFilterTests(unittest.TestCase):
    PAGE = {"id": "ai", "title": "T", "layout": "list", "widgets": ["ai_list"]}

    def _profile(self, page_over=None, **top):
        page = dict(self.PAGE, **(page_over or {}))
        return {"schema": 1, "version": 1, "pages": [page], **top}

    def test_filter_normalized_and_kept(self):
        out = validate_profile(self._profile({"filter": "  ZCode "}))
        self.assertEqual(out["pages"][0]["filter"], "zcode")

    def test_filter_defaults_to_empty(self):
        out = validate_profile(self._profile())
        self.assertEqual(out["pages"][0]["filter"], "")

    def test_bad_filter_rejected(self):
        # "UPPER" 不算非法：filter 会被规范化为小写（test_filter_normalized_and_kept）
        for bad in ("带中文", "a" * 13, "has space", "a,b"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_profile(self._profile({"filter": bad}))

    def test_autopage_passthrough(self):
        self.assertEqual(validate_profile(self._profile({}, autopage=1))["autopage"], 1)
        self.assertEqual(validate_profile(self._profile({}, autopage=True))["autopage"], 1)
        self.assertEqual(validate_profile(self._profile({}, autopage=0))["autopage"], 0)
        self.assertEqual(validate_profile(self._profile())["autopage"], 0)

    def test_filter_survives_export_round_trip(self):
        once = validate_profile(self._profile({"filter": "codex"}))
        self.assertEqual(validate_profile(once), once)


class FirmwareParityTests(unittest.TestCase):
    """预览与固件共享工具页几何——关键常量改任何一侧，测试先红。"""

    def test_tool_page_geometry_declared_once_each(self):
        ui = (REPO / "firmware" / "src" / "ui.cpp").read_text()
        js = (CLIENT / "workbuddy" / "web_assets" / "app.js").read_text()
        for constant in ("94 + 70 * i", "304, 30", "304, 66"):
            self.assertIn(constant, ui, f"固件缺少工具页几何：{constant}")
            self.assertIn(constant.replace(" ", ""), js.replace(" ", ""),
                          f"预览缺少工具页几何：{constant}")

    def test_generating_label_present(self):
        ui = (Path(__file__).resolve().parents[2] / "firmware" / "src" / "ui.cpp").read_text()
        self.assertIn('"生成中"', ui, "state_short 缺 GENERATING 映射（此前落兜底「处理中」）")


class AutoPageTests(unittest.TestCase):
    """autoPages() 生成器的行为（在 node 里跑，python 侧锁源码存在性）。"""

    def test_generator_exists_and_wired(self):
        js = (CLIENT / "workbuddy" / "web_assets" / "app.js").read_text()
        for token in ("function autoPages()", "function regenAutoPages()",
                      "draft.autopage=e.target.checked?1:0",
                      "if(draft.autopage) regenAutoPages()"):
            self.assertIn(token, js, f"自动分页缺少：{token}")

    def test_preserves_non_ai_pages(self):
        js = (CLIENT / "workbuddy" / "web_assets" / "app.js").read_text()
        # 生成器按「是否含 ai_list」区分 AI 页与非 AI 页，后者原样保留
        self.assertIn("includes('ai_list')", js)


class TrackingCapTests(unittest.TestCase):
    def test_max_tasks_at_least_wire_slots(self):
        """跟踪上限（Web）≥ 线上槽位（固件），否则工具页会饿着。"""
        self.assertGreaterEqual(MAX_TASKS, AI_WIRE_SLOTS)


if __name__ == "__main__":
    unittest.main()
