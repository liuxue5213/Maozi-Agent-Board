"""Windows 扩展采集：psutil 通用字段 + NVML（可选）+ WMI 温度/风扇。

温度/风扇取值顺序（spec §3.2.5：Windows 无 T2 限制，普通用户即可读）：
  1. LibreHardwareMonitor WMI 命名空间（root/LibreHardwareMonitor，用户装了
     LHM 并以管理员跑过一次后可用，数值最准，含 CPU 温度与各风扇）
  2. 内核热区 MSAcpi_ThermalZoneTemperature（root/wmi，十分之一开尔文，普遍存在）
  3. Win32_Fan（root/CIMV2，很多台式机不暴露）

诚实原则：读不到就维持 caps=False，设备端自动隐藏对应数据位，绝不编造。
WMI/PowerShell 调用较慢（百毫秒级），内部 5s 节流。
"""
from __future__ import annotations

import subprocess
import time

from .common import Collector

_THROTTLE = 5.0
_PS_TIMEOUT = 4.0


def _run_ps(query: str) -> str:
    """跑一条 PowerShell CIM 查询，返回 strip 后的 stdout（失败返回空串）。"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
            capture_output=True, text=True, timeout=_PS_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


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
        self._thermal_cache = ({}, 0.0)   # ({cputemp, fan}, monotonic_at)
        # 温度实际来源：lhm=LHM 实测 / zone=系统热区 / ""=还没有读数。
        # 随 /api/status 暴露，Web 自检面板据此给「装 LHM」的引导。
        self.thermal_hint = ""

    # ---- WMI 查询（全部可被单测 monkeypatch _run_ps） ----

    @staticmethod
    def _query_lhm_cpu_temp() -> str:
        return _run_ps("Get-CimInstance -Namespace root/LibreHardwareMonitor "
                       "-ClassName Sensor -ErrorAction Stop | "
                       "Where-Object { $_.SensorType -eq 'Temperature' -and "
                       "$_.Name -match 'CPU' } | "
                       "Select-Object -First 1 -ExpandProperty Value")

    @staticmethod
    def _query_thermal_zone() -> str:
        # CurrentTemperature = 开尔文×10
        return _run_ps("Get-CimInstance -Namespace root/wmi "
                       "-ClassName MSAcpi_ThermalZoneTemperature "
                       "-ErrorAction Stop | Select-Object -First 1 "
                       "-ExpandProperty CurrentTemperature")

    @staticmethod
    def _query_lhm_fan() -> str:
        return _run_ps("Get-CimInstance -Namespace root/LibreHardwareMonitor "
                       "-ClassName Sensor -ErrorAction Stop | "
                       "Where-Object { $_.SensorType -eq 'Fan' } | "
                       "Select-Object -First 1 -ExpandProperty Value")

    @staticmethod
    def _query_win32_fan() -> str:
        return _run_ps("Get-CimInstance -Namespace root/CIMV2 "
                       "-ClassName Win32_Fan -ErrorAction Stop | "
                       "Select-Object -First 1 -ExpandProperty DesiredSpeed")

    def _sample_thermal(self) -> dict:
        """带 5s 节流的温度/风扇采样。返回 {cputemp, fan}（键可缺）。"""
        now = time.monotonic()
        values, at = self._thermal_cache
        if now - at < _THROTTLE:
            return dict(values)
        out = {}
        hint = ""
        # ⚠️ 两个来源单位不同：LHM 的 Value 就是 ℃；热区是开尔文×10。
        # 不能统一走换算——那会把 57℃ 算成 -267℃ 而永远丢弃。
        raw = self._query_lhm_cpu_temp()
        if raw:
            try:
                celsius = float(raw)
                if -10 < celsius < 150:     # 合理 CPU 温度区间
                    out["cputemp"] = round(celsius, 1)
                    hint = "lhm"
            except (TypeError, ValueError):
                pass
        if "cputemp" not in out:
            raw = self._query_thermal_zone()
            try:
                celsius = float(raw) / 10.0 - 273.15
                # 热区偶尔返回死值（未就绪 273.15K=0℃），>0℃ 才可信
                if celsius > 0:
                    out["cputemp"] = round(celsius, 1)
                    hint = "zone"
            except (TypeError, ValueError):
                pass
        raw = self._query_lhm_fan() or self._query_win32_fan()
        try:
            rpm = int(float(raw))
            if rpm >= 0:
                out["fan"] = rpm
        except (TypeError, ValueError):
            pass
        self._thermal_cache = (out, now)
        self.thermal_hint = hint
        return dict(out)

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
        try:
            t.update(self._sample_thermal())
        except Exception:
            pass
        self.caps["cpu_temp"] = "cputemp" in t
        self.caps["fan"] = "fan" in t
        return t
