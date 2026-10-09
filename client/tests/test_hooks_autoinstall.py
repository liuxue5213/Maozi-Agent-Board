"""Hook 快通道自动注入回归（AI 采集第 2 层）。

全部在临时目录里跑，绝不触碰真实 ~/.claude/settings.json。
锁定四条事故防线：
1. 加法合并——用户已有 hooks 原样保留；
2. 幂等——重复安装不产生重复条目；
3. 首次安装备份 .bak，重复安装不覆盖最早的备份；
4. 卸载只删带标记的条目。
外加：损坏的 settings.json 必须中止而不是写坏它；HTTP 端点往返。
"""
import json
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from workbuddy import hooks


class HooksLogicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Path(self.tmp.name) / "settings.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_creates_three_marked_events(self):
        r = hooks.install(self.settings, port=8799)
        self.assertEqual(sorted(r["installed"]), ["PostToolUse", "Stop", "UserPromptSubmit"])
        data = json.loads(self.settings.read_text())
        cmds = [h["command"]
                for ev in data["hooks"].values() for e in ev
                for h in e["hooks"]]
        self.assertEqual(len(cmds), 3)
        self.assertTrue(all("workbuddy-hook" in c for c in cmds))
        self.assertTrue(all("8799" in c for c in cmds))

    def test_user_hooks_survive_install_and_uninstall(self):
        self.settings.write_text(json.dumps({
            "model": "opus",
            "hooks": {"Stop": [{"hooks": [{"type": "command",
                                           "command": "echo mine"}]}]},
        }))
        hooks.install(self.settings)
        data = json.loads(self.settings.read_text())
        self.assertEqual(data["model"], "opus")
        stop_entries = data["hooks"]["Stop"]
        self.assertEqual(len(stop_entries), 2, "用户的条目 + 我们的条目")
        r = hooks.uninstall(self.settings)
        self.assertEqual(set(r["removed"]),
                         {"Stop", "UserPromptSubmit", "PostToolUse"})
        data = json.loads(self.settings.read_text())
        self.assertEqual(data["model"], "opus",
                         "卸载不得碰 hooks 之外的字段")
        self.assertEqual(set(data["hooks"].keys()), {"Stop"},
                         "我们注入的另外两个事件应整体移除")
        self.assertEqual(len(data["hooks"]["Stop"]), 1)
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"],
                         "echo mine")

    def test_install_is_idempotent(self):
        first = hooks.install(self.settings)
        self.assertEqual(len(first["installed"]), 3)
        second = hooks.install(self.settings)
        self.assertEqual(second["installed"], [])
        self.assertEqual(sorted(second["skipped"]),
                         ["PostToolUse", "Stop", "UserPromptSubmit"])
        data = json.loads(self.settings.read_text())
        total = sum(len(ev) for ev in data["hooks"].values())
        self.assertEqual(total, 3, "重复安装不得叠加条目")

    def test_backup_once_never_overwritten(self):
        self.settings.write_text(json.dumps({"v": 1}))
        hooks.install(self.settings)
        bak = json.loads(Path(str(self.settings) + ".bak").read_text())
        self.assertEqual(bak, {"v": 1}, "备份必须是安装前的原始内容")
        hooks.uninstall(self.settings)
        hooks.install(self.settings)   # 第二次安装
        bak2 = json.loads(Path(str(self.settings) + ".bak").read_text())
        self.assertEqual(bak2, {"v": 1}, "不得用后续内容覆盖最早备份")

    def test_broken_settings_aborts_without_writing(self):
        self.settings.write_text("{这不是JSON")
        with self.assertRaises(ValueError):
            hooks.install(self.settings)
        self.assertEqual(self.settings.read_text(), "{这不是JSON",
                         "中止时原文件必须原样保留")

    def test_status_shapes(self):
        s = hooks.status(self.settings)
        self.assertFalse(s["wired"])
        self.assertFalse(s["settings_found"])
        self.settings.write_text("{}")     # 预置文件 → 首次安装会做备份
        hooks.install(self.settings)
        s = hooks.status(self.settings)
        self.assertTrue(s["wired"])
        self.assertEqual(s["events"], ["PostToolUse", "Stop", "UserPromptSubmit"])
        self.assertTrue(s["backup_exists"])


class HookEndpointTests(unittest.TestCase):
    """端点往返：install → status(wired) → uninstall（用独立端口避免撞真服务）。"""
    PORT = 8751

    @classmethod
    def setUpClass(cls):
        import threading
        from workbuddy.web import start_settings_server
        from workbuddy import hooks as hooks_mod

        cls.tmp = tempfile.TemporaryDirectory()
        cls.settings = Path(cls.tmp.name) / "settings.json"
        # 把端点指向临时 settings（web.py 固定用 Path.home()，这里 monkeypatch）
        cls._orig = hooks_mod.__dict__["hooks_settings_path"] if False else None
        import workbuddy.web as web
        cls._orig_fn = web.hooks_settings_path
        web.hooks_settings_path = lambda: cls.settings

        class MiniApp:
            class cfg:
                web_port = cls.PORT
            class health:
                @staticmethod
                def event(*a, **k):
                    pass
            def status_snapshot(self):
                return {"profile": {}}

        cls.server = start_settings_server(MiniApp(), cls.PORT)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.PORT}/api/hooks-status", timeout=1)
                break
            except Exception:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        import workbuddy.web as web
        web.hooks_settings_path = cls._orig_fn
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_round_trip(self):
        def post(path):
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.PORT}{path}",
                data=b"{}", headers={"Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return json.loads(resp.read())

        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.PORT}/api/hooks-status", timeout=2) as r:
            self.assertFalse(json.loads(r.read())["wired"])
        post("/api/hooks-install")
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.PORT}/api/hooks-status", timeout=2) as r:
            self.assertTrue(json.loads(r.read())["wired"])
        post("/api/hooks-uninstall")
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.PORT}/api/hooks-status", timeout=2) as r:
            self.assertFalse(json.loads(r.read())["wired"])


if __name__ == "__main__":
    unittest.main()
