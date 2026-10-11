#!/usr/bin/env python3
"""直接串口取设备屏实拍，存成 PNG。不依赖客户端，因此不受客户端遥测干扰。

**为什么需要这个工具**：工作台的「实拍设备屏幕」在客户端运行时**收不到整帧**——
设备发一帧是 160×240×2 = 76800 字节，base64 分帧后约 113KB，115200 波特率下要
约 10 秒；这 10 秒里设备主循环完全阻塞，而客户端仍在每秒向下写遥测，传输途中
就会丢帧，`_finish_snapshot` 永远等不到 END（客户端还不会记日志，表现为「点了
实拍没反应」）。把客户端停掉、用本脚本直接读串口，同一台设备同一帧：
854 个 DATA 帧 + END、76800 字节、零解码失败。

所以工作流是：
    1. pkill -f "workbuddy run"        # 释放串口
    2. python3 tools/device_snapshot.py -o /tmp/screen.png
    3. 重新启动客户端

用法：
    python3 tools/device_snapshot.py [-p /dev/cu.usbserial-XXXX] [-o out.png]
                                     [--wait 6]   # 拍第二张前等待秒数（验证秒针用）
"""
from __future__ import annotations

import argparse
import base64
import struct
import sys
import time
import zlib
from pathlib import Path

try:
    import serial
except ImportError:  # pragma: no cover - 环境缺依赖时给出可执行的提示
    sys.exit("需要 pyserial：pip3 install pyserial")


def read_frame(ser, timeout: float = 40.0):
    """读一帧快照：SNAP=BEGIN,w,h → SNAP=DATA,seq,b64 … → SNAP=END。"""
    buf = meta = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = ser.readline().decode("ascii", "ignore").strip()
        if not line.startswith("SNAP="):
            continue
        body = line[5:]
        if body.startswith("BEGIN,"):
            parts = body.split(",")
            meta, buf = (int(parts[1]), int(parts[2])), bytearray()
        elif body.startswith("DATA,") and buf is not None:
            b64 = body.split(",", 2)[2]
            buf += base64.b64decode(b64 + "=" * (-len(b64) % 4))
        elif body == "END" and buf is not None:
            return meta, bytes(buf)
    return meta, None


def rgb565_to_rgb(raw: bytes, w: int, h: int) -> bytes:
    """RGB565（小端）→ RGB888 行数据（PNG 的 filter byte 逐行补 0）。"""
    out = bytearray()
    for y in range(h):
        out.append(0)
        for x in range(w):
            i = (y * w + x) * 2
            v = raw[i] | (raw[i + 1] << 8)
            r5, g6, b5 = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
            out += bytes(((r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2)))
    return bytes(out)


def write_png(rgb_rows: bytes, w: int, h: int, path: Path) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rgb_rows, 6))
        + chunk(b"IEND", b""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--port", default="/dev/cu.usbserial-A5069RR4")
    ap.add_argument("-o", "--out", default="/tmp/device-screen.png")
    ap.add_argument("--wait", type=float, default=0.0,
                    help="拍第二张前等待秒数（给个 6 秒就能比对秒针是否在走）")
    args = ap.parse_args()

    with serial.Serial(args.port, 921600, timeout=0.5) as ser:
        time.sleep(0.4)
        ser.reset_input_buffer()
        for index, suffix in enumerate(("", "-2") if args.wait else ("",)):
            if index:
                time.sleep(args.wait)
            ser.write(b"SNAP=1\n")
            ser.flush()
            meta, raw = read_frame(ser)
            if not raw or not meta:
                print("取帧失败：设备无响应或帧不完整")
                return 1
            expected = meta[0] * meta[1] * 2
            if len(raw) != expected:
                print(f"取帧失败：收到 {len(raw)} 字节，应为 {expected}（丢帧）")
                return 1
            out = Path(args.out if not suffix else
                       str(Path(args.out).with_name(Path(args.out).stem + suffix + ".png")))
            write_png(rgb565_to_rgb(raw, *meta), meta[0], meta[1], out)
            print(f"{meta[0]}×{meta[1]} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
