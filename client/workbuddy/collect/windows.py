"""Windows 扩展采集：psutil 通用字段 + NVML（可选）。

TODO(下一里程碑): 通过 pythonnet 调 LibreHardwareMonitorLib 采集 CPU 温度/风扇
（需要管理员权限，内核驱动 WinRing0）。当前 NVML 可用时输出 N 卡数据。
"""
from .common import Collector


class WindowsCollector(Collector):
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

    def extra(self, now: float) -> dict:
        t = {}
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
        return t
