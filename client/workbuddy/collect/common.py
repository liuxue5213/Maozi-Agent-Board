"""硬件采集器：common 为三平台通用层（psutil），平台模块提供扩展。"""
import os

from ..config import Config


class Collector:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.caps = {"cpu_temp": False, "gpu": False, "fan": False, "bat": False}
        self._last_net = None

    def sample(self, now: float) -> dict:
        import psutil
        import sys

        vm = psutil.virtual_memory()
        # macOS：根目录是只读系统卷，真实用量在数据卷
        disk_path = self.cfg.disk_path
        if sys.platform == "darwin" and disk_path == "/":
            data_vol = "/System/Volumes/Data"
            if os.path.isdir(data_vol):
                disk_path = data_vol
        du = psutil.disk_usage(disk_path)
        t = {
            "cpu": int(psutil.cpu_percent(interval=None)),
            "mem": int(vm.percent),
            "memused": round(vm.used / 1024**3, 1),
            "memtotal": round(vm.total / 1024**3, 1),
            "disk": int(du.percent),
            "diskfree": round(du.free / 1024**3, 1),
            "disktotal": round(du.total / 1024**3, 1),
            "uptime": int(now - psutil.boot_time()),
        }

        try:  # CPU 频率：macOS 返回基频，Windows/Linux 返回实时频率
            cf = psutil.cpu_freq()
            if cf and cf.current:
                t["cpufreq"] = int(cf.current)
        except Exception:
            pass

        bat = None
        try:
            bat = psutil.sensors_battery()
        except Exception:
            pass
        if bat is not None:
            t["bat"] = int(bat.percent)
            self.caps["bat"] = True

        rx, tx = self._net_rates(now)
        if rx is not None:
            t["netrx"], t["nettx"] = rx, tx

        t.update(self.extra(now))
        return t

    def extra(self, now: float) -> dict:
        return {}

    def _net_rates(self, now: float):
        import psutil

        try:
            counters = psutil.net_io_counters(pernic=True)
        except Exception:
            return None, None
        rx = sum(n.bytes_recv for name, n in counters.items() if not name.startswith("lo"))
        tx = sum(n.bytes_sent for name, n in counters.items() if not name.startswith("lo"))
        last = self._last_net
        self._last_net = (rx, tx, now)
        if not last:
            return None, None
        dt = now - last[2]
        if dt <= 0:
            return None, None
        return round((rx - last[0]) * 8 / 1e6 / dt, 1), round((tx - last[1]) * 8 / 1e6 / dt, 1)


def create(cfg: Config) -> Collector:
    import sys

    if sys.platform == "darwin":
        from .macos import MacCollector
        return MacCollector(cfg)
    if sys.platform == "win32":
        from .windows import WindowsCollector
        return WindowsCollector(cfg)
    from .linux import LinuxCollector
    return LinuxCollector(cfg)
