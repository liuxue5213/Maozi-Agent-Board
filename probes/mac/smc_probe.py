#!/usr/bin/env python3
"""Enumerate macOS SMC sensor keys and read temperatures/fan speeds without sudo."""
import ctypes
import ctypes.util
import struct

iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"), use_errno=True)
libc = ctypes.CDLL(None)

# --- IOKit / Mach signatures ---
iokit.IOServiceMatching.restype = ctypes.c_void_p
iokit.IOServiceGetMatchingService.restype = ctypes.c_void_p
iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, ctypes.c_void_p]
iokit.IOServiceOpen.restype = ctypes.c_int
iokit.IOServiceOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                                ctypes.POINTER(ctypes.c_void_p)]
iokit.IOServiceClose.argtypes = [ctypes.c_void_p]
iokit.IOConnectCallStructMethod.restype = ctypes.c_int
iokit.IOConnectCallStructMethod.argtypes = [
    ctypes.c_void_p, ctypes.c_uint,
    ctypes.c_void_p, ctypes.c_size_t,
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
]
iokit.IOConnectCallScalarMethod.restype = ctypes.c_int
iokit.IOConnectCallScalarMethod.argtypes = [
    ctypes.c_void_p, ctypes.c_uint,
    ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint,
    ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint),
]
libc.mach_task_self_.restype = ctypes.c_uint
libc.mach_task_self_.argtypes = None
# mach_task_self_ is an exported DATA symbol, not a function
TASK_PORT = ctypes.c_uint.in_dll(libc, "mach_task_self_").value


class SMCVersion(ctypes.Structure):
    _fields_ = [("major", ctypes.c_char), ("minor", ctypes.c_char),
                ("build", ctypes.c_char), ("reserved", ctypes.c_char),
                ("release", ctypes.c_uint16)]


class SMCPLimitData(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint16), ("length", ctypes.c_uint16),
                ("flags", ctypes.c_uint32)]


class SMCKeyInfoData(ctypes.Structure):
    # IOByteCount is 64-bit under LP64 -> total SMCKeyData size must be 80
    _fields_ = [("dataSize", ctypes.c_uint64), ("dataType", ctypes.c_uint32),
                ("dataAttributes", ctypes.c_char)]


class SMCKeyData(ctypes.Structure):
    _fields_ = [("key", ctypes.c_uint32), ("vers", SMCVersion),
                ("plimit", SMCPLimitData), ("keyInfo", SMCKeyInfoData),
                ("result", ctypes.c_char), ("status", ctypes.c_char),
                ("data", ctypes.c_ubyte * 32), ("dataSize", ctypes.c_uint32)]


SZ = ctypes.sizeof(SMCKeyData)

conn = ctypes.c_void_p()
service = iokit.IOServiceGetMatchingService(0, iokit.IOServiceMatching(b"AppleSMC"))
if not service:
    print("AppleSMC service not found")
    raise SystemExit(1)
r = iokit.IOServiceOpen(ctypes.c_void_p(service), TASK_PORT, 0, ctypes.byref(conn))
if r != 0:
    print(f"IOServiceOpen failed: 0x{r:x} (may need sudo)")
    raise SystemExit(1)

# open user client (selector 0)
iokit.IOConnectCallScalarMethod(conn, 0, None, 0, None, None)


def call(selector, inp):
    out = SMCKeyData()
    size = ctypes.c_size_t(SZ)
    r = iokit.IOConnectCallStructMethod(conn, selector, ctypes.byref(inp), SZ,
                                        ctypes.byref(out), ctypes.byref(size))
    return r, out


def read_key(key_int):
    inp = SMCKeyData()
    inp.key = key_int
    inp.dataSize = 0
    r, out = call(5, inp)  # kSMCUserClientReadKey
    if r != 0:
        return None
    inp.dataSize = out.keyInfo.dataSize
    r, out = call(5, inp)
    if r != 0:
        return None
    return (out.keyInfo.dataType, bytes(out.data)[:inp.dataSize])


def decode(data_type, data):
    dt = data_type.to_bytes(4, "big").decode("ascii", "ignore")
    try:
        if dt == "sp78":
            return dt, struct.unpack(">h", data[:2])[0] / 256.0
        if dt == "flt ":
            for fmt in (">f", "<f"):
                v = struct.unpack(fmt, data[:4])[0]
                if -100 < v < 200:
                    return dt, v
            return dt, None
        if dt == "fpe2":
            return dt, ((data[0] << 8) | data[1]) / 4.0
        if dt == "ui8 ":
            return dt, list(data)
        if dt == "ui16":
            return dt, struct.unpack(">H", data[:2])[0]
        return dt, None
    except Exception:
        return dt, None


# --- enumerate all keys via kSMCUserClientReadIndex (selector 8) ---
keys = {}  # name -> (key_int, data_type_str, size)
idx = 0
while idx < 3000:
    inp = SMCKeyData()
    inp.key = idx
    inp.dataSize = 4
    r, out = call(8, inp)
    if r != 0 or out.key == 0:
        break
    key_int = out.key
    name = key_int.to_bytes(4, "big").decode("ascii", "ignore")
    info = read_key(key_int)
    if info:
        dt, data = info
        dts = dt.to_bytes(4, "big").decode("ascii", "ignore")
        keys[name] = (key_int, dts, len(data))
    idx += 1

print(f"total SMC keys enumerated: {len(keys)}")

# --- temperatures: type sp78/flt, name starts with T ---
print("\n--- TEMPERATURES ---")
for name in sorted(keys):
    key_int, dts, n = keys[name]
    if not name.startswith("T") or dts not in ("sp78", "flt "):
        continue
    info = read_key(key_int)
    if not info:
        continue
    _, (dt, v) = name, decode(*info)
    if v is not None and -30 < v < 160:
        print(f"{name}: {v:.1f} C  ({dt})")

# --- fans ---
print("\n--- FANS ---")
if "FNum" in keys:
    info = read_key(keys["FNum"][0])
    if info:
        print(f"fan count: {info[1][0]}")
for name in sorted(keys):
    key_int, dts, n = keys[name]
    if dts == "fpe2" and name.startswith("F"):
        info = read_key(key_int)
        if info:
            _, v = decode(*info)
            if v is not None and 0 <= v < 20000:
                print(f"{name}: {v:.0f} RPM")

iokit.IOServiceClose(conn)
