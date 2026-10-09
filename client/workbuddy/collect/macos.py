"""macOS 扩展采集：SMC 温度/风扇（非 T2 机器可用，T2 机器自动禁用）+ 基频 + GPU 型号。"""
import subprocess
import ctypes
import ctypes.util
import struct
import threading

from .common import Collector

SMC_KEYS = {"cputemp": "TC0P", "gputemp": "TG0D", "fan": "F0Ac", "fan2": "F1Ac"}


class MacCollector(Collector):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.caps.update({"cpu_temp": False, "fan": False})
        self._smc = _SMC()
        self._last_smc = 0.0
        self._extra = {}
        self._gpu_name = ""
        threading.Thread(target=self._detect_gpu, daemon=True).start()

    def _detect_gpu(self):
        try:
            out = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=15,
            ).stdout
            names = []
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("Chipset Model:"):
                    name = line.split(":", 1)[1].strip()
                    if name and name not in names:
                        names.append(name)
            self._gpu_name = " + ".join(names)
        except Exception:
            self._gpu_name = ""

    def sample(self, now: float) -> dict:
        t = super().sample(now)
        if not self._smc.ok and self._smc.tried:
            return t
        if now - self._last_smc < 5.0:
            t.update({k: v for k, v in self._extra.items() if v is not None})
            return t
        self._last_smc = now
        vals = self._smc.read_all()
        if vals is None:  # T2 机器：SMC 被拒，永久禁用
            return t
        self._extra = vals
        self.caps["cpu_temp"] = vals.get("cputemp") is not None
        self.caps["fan"] = vals.get("fan") is not None
        t.update({k: v for k, v in vals.items() if v is not None})
        return t


class _SMC:
    """AppleSMC 免 sudo 读取。T2 机器返回 IPC 错误 -> ok=False。"""

    def __init__(self):
        self.tried = False
        self.ok = False
        self._conn = None
        self._lib = None

    def _open(self):
        if self.tried:
            return self.ok
        self.tried = True
        try:
            iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
            libc = ctypes.CDLL(None)
            task_port = ctypes.c_uint.in_dll(libc, "mach_task_self_").value

            class V(ctypes.Structure):
                _fields_ = [(n, ctypes.c_char) for n in ("a", "b", "c", "d")] + [
                    ("release", ctypes.c_uint16)]

            class P(ctypes.Structure):
                _fields_ = [("version", ctypes.c_uint16), ("length", ctypes.c_uint16),
                            ("flags", ctypes.c_uint32)]

            class KI(ctypes.Structure):
                _fields_ = [("dataSize", ctypes.c_uint64), ("dataType", ctypes.c_uint32),
                            ("dataAttributes", ctypes.c_char)]

            class KD(ctypes.Structure):
                _fields_ = [("key", ctypes.c_uint32), ("vers", V), ("plimit", P),
                            ("keyInfo", KI), ("result", ctypes.c_char), ("status", ctypes.c_char),
                            ("data", ctypes.c_ubyte * 32), ("data_size", ctypes.c_uint32)]

            self._KD = KD
            iokit.IOServiceMatching.restype = ctypes.c_void_p
            iokit.IOServiceGetMatchingService.restype = ctypes.c_void_p
            iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, ctypes.c_void_p]
            iokit.IOServiceOpen.restype = ctypes.c_int
            iokit.IOServiceOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                                            ctypes.POINTER(ctypes.c_void_p)]
            iokit.IOConnectCallStructMethod.restype = ctypes.c_int
            iokit.IOConnectCallStructMethod.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t,
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
            iokit.IOConnectCallScalarMethod.restype = ctypes.c_int
            iokit.IOConnectCallScalarMethod.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint,
                ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint)]

            svc = iokit.IOServiceGetMatchingService(0, iokit.IOServiceMatching(b"AppleSMC"))
            if not svc:
                return False
            conn = ctypes.c_void_p()
            if iokit.IOServiceOpen(ctypes.c_void_p(svc), task_port, 0, ctypes.byref(conn)):
                return False
            if iokit.IOConnectCallScalarMethod(conn, 0, None, 0, None, None):
                return False
            self._lib = iokit
            self._conn = conn
            # 试读一个键验证本机是否允许（T2 会在这里失败）
            if self._read_key(int.from_bytes(b"TC0P", "big")) is None:
                self._close()
                return False
            self.ok = True
            return True
        except Exception:
            return False

    def _close(self):
        try:
            self._lib.IOServiceClose(self._conn)
        except Exception:
            pass
        self._conn = None

    def _read_key(self, key_int):
        try:
            KD = self._KD
            inp = KD()
            inp.key = key_int
            out = KD()
            size = ctypes.c_size_t(ctypes.sizeof(KD))
            if self._lib.IOConnectCallStructMethod(
                    self._conn, 5, ctypes.byref(inp), ctypes.sizeof(KD),
                    ctypes.byref(out), ctypes.byref(size)):
                return None
            inp.data_size = out.keyInfo.dataSize
            out = KD()
            if self._lib.IOConnectCallStructMethod(
                    self._conn, 5, ctypes.byref(inp), ctypes.sizeof(KD),
                    ctypes.byref(out), ctypes.byref(size)):
                return None
            dt = out.keyInfo.dataType.to_bytes(4, "big")
            data = bytes(out.data)[: inp.data_size]
            if dt == b"sp78":
                return struct.unpack(">h", data[:2])[0] / 256.0
            if dt == b"flt ":
                return struct.unpack(">f", data[:4])[0]
            if dt == b"fpe2":
                return ((data[0] << 8) | data[1]) / 4.0
        except Exception:
            return None
        return None

    def read_all(self):
        if not self._open():
            return None
        vals = {}
        for name, key in SMC_KEYS.items():
            v = self._read_key(int.from_bytes(key.encode(), "big"))
            if v is not None:
                vals[name] = round(v, 1) if "temp" in name else round(v)
        return vals
