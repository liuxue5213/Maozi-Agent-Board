"""OpenCode 终端 agent 会话监听：~/.local/share/opencode/opencode.db (SQLite)。

OpenCode 是主流开源终端 AI agent（opencode.ai）。会话/消息存在单文件
SQLite 库里，这里以 **只读 URI** 打开——绝不干扰 OpenCode 自身的写入。
库不存在 = 用户没装/没用过，安静返回空。

状态推断（日志推断的三段近似，与 Codex/Claude 源同一口径）：
  会话最后一条消息是 user            → THINKING（agent 正在生成回复）
  最后一条是 assistant 且刚有工具动作 → TOOL（正在执行工具）
  最后一条是 assistant               → WAIT_INPUT（等用户下一句）
"""
import sqlite3
import time
from pathlib import Path

from . import (Activity, GENERATING, THINKING, TOOL, WAIT_INPUT,
               tool_to_task)

DB_NAME = "opencode.db"
_FRESH_MS = 90_000          # 会话活跃窗口（毫秒）
_TOOL_MS = 20_000           # 工具动作的「正在进行」窗口


def _now_ms() -> int:
    return int(time.time() * 1000)


class OpenCodeSource:
    NAME = "opencode"
    KIND = "session"   # 读取真实会话状态

    def __init__(self, root: Path):
        self.root = Path(root)
        self.db = self.root / DB_NAME

    def poll_all(self, now, cache) -> list:
        if not self.db.exists():
            return []
        try:
            # 只读打开：OpenCode 可能正在写库（WAL），这里绝不加锁阻塞它
            con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True, timeout=0.5)
            try:
                return self._poll_open(con, now)
            finally:
                con.close()
        except (sqlite3.Error, OSError):
            return []              # 库损坏/被锁/缺 JSON1：安静放弃，不影响其它源

    def _poll_open(self, con: sqlite3.Connection, now) -> list:
        now_ms = _now_ms() if now is None else int(now * 1000)
        fresh = now_ms - _FRESH_MS
        # 每个活跃会话的最新一条消息（role 在 data JSON 里，SQLite JSON1 解出）
        rows = con.execute(
            """SELECT m.session_id, s.directory,
                      json_extract(m.data, '$.role'), m.time_updated
               FROM message m JOIN session s ON s.id = m.session_id
               WHERE m.time_updated > ?
               ORDER BY m.time_updated DESC""",
            (fresh,)).fetchall()
        # 近 20 秒内有工具动作的会话（TOOL 状态依据）
        tool_rows = con.execute(
            """SELECT session_id, MAX(time_updated)
               FROM part
               WHERE time_updated > ? AND json_extract(data, '$.type') = 'tool'
               GROUP BY session_id""",
            (now_ms - _TOOL_MS,)).fetchall()
        last_tool = {sid: int(ts) for sid, ts in tool_rows}

        acts = []
        seen = set()
        for sid, directory, role, updated in rows:
            if sid in seen:
                continue                     # 已是该会话最新一条
            seen.add(sid)
            if role not in ("user", "assistant"):
                continue
            if last_tool.get(sid, 0) >= updated and role == "assistant":
                state, detail = TOOL, "tool"
            elif role == "user":
                state, detail = THINKING, "prompt"
            else:
                state, detail = WAIT_INPUT, "reply"
            project = (directory or "").replace("\\", "/").rstrip("/").split("/")[-1]
            acts.append(Activity("opencode", state, updated / 1000.0, sid=sid,
                                 project=project, detail=detail,
                                 task=tool_to_task(detail)))
        return acts
