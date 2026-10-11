"""CLI 入口：python3 -m workbuddy run|once|ports"""
import argparse
import os
import sys
import time
from pathlib import Path

PID_PATH = Path.home() / ".workbuddy" / "workbuddy.pid"


def _acquire_instance_lock() -> bool:
    """单实例锁：两个客户端同时跑会抢同一个串口（症状：遥测错乱、互踢断线）。

    PID 文件记录在跑实例；崩溃残留的陈旧 PID 用 os.kill(pid,0) 探活清除。
    """
    try:
        PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        if PID_PATH.exists():
            try:
                old = int(PID_PATH.read_text().strip())
                if old != os.getpid():
                    os.kill(old, 0)          # 探活：活着就拒绝，异常=陈旧
                    print(f"[workbuddy] 已有实例在运行（PID {old}），本次退出。"
                          f"如确认无残留可删除 {PID_PATH}")
                    return False
            except (ValueError, ProcessLookupError, PermissionError):
                pass                          # 陈旧/损坏的 PID 文件，接管
        PID_PATH.write_text(str(os.getpid()))
    except OSError as exc:
        print(f"[workbuddy] 单实例锁不可用（{exc}），继续启动")
    return True


def _release_instance_lock():
    try:
        if PID_PATH.exists() and PID_PATH.read_text().strip() == str(os.getpid()):
            PID_PATH.unlink()
    except OSError:
        pass


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
    p_run.add_argument("--tray", action="store_true",
                       help="托盘常驻模式：关闭窗口不退到通知区域继续运行")
    p_run.add_argument("--no-window", action="store_true",
                       help="托盘模式下不打开原生窗口，只驻留托盘")
    p_run.add_argument("--mini", action="store_true",
                       help="同时打开置顶迷你悬浮窗（类副屏，优化#4）")

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
    if cmd == "run" and not _acquire_instance_lock():
        return 1
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

    mini_window = None
    if cmd == "run" and args.mini:
        # 优化#4：置顶迷你悬浮窗（类副屏）。等 Web 服务起来再开窗，
        # 失败只提示不阻塞主流程（无 pywebview 时退回浏览器打开 /mini）。
        from .mini import start_mini
        import urllib.request as _rq
        deadline = time.time() + 8
        ready = False
        while time.time() < deadline:
            try:
                _rq.urlopen(f"http://127.0.0.1:{cfg.web_port}/mini", timeout=0.6)
                ready = True
                break
            except Exception:
                time.sleep(0.25)
        if ready:
            try:
                mini_window = start_mini(f"http://127.0.0.1:{cfg.web_port}/mini")
            except Exception as exc:
                print(f"迷你悬浮窗启动失败（{exc}），可浏览器打开 "
                      f"http://127.0.0.1:{cfg.web_port}/mini")
        else:
            print("Web 服务未就绪，迷你悬浮窗未启动")

    if cmd == "run" and args.tray:
        # 托盘常驻：采集循环退到后台线程，主线程留给窗口/托盘
        from .tray import run_with_tray, tray_available
        if not tray_available():
            print("未安装托盘依赖（pystray / Pillow），退回普通常驻模式。"
                  "安装：pip install pystray pillow")
            app.run(cfg.duration)
            return 0
        return run_with_tray(app, cfg, use_window=not args.no_window)

    try:
        app.run(cfg.duration)
    except KeyboardInterrupt:
        app.shutdown()
    finally:
        if mini_window is not None:
            mini_window.stop()
        if cmd == "run":
            _release_instance_lock()
    return 0


def encode_frame(t, caps):
    from .protocol import encode_caps, encode_telemetry
    return encode_caps(caps) + encode_telemetry(t)


if __name__ == "__main__":
    sys.exit(main())
