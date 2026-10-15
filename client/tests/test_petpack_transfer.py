"""角色包传输边界：图片事务不丢包，旧包不残留新包的可选帧。"""
import threading
import time

from workbuddy.app import App


class _Link:
    status = "connected:mock"

    def __init__(self, app):
        self.app = app
        self.sent = []
        self.cleared = False

    def clear_pending(self):
        self.cleared = True

    def send(self, data, *, block=False):
        assert self.app._img_active
        assert self.cleared
        assert block
        self.sent.append(data)
        if data.startswith(b"IMG=END,"):
            name = data.decode().strip().split(",", 1)[1]
            self.app._img_reply = {"ok": True,
                                   "reason": f"/{name},{len(b'sample-jpeg')}"}
        elif data.startswith(b"IMG=DELETE,"):
            name = data.decode().strip().split(",", 1)[1]
            self.app._img_reply = {"ok": True, "reason": f"DELETED,{name}"}
        return True


def test_image_transfer_is_exclusive_and_uses_backpressure():
    app = App.__new__(App)
    app._img_lock = threading.Lock()
    app._img_active = False
    app._img_reply = None
    app._stop = False
    app.health = type("Health", (), {"event": lambda *a, **k: None})()
    app.link = _Link(app)
    assert app.send_bg_image(b"sample-jpeg", "pet_idle.jpg")
    assert not app._img_active
    assert app.link.sent[0].startswith(b"IMG=BEGIN,")
    assert app.link.sent[-1] == b"IMG=END,pet_idle.jpg\n"


def test_image_upload_rejects_concurrent_ota_without_sending_frames():
    app = App.__new__(App)
    app._transfer_lock = threading.RLock()
    app._img_lock = threading.Lock()
    app._img_active = False
    app._ota_active = False
    app._snap_until = 0.0
    app.link = _Link(app)
    ready = threading.Event()
    release = threading.Event()
    def ota_owner():
        with app._transfer_lock:
            ready.set()
            release.wait(2)
    worker = threading.Thread(target=ota_owner)
    worker.start()
    assert ready.wait(1)
    try:
        assert not app.send_bg_image(b"sample-jpeg", "pet_idle.jpg")
        assert app._last_bg_result["reason"] == "设备正忙于其他传输"
        assert app.link.sent == []
    finally:
        release.set()
        worker.join()


def test_delete_waits_for_matching_device_ack():
    app = App.__new__(App)
    app._img_lock = threading.Lock()
    app._img_active = False
    app._img_reply = None
    app._stop = False
    app.health = type("Health", (), {"event": lambda *a, **k: None})()
    app.link = _Link(app)
    assert app.delete_album_image("pet_idle_b.jpg")
    assert app.link.sent == [b"IMG=DELETE,pet_idle_b.jpg\n"]
    assert not app._img_active


def test_image_transfer_ignores_wrong_file_ack():
    app = App.__new__(App)
    app._img_lock = threading.Lock()
    app._img_active = False
    app._img_reply = None
    app._stop = False
    app.health = type("Health", (), {"event": lambda *a, **k: None})()
    app.link = _Link(app)
    original_send = app.link.send
    def send_with_stale_ack(data, *, block=False):
        result = original_send(data, block=block)
        if data.startswith(b"IMG=END,"):
            app._img_reply = {"ok": True, "reason": "/pet_old.jpg,11"}
            threading.Thread(target=lambda: (time.sleep(0.15),
                setattr(app, "_img_reply", {"ok": True,
                    "reason": "/pet_idle.jpg,11"})), daemon=True).start()
        return result
    app.link.send = send_with_stale_ack
    assert app.send_bg_image(b"sample-jpeg", "pet_idle.jpg")


def test_old_pack_clears_optional_and_working_b_frames(tmp_path):
    d = tmp_path / "petpack_old"
    d.mkdir()
    for state in App.PACK_STATES:
        (d / f"pet_{state}.jpg").write_bytes(b"jpeg")
    app = App.__new__(App)
    app._pack_lock = threading.Lock()
    app.petpack_base = lambda: tmp_path
    sent, deleted = [], []
    app.send_bg_image = lambda data, name: sent.append(name) or True
    app.delete_album_image = lambda name: deleted.append(name) or True
    result = app.apply_petpack("old")
    assert result["ok"]
    assert result["sent"] == len(App.PACK_STATES)
    assert app.pack_progress == {"active": False, "name": "old",
                                 "sent": len(App.PACK_STATES),
                                 "total": len(App.PACK_STATES),
                                 "current": "完成", "ok": True}
    assert deleted[0] == "pet_custom.jpg"
    assert deleted[1:] == ["pet_working_b.jpg", "pet_feeding.jpg",
                           "pet_drinking.jpg", "pet_playing.jpg",
                           "pet_idle_b.jpg", "pet_thinking_b.jpg",
                           "pet_sleeping_b.jpg", "pet_greeting_b.jpg",
                           "pet_happy_b.jpg", "pet_feeding_b.jpg",
                           "pet_drinking_b.jpg", "pet_playing_b.jpg"]
    assert sent == [f"pet_{state}.jpg" for state in App.PACK_STATES]


def test_motion_pack_sends_every_frame_and_reports_progress(tmp_path):
    d = tmp_path / "petpack_motion"
    d.mkdir()
    states = App.PACK_STATES + ("working_b",) + App.PACK_INTERACTIONS + App.PACK_MOTION
    for state in states:
        (d / f"pet_{state}.jpg").write_bytes(b"jpeg")
    app = App.__new__(App)
    app._pack_lock = threading.Lock()
    app.petpack_base = lambda: tmp_path
    sent, deleted = [], []
    app.send_bg_image = lambda data, name: sent.append(name) or True
    app.delete_album_image = lambda name: deleted.append(name) or True
    result = app.apply_petpack("motion")
    assert result["ok"] and result["sent"] == len(states)
    assert app.pack_progress["total"] == len(states)
    assert sent == [f"pet_{state}.jpg" for state in states]
    assert deleted == ["pet_custom.jpg"]


def test_failed_frame_removes_stale_image_from_previous_pack(tmp_path):
    d = tmp_path / "petpack_partial"
    d.mkdir()
    for state in App.PACK_STATES:
        (d / f"pet_{state}.jpg").write_bytes(b"jpeg")
    app = App.__new__(App)
    app._pack_lock = threading.Lock()
    app.petpack_base = lambda: tmp_path
    deleted = []
    app.send_bg_image = lambda data, name: name != "pet_working.jpg"
    app.delete_album_image = lambda name: deleted.append(name) or True
    app._last_bg_result = {"reason": "CRC_MISMATCH"}
    result = app.apply_petpack("partial")
    assert not result["ok"] and "working" in result["failed"]
    assert "pet_working.jpg" in deleted
