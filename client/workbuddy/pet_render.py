"""宠物渲染路线收口（P1-2）：canonical 矢量 vs 可选皮肤 的**单一事实源**。

决策依据：docs/产品愿景与范围边界_v1.md 决策 2 ——
  • 矢量 draw_pet 为 **canonical（内置）**：9 态程序化绘制，零下载、零 PSRAM 大块占用；
  • JPEG 角色包（petpack_*）降级为 **可选皮肤**：受 PSRAM 预算 + 缺帧回退约束，不进出厂固件。

物理实现：客户端按 day/night/weather 三时段选择皮肤包，把对应 pet_<状态>.jpg
上传到设备 LittleFS；设备端「有图渲染、无图矢量回落」就是本模型的落地。本模块
让客户端（Web 库 + 设备下发判断）只有一个权威口径：当前到底渲染内置矢量还是某
皮肤，以及 PSRAM 占用与回落状态。

为避免与 web.py 形成循环导入，canonical 9 态在此本地定义（与 web.PETPACK_CANON_STATES
保持一致，改一处两处同步）。
"""
from __future__ import annotations

import time
import zlib

# canonical 矢量宠物永远具备的 9 个规范状态（镜像 web.PETPACK_CANON_STATES）。
BUILTIN_STATES = ("idle", "thinking", "working", "working_b",
                  "sleepy", "sleeping", "greeting", "happy", "error")

BUILTIN_NAME = "像素宠物（内置）"
BUILTIN_KIND = "builtin"

# 资源预算（决策 4）：单帧源 JPEG 硬上限 ≤100KB；解码后 RGB565 320×480 = 300KB/缓冲；
# workanim LRU 缓存 12 槽 → 最坏 3.6MB。皮肤整包解码落地到设备后再上屏。
PET_SKIN_SOURCE_CAP_KB = 100        # 单帧源 JPEG 上限（与固件 MAX_IMG 对齐）
PET_DECODED_FRAME_KB = 300          # 320×480×2 字节
PET_ANIM_CACHE_SLOTS = 12           # bgimg.cpp anim_cache 槽数
PET_SKIN_PSRAM_BUDGET_KB = PET_DECODED_FRAME_KB * PET_ANIM_CACHE_SLOTS  # = 3600 最坏

# day/night/weather 三时段选择键 → 中文角色标签
ROLE_LABEL = {"day_pack": "白天", "night_pack": "夜晚", "weather_pack": "雨雪"}


# ---------------------------------------------------------------------------
# 宠物包 manifest 构建器（P1-4 资产管线）
# ---------------------------------------------------------------------------
# 单一事实源：客户端（compute_pack_info / 上传预检）与生成器（pet_artwork.py）
# 共用同一构建逻辑，保证磁盘帧的 CRC32 / 尺寸 / 预算口径完全一致。manifest
# 是「皮肤包自描述 + 可审计」的载体：每台设备下发前先比对 manifest 中的 CRC32
# 与磁盘实际文件，可提前抓出「部分下载 / 损坏 / 被改」的源文件（线路层 IMG 事务
# 的 CRC32 只能保证传输无误，抓不到源文件本身已坏）。
def pack_frame_detail(d: "Path", state: str) -> dict:
    """盘点单帧：文件名 + CRC32(hex) + 源字节数 + 解码后 KB（RGB565 320×480）。"""
    f = d / f"pet_{state}.jpg"
    if f.is_file():
        raw = f.read_bytes()
        size = len(raw)
        crc = zlib.crc32(raw) & 0xFFFFFFFF
    else:
        size, crc = 0, 0
    return {"state": state, "file": f.name, "size_bytes": size,
            "crc32": f"{crc:08X}", "decoded_kb": PET_DECODED_FRAME_KB}


