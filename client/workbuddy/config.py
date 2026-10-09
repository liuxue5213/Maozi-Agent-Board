"""客户端配置：默认值 + ~/.workbuddy/config.json 覆盖。"""
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_PATH = Path.home() / ".workbuddy" / "config.json"

DEFAULT_ROOTS = {
    "claude": Path.home() / ".claude" / "projects",
    "codex": Path.home() / ".codex" / "sessions",
    "zcode": Path.home() / ".zcode" / "cli",
    # WorkBuddy 自己：db 存会话状态，sessions/<pid>.json 存进程心跳
    "workbuddy": Path.home() / ".workbuddy",
}


@dataclass
class Config:
    interval: float = 1.0            # 遥测刷新秒数
    duration: float = 0.0            # >0 则运行指定秒数后退出
    port: str = ""                   # 强制串口路径，空 = 自动扫描
    vid: int = 0x303A                # Espressif USB CDC VID
    console: bool = True             # 控制台输出
    console_interval: float = 1.0    # 控制台打印间隔秒（GUI 模式不打印）
    serial: bool = True              # 串口推送
    ai_watch: bool = True            # AI 任务检测
    web: bool = True                 # 本机设置页
    web_port: int = 8765
    open_settings: bool = False
    disk_path: str = "/"             # 监控的磁盘
    reminders: list = field(default_factory=list)  # [{"time":"09:30","text":"站会"}]
    theme: dict = field(default_factory=lambda: {
        "bg": "0B1220", "card": "141E32", "accent": "22D3EE",
        "text": "F3F7FF", "sub": "8CA3C7", "bright": 90})
    roots: dict = field(default_factory=lambda: {k: str(v) for k, v in DEFAULT_ROOTS.items()})

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                roots = data.pop("roots", {})
                for k, v in data.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
                cfg.roots.update({k: str(v) for k, v in roots.items()})
            except Exception:
                pass  # 配置损坏时静默回退默认值
        return cfg

    def save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def root(self, tool: str) -> Path:
        return Path(self.roots.get(tool, str(DEFAULT_ROOTS[tool])))
