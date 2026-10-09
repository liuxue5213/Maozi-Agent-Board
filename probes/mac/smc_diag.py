#!/usr/bin/env python3
"""Diagnose which SMC input-field combination the user client expects."""
import ctypes
import ctypes.util
import struct

iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
libc = ctypes.CDLL(None)

iokit.IOServiceMatching.restype = ctypes.c_void_p
iokit.IOServiceGetMatchingService.restype = ctypes.c_void_p
iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, ctypes.c_void_p]
iokit.IOServiceOpen.restype = ctypes.c_int
iokit.IOServiceOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                                ctypes.POINTER(ctypes.c_void_p)]
iokit.IOServiceClose.argtypes = [ctypes.c_void_p]
iokit.IOConnectCallStructMethod.restype = ctypes.c_int
iokit.IOConnectCallStructMethod.argtypes = [
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
iokit.IOConnectCallScalarMethod.restype = ctypes.c_int
iokit.IOConnectCallScalarMethod.argtypes = [
    ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint,
    ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint)]

TASK_PORT = ctypes.c_uint.in_dll(libc, "mach_task_self_").value


class SMCVersion(ctypes.Structure):
    _fields_ = [("major", ctypes.c_char), ("minor", ctypes.c_char),
                ("build", ctypes.c_char), ("reserved", ctypes.c_char),
                ("release", ctypes.c_uint16)]


class SMCPLimitData(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint16), ("length", ctypes.c_uint16),
                ("flags", ctypes.c_uint32)]


class SMCKeyInfoData(ctypes.Structure):
    _fields_ = [("dataSize", ctypes.c_uint64), ("dataType", ctypes.c_uint32),
                ("dataAttributes", ctypes.c_char)]


class SMCKeyData(ctypes.Structure):
    _fields_ = [("key", ctypes.c_uint32), ("vers", SMCVersion),
                ("plimit", SMCPLimitData), ("keyInfo", SMCKeyInfoData),
                ("result", ctypes.c_char), ("status", ctypes.c_char),
                ("data", ctypes.c_ubyte * 32), ("data_size", ctypes.c_uint32)]
    _pack_ = 0


print("sizeof(SMCKeyData) =", ctypes.sizeof(SMCKeyData))

conn = ctypes.c_void_p()
service = iokit.IOServiceGetMatchingService(0, iokit.IOServiceMatching(b"AppleSMC"))
r = iokit.IOServiceOpen(ctypes.c_void_p(service), TASK_PORT, 0, ctypes.byref(conn))
print(f"IOServiceOpen: 0x{r:x}")
r0 = iokit.IOConnectCallScalarMethod(conn, 0, None, 0, None, None)
print(f"userclient open (selector 0): 0x{r0:x}")

SZ = ctypes.sizeof(SMCKeyData)


def call(selector, inp):
    out = SMCKeyData()
    size = ctypes.c_size_t(SZ)
    r = iokit.IOConnectCallStructMethod(conn, selector, ctypes.byref(inp), SZ,
                                        ctypes.byref(out), ctypes.byref(size))
    return r, out


def key_of(s):
    return int.from_bytes(s.encode(), "big")


# --- read-key 'TC0P': try variant combinations for the info call ---
ktc = key_of("TC0P")
variants = {
    "zeroed": lambda i: None,
    "keyInfo.dataSize=0": lambda i: setattr(i.keyInfo, "dataSize", 0),
    "data_size=0": lambda i: setattr(i, "data_size", 0),
    "keyInfo.dataSize=32": lambda i: setattr(i.keyInfo, "dataSize", 32),
    "data_size=32": lambda i: setattr(i, "data_size", 32),
}
for label, fn in variants.items():
    inp = SMCKeyData()
    inp.key = ktc
    fn(inp)
    r, out = call(5, inp)
    extra = ""
    if r == 0:
        extra = (f" -> out.result={out.result[0]} keyInfo.dataSize={out.keyInfo.dataSize} "
                 f"type={out.keyInfo.dataType.to_bytes(4, 'big')} data_size={out.data_size}")
    print(f"readkey info [{label}]: 0x{r:x}{extra}")

# --- read-index idx 0 variants ---
idx_variants = {
    "zeroed": lambda i: None,
    "keyInfo.dataSize=4": lambda i: setattr(i.keyInfo, "dataSize", 4),
    "data_size=4": lambda i: setattr(i, "data_size", 4),
}
for label, fn in idx_variants.items():
    inp = SMCKeyData()
    inp.key = 0
    fn(inp)
    r, out = call(8, inp)
    extra = ""
    if r == 0:
        extra = f" -> out.key={out.key.to_bytes(4, 'big')}"
    print(f"readindex [0] [{label}]: 0x{r:x}{extra}")

iokit.IOServiceClose(conn)
