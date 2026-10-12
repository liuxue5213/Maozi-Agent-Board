"""手机推送与旧城市迁移的单测。"""
import json
import threading
import time
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from workbuddy.app import App


class _Health:
    def event(self, *a, **k):
        pass


def _bare_app(cfg):
    """绕过 __init__（psutil/串口等重依赖），只造出推送方法需要的最小对象。"""
    app = App.__new__(App)
    app.cfg = cfg
    app.health = _Health()
    return app


class _Cfg:
    push_url = ""
    push_states = "ERROR,WAIT_INPUT,ASK"


def test_push_disabled_without_url():
    app = _bare_app(_Cfg())
    # 不设 push_url：不应创建线程、不应抛异常
    app._push_notify("zcode · demo", "ERROR")
    assert True


def test_push_post_json_reaches_local_server():
    received = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append({"path": self.path,
                             "title": self.headers.get("X-Title"),
                             "data": json.loads(body)})
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = _Cfg()
        cfg.push_url = f"http://127.0.0.1:{srv.server_port}/topic"
        app = _bare_app(cfg)
        app._push_notify("zcode · demo", "WAIT_INPUT")
        for _ in range(50):
            if received:
                break
            threading.Event().wait(0.1)
        assert received, "推送未到达本地测试服务"
        # X-Title 头按 ntfy 约定是百分号编码的 UTF-8（头只允许 latin-1），
        # 真正的标题文本在 JSON body 里
        assert "WorkBuddy" in received[0]["title"]
        assert received[0]["data"]["title"].startswith("WorkBuddy ·")
        assert "demo" in received[0]["data"]["body"]
    finally:
        srv.shutdown()


def test_push_placeholder_get_url():
    received = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = _Cfg()
        cfg.push_url = f"http://127.0.0.1:{srv.server_port}/key/{{title}}/{{body}}"
        app = _bare_app(cfg)
        app._push_notify("codex · 项目A", "ERROR")
        for _ in range(50):
            if received:
                break
            threading.Event().wait(0.1)
        assert received and "%E5%87%BA%E9%94%99" in received[0], \
            f"Bark 占位符 URL 未按 GET 展开：{received}"
    finally:
        srv.shutdown()


def test_push_states_filter():
    cfg = _Cfg()
    cfg.push_states = "ERROR"          # DONE 不推
    app = _bare_app(cfg)
    assert app._push_notify("x", "DONE") is None
    # DONE 在默认集合之外，同上不应触发（无 push_url 时是 no-op，这里只验证不抛）


# ---- 安静时段 ----

def test_quiet_hours_parse_and_windows():
    from workbuddy.app import _in_quiet_hours
    # 固定“现在”= 12:00
    noon = __import__("time").mktime(
        __import__("time").strptime("2026-10-11 12:00:00", "%Y-%m-%d %H:%M:%S"))
    night = __import__("time").mktime(
        __import__("time").strptime("2026-10-11 23:30:00", "%Y-%m-%d %H:%M:%S"))
    early = __import__("time").mktime(
        __import__("time").strptime("2026-10-11 06:00:00", "%Y-%m-%d %H:%M:%S"))
    assert not _in_quiet_hours("", noon)                    # 未启用
    assert not _in_quiet_hours("garbage", noon)             # 坏格式=不启用
    assert not _in_quiet_hours("09:00-10:00", noon)         # 窗口外
    assert _in_quiet_hours("09:00-10:00", night) is False
    assert _in_quiet_hours("11:00-13:00", noon)             # 窗口内
    assert _in_quiet_hours("23:00-07:30", night)            # 跨零点：深夜在内
    assert _in_quiet_hours("23:00-07:30", early)            # 跨零点：清晨在内
    assert not _in_quiet_hours("23:00-07:30", noon)         # 跨零点：白昼在外
    assert not _in_quiet_hours("10:00-10:00", noon)         # 零长窗口=不启用


def test_push_notify_respects_quiet_hours():
    received = []

    class H2(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(1)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H2)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from workbuddy import app as app_mod
        cfg = _Cfg()
        cfg.push_url = f"http://127.0.0.1:{srv.server_port}/topic"
        cfg.push_quiet = "00:00-23:59"                 # 几乎全天安静
        app = _bare_app(cfg)
        with mock.patch.object(app_mod.time, "localtime",
                               return_value=__import__("time").gmtime(0)):
            # gmtime(0)=1970-01-01 00:00 UTC，localtime 结构里 hour=0,min=0 → 窗口内
            app._push_notify("zcode · demo", "ERROR")
        for _ in range(20):
            if received:
                break
            threading.Event().wait(0.1)
        assert not received, "安静时段内不应推送"
    finally:
        srv.shutdown()


# ---- 服务端收件箱 ----

def _inbox_app(tasks=(), calendar=(), telemetry=None, tmp=None):
    import time as t_mod
    from workbuddy import app as app_mod
    app = App.__new__(App)
    app.tasks = list(tasks)
    app.profile = {"calendar": list(calendar)}
    app.profile_lock = threading.Lock()
    app.last = telemetry or {}
    app.health = _Health()
    # 静音文件隔离到临时目录
    state = tmp / "inbox-state.json"
    app_mod.INBOX_STATE_PATH = state
    app._inbox_seen_cache = None
    return app, t_mod


def test_inbox_items_tasks_calendar_disk(tmp_path):
    # 事件时刻用「现在+50 分钟」动态生成：测试不随墙钟过期
    soon_dt = time.localtime(time.time() + 50 * 60)
    soon = (f"{soon_dt.tm_year:04d}-{soon_dt.tm_mon:02d}-{soon_dt.tm_mday:02d}",
            f"{soon_dt.tm_hour:02d}:{soon_dt.tm_min:02d}")
    app, tm = _inbox_app(
        tasks=[{"tool": "zcode", "id": "a1", "state": "WAIT_INPUT",
                "project": "maozi-diy", "age": 65},
               {"tool": "codex", "id": "b2", "state": "GENERATING"}],
        calendar=[{"id": "e1", "date": soon[0], "time": soon[1],
                   "title": "今晚", "enabled": True},
                  {"id": "e2", "date": "2027-01-01", "time": "09:00",
                   "title": "远期", "enabled": True},
                  {"id": "e1", "date": "2026-10-11", "time": "08:00",
                   "title": "已停用", "enabled": False}],
        telemetry={"disk": 96, "diskfree": 18.5},
        tmp=tmp_path)
    items = app.inbox_items()
    ids = [x["id"] for x in items]
    assert "t:zcode:a1" in ids          # 等你回复
    assert "t:codex:b2" not in ids      # 进行中不进收件箱
    assert "c:e1" in ids and "c:e2" not in ids
    assert "hw:disk" in ids             # 磁盘 ≥90%
    assert app.inbox_count() == len(items)


def test_inbox_dismiss_and_snooze(tmp_path):
    app, tm = _inbox_app(
        tasks=[{"tool": "zcode", "id": "a1", "state": "ERROR",
                "project": "x", "age": 10}],
        telemetry={"disk": 95}, tmp=tmp_path)
    app.inbox_dismiss(["t:zcode:a1"], snooze_minutes=30)
    assert all(x["id"] != "t:zcode:a1" for x in app.inbox_items())
    # hw:disk 的「知道了」最多静音 12h（条目 id 恒定，永久静音会哑掉未来告警）
    app.inbox_dismiss(["hw:disk"])
    until = app._inbox_seen()["hw:disk"]
    assert until > tm.time() + 11 * 3600
    assert all(x["id"] != "hw:disk" for x in app.inbox_items())
