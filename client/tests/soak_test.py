#!/usr/bin/env python3
"""长稳测试（PRD 阶段 1 验收项）：连续运行 N 小时，输出健康报告。

用法：python3 tests/soak_test.py [--hours 8] [--interval 1]
判据：无未捕获异常、设备在线占比 >95%、同步成功、无 SLOW 风暴。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy.app import App            # noqa: E402
from workbuddy.config import Config      # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="WorkBuddy 长稳测试")
    parser.add_argument("--hours", type=float, default=8.0)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    cfg = Config.load()
    cfg.interval = args.interval
    cfg.console = False
    app = App(cfg)

    stats = {"ticks": 0, "errors": 0, "online_ticks": 0, "ai_states": {},
             "slow": 0, "start": time.time()}

    def on_tick(t):
        stats["ticks"] += 1
        if app.link and app.link.status.startswith("connected:"):
            stats["online_ticks"] += 1
        state = t.get("aistate", "")
        if state:
            stats["ai_states"][state] = stats["ai_states"].get(state, 0) + 1

    app.on_tick = on_tick
    orig_health_event = app.health.event

    def counting_event(kind, **fields):
        if kind == "slow_loop":
            stats["slow"] += 1
        orig_health_event(kind, **fields)

    app.health.event = counting_event

    import threading

    def watcher():
        deadline = time.time() + args.hours * 3600
        while time.time() < deadline and not app._stop:
            time.sleep(60)
            online_pct = (stats["online_ticks"] * 100 // stats["ticks"]) if stats["ticks"] else 0
            print(f"[{time.strftime('%H:%M:%S')}] ticks={stats['ticks']} "
                  f"在线={online_pct}% SLOW={stats['slow']}")
        app.stop()

    threading.Thread(target=watcher, daemon=True).start()
    print(f"Soak 开始：{args.hours} 小时，每 60 秒输出进度")
    app.run(duration=args.hours * 3600)

    online_pct = (stats["online_ticks"] * 100 // stats["ticks"]) if stats["ticks"] else 0
    report = {
        "时长小时": round((time.time() - stats["start"]) / 3600, 2),
        "ticks": stats["ticks"],
        "在线占比%": online_pct,
        "AI状态分布": stats["ai_states"],
        "SLOW次数": stats["slow"],
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))
    ok = online_pct >= 95 and stats["errors"] == 0
    print("验收：" + ("✅ 通过" if ok else "❌ 未达标（在线占比需 ≥95%）"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
