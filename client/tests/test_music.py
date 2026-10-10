"""默认音乐软件 + 媒体命令路由的回归测试。

背景（用户实测）：「音乐播放的快捷操作每次都默认打开苹果的音乐」。根因是
control.py 用 AppleScript 逐个试候选播放器，而 macOS 上只有 Apple Music 与
Spotify 有 AppleScript 字典——网易云音乐（com.netease.163music）、QQ音乐都没有，
`tell application "NeteaseMusic" to playpause` 直接报错，于是循环必然落到
Music.app。

修法：媒体命令改走 macOS MediaRemote（系统「正在播放」中枢），与具体播放器
无关；默认音乐软件可在客户端设置里指定。

这里把 ctypes/子进程边界全部挡在测试外，只锁「选谁、发什么命令、按什么顺序」。
"""
import ctypes
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy import music


class _FakeLib:
    """假的 MediaRemote：只记录被下发的命令。"""

    def __init__(self, delivered=True):
        self.calls = []
        self.delivered = delivered

    def MRMediaRemoteSendCommand(self, code, options):
        self.calls.append((code, options))
        return self.delivered


class MusicModuleTests(unittest.TestCase):
    def setUp(self):
        self._saved = (music._lib, music._queue)
        music._consts.clear()

    def tearDown(self):
        music._lib, music._queue = self._saved

    # ---- 命令映射 ----

    def test_actions_map_to_mediaremote_commands(self):
        fake = _FakeLib()
        music._lib, music._queue = fake, object()
        for action, expected in (("play", 0), ("pause", 1), ("toggle", 2),
                                 ("stop", 3), ("next", 4), ("prev", 5)):
            self.assertTrue(music.send(action), action)
            self.assertEqual(fake.calls[-1][0], expected, action)

    def test_unknown_action_is_not_sent(self):
        fake = _FakeLib()
        music._lib, music._queue = fake, object()
        self.assertFalse(music.send("rewind"))
        self.assertEqual(fake.calls, [])

    def test_send_reports_failure_when_not_delivered(self):
        music._lib, music._queue = _FakeLib(delivered=False), object()
        self.assertFalse(music.send("play"))

    def test_send_is_false_without_the_framework(self):
        """非 macOS / 加载失败时返回 False，让调用方走兜底，而不是假装成功。"""
        music._lib, music._queue = False, None
        self.assertFalse(music.send("play"))

    def test_now_playing_is_none_when_unsupported(self):
        music._lib, music._queue = False, None
        self.assertIsNone(music.now_playing())

    # ---- 应用发现 ----

    def _make_app(self, root: Path, name: str, bundle: str):
        app = root / f"{name}.app" / "Contents"
        app.mkdir(parents=True)
        with (app / "Info.plist").open("wb") as handle:
            plistlib.dump({"CFBundleIdentifier": bundle, "CFBundleName": name}, handle)
        return app

    def test_discovers_players_and_skips_non_players(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_app(root, "NeteaseMusic", "com.netease.163music")
            self._make_app(root, "QQMusic", "com.tencent.QQMusicMac")
            self._make_app(root, "UURemote", "com.netease.uuremote")      # 遥控器，不是播放器
            self._make_app(root, "Updater", "com.netease.163music.updater")
            self._make_app(root, "Numbers", "com.apple.Numbers")
            saved = music._APP_DIRS
            music._APP_DIRS = (root,)
            try:
                found = {a["id"] for a in music.installed_apps()}
            finally:
                music._APP_DIRS = saved
        self.assertIn("com.netease.163music", found)
        self.assertIn("com.tencent.QQMusicMac", found)
        self.assertNotIn("com.netease.uuremote", found)
        self.assertNotIn("com.apple.Numbers", found)

    def test_known_bundle_gets_friendly_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_app(root, "NeteaseMusic", "com.netease.163music")
            saved = music._APP_DIRS
            music._APP_DIRS = (root,)
            try:
                found = music.installed_apps()
            finally:
                music._APP_DIRS = saved
        self.assertEqual(found[0]["name"], "网易云音乐")

    # ---- 选择默认软件 ----

    def _patch_apps(self, apps):
        saved = music.installed_apps
        music.installed_apps = lambda: list(apps)
        self.addCleanup(lambda: setattr(music, "installed_apps", saved))

    def test_auto_prefers_netease_over_apple(self):
        self._patch_apps([
            {"id": "com.apple.Music", "name": "音乐（Apple）", "path": "/A/Music.app"},
            {"id": "com.netease.163music", "name": "网易云音乐", "path": "/A/NM.app"},
        ])

        class Cfg:
            music_app = "auto"
        self.assertEqual(music.default_app(Cfg())["id"], "com.netease.163music")

    def test_explicit_choice_wins(self):
        self._patch_apps([
            {"id": "com.apple.Music", "name": "音乐（Apple）", "path": "/A/Music.app"},
            {"id": "com.netease.163music", "name": "网易云音乐", "path": "/A/NM.app"},
        ])

        class Cfg:
            music_app = "com.apple.Music"
        self.assertEqual(music.default_app(Cfg())["id"], "com.apple.Music")

    def test_configured_but_missing_app_is_still_honoured(self):
        """软件被卸载/没被扫到也要认配置值，否则前端下拉框会显示成空。"""
        self._patch_apps([])

        class Cfg:
            music_app = "com.example.whatever"
        self.assertEqual(music.default_app(Cfg())["id"], "com.example.whatever")

    # ---- 「点一下就开始放歌」的顺序 ----

    def test_play_default_launches_then_plays(self):
        calls = []
        self._patch_apps([{"id": "com.netease.163music", "name": "网易云音乐", "path": "/A/NM.app"}])
        saved = (music.is_running, music.open_app, music.send)
        music.is_running = lambda app: False
        music.open_app = lambda app: calls.append(("open", app["id"])) or True
        music.send = lambda action: calls.append(("send", action)) or True
        try:
            result = music.play_default(type("C", (), {"music_app": "auto"})(), settle=0)
        finally:
            music.is_running, music.open_app, music.send = saved
        self.assertEqual(calls, [("open", "com.netease.163music"), ("send", "play")])
        self.assertTrue(result["ok"] and result["launched"])

    def test_play_default_does_not_relaunch_running_app(self):
        calls = []
        self._patch_apps([{"id": "com.netease.163music", "name": "网易云音乐", "path": "/A/NM.app"}])
        saved = (music.is_running, music.open_app, music.send)
        music.is_running = lambda app: True
        music.open_app = lambda app: calls.append("open") or True
        music.send = lambda action: calls.append(action) or True
        try:
            result = music.play_default(type("C", (), {"music_app": "auto"})(), settle=0)
        finally:
            music.is_running, music.open_app, music.send = saved
        self.assertEqual(calls, ["play"])
        self.assertFalse(result["launched"])

    def test_play_default_without_any_app_reports_reason(self):
        self._patch_apps([])
        saved = music.installed_apps
        try:
            result = music.play_default(type("C", (), {"music_app": "auto"})(), settle=0)
        finally:
            music.installed_apps = saved
        self.assertFalse(result["ok"])
        self.assertIn("reason", result)


class LaunchRoutingTests(unittest.TestCase):
    """快捷应用里的 @music 保留字必须走音乐流程，而不是当成 shell 命令。"""

    def test_music_token_routes_to_music_play(self):
        from workbuddy.app import App
        app = App.__new__(App)
        app.profile = {"apps": [{"id": "m1", "name": "音乐", "command": music.MUSIC_COMMAND}]}
        import threading
        app.profile_lock = threading.Lock()
        app.cfg = type("C", (), {"music_app": "auto"})()
        app.health = None
        called = []
        app.music_play = lambda: called.append(True) or {"ok": True, "app": "网易云音乐"}
        result = app._launch_app("m1")
        self.assertEqual(called, [True])
        self.assertEqual(result["app"], "网易云音乐")

    def test_plain_command_still_launches_via_control(self):
        from workbuddy.app import App
        app = App.__new__(App)
        app.profile = {"apps": [{"id": "s1", "name": "Safari", "command": "open -a Safari"}]}
        import threading
        app.profile_lock = threading.Lock()
        launched = []
        app.control = type("C", (), {"launch": lambda self, cmd: launched.append(cmd)})()
        app.cfg = type("C", (), {"music_app": "auto"})()
        app._launch_app("s1")
        self.assertEqual(launched, ["open -a Safari"])


class ControlFallbackTests(unittest.TestCase):
    """control.media 要先走 MediaRemote；只有它不可用时才退回 AppleScript。"""

    def test_media_prefers_mediaremote(self):
        from workbuddy.control import ControlApp
        if sys.platform != "darwin":
            self.skipTest("仅 macOS 走这条路径")
        calls = []
        saved_send = music.send
        music.send = lambda action: calls.append(action) or True
        try:
            ControlApp().media("next")
        finally:
            music.send = saved_send
        self.assertEqual(calls, ["next"])

    def test_mediaremote_commands_need_no_applescript_dictionary(self):
        """网易云/QQ音乐没有 AppleScript 字典，因此候选列表里不该出现它们——
        真正生效的是 MediaRemote 这条与具体软件无关的通道。"""
        from workbuddy import control
        self.assertNotIn("NeteaseMusic", control._MAC_PLAYERS)
        self.assertNotIn("QQMusic", control._MAC_PLAYERS)


if __name__ == "__main__":
    unittest.main()
