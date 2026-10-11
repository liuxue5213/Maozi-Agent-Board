"""串口链路：自动扫描 ESP32（VID 0x303A）、断线自动重连、上行解析回调。"""
import os
import queue
import threading
import time

# WB_SERIAL_DEBUG=1 时打印非遥测的收发行（排除 AI/DN 刷屏），排查协议层问题用
_SERIAL_DEBUG = os.environ.get("WB_SERIAL_DEBUG") == "1"

try:
    import serial
    import serial.tools.list_ports
    HAVE_SERIAL = True
except ImportError:
    HAVE_SERIAL = False


class SerialLink:
    def __init__(self, cfg, on_uplink=None):
        self.cfg = cfg
        self.on_uplink = on_uplink
        self._q = queue.Queue(maxsize=256)
        self._port = None
        self._stop = False
        self._just_connected = False
        self._last_rx_at = 0.0
        self._rxbuf = bytearray()       # 跨批次保留的不完整行
        if not cfg.serial:
            self._status = "disabled"
        elif not HAVE_SERIAL:
            self._status = "no-pyserial"
        else:
            self._status = "scanning"
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        if self._status not in ("disabled", "no-pyserial"):
            self._thread.start()

    def stop(self):
        self._stop = True

    @property
    def status(self) -> str:
        return self._status

    def consume_connected(self) -> bool:
        """连接建立瞬间返回一次 True（用于发 CAPS + 首帧快照）。"""
        if self._just_connected:
            self._just_connected = False
            return True
        return False

    def send(self, text):
        """text 为 str（自动编码）或 bytes（如背景图分帧，原样发送）。"""
        if self._port is None:
            return
        data = text if isinstance(text, (bytes, bytearray)) else \
            text.encode("utf-8", errors="replace")
        try:
            self._q.put_nowait(data)
        except queue.Full:
            try:
                self._q.get_nowait()
                self._q.put_nowait(data)
            except queue.Empty:
                pass

    # ---- 后台线程 ----

    def _scan(self):
        for p in serial.tools.list_ports.comports():
            if self.cfg.port:
                if p.device == self.cfg.port:
                    return p.device
                continue
            if (p.vid or 0) == self.cfg.vid:
                return p.device
            name = f"{p.device} {p.description}".lower()
            if "usbmodem" in name or "usbserial" in name:
                return p.device
        return None

    def _run(self):
        from .protocol import parse_uplink

        while not self._stop:
            if self._port is None:
                dev = self._scan()
                if dev:
                    try:
                        self._port = serial.Serial(dev, 921600, timeout=0.2, write_timeout=0.2)
                        self._status = f"connected:{dev}"
                        self._last_rx_at = time.monotonic()
                        self._just_connected = True
                    except Exception:
                        self._status = "scanning"
                        time.sleep(2)
                else:
                    self._status = "waiting"
                    time.sleep(3)
                continue

            try:
                while True:
                    data = self._q.get_nowait()
                    self._port.write(data)
                    if _SERIAL_DEBUG and not data.startswith(b"AI") \
                            and not data.startswith(b"DN"):
                        print(f"[tx {time.strftime('%H:%M:%S')}] "
                              f"{data[:80]!r}", flush=True)
            except queue.Empty:
                pass
            except Exception:
                self._drop()
                continue

            try:
                # 批量排水：FTDI 驱动 RX 缓冲只有几 KB，921600 下 ~92KB/s，
                # 缓冲 40ms 就满——逐行 readline 的 Python 开销叠加 web 线程
                # 抢 GIL，曾稳定丢 15-25% 的实拍帧（直读无此问题）。一次性
                # 读走 in_waiting 全部字节再本地分行，溢出窗口缩到一个 syscall。
                raw = self._port.read(max(1, self._port.in_waiting))
                if raw:
                    self._last_rx_at = time.monotonic()
                    self._status = f"connected:{self._port.port}"
                    self._rxbuf += raw
                    if _SERIAL_DEBUG:
                        print(f"[rx {time.strftime('%H:%M:%S')}] "
                              f"{raw[:120]!r}", flush=True)
                    while True:
                        nl = self._rxbuf.find(b"\n")
                        if nl < 0:
                            break
                        line = bytes(self._rxbuf[:nl])
                        del self._rxbuf[:nl + 1]
                        if not line:
                            continue
                        d = parse_uplink(line.decode("utf-8", errors="ignore"))
                        if d and self.on_uplink:
                            self.on_uplink(d)
                    if len(self._rxbuf) > 8192:
                        self._rxbuf.clear()   # 坏流防御：半行残渣不无限累积
                    continue
                if time.monotonic() - self._last_rx_at > 15.0:
                    self._status = f"stale:{self._port.port}"
            except Exception:
                self._drop()
                continue
            time.sleep(0.02)

    def _drop(self):
        self._rxbuf.clear()
        try:
            if self._port is not None:
                self._port.close()
        except Exception:
            pass
        self._port = None
        self._status = "waiting"
        # 断线前排队的遥测不能在重连后作为新数据发出。
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
