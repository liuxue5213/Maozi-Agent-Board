"""客户端配置：默认值 + ~/.workbuddy/config.json 覆盖。"""
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_PATH = Path.home() / ".workbuddy" / "config.json"

# 最近一次加载降级的原因（文件损坏被隔离备份后回退默认值），空 = 正常。
# Web 自检面板会读取它提示用户，避免「设置悄悄变回默认」却毫无线索。
LOAD_WARNING = ""


def _quarantine(path: Path) -> str:
    """把读不了的配置改名留证（.bad-时间戳），而不是让坏文件继续留在原地。"""
    import time as _time
    backup = path.with_name(path.name + f".bad-{_time.strftime('%Y%m%d-%H%M%S')}")
    try:
        path.replace(backup)
        return str(backup)
    except OSError:
        return ""

DEFAULT_ROOTS = {
    "claude": Path.home() / ".claude" / "projects",
    "codex": Path.home() / ".codex" / "sessions",
    "opencode": Path.home() / ".local/share/opencode",
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
    proc_gate: bool = True           # 用进程表筛掉「已关闭工具」的残留会话
    # 默认音乐软件（bundle id）。"auto" = 自动挑：优先网易云 → QQ音乐 → Apple 音乐
    music_app: str = "auto"
    web: bool = True                 # 本机设置页
    web_port: int = 8765
    open_settings: bool = False
    disk_path: str = "/"             # 监控的磁盘
    # 手机推送（Bark/ntfy）：出错/等你回复时 POST 通知。空 = 不推送。
    # Bark 示例：https://api.day.app/你的Key/{title}/{body}
    # ntfy 示例：https://ntfy.sh/你的主题
    push_url: str = ""
    push_states: str = "ERROR,WAIT_INPUT,ASK"   # 触发推送的状态集合
    # 安静时段 "HH:MM-HH:MM"（可跨零点，如 23:00-07:30）：窗口内不推手机，
    # 事件仍进 Web 收件箱。空 = 不启用。
    push_quiet: str = ""
    # 系统日历只读接入（macOS EventKit）：事件进收件箱，不写 profile。默认关——
    # 打开时才首次访问日历（TCC 授权弹窗），被拒不影响手工与 ICS 日程。
    syscal: bool = False
    # 硬件告警阈值：CPU 温度（℃，持续 60s 防抖、回落到 -10℃ 滞后恢复）与
    # 磁盘使用率（%）。设备角标与收件箱共用。
    hw_cpu_temp: float = 85.0
    hw_disk: int = 90
    reminders: list = field(default_factory=list)  # [{"time":"09:30","text":"站会"}]
    theme: dict = field(default_factory=lambda: {
        "bg": "0B1220", "card": "141E32", "accent": "22D3EE",
        "text": "F3F7FF", "sub": "8CA3C7", "bright": 90})
    roots: dict = field(default_factory=lambda: {k: str(v) for k, v in DEFAULT_ROOTS.items()})

    @classmethod
    def load(cls) -> "Config":
        global LOAD_WARNING
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("配置必须是对象")
            except Exception as exc:
                # 损坏文件改名留证再回退默认：静默覆盖会让用户失去排查线索
                backup = _quarantine(CONFIG_PATH)
                LOAD_WARNING = f"客户端配置损坏已回退默认（备份 {backup}）" if backup \
                    else f"客户端配置损坏已回退默认：{exc}"
                print(f"[workbuddy] {LOAD_WARNING}")
                return cfg
            roots = data.pop("roots", {})
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            cfg.roots.update({k: str(v) for k, v in roots.items()})
        return cfg

    def save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def root(self, tool: str) -> Path:
        return Path(self.roots.get(tool, str(DEFAULT_ROOTS[tool])))
