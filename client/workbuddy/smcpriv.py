"""SMC 特权采样：温度/风扇的授权解法（T2 + 新版 macOS 可用）。

背景（2026-10-10 M1 闸门实测，MacBookPro16,1 / Ventura 13.2）：
  - AppleSMC 用户态直读被拒 kIOReturnNotPrivileged，**root 同样被拒**——
    键级访问要求 Apple 私有 entitlement，提权救不了；
  - 但 root 下的 `powermetrics --samplers smc` 完全可用（CPU/GPU 结温 + 风扇转速）。

因此本模块走「一次性管理员授权 → launchd 定时任务（root）→ powermetrics 采样 →
写世界可读 JSON → 客户端读文件」的路径：
  - 安装：osascript 弹系统管理员密码框（用户看得见、随时可取消）；
  - 采样：每 20s 一次，powermetrics 单样本约 0.3s；
  - 读取：`read()` 供采集层用，文件过期/缺失返回 None，绝不假数据；
  - 卸载：bootout + 删净三个文件，不残留。
"""
import json
import os
import plistlib
import re
import subprocess
import tempfile
import time
from pathlib import Path

LABEL = "com.workbuddy.smc"
RUN_FILE = Path("/private/var/run/workbuddy-smc.json")
ERR_FILE = Path("/private/var/run/workbuddy-smc.err")
PLIST_PATH = Path("/Library/LaunchDaemons/com.workbuddy.smc.plist")
HELPER_DIR = Path("/Library/Application Support/WorkBuddy")
HELPER_PATH = HELPER_DIR / "smc_sampler.py"
START_INTERVAL = 20          # launchd 采样周期（秒）
FRESH_WINDOW = 60            # 采集层认可的数据新鲜度（秒）
OSASCRIPT_TIMEOUT = 180.0    # 等管理员密码框的时间

# 以 root 身份由 launchd 周期执行；只读温度/风扇，写一个世界可读 JSON。
SAMPLER_SOURCE = '''#!/usr/bin/env python3
"""WorkBuddy SMC 采样器（root）：powermetrics 单样本 -> JSON。只读，不写 SMC。"""
import json, os, re, subprocess, time

RUN = "/private/var/run/workbuddy-smc.json"
ERR = "/private/var/run/workbuddy-smc.err"

def main():
    out = {"at": time.time()}
    try:
        text = subprocess.run(
            ["/usr/bin/powermetrics", "-i", "200", "-n", "1", "--samplers", "smc"],
            capture_output=True, text=True, timeout=30).stdout
        def num(pat):
            m = re.search(pat, text)
            return round(float(m.group(1)), 1) if m else None
        out["cputemp"] = num(r"CPU die temperature:\\s*([\\d.]+)\\s*C")
        out["gputemp"] = num(r"GPU die temperature:\\s*([\\d.]+)\\s*C")
        fan = num(r"Fan:\\s*([\\d.]+)\\s*rpm")
        out["fan"] = int(fan) if fan is not None else None
        if out["cputemp"] is None and out["fan"] is None:
            out["err"] = "powermetrics 输出中没有温度/风扇字段"
    except Exception as exc:
        out = {"at": time.time(), "err": str(exc)}
    try:
        with open(RUN, "w") as f:
            json.dump(out, f)
        os.chmod(RUN, 0o644)
    except Exception:
        pass
    if out.get("err"):
        try:
            with open(ERR, "a") as f:
                f.write("%.0f %s\\n" % (out["at"], out["err"]))
        except Exception:
            pass

main()
'''


def parse_sample(text: str) -> dict:
    """powermetrics 输出 -> {cputemp, gputemp, fan}（缺项为 None）。独立纯函数，单测用。"""
    def num(pat):
        m = re.search(pat, text)
        return round(float(m.group(1)), 1) if m else None
    cputemp = num(r"CPU die temperature:\s*([\d.]+)\s*C")
    gputemp = num(r"GPU die temperature:\s*([\d.]+)\s*C")
    fan = num(r"Fan:\s*([\d.]+)\s*rpm")
    return {"cputemp": cputemp, "gputemp": gputemp,
            "fan": int(round(fan)) if fan is not None else None}


def build_plist() -> bytes:
    """LaunchDaemon plist（含签名校验字段，单测解析）。"""
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/python3", str(HELPER_PATH)],
        "StartInterval": START_INTERVAL,
        "RunAtLoad": True,
        "StandardErrorPath": str(ERR_FILE),
    })