def build_pack_manifest(name: str, d: "Path", present, missing, fallback) -> dict:
    """构造完整包 manifest（含 per-frame CRC / 尺寸 / 预算 / 源上限校验）。

    present/missing/fallback 来自 web.compute_pack_info 的相同口径；d 用于读盘算
    CRC。返回结构同时被 Web 库展示、上传预检、生成器落地复用。"""
    frames = [pack_frame_detail(d, s) for s in present]
    total_decoded = sum(fr["decoded_kb"] for fr in frames)
    over_source = [fr["state"] for fr in frames
                   if fr["size_bytes"] > PET_SKIN_SOURCE_CAP_KB * 1024]
    return {
        "name": name,
        "states": list(present),
        "missing": list(missing),
        "fallback": fallback,
        "frames": frames,
        "total_decoded_kb": total_decoded,
        "source_cap_kb": PET_SKIN_SOURCE_CAP_KB,
        "over_source_cap": over_source,          # 单帧源超 100KB → 设备解码会失败
        "psram_budget_kb": PET_SKIN_PSRAM_BUDGET_KB,
        "within_budget": total_decoded <= PET_SKIN_PSRAM_BUDGET_KB,
        # 帧数超过 anim_cache 槽数（12）时，最坏驻留受 LRU 约束，但应给出缓存压力提示
        "cache_pressure": len(frames) > PET_ANIM_CACHE_SLOTS,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def builtin_descriptor() -> dict:
    """canonical 矢量宠物的不可变描述（Web/设备共享，PSRAM 占用恒为 0）。"""
    return {
        "name": BUILTIN_NAME,
        "kind": BUILTIN_KIND,
        "canonical": True,
        "frames": len(BUILTIN_STATES),
        "frame_list": list(BUILTIN_STATES),
        "missing": [],
        "fallback": {},
        "psram_kb": 0,
        "complete": True,
        "active": False,      # 由 render_model 按 profile 填充
    }


def pack_descriptor(name: str, info: dict, active: bool) -> dict:
    """把 compute_pack_info 的结果收口为带 kind/canonical/active/预算 的描述。"""
    complete = not info["missing"]
    frames = len(info["present"])
    # 皮肤 PSRAM 占用 = 已解码帧数 × 单帧解码大小；上屏时经 LRU 缓存，
    # 实际峰值不超过 PET_SKIN_PSRAM_BUDGET_KB。
    psram_kb = frames * PET_DECODED_FRAME_KB
    over_source = info.get("over_source_cap") or []
    return {
        "name": name,
        "kind": "skin",
        "canonical": False,
        "frames": frames,
        "frame_list": info["present"],
        "missing": info["missing"],
        "fallback": info["fallback"],
        "manifest": info.get("manifest"),
        "psram_kb": psram_kb,
        "total_decoded_kb": info.get("total_decoded_kb", psram_kb),
        "within_budget": info.get("within_budget", True),
        "over_source_cap": over_source,       # 非空 = 有帧超过单帧源上限 100KB
        "cache_pressure": info.get("cache_pressure", False),
        "source_ok": not over_source,
        "complete": complete,
        "active": active,
    }


def active_pack_roles(profile_pet: dict | None) -> dict:
    """{pack_name: [角色标签...]}——哪些包正被 day/night/weather 引用。"""
    roles: dict[str, list[str]] = {}
    pp = profile_pet or {}
    for key, label in ROLE_LABEL.items():
        name = pp.get(key) or ""
        if name:
            roles.setdefault(name, []).append(label)
    return roles


def render_model(profile_pet: dict | None, packs_raw) -> dict:
    """单一事实源：当前设备实际渲染什么。

    packs_raw: iterable of (name, info) ，info 来自 web.compute_pack_info。
    返回：
      builtin            canonical 矢量描述（永远存在）
      packs              皮肤描述列表（含 active 标记）
      skin               当前激活皮肤描述或 None
      active_kind        "builtin" | "skin"
      fallback_to_builtin 是否因激活皮肤缺失/不完整而回落矢量
      psram_kb           实际 PSRAM 占用估计
      psram_budget_kb    皮肤 PSRAM 硬预算（最坏）
    """
    roles = active_pack_roles(profile_pet)
    builtin = builtin_descriptor()
    packs = [pack_descriptor(n, info, active=n in roles)
             for n, info in packs_raw]
    active_name = next(iter(roles), None)
    skin = None
    fallback = False
    if active_name:
        skin = next((p for p in packs if p["name"] == active_name), None)
        if skin is None or not skin["complete"]:
            fallback = True          # 激活皮肤缺失/不完整 → 矢量回落
            skin = None
    if skin is not None:
        skin = dict(skin)
        skin["active"] = True
        skin["roles"] = roles.get(active_name, [])
        builtin = dict(builtin)
        active_kind = "skin"
        psram_kb = skin["psram_kb"]
    else:
        builtin = dict(builtin)
        builtin["active"] = True
        active_kind = "builtin"
        psram_kb = 0
    return {
        "builtin": builtin,
        "packs": packs,
        "skin": skin,
        "active_kind": active_kind,
        "fallback_to_builtin": fallback,
        "psram_kb": psram_kb,
        "psram_budget_kb": PET_SKIN_PSRAM_BUDGET_KB,
    }
