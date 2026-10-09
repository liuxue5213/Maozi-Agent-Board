#!/usr/bin/env python3
"""M1 可行性闸门（SMC_PRIVILEGED_PLAN §6）：确认 root 下 SMC 温度/风扇可读。

用法（AI 沙箱内没有 sudo，由你在终端执行）：
    sudo python3 probes/mac/probe_smc.py

判读：
    输出 TC0P/F0Ac 数值 → M1 通过（root 可读，特权 helper 方案成立）。
    kIOReturnNotPrivileged(0xe00002c7) → root 也被拒，项目按计划终止。
只读探测，不写任何 SMC 键、不装任何东西。
"""
import ctypes
import ctypes.util
import sys

KERNEL_INDEX_SMC = 2
CMD_READ_KEYINFO = 9
CMD_READ_BYTES = 5


class SMCVersion(ctypes.Structure):
    _fields_ = [("major", ctypes.c_uint8), ("minor", ctypes.c_uint8),
                ("build", ctypes.c_uint8), ("revis", ctypes.c_uint8),
                ("release", ctypes.c_uint16)]


class SMCPLimitData(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint16), ("length", ctypes.c_uint16),
                ("cpuPLimit", ctypes.c_uint32), ("gpuPLimit", ctypes.c_uint32),
                ("memPLimit", ctypes.c_uint32)]


class SMCKeyInfoData(ctypes.Structure):
    _fields_ = [("dataSize", ctypes.c_uint32),
                ("dataType", ctypes.c_char * 4),
                ("dataAttributes", ctypes.c_uint8)]


class SMCBytes(ctypes.Array):
    _type_ = ctypes.c_uint8
    _length_ = 32


class SMCParamStruct(ctypes.Structure):
    _fields_ = [("key", ctypes.c_char * 4),
                ("vers", SMCVersion),
                ("pLimitData", SMCPLimitData),
                ("keyInfo", SMCKeyInfoData),
                ("result", ctypes.c_uint8),
                ("status", ctypes.c_uint8),
                ("data8", ctypes.c_uint8),
                ("data32", ctypes.c_uint32),
                ("bytes", SMCBytes),
                # 内核侧结构是 80 字节；自然对齐只算出 76。尾部加 8 字节安全垫，
                # 显式传 80——尺寸小于内核预期会导致越界拷贝（实测直接段错误）。
                ("pad", ctypes.c_uint8 * 8)]


SMC_STRUCT_SIZE = 80