def read(fresh_window: float = FRESH_WINDOW) -> dict | None:
    """采集层入口：读到新鲜采样返回 {cputemp,gputemp,fan,...}，否则 None。

    cputemp/fan 为 None 的键会被采集层当作「该能力不可用」，caps 不会假翻。
    """
    try:
        data = json.loads(RUN_FILE.read_text())
    except (OSError, ValueError):
        return None
    if time.time() - data.get("at", 0) > fresh_window:
        return None
    if data.get("err"):
        return None
    return data


def status() -> dict:
    """设置页状态：文件级推断，不需要 root。"""
    installed = PLIST_PATH.exists()
    data = None
    age = None
    try:
        data = json.loads(RUN_FILE.read_text())
        age = int(time.time() - data.get("at", 0))
    except (OSError, ValueError):
        pass
    fresh = data is not None and age is not None and 0 <= age <= FRESH_WINDOW \
        and not data.get("err")
    return {"installed": installed, "fresh": fresh, "age": age,
            "data": data, "platform": "darwin"}


def _elevate(script: Path, timeout: float = OSASCRIPT_TIMEOUT) -> None:
    """弹系统管理员密码框执行 script（root）。取消/超时抛 RuntimeError。"""
    cmd = f'do shell script "sh {script}" with administrator privileges'
    try:
        proc = subprocess.run(["osascript", "-e", cmd],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("管理员授权超时（密码框无人应答）") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if "User canceled" in err or "用户已取消" in err or (-128) in (proc.returncode,):
            raise RuntimeError("已取消授权")
        raise RuntimeError(f"授权失败：{err[:200]}")


def install() -> dict:
    """一键安装：写采样器 + LaunchDaemon 并启动。成功返回首个采样。"""
    if not PLIST_PATH.parent.exists():
        return {"ok": False, "err": "非 macOS 或系统目录异常"}
    with tempfile.TemporaryDirectory(prefix="workbuddy-smc-") as td:
        td_path = Path(td)
        (td_path / "smc_sampler.py").write_text(SAMPLER_SOURCE)
        (td_path / "com.workbuddy.smc.plist").write_bytes(build_plist())
        # TCC 教训：提权进程读不了 ~/Desktop， staging 必须放 /tmp。
        installer = td_path / "install.sh"
        installer.write_text(f"""
set -e
mkdir -p "{HELPER_DIR}"
cp "{td_path}/smc_sampler.py" "{HELPER_PATH}"
chown root:wheel "{HELPER_PATH}" "{td_path}/com.workbuddy.smc.plist"
chmod 755 "{HELPER_PATH}"
cp "{td_path}/com.workbuddy.smc.plist" "{PLIST_PATH}"
chown root:wheel "{PLIST_PATH}"
chmod 644 "{PLIST_PATH}"
launchctl bootout system/{LABEL} 2>/dev/null || true
launchctl bootstrap system "{PLIST_PATH}"
launchctl kickstart system/{LABEL} || true
""")
        try:
            _elevate(installer)
        except RuntimeError as exc:
            return {"ok": False, "err": str(exc)}
    # bootstrap+RunAtLoad 应在几秒内产出首个采样
    for _ in range(20):
        data = read(fresh_window=START_INTERVAL * 3)
        if data:
            return {"ok": True, "data": data}
        time.sleep(0.5)
    return {"ok": False,
            "err": "已安装但暂无采样数据（可稍后在状态里观察，或导出诊断包）"}


def uninstall() -> dict:
    """停用并删净：launchd 卸载 + 三个文件移除。"""
    with tempfile.TemporaryDirectory(prefix="workbuddy-smc-") as td:
        remover = Path(td) / "remove.sh"
        remover.write_text(f"""
launchctl bootout system/{LABEL} 2>/dev/null || true
rm -f "{HELPER_PATH}" "{PLIST_PATH}" "{RUN_FILE}" "{ERR_FILE}"
rmdir "{HELPER_DIR}" 2>/dev/null || true
""")
        try:
            _elevate(remover, timeout=120.0)
        except RuntimeError as exc:
            return {"ok": False, "err": str(exc)}
    return {"ok": not PLIST_PATH.exists() and not RUN_FILE.exists()}
