"""Linux 扩展采集：hwmon 温度/风扇 + GPU（NVML 可选 / amdgpu sysfs）。"""
import glob
from pathlib import Path

from .common import Collector

_LABEL_MAP = {"coretemp": "cputemp", "amdgpu": "gputemp", "nouveau": "gputemp"}


class LinuxCollector(Collector):
    def __init__(self, cfg):
        super().__init__(cfg)
        self._nvml = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self._nvml = pynvml
            self._gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.caps["gpu"] = True
        except Exception:
            self._nvml = None

    def _hwmon_temp(self):
        for hw in Path("/sys/class/hwmon").glob("hwmon*"):
            try:
                name = (hw / "name").read_text().strip()
                for inp in hw.glob("temp*_input"):
                    label = ""
                    lbl = inp.with_name(inp.name.replace("_input", "_label"))
                    if lbl.exists():
                        label = lbl.read_text().strip()
                    if name in _LABEL_MAP or label.lower().startswith("package"):
                        return int(inp.read_text()) / 1000.0
            except Exception:
                continue
        return None

    def _hwmon_fan(self):
        for hw in Path("/sys/class/hwmon").glob("hwmon*"):
            for fan in hw.glob("fan*_input"):
                try:
                    return int(fan.read_text())
                except Exception:
                    continue
        return None

    def _amd_gpu_busy(self):
        for card in glob.glob("/sys/class/drm/card*/device/gpu_busy_percent"):
            try:
                return int(Path(card).read_text().strip())
            except Exception:
                continue
        return None

    def extra(self, now: float) -> dict:
        t = {}
        cputemp = self._hwmon_temp()
        if cputemp is not None:
            t["cputemp"] = round(cputemp, 1)
            self.caps["cpu_temp"] = True
        fan = self._hwmon_fan()
        if fan:
            t["fan"] = fan
            self.caps["fan"] = True

        if self._nvml is not None:
            try:
                nv = self._nvml
                h = self._gpu_handle
                t["gpu"] = int(nv.nvmlDeviceGetUtilizationRates(h).gpu)
                mem = nv.nvmlDeviceGetMemoryInfo(h)
                t["gpumem"] = int(mem.used * 100 / mem.total)
                t["gputemp"] = float(nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU))
            except Exception:
                pass
        elif t.get("gputemp") is None:
            busy = self._amd_gpu_busy()
            if busy is not None:
                t["gpu"] = busy
                self.caps["gpu"] = True
        return t
