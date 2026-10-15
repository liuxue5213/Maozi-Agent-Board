"""桌面宠物状态机：饱食/水分/活力随时间自然衰减，喂食/喝水/运动交互恢复。

设计原则（与整工程一致）：客户端是大脑，设备是显示+触控——
状态在这里计算并持久化（~/.workbuddy/pet.json），经遥测 PS 行下发设备；
设备上的 喂食/喝水/运动 触屏按钮以 PET= 上行到达，interact() 处理后
下一帧 PS 反映结果。设备端不需要任何宠物游戏逻辑。

衰减速率刻意放缓（饱食 ~33 小时见底）——宠物是陪伴，不是负担；
低于 20 时设备表情会变成对应提醒（饿/渴/累），网页可手动喂回。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

DATA = Path.home() / ".workbuddy" / "pet.json"

PET_TYPES = {"robot": "机器人", "cat": "小猫", "chick": "小鸡", "dog": "小狗"}

# 每小时自然衰减
DECAY_PER_HOUR = {"hunger": 3.0, "thirst": 4.0, "energy": 1.5}
# 交互效果（正负皆可：运动消耗活力、增加饥饿速度的模型太复杂，v1 直接扣活力）
INTERACT = {
    "feed":  {"hunger": +40, "energy": +5},
    "water": {"thirst": +40},
    "play":  {"energy": -12, "hunger": -6, "thirst": -6},
}
CLAMP = (0, 100)


def _clamp(v: float) -> int:
    return int(max(CLAMP[0], min(CLAMP[1], round(v))))


class Pet:
    """宠物状态：惰性衰减——读取时按 updated_at 折算，不依赖定时器。"""

    def __init__(self, get_config):
        self.get_config = get_config        # 返回 {"type","name"} 的回调
        self._state = self._load()

    # ---- 持久化 ----

    def _load(self) -> dict:
        try:
            data = json.loads(DATA.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        return {
            "hunger": _clamp(data.get("hunger", 80)),
            "thirst": _clamp(data.get("thirst", 75)),
            "energy": _clamp(data.get("energy", 90)),
            "updated_at": float(data.get("updated_at", time.time())),
        }

    def _save(self) -> None:
        try:
            DATA.write_text(json.dumps(self._state, ensure_ascii=False),
                            encoding="utf-8")
        except OSError:
            pass

    # ---- 状态 ----

    def _decayed(self) -> dict:
        hours = max(0.0, (time.time() - self._state["updated_at"]) / 3600.0)
        out = {}
        for key, rate in DECAY_PER_HOUR.items():
            out[key] = _clamp(self._state[key] - hours * rate)
        return out

    def _config(self) -> dict:
        try:
            cfg = self.get_config() or {}
        except Exception:
            cfg = {}
        ptype = cfg.get("type") if cfg.get("type") in PET_TYPES else "robot"
        return {"type": ptype, "name": str(cfg.get("name") or PET_TYPES[ptype])}

    def snapshot(self) -> dict:
        """当前状态（含按时间折算后的衰减）+ 派生心情。"""
        meters = self._decayed()
        self._state.update({**meters, "updated_at": time.time()})
        lowest = min(meters, key=meters.get)
        mood = "ok"
        if meters[lowest] < 20:
            mood = {"hunger": "hungry", "thirst": "thirsty",
                    "energy": "tired"}[lowest]
        return {**self._config(), **meters, "mood": mood}

    def interact(self, action: str) -> dict:
        """喂食/喝水/运动：应用增量并持久化，返回交互后的完整状态。"""
        deltas = INTERACT.get(action)
        if not deltas:
            return self.snapshot()
        meters = self._decayed()
        for key, delta in deltas.items():
            meters[key] = _clamp(meters[key] + delta)
        self._state.update({**meters, "updated_at": time.time()})
        self._save()
        return self.snapshot()

    def renamed(self) -> bool:
        """配置（类型/名字）可能变了——重存一份状态文件即可，无其他动作。"""
        self._save()
        return True
