"""诊断包导出：日志 + 脱敏配置 + 版本状态，打包为 zip。

严守隐私约束：不含任务名、会话内容、错误正文全文、路径、应用命令。
"""
from __future__ import annotations

import io
import json
import time
import zipfile

from . import __version__


def sanitized_profile(profile: dict) -> dict:
    """副本脱敏：apps 命令是可执行输入，替换为占位。"""
    out = json.loads(json.dumps(profile, ensure_ascii=False))
    for app in out.get("apps", []):
        app["command"] = "***"
    return out


def build_bundle(app) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for line in app.health.recent_lines(days=7):
            if line.strip():
                z.writestr("usage.jsonl", line)  # 追加式：多天内容合并
        with app.profile_lock:
            z.writestr("profile-sanitized.json",
                       json.dumps(sanitized_profile(app.profile), indent=1, ensure_ascii=False))
            status = {
                "client_version": __version__,
                "fw_caps": app.fw_caps,
                "profile_status": app.profile_status,
                "profile_error": app.profile_error,
                "applied_version": app.applied_version,
                "device": app.link.status if app.link else "串口关闭",
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        z.writestr("status.json", json.dumps(status, indent=1, ensure_ascii=False))
        z.writestr("README.txt",
                   "WorkBuddy 诊断包\n"
                   "================\n"
                   "usage.jsonl      使用事件（页面/同步/在线时长，无对话内容）\n"
                   "profile-sanitized.json  屏幕配置（应用启动命令已脱敏）\n"
                   "status.json      客户端/固件版本与同步状态\n")
    return buf.getvalue()
