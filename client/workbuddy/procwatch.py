"""按进程关键字识别 AI 编辑器 / CLI 当前是否在用。

**为什么需要这一层**：日志推断只能回答「这个工具写过日志」，回答不了
「它还开着吗」。工具关闭后日志不再变化，最后一次状态会被新鲜度窗口兜住
继续显示——Codex 结束一轮会写 `task_complete` → 等你回复，而这一状态的新鲜
窗口是 30 分钟（`aiwatch._WAIT_FRESH`）。用户明明关掉了软件，小屏却停在
Codex 上，就是这条路径。

进程表是更硬的信号：进程不在，就不存在「正在进行的会话」。本模块只回答一个
问题——某工具现在是否在跑，判断留给调用方（见 `should_gate`）。

**失败方向很重要**：扫描不可用（没装 psutil、系统调用失败）时返回 `None`，
调用方必须放弃门控。宁可多显示一个已关闭的工具，也不能误删正在跑的会话。
"""
from __future__ import annotations

import os
import time

# 有日志来源、参与任务门控的工具。键与 aiwatch 的 source 一致。
TOOL_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
    "zcode": "ZCode",
    "workbuddy": "WorkBuddy 智能体",
}

# 只做「是否在用」展示、不参与门控的编辑器（我们不解析它们的日志，
# 所以不能凭它们决定会话存亡）。
EDITOR_LABELS = {
    "cursor": "Cursor",
    "windsurf": "Windsurf",
    "trae": "Trae",
    "vscode": "VS Code",
    "zed": "Zed",
    "gemini": "Gemini CLI",
}

# 每个工具允许的进程名 / 路径特征。**只在进程名与可执行文件路径上匹配**，
# 不在任意命令行参数里做子串匹配——用户实测过这个坑：ZCode 自己的 agent
# shell 命令行里一旦出现 "codex"/"claude"（读文档、grep、跑脚本都会），
# 子串匹配就会把已经关掉的工具判成「在运行」，门控随之失效。
MATCH = {
    "claude":    (("claude", "claude-code"), ()),
    "codex":     (("codex",), ()),
    "opencode":  (("opencode",), ()),
    "zcode":     (("zcode", "zcode-cli", "zcode-host-local"), ("zcode.app",)),
    "workbuddy": (("workbuddy", "workbuddy-agent"), ()),
    # 以下只做「是否在用」展示，不参与门控
    "cursor":    (("cursor",), ("cursor.app",)),
    "windsurf":  (("windsurf",), ("windsurf.app",)),
    "trae":      (("trae",), ("trae.app",)),
    "vscode":    (("code", "code - insiders"), ("visual studio code.app",)),
    "zed":       (("zed",), ("zed.app",)),
    "gemini":    (("gemini",), ("gemini.app",)),
}
# 以脚本形式承载 CLI 的解释器（Claude Code / Codex 常以 node 脚本运行）：
# 只有这些进程才允许看参数——且仅认「恰好等于工具名」或「指向真实存在的
# 可执行文件」的参数，避免把文档路径、grep 模式、heredoc 正文当进程名。
WRAPPERS = {"python", "python2", "python3", "node", "deno", "bun", "ruby",
            "bash", "sh", "zsh", "fish", "pwsh"}

_TTL = 3.0          # 进程表扫描缓存秒数：主循环 1s 一拍，不必每拍都枚举进程
_LOG_GRACE = 20.0   # 日志这么新就一律不门控（刚写完日志比进程名更可信）
_cache: tuple[float, set | None] | None = None


def _norm(name: str) -> str:
    name = (name or "").strip().lower()
    for suffix in (".exe", ".app"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def _matches(key: str, name: str, exe: str, args: list) -> bool:
    names, paths = MATCH[key]
    base = _norm(name)
    if base in names:
        return True
    exe_low = (exe or "").replace("\\", "/").lower()
    if exe_low and any(hint in exe_low for hint in paths):
        return True
    if base not in WRAPPERS:
        return False
    for arg in args[1:]:
        token = _norm(arg.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
        if token in names:
            return True
        # 形如 node /usr/local/bin/claude：参数本身是一个存在的可执行文件
        if "/" in arg and os.path.exists(arg) and token in names:
            return True
    return False


def _scan() -> set | None:
    """枚举进程，返回在运行的工具集合；不可用返回 None。"""
    try:
        import psutil
    except Exception:
        return None
    found: set = set()
    try:
        for proc in psutil.process_iter(["name", "cmdline", "exe"]):
            try:
                info = proc.info
                name = info.get("name") or ""
                args = info.get("cmdline") or []
                exe = info.get("exe") or ""
            except Exception:
                # 权限不足或进程已退出：跳过单个进程，不放弃整次扫描
                continue
            if not name and not args:
                continue
            for key in MATCH:
                if key not in found and _matches(key, name, exe, args):
                    found.add(key)
    except Exception:
        return None
    return found


def running_tools(now: float | None = None, ttl: float = _TTL) -> set | None:
    """当前在运行的工具集合；None 表示扫描不可用（调用方不要门控）。"""
    global _cache
    now = time.monotonic() if now is None else now
    if _cache is not None and now - _cache[0] < ttl:
        return _cache[1]
    found = _scan()
    _cache = (now, found)
    return found


def reset_cache() -> None:
    """测试用：清掉扫描缓存。"""
    global _cache
    _cache = None


def should_gate(tool: str, running: set, log_age: float,
                grace: float = _LOG_GRACE) -> bool:
    """该工具的会话是否应当被判为「已结束」而丢弃。

    三条同时成立才丢：这个工具有进程特征、它不在运行、它的日志也已经安静。
    第三个条件是为了让「刚写完日志」压过「进程名没认出来」——那才是最可能
    误伤正在跑的会话的场景。
    """
    if tool not in MATCH:
        return False
    if tool in running:
        return False
    return log_age > grace


def report() -> dict:
    """给 Web 工作台用的可读报告（概览页「AI 工具」面板）。"""
    running = running_tools()
    if running is None:
        return {"ok": False, "error": "进程扫描不可用", "tools": []}
    tools = [{"id": key, "label": label, "running": key in running,
              "gates": True, "capability": "session"}
             for key, label in TOOL_LABELS.items()]
    # 编辑器只在运行时列出：它们没有日志来源，未运行不必占位置
    tools += [{"id": key, "label": label, "running": key in running,
               "gates": False, "capability": "process"}
              for key, label in EDITOR_LABELS.items() if key in running]
    return {"ok": True, "error": "", "tools": tools}
