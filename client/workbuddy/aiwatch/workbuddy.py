"""WorkBuddy 会话监听：~/.workbuddy/workbuddy.db的 sessions 表 + sessions/*.json 心跳。

与 claude/codex/zcode 的差异——WorkBuddy 不写 jsonl 事件流，而是把会话状态
落在两处，二者互补：

  1. workbuddy.db → sessions表：权威状态（status/title/cwd），
     但 updated_at 只在消息边界更新，长回合中间不动；
  2. sessions/<pid>.json：每个活进程的心跳（lastHeartbeat 毫秒级刷新），
     用来把"正在跑的长回合"也点亮，避免明明在干活却显示成"等你回复"。

取两者的 max() 作为活动时间，既能第一时间反映活跃，也不会把刚结束的会话
长期留在屏幕上。

⚠️ 踩过的坑：
  · 时间戳是**毫秒**，不是秒。当成秒会让 age 算成 5 万小时，会话永远被
    "已过期"过滤掉——这正是「屏幕识别不到 workbuddy 会话」的一个直接成因。
  · db 被 WorkBuddy 进程持锁写入，必须**只读**打开并设短 busy timeout，
    且绝不能写（写会污染主进程数据）。
"""
import json
import sqlite3
import time
from pathlib import Path

from . import Activity, ERROR, GENERATING, IDLE, THINKING, WAIT_INPUT

# db 状态 → 屏幕状态。terminated/archived 视为无活动，直接丢弃。
_STATUS_MAP = {
    "working": GENERATING,
    "completed": WAIT_INPUT,
    "error": ERROR,
    "terminated": IDLE,
    "archived": IDLE,
}


def _ms_to_s(value) -> float:
    """db 的 created_at/updated_at/last_activity_at 都是毫秒。"""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0:
        return 0.0
    # 防御：万一将来变成秒，直接返回，不要再乘 1000
    return v / 1000.0 if v > 1e11 else v


class WorkBuddySource:
    NAME = "workbuddy"
    KIND = "session"   # 读取真实会话状态（sqlite 只读 + 心跳）

    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def _db(self) -> Path:
        return self.root / "workbuddy.db"

    def _heartbeats(self, now: float) -> dict:
        """pid 会话文件 → {sessionId: lastHeartbeat(秒)}。

        心跳文件与 db 分离：这里只取活跃会话的，文件不存在或损坏都直接跳过。
        """
        out = {}
        d = self.root / "sessions"
        try:
            entries = list(d.glob("*.json"))
        except OSError:
            return out
        for p in entries:
            try:
                if now - p.stat().st_mtime > 30:   # 心跳文件本身也按mtime 判新鲜度
                    continue
                data = json.loads(p.read_text(encoding="utf-8"))
                sid = str(data.get("sessionId") or "").strip()
                hb = _ms_to_s(data.get("lastHeartbeat"))
                if sid and hb:
                    out[sid] = max(out.get(sid, 0.0), hb)
            except (OSError, ValueError, TypeError):
                continue
        return out

    def poll_all(self, now, cache) -> list:
        db = self._db
        if not db.exists():
            return []
        beats = self._heartbeats(now)
        acts = []
        try:
            # 只读连接：绝不能写主进程正在使用的库。
            # timeout 短一点，拿不到锁就下次轮询再说（UI 有轮询周期兜底）。
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.25)
        except sqlite3.Error:
            return []
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, cwd, title, status, created_at, updated_at, last_activity_at,"
                "       is_background_automation, deleted_at "
                "FROM sessions WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT 12"
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()

        for r in rows:
            try:
                sid = str(r["id"] or "").strip()
                if not sid:
                    continue
                status = str(r["status"] or "").lower()
                state = _STATUS_MAP.get(status)
                if state is None or state == IDLE:
                    continue
                # completed 会话只有「进程还活着（有实时心跳）」才视为在等你回复；
                # 否则它已经结束 / 被关掉，再挂着「等你回复」会误导（最长滞留 30 分钟）。
                # 有心跳 = 会话窗口还开着，确实在等你的下一条消息；没心跳 = 已结束，丢弃。
                if state == WAIT_INPUT and not beats.get(sid):
                    continue
                # 后台自动化（定时任务）不是用户正在盯的交互会话，排除
                if r["is_background_automation"]:
                    continue
                # 活动时间 = max(db 更新时间, 进程心跳)，让长回合也保持点亮
                ts = max(_ms_to_s(r["updated_at"]),
                         _ms_to_s(r["last_activity_at"]),
                         beats.get(sid, 0.0))
                if ts <= 0:
                    continue
                cwd = str(r["cwd"] or "")
                project = cwd.replace("\\", "/").rstrip("/").split("/")[-1]
                if not project:
                    project = str(r["title"] or "")[:24]
                acts.append(Activity("workbuddy", state, ts, sid=sid,
                                     project=project,
                                     detail=status,
                                     err="" if state != ERROR else status,
                                     task="CODE" if state == GENERATING else ""))
            except (sqlite3.Error, TypeError, ValueError):
                continue
        return acts