def load_iokit():
    lib = ctypes.CDLL(ctypes.util.find_library("IOKit"), use_errno=True)
    # 不设 argtypes 直接调 IOKit 会段错误（本机实测），全部显式声明
    lib.IOServiceMatching.restype = ctypes.c_void_p
    lib.IOServiceMatching.argtypes = [ctypes.c_char_p]
    lib.IOServiceGetMatchingService.restype = ctypes.c_uint32
    lib.IOServiceGetMatchingService.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    lib.IOServiceOpen.restype = ctypes.c_int
    lib.IOServiceOpen.argtypes = [ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    lib.IOConnectCallStructMethod.restype = ctypes.c_int
    # ⚠️ 最后一个参数是 size_t*（内核回写实际输出尺寸），必须传指针——
    # 按值传会让内核往小地址写 → 进程段错误（本机实测，还弹了系统崩溃框）。
    lib.IOConnectCallStructMethod.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    lib.mach_task_self.restype = ctypes.c_uint32
    return lib


def smc_connect(lib):
    matching = lib.IOServiceMatching(b"AppleSMC")
    service = lib.IOServiceGetMatchingService(0, matching)  # kIOMasterPortDefault
    if not service:
        return None, "未找到 AppleSMC 服务"
    connect = ctypes.c_void_p()
    kr = lib.IOServiceOpen(service, lib.mach_task_self(), KERNEL_INDEX_SMC,
                           ctypes.byref(connect))
    if kr != 0 or not connect.value:
        return None, f"IOServiceOpen 失败 kr={kr:#x}"
    return connect, None


def smc_read_key(lib, connect, key: str):
    """返回 (data: bytes|None, dtype: bytes|None, err: str|None)。"""
    inp = SMCParamStruct()
    inp.key = key.encode()
    inp.data8 = CMD_READ_KEYINFO
    out = SMCParamStruct()
    out_size = ctypes.c_size_t(SMC_STRUCT_SIZE)
    kr = lib.IOConnectCallStructMethod(
        connect, CMD_READ_KEYINFO, ctypes.byref(inp), SMC_STRUCT_SIZE,
        ctypes.byref(out), ctypes.byref(out_size))
    if kr != 0:
        return None, None, f"KEYINFO kr={kr:#x}"
    if out.result != 0:
        return None, None, f"result={out.result:#x}"
    size, dtype = out.keyInfo.dataSize, bytes(out.keyInfo.dataType)

    inp2 = SMCParamStruct()
    inp2.key = key.encode()
    inp2.keyInfo.dataSize = size
    inp2.data8 = CMD_READ_BYTES
    out2 = SMCParamStruct()
    out2_size = ctypes.c_size_t(SMC_STRUCT_SIZE)
    kr = lib.IOConnectCallStructMethod(
        connect, CMD_READ_BYTES, ctypes.byref(inp2), SMC_STRUCT_SIZE,
        ctypes.byref(out2), ctypes.byref(out2_size))
    if kr != 0:
        return None, None, f"READ kr={kr:#x}"
    if out2.result != 0:
        return None, None, f"result={out2.result:#x}"
    return bytes(out2.bytes[:size]), dtype, None


def read_value(lib, connect, key):
    """按 dataType 解析为浮点。返回 (value|None, err|None)。"""
    data, dtype, err = smc_read_key(lib, connect, key)
    if err:
        return None, err
    if not data:
        return None, "空数据"
    if dtype == b"flts" and len(data) == 4:          # IEEE 754 float
        return ctypes.cast(data, ctypes.POINTER(ctypes.c_float)).contents.value, None
    if dtype in (b"fp88", b"sp78") and len(data) == 2:  # 定点 8.8
        return (data[0] * 256 + data[1]) / 256.0, None
    if dtype == b"ui8 " and len(data) == 1:
        return float(data[0]), None
    if dtype == b"ui16" and len(data) == 2:
        return float(data[0] * 256 + data[1]), None
    return None, f"未知数据类型 {dtype!r}（size={len(data)}）"


TEMP_KEYS = ["TC0P", "TC0E", "TC0D", "TC0H", "Tp09", "Tp01"]


def pretty_err(err):
    """把常见 kr 翻译成人话（T2 机器非 root 时的预期值）。"""
    if err and "-0x1ffffd39" in err:
        return f"{err} = kIOReturnNotPrivileged，需 root（sudo）"
    return err
FAN_KEYS = ["F0Ac", "F0Af", "F1Ac"]   # SMC 键固定 4 字符


def main() -> int:
    lib = load_iokit()
    connect, err = smc_connect(lib)
    if err:
        print(f"❌ {err}")
        return 1
    print("✅ AppleSMC 已连接")

    temp_key = fan_key = None
    for key in TEMP_KEYS:
        value, err = read_value(lib, connect, key)
        if err is None and value is not None and -10 < value < 130:
            print(f"   温度 {key} = {value:.1f} ℃")
            temp_key = temp_key or key
        else:
            print(f"   温度 {key} = 不可读（{pretty_err(err)}）")
    for key in FAN_KEYS:
        value, err = read_value(lib, connect, key)
        if err is None and value is not None and value >= 0:
            print(f"   风扇 {key} = {value:.0f} RPM")
            fan_key = fan_key or key
        else:
            print(f"   风扇 {key} = 不可读（{pretty_err(err)}）")

    if temp_key:
        print(f"\n🎯 M1 通过：root 可读温度（{temp_key}）"
              + (f"、风扇（{fan_key}）" if fan_key else "；风扇键可后续排查")
              + "\n   下一步：M2 helper（client/tools/smc/maozi-smcd.c）")
        return 0
    print("\n❌ M1 失败：root 下也读不到温度。"
          "若错误为 0xe00002c7（NotPrivileged），说明系统进一步收紧，"
          "按计划终止项目、无沉没成本。")
    return 2


if __name__ == "__main__":
    sys.exit(main())
