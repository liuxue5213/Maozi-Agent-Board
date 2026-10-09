"""CLI 入口：python3 -m workbuddy run|once|ports"""
import argparse
import sys
import time


def main(argv=None):
    from . import __version__
    from .config import Config

    parser = argparse.ArgumentParser(
        prog="workbuddy", description="AI WorkBuddy 桌面智能状态屏客户端")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="常驻运行（默认）")
    p_run.add_argument("--interval", type=float, default=1.0)
    p_run.add_argument("--console-interval", type=float, default=1.0,
                       help="控制台打印间隔秒（默认 1，0.5~60）")
    p_run.add_argument("--duration", type=float, default=0.0, help="运行 N 秒后退出（调试用）")
    p_run.add_argument("--port", default="", help="强制串口路径")
    p_run.add_argument("--no-serial", action="store_true")
    p_run.add_argument("--no-console", action="store_true")
    p_run.add_argument("--no-ai", action="store_true")
    p_run.add_argument("--no-web", action="store_true")
    p_run.add_argument("--web-port", type=int, default=8765)
    p_run.add_argument("--open-settings", action="store_true")
    p_run.add_argument("--disk", default="/", help="监控磁盘路径")

    sub.add_parser("once", help="单次采集并打印协议帧，退出")
    sub.add_parser("ports", help="列出串口设备")

    args = parser.parse_args(argv)
    cmd = args.cmd or "run"

    if cmd == "ports":
        try:
            import serial.tools.list_ports
            for p in serial.tools.list_ports.comports():
                vid = f"{p.vid:#06x}" if p.vid else "------"
                pid = f"{p.pid:#06x}" if p.pid else "------"
                print(f"{p.device}  VID:PID={vid}:{pid}  {p.description}")
        except ImportError:
            print("未安装 pyserial")
        return 0

    cfg = Config.load()
    if cmd == "run":
        cfg.interval = args.interval
        cfg.duration = args.duration
        cfg.console_interval = max(0.1, args.console_interval)
        if args.port:
            cfg.port = args.port
        if args.no_serial:
            cfg.serial = False
        if args.no_console:
            cfg.console = False
        if args.no_ai:
            cfg.ai_watch = False
        if args.no_web:
            cfg.web = False
        cfg.web_port = args.web_port
        cfg.open_settings = args.open_settings
        cfg.disk_path = args.disk
    else:  # once
        cfg.serial = False
        cfg.duration = 0

    from .app import App
    app = App(cfg)

    if cmd == "once":
        t = app.tick(time.time())
        print(app.render(t))
        print("--- 协议帧 ---")
        print(encode_frame(t, app.collector.caps))
        return 0

    app.run(cfg.duration)
    return 0


def encode_frame(t, caps):
    from .protocol import encode_caps, encode_telemetry
    return encode_caps(caps) + encode_telemetry(t)


if __name__ == "__main__":
    sys.exit(main())
