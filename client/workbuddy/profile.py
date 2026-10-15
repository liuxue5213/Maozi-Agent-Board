"""User editable display profile and the bounded CFG wire format."""
from __future__ import annotations

import base64
import json
import os
import re
import zlib
from copy import deepcopy
from datetime import date
from pathlib import Path

from .holiday import keys as _holiday_keys

PROFILE_PATH = Path.home() / ".workbuddy" / "profile.json"
WIDGET_UNITS = {
    "ai_list": 4, "hw_cards": 4, "clock": 1, "environment": 1,
    "reminder_list": 2, "today_calendar": 2, "alarm": 1,
    "weather": 1, "countdown": 1, "ai_cost": 1, "pet": 4, "control": 4,
}
DEFAULT_PROFILE = {
    "schema": 1, "version": 1,
    "pet": {"type": "robot", "name": "帽子", "sleep": 1, "greet": 0},
    "pages": [
        {"id": "ai", "title": "帽子工作台", "layout": "list", "widgets": ["ai_list"]},
        {"id": "computer", "title": "电脑", "layout": "grid_2x2", "widgets": ["hw_cards"]},
        {"id": "environment", "title": "环境", "layout": "grid_2x2",
         "widgets": ["environment", "clock"]},
        {"id": "control", "title": "控制", "layout": "list", "widgets": ["control"]},
    ],
    "reminders": [], "calendar": [], "alarms": [], "apps": [], "countdowns": [],
    "birthdays": [], "holidays": [],
    "weather": {"city": ""},
    "theme": {"bg": "060A12", "grad": "0E1726", "card": "28384E", "accent": "4CC9F0",
              "text": "F1F5F9", "sub": "A6B6C9", "card_edge": "3C5070", "bright": 90},
}


# 自由布局组件的「常规尺寸」（设备像素）。单一真源：Web 编辑器 app.js 的 SIZE 表
# 必须与之一致，否则「预览 ≠ 真机」——有 test_editor_size_consistency.py 锁住。
FREE_SIZES = {"clock": (220, 60), "date": (260, 28), "lunar": (304, 28),
              "weather": (240, 36), "sys": (280, 28), "quote": (300, 96),
              "ai_cost": (304, 116), "pet": (96, 120),
              "remind": (304, 28), "alarm": (280, 28),
              "countdown": (304, 28), "env": (240, 28), "net": (240, 28),
              "text": (200, 28), "stat": (304, 28), "music": (304, 76),
              "ai_focus": (304, 64), "calendar": (304, 28)}
# 「大字档」尺寸：clock/weather 有专属档，其余非 ai_cost/pet/music 者 +40/+12；
# ai_cost/pet/music 不支持大字（不在表中 → 校验时大字被重置）。app.js 的 BIG_FIXED 同源。
FREE_BIG_SIZES = {"clock": (260, 64), "weather": (240, 40)}
FREE_BIG_SIZES.update({k: (v[0] + 40, v[1] + 12)
                       for k, v in FREE_SIZES.items()
                       if k not in FREE_BIG_SIZES and k not in ("ai_cost", "pet", "music")})


# 面板像素格式：与颜色映射文件 client/workbuddy/web_assets/color-map.json 的
# panel.pixelFormat 对应（ST7796 / RGB565）。对比度必须按量化后的值核算。
PANEL_BITS = (5, 6, 5)


def panel_rgb888(hex_color: str) -> str:
    """把请求的 sRGB888 按面板像素格式量化后还原——屏幕真正显示的颜色。

    屏幕不是 888 直出：每个通道先被截到 5/6/5 位，再由 LVGL 位复制还原。
    用请求色核算对比度会高估可读性（深色主题里量化把暗部压得更暗是常态），
    所以校验一律走这个函数。换屏时同步改 PANEL_BITS 与映射文件。
    """
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    br, bg, bb = PANEL_BITS

    def expand(value: int, bits: int) -> int:
        return value if bits >= 8 else ((value << (8 - bits)) | (value >> (2 * bits - 8))) & 0xFF

    return "%02X%02X%02X" % (expand(r >> (8 - br), br),
                             expand(g >> (8 - bg), bg),
                             expand(b >> (8 - bb), bb))


def blend888(front: str, back: str, alpha: float) -> str:
    """把 front 以 alpha 叠在 back 上（888 空间）。"""
    def rgb(value):
        return [int(value[i:i + 2], 16) for i in (0, 2, 4)]
    return "%02X%02X%02X" % tuple(
        max(0, min(255, round(f * alpha + b * (1 - alpha))))
        for f, b in zip(rgb(front), rgb(back)))


def default_profile():
    return deepcopy(DEFAULT_PROFILE)


def _label(value, maximum):
    value = str(value).strip()
    if not value or len(value.encode("utf-8")) > maximum or "\n" in value:
        raise ValueError(f"文字不能为空或超过 {maximum} 字节")
    for char in value:
        if char in "℃·…°":
            continue
        try:
            char.encode("gb2312")
        except UnicodeEncodeError as exc:
            raise ValueError(f"设备字库暂不支持字符：{char}") from exc
    return value


def _clock(value):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(value)):
        raise ValueError("时间需为 HH:MM")
    return str(value)


def _days(value):
    if not isinstance(value, list) or any(type(x) is not int or x < 1 or x > 7 for x in value):
        raise ValueError("星期需为 1 到 7 的数组")
    return sorted(set(value))


def _birthday_md(value):
    """生日日期归一化为 MM-DD：接受 MM-DD 或 YYYY-MM-DD（年份忽略，按每年重复）。"""
    parts = str(value or "").strip().split("-")
    try:
        if len(parts) == 3:
            m, d = int(parts[1]), int(parts[2])
        elif len(parts) == 2:
            m, d = int(parts[0]), int(parts[1])
        else:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("生日日期需为 MM-DD 或 YYYY-MM-DD") from exc
    if not (1 <= m <= 12 and 1 <= d <= 31):
        raise ValueError("生日日期不合法")
    return f"{m:02d}-{d:02d}"


def validate_profile(raw):
    """Return a normalized profile; reject layouts the 320×480 device cannot show."""
    if not isinstance(raw, dict):
        raise ValueError("配置必须是对象")
    pages = raw.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 10:
        raise ValueError("页面数量需为 1–10")
    pet = raw.get("pet", {})
    if not isinstance(pet, dict):
        raise ValueError("宠物配置格式无效")
    ptype = str(pet.get("type", "robot"))
    if ptype not in ("robot", "cat", "chick", "dog"):
        raise ValueError("宠物类型只支持 robot / cat / chick / dog")
    def _pet_flag(key, default):
        try:
            return 1 if int(pet.get(key, default)) else 0
        except (TypeError, ValueError):
            return default
    pet_sleep = _pet_flag("sleep", 1)   # 凌晨自动睡觉（0=仅犯困提示）
    pet_greet = _pet_flag("greet", 0)   # 整点/半点问候
    pet_stats = _pet_flag("show_stats", 1)    # 角色页数值行（饱食/水分/活力）
    pet_btns = _pet_flag("show_buttons", 1)   # 角色页喂食/喝水/运动按钮
    try:
        pet_overlay = max(0, min(2, int(pet.get("overlay", 0) or 0)))
    except (TypeError, ValueError):
        pet_overlay = 0   # 叠加指标（旧版）：0=无 1=今日Token 2=焦点会话；新版请改用 fields 多选
    try:
        pet_density = max(0, min(2, int(pet.get("density", 2))))
    except (TypeError, ValueError):
        pet_density = 2   # 角色页信息密度：0=单行 1=双行 2=自动摘要
    # 字段白名单：title=任务名 turn_total=本轮总Token turn_out=本轮输出；
    # turn 保留为 turn_out 别名以兼容旧档案与既有测试。
    allowed_pet_fields = {"project", "tool", "state", "elapsed", "model",
                          "turn", "turn_total", "turn_out", "today", "message", "title"}
    if "fields" in pet:
        pet_fields = pet["fields"]
        if (not isinstance(pet_fields, list) or len(pet_fields) > 4
                or any(not isinstance(f, str) or f not in allowed_pet_fields for f in pet_fields)
                or len(set(pet_fields)) != len(pet_fields)):
            raise ValueError("宠物页信息最多选择 4 项，且不可重复")
    else:
        pet_fields = ["today"] if pet_overlay == 1 else (["tool", "state"] if pet_overlay == 2 else [])
    # 昼夜自动切包：包名只作 outputs/petpack_<名>/ 目录引用，白名单字符集
    # 连续使用电脑的活动提醒（分钟，0=关）；键鼠活动只能估算电脑使用，不能计步。
    try:
        pet_break = max(0, min(240, int(pet.get("break_min", 0) or 0)))
    except (TypeError, ValueError):
        pet_break = 0
    try:
        pet_rest = max(1, min(60, int(pet.get("rest_min", 5) or 5)))
    except (TypeError, ValueError):
        pet_rest = 5
    try:
        pet_desktop_notify = 1 if int(pet.get("desktop_notify", 1)) else 0
    except (TypeError, ValueError):
        pet_desktop_notify = 1
    pet_day = str(pet.get("day_pack", "") or "")
    pet_night = str(pet.get("night_pack", "") or "")
    pet_weather = str(pet.get("weather_pack", "") or "")
    if not re.fullmatch(r"[a-z0-9_]{0,24}", pet_day):
        pet_day = ""
    if not re.fullmatch(r"[a-z0-9_]{0,24}", pet_night):
        pet_night = ""
    if not re.fullmatch(r"[a-z0-9_]{0,24}", pet_weather):
        pet_weather = ""
    # 用量看板（P1-2）：日预算阈值（千 token，0=不设限）。超限提醒由
    # Web 端按 status 的 usage_budget.exceeded 触发，这里只做校验与钳制。
    usage_raw = raw.get("usage")
    if not isinstance(usage_raw, dict):
        usage_raw = {}
    try:
        budget_daily_k = max(0, min(10_000_000, int(usage_raw.get("budget_daily_k", 0) or 0)))
    except (TypeError, ValueError):
        budget_daily_k = 0
    out = {"schema": 1, "version": int(raw.get("version", 1)), "pages": [],
           "reminders": [], "calendar": [], "alarms": [], "apps": [], "countdowns": [],
           "birthdays": [], "holidays": [],
    "weather": {"city": ""}, "theme": {}, "smc": {"interval": 20},
    "usage": {"budget_daily_k": budget_daily_k},
    "pet": {"type": ptype, "name": _label(pet.get("name", "") or "帽子", 12),
            "sleep": pet_sleep, "greet": pet_greet,
            "autopack": _pet_flag("autopack", 0),
            "show_stats": pet_stats, "show_buttons": pet_btns,
            "overlay": pet_overlay,
            "density": pet_density,
            "fields": pet_fields,
            "break_min": pet_break,
            "rest_min": pet_rest,
            "desktop_notify": pet_desktop_notify,
            "day_pack": pet_day, "night_pack": pet_night,
            "weather_pack": pet_weather}}
    # 自动分页：开启时由客户端按活跃工具重排 AI 页（全部 + 每工具一页）。
    # 设备不认识这个键（未知 JSON 键被忽略），它只是客户端重排的授权开关。
    try:
        out["autopage"] = 1 if int(raw.get("autopage", 0) or 0) else 0
    except (TypeError, ValueError):
        out["autopage"] = 0
    if out["version"] < 1:
        raise ValueError("版本号无效")
    weather = raw.get("weather", {})
    if not isinstance(weather, dict):
        raise ValueError("天气配置格式无效")
    city = str(weather.get("city", "")).strip()
    if city:
        out["weather"]["city"] = _label(city, 20)
    # 多城市：每城一个坐标（搜索时由地理编码解析），设备按名字匹配天气页。
    # 旧的单 city 字段仍接受（兼容旧档案），但新功能一律走 cities。
    cities = weather.get("cities", [])
    if not isinstance(cities, list) or len(cities) > 3:
        raise ValueError("天气城市最多 3 个")
    out["weather"]["cities"] = []
    # 诗句库（屏保「诗词短句」轮换用）：每条 ≤20 字，设备全量字库覆盖
    poems = raw.get("poems", [])
    if not isinstance(poems, list) or len(poems) > 10:
        raise ValueError("诗句库最多 10 条")
    out["poems"] = []
    seen_poems = set()
    for poem in poems:
        if not isinstance(poem, str):
            raise ValueError("诗句格式无效")
        if not poem.strip():
            continue                      # 空条目直接跳过
        verse = _label(poem, 60)
        if not verse or verse in seen_poems:
            continue
        seen_poems.add(verse)
        out["poems"].append(verse)
    # 用户库为空 → 启用内置名句（带作者署名，竞品「秋日清和/王维」观感）；
    # 用户一旦配置自己的诗句，完全以用户库为准。
    if not out["poems"]:
        from .poems import BUILTIN as _POEMS
        for verse in _POEMS[:10]:
            out["poems"].append(verse)
    seen_names = set()
    for entry in cities:
        if not isinstance(entry, dict):
            raise ValueError("天气城市格式无效")
        name = _label(entry.get("name", ""), 12)
        if not name or name in seen_names:
            raise ValueError("天气城市名不能为空或重复")
        seen_names.add(name)
        try:
            lat = round(float(entry.get("lat")), 4)
            lon = round(float(entry.get("lon")), 4)
        except (TypeError, ValueError):
            raise ValueError("天气城市缺少坐标（请通过搜索添加）")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("天气城市坐标无效")
        # label 是网页确认用的省市标签，设备不读它——不做 GB2312/长度苛限，
        # 只剔除线上分隔符并限长（省得撑 8KB 配置预算）。
        label = "".join(ch for ch in (str(entry.get("label", "") or name))
                        if ch not in ",=;\r\n")[:64]
        out["weather"]["cities"].append(
            {"name": name, "lat": lat, "lon": lon, "label": label})
    seen_weather_names = {c["name"] for c in out["weather"]["cities"]}

    ids = set()
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("页面格式无效")
        pid = str(page.get("id", ""))
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,16}", pid) or pid in ids:
            raise ValueError("页面 ID 必须唯一且只包含字母、数字、-、_")
        ids.add(pid)
        layout = page.get("layout")
        widgets = page.get("widgets")
        if layout == "free":
            # 自由布局页（竞品拖拽编辑器 v1）：components=[{t,x,y}]，
            # 组件类型白名单 + 坐标钳位进屏；widgets 弃用存 []。
            comps = page.get("components")
            if not isinstance(comps, list) or len(comps) > 8:
                raise ValueError("自由布局页组件需为 ≤8 项的列表")
            allowed = tuple(FREE_SIZES)
            sizes = FREE_SIZES
            colors = (0, 1, 2, 3)   # 0=正文 1=强调 2=次要 3=警示
            big_sizes = FREE_BIG_SIZES
            out_comps = []
            for c in comps:
                if not isinstance(c, dict):
                    raise ValueError("自由布局组件格式无效")
                t = str(c.get("t", ""))
                if t not in allowed:
                    raise ValueError(f"未知自由组件：{t[:20]}")
                try:
                    big = 1 if int(c.get("big", 0) or 0) else 0
                except (TypeError, ValueError):
                    big = 0
                if big and t not in big_sizes:
                    big = 0
                hidden = 1 if int(c.get("hidden", 0) or 0) else 0
                w, h = big_sizes[t] if big else sizes[t]
                try:
                    x = max(0, min(320 - w, int(c.get("x", 0))))
                    y = max(0, min(480 - h, int(c.get("y", 0))))
                except (TypeError, ValueError):
                    x = y = 0
                try:
                    group = max(0, min(99, int(c.get("group", 0) or 0)))
                except (TypeError, ValueError):
                    group = 0
                entry = {"t": t, "x": x, "y": y, "big": big, "hidden": hidden,
                         "group": group}
                if t == "weather":
                    # 数据绑定（竞品#59）：weather 组件绑定城市下标（0-2）
                    try:
                        entry["city"] = max(0, min(2, int(c.get("city", 0)) or 0))
                    except (TypeError, ValueError):
                        entry["city"] = 0
                if t == "text":
                    # 自定义文字：≤24 字节（8 汉字）；color 0=正文 1=强调
                    # 2=次要 3=警示（固件按主题色解析）
                    text = _label(str(c.get("text", "")) or "自定义文字", 24)
                    try:
                        color = max(0, min(3, int(c.get("color", 1)) or 0))
                    except (TypeError, ValueError):
                        color = 1
                    entry.update({"text": text, "color": color})
                out_comps.append(entry)
            out["pages"].append({"id": pid, "title": _label(page.get("title", ""), 24),
                                 "layout": "free", "widgets": [],
                                 "filter": "", "city": "",
                                 "components": out_comps})
            continue
        if layout not in ("list", "grid_2x2") or not isinstance(widgets, list):
            raise ValueError("页面布局或组件列表无效")
        if not 1 <= len(widgets) <= 4 or any(w not in WIDGET_UNITS for w in widgets):
            raise ValueError("每页需放 1–4 个支持的组件")
        if len(widgets) != len(set(widgets)):
            raise ValueError("同一页面不能重复放置同一组件")
        if any(WIDGET_UNITS[w] == 4 for w in widgets) and len(widgets) != 1:
            raise ValueError("全屏组件需独占页面")
        if sum(WIDGET_UNITS[w] for w in widgets) > 4:
            raise ValueError("组件超过页面四个插槽")
        if layout == "grid_2x2":
            slot = 0
            for widget in widgets:
                units = WIDGET_UNITS[widget]
                if units == 2 and slot % 2:
                    slot += 1
                slot += units
            if slot > 4:
                raise ValueError("横向组件需从新的一行开始，请调整组件顺序")
        # filter：本页只显示哪个工具的会话（空 = 全部）。工具页由此而来：
        # 客户端按活跃工具生成「全部 + 每工具一页」，固件按它过滤 ai0..ai7。
        flt = str(page.get("filter", "") or "").strip().lower()
        if flt and not re.fullmatch(r"[a-z0-9_]{1,12}", flt):
            raise ValueError("页面 filter 只允许 1-12 位小写字母、数字、下划线")
        # city：天气页绑定哪个城市（须在 weather.cities 里）。一个城市
        # 一个独立小屏页面，固件按名字匹配该城的实况与预报数据。
        city = str(page.get("city", "") or "").strip()
        if city:
            city = _label(city, 12)
            if city not in seen_weather_names:
                raise ValueError(f"页面绑定的城市「{city}」未在天气城市里配置")
        out["pages"].append({"id": pid, "title": _label(page.get("title", ""), 24),
                             "layout": layout, "widgets": widgets[:], "filter": flt,
                             "city": city})

    for key, limit in (("reminders", 20), ("calendar", 50), ("alarms", 16)):
        items = raw.get(key, [])
        if not isinstance(items, list) or len(items) > limit:
            raise ValueError(f"{key} 数量超出限制")
        item_ids = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"{key} 条目格式无效")
            iid = str(item.get("id", ""))
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,16}", iid) or iid in item_ids:
                raise ValueError(f"{key} ID 无效或重复")
            item_ids.add(iid)
            clean = {"id": iid, "time": _clock(item.get("time")),
                     "enabled": bool(item.get("enabled", True))}
            if key == "calendar":
                try:
                    clean["date"] = date.fromisoformat(str(item.get("date"))).isoformat()
                except ValueError as exc:
                    raise ValueError("日历日期需为 YYYY-MM-DD") from exc
                clean["title"] = _label(item.get("title", ""), 48)
            else:
                clean["weekdays"] = _days(item.get("weekdays", [1, 2, 3, 4, 5, 6, 7]))
                if not clean["weekdays"]:
                    raise ValueError("重复星期不能为空")
                clean["text" if key == "reminders" else "label"] = _label(
                    item.get("text" if key == "reminders" else "label", ""), 48)
            out[key].append(clean)

    countdowns = raw.get("countdowns", [])
    if not isinstance(countdowns, list) or len(countdowns) > 8:
        raise ValueError("倒计时最多 8 条")
    cd_ids = set()
    for item in countdowns:
        if not isinstance(item, dict):
            raise ValueError("倒计时格式无效")
        cid = str(item.get("id", ""))
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,16}", cid) or cid in cd_ids:
            raise ValueError("倒计时 ID 无效或重复")
        cd_ids.add(cid)
        clean = {"id": cid, "enabled": bool(item.get("enabled", True))}
        try:
            clean["date"] = date.fromisoformat(str(item.get("date"))).isoformat()
        except ValueError as exc:
            raise ValueError("倒计时日期需为 YYYY-MM-DD") from exc
        clean["time"] = _clock(item.get("time"))
        clean["title"] = _label(item.get("title", ""), 48)
        out["countdowns"].append(clean)

    # 生日提醒（多身份）：只存称呼 + 月-日，按年重复；客户端据「临近一个月」算屏保倒计时
    birthdays = raw.get("birthdays", [])
    if not isinstance(birthdays, list) or len(birthdays) > 8:
        raise ValueError("生日最多 8 条")
    bd_ids = set()
    for item in birthdays:
        if not isinstance(item, dict):
            raise ValueError("生日格式无效")
        bid = str(item.get("id", ""))
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,16}", bid) or bid in bd_ids:
            raise ValueError("生日 ID 无效或重复")
        bd_ids.add(bid)
        name = _label(item.get("name", ""), 12)
        if not name:
            raise ValueError("生日需填写称呼")
        out["birthdays"].append({"id": bid, "enabled": bool(item.get("enabled", True)),
                                 "name": name, "date": _birthday_md(item.get("date"))})

    # 节日（从预设目录里选）：被选中即屏保常显「距 X 还有 N 天」，无临近门槛
    holidays = raw.get("holidays", [])
    if not isinstance(holidays, list) or len(holidays) > 12:
        raise ValueError("节日最多选 12 个")
    valid_holidays = set(_holiday_keys())
    seen_holidays = set()
    for key in holidays:
        hol = str(key)
        if hol not in valid_holidays:
            raise ValueError(f"未知节日：{hol}")
        if hol in seen_holidays:
            raise ValueError(f"节日重复：{hol}")
        seen_holidays.add(hol)
        out["holidays"].append(hol)

    apps = raw.get("apps", [])
    if not isinstance(apps, list) or len(apps) > 12:
        raise ValueError("快捷应用最多 12 个")
    app_ids = set()
    for app in apps:
        if not isinstance(app, dict):
            raise ValueError("应用格式无效")
        aid = str(app.get("id", ""))
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,12}", aid) or aid in app_ids:
            raise ValueError("应用 ID 无效或重复（字母数字-_，≤12 位）")
        app_ids.add(aid)
        command = str(app.get("command", "")).strip()
        if not command or len(command) > 160 or "\n" in command:
            raise ValueError("启动命令不能为空、超过 160 字符或包含换行")
        out["apps"].append({"id": aid, "name": _label(app.get("name") or aid, 16),
                            "command": command})

    # SMC 特权采样间隔（秒）：launchd 周期，只影响主机侧温度/风扇节奏，
    # 设备不认识这个键（未知 JSON 键被忽略）。改变装中的采样器需重新授权安装。
    smc = raw.get("smc", {})
    if not isinstance(smc, dict):
        raise ValueError("smc 配置格式无效")
    try:
        smc_interval = max(10, min(3600, int(smc.get("interval", 20))))
    except (TypeError, ValueError):
        smc_interval = 20
    out["smc"] = {"interval": smc_interval}

    theme = raw.get("theme", DEFAULT_PROFILE["theme"])
    if not isinstance(theme, dict):
        raise ValueError("主题格式无效")
    for key in ("bg", "grad", "card", "accent", "text", "sub", "card_edge"):
        # card_edge 允许旧档案缺失（回退到描边默认色而非 bg）；
        # 其余颜色缺失时回退到默认主题同键值。
        fallback = DEFAULT_PROFILE["theme"].get(key) or theme.get("bg", "")
        value = str(theme.get(key, fallback)).upper().lstrip("#")
        if not re.fullmatch(r"[0-9A-F]{6}", value):
            raise ValueError(f"主题颜色 {key} 无效")
        out["theme"][key] = value
    bright = int(theme.get("bright", 90))
    if not 5 <= bright <= 100:
        raise ValueError("亮度需为 5–100")
    out["theme"]["bright"] = bright
    out["theme"]["idledim"] = max(0, min(3600, int(theme.get("idledim", 60))))
    out["theme"]["saver"] = max(0, min(3, int(theme.get("saver", 0))))
    out["theme"]["savermsg"] = _label(theme.get("savermsg", "") or "代码写完了吗？起来喝口水", 60)
    out["theme"]["topclock"] = 1 if int(theme.get("topclock", 1)) else 0
    out["theme"]["topwx"] = 1 if int(theme.get("topwx", 1)) else 0
    out["theme"]["card_opa"] = max(30, min(100, int(theme.get("card_opa", 100))))
    out["theme"]["bright_auto"] = 1 if int(theme.get("bright_auto", 0)) else 0
    # 屏保增强：相册轮播间隔秒（样式4）、切换效果（0 立即 / 1 淡入 / 2 滑入）、
    # 诗句轮换间隔分钟（样式 3）
    out["theme"]["svint"] = max(15, min(3600, int(theme.get("svint", 60))))
    out["theme"]["svfx"] = max(0, min(2, int(theme.get("svfx", 1))))
    out["theme"]["pint"] = max(1, min(120, int(theme.get("pint", 5))))
    # 下面所有比值都按屏幕实际显示的颜色（RGB565 量化后）核算，而不是请求色。
    panel = {key: panel_rgb888(out["theme"][key])
             for key in ("bg", "grad", "card", "card_edge", "accent", "text", "sub")}
    out["theme"]["panel"] = panel   # 便于诊断包/导出核对：请求值 → 上屏值

    # 任务视图 tv.* 参数（AI 页渲染细节 + 空闲切页；缺省 = 现状渲染）
    tv = raw.get("tv", {})
    if not isinstance(tv, dict):
        tv = {}

    def _tv_int(key, default, lo, hi):
        try:
            v = int(tv.get(key, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    out["tv"] = {
        "other_tasks": _tv_int("other_tasks", 1, 0, 1),
        "task_value": _tv_int("task_value", 0, 0, 1),
        "tl_mode": _tv_int("tl_mode", 0, 0, 1),
        "tl_time": _tv_int("tl_time", 0, 0, 2),
        "idle_page": _tv_int("idle_page", 0, 0, 10),
        "name_mode": _tv_int("name_mode", 0, 0, 1),   # 0=页面标题 1=自动智能体名
        "tl_range": _tv_int("tl_range", 0, 0, 1),     # 0=含近期完成 1=仅进行中
        "task_detail": _tv_int("task_detail", 0, 0, 2),  # 0=运行信息 1=最近消息 2=不显示
    }

    # AI 用量卡看板参数（竞品图4「内容组合/显示指标」；缺省=现状三槽轮换）
    # metrics 勾选与顺序即展示槽位；models=分模型消耗（每模型一槽，设备端
    # 从 AIB0..2 取按消耗降序的最多 3 个）。金额换算已按用户要求移除。
    ac = raw.get("ai_cost", {})
    if not isinstance(ac, dict):
        ac = {}
    allowed = ("today", "turn", "model", "models")
    raw_mets = ac.get("metrics") if isinstance(ac.get("metrics"), list) else []
    seen = set()
    metrics = [m for m in raw_mets
               if isinstance(m, str) and m in allowed and not (m in seen or seen.add(m))]
    if not metrics:
        metrics = ["today", "turn", "model"]
    out["ai_cost"] = {"mode": 1 if ac.get("mode") in (1, "fixed") else 0,
                      "metrics": metrics}

    def luminance(hex_color):
        values = [int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
        return sum(a * b for a, b in zip(linear, (0.2126, 0.7152, 0.0722)))

    def ratio(first, second):
        light, dark = sorted((luminance(first), luminance(second)), reverse=True)
        return (light + 0.05) / (dark + 0.05)

    # 卡片是**半透明**的：固件用 box() 的 bg_opa=card_opa 把 card 叠在渐变背景上，
    # 所以屏幕上真正的卡片填充色是**合成结果**，不是 card 本身。不透明度越低，
    # 填充越贴近背景、边界越看不清——只按 card 原色校验会系统性高估可辨识度
    # （实测 15 套已发布主题里有 11 套的实际边界低于 1.5:1）。
    # 背景是 bg→grad 的竖向渐变，卡片横跨多个高度，因此两端都算、取最差。
    alpha = out["theme"]["card_opa"] / 100.0
    fills = {side: panel_rgb888(blend888(panel["card"], panel[side], alpha))
             for side in ("bg", "grad")}
    out["theme"]["panel_fill"] = fills      # 屏幕上卡片的实际填充色（便于核对）

    if ratio(panel["text"], panel["bg"]) < 4.5:
        raise ValueError("主题文字与背景的对比度不足"
                         "（已按 RGB565 上屏值核算，实际观感比请求色更暗）")
    for side, fill in fills.items():
        if ratio(panel["text"], fill) < 4.5:
            raise ValueError("主题文字与卡片（含不透明度合成）的对比度不足，"
                             "请调亮 text 或调亮 card / 提高卡片不透明度")
    # 次要文字（标签、单位、说明）：低于 3:1 时 20px 正文在 1m 外基本读不出来。
    if ratio(panel["sub"], panel["bg"]) < 3.0:
        raise ValueError("主题次要文字与背景的对比度不足（已按 RGB565 上屏值核算），"
                         "请调亮 sub 或调暗底色")
    for side, fill in fills.items():
        if ratio(panel["sub"], fill) < 3.0:
            raise ValueError("主题次要文字与卡片（含不透明度合成）的对比度不足，"
                             "请调亮 sub 或调亮 card / 提高卡片不透明度")
    # 卡片边界必须能看出来。深色主题难达 WCAG 3:1 的非文本要求，因此用 1.5:1 作
    # 底线。固件还给每张卡片画了 1px card_edge 描边（config.h 的 COL_CARD_EDGE
    # 就是为弱对比屏加的），但描边是**补充**而非替代——纯填充看不出边界时，实机
    # 观感就是"整片糊在一起"，所以这里仍然只认填充色差（合成后的填充）。
    for side, fill in fills.items():
        if ratio(fill, panel[side]) < 1.5:
            raise ValueError("卡片与背景亮度过近（已按 RGB565 上屏值 + 不透明度合成核算），"
                             "卡片边界会看不见：请调亮 card、调暗 bg 或提高卡片不透明度")
    if len(_payload(out)) > 8192:
        raise ValueError("配置超过 8 KB 设备限制")
    return out


def _payload(profile):
    return json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# 最近一次 load_profile 降级的原因（文件损坏/校验不过被隔离备份后回退默认），
# 空 = 正常。App 启动时读走放进 status_snapshot，Web 自检面板据此提示。
LOAD_WARNING = ""


def _quarantine_profile() -> str:
    import time as _time
    backup = PROFILE_PATH.with_name(
        PROFILE_PATH.name + f".bad-{_time.strftime('%Y%m%d-%H%M%S')}")
    try:
        PROFILE_PATH.replace(backup)
        return str(backup)
    except OSError:
        return ""


def load_profile():
    global LOAD_WARNING
    LOAD_WARNING = ""
    try:
        raw = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default_profile()          # 首次运行，不是故障
    except (OSError, ValueError) as exc:
        backup = _quarantine_profile()
        LOAD_WARNING = f"屏幕档案损坏已回退默认（备份 {backup}）" if backup \
            else f"屏幕档案读取失败已回退默认：{exc}"
        print(f"[workbuddy] {LOAD_WARNING}")
        return default_profile()
    try:
        return validate_profile(raw)
    except (ValueError, TypeError) as exc:
        backup = _quarantine_profile()
        LOAD_WARNING = f"屏幕档案校验未通过已回退默认（备份 {backup}）：{exc}" \
            if backup else f"屏幕档案校验未通过已回退默认：{exc}"
        print(f"[workbuddy] {LOAD_WARNING}")
        return default_profile()


def save_profile(profile):
    profile = validate_profile(profile)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = PROFILE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, PROFILE_PATH)
    return profile


def encode_profile(profile):
    clean = validate_profile(profile)
    # 生日/节日是纯客户端特性：设备只需客户端算好的 lunar 屏保行，下发它们会平白
    # 撑大 CFG 包、逼近固件 JsonDocument 容量，故从线路载荷里剔除。
    clean.pop("birthdays", None)
    clean.pop("holidays", None)
    payload = _payload(clean)
    version = profile["version"]
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    lines = [f"CFG=BEGIN,{version},{len(payload)},{crc:08X}\n"]
    for seq, start in enumerate(range(0, len(payload), 90)):
        chunk = base64.b64encode(payload[start:start + 90]).decode("ascii")
        lines.append(f"CFG=DATA,{version},{seq},{chunk}\n")
    lines.append(f"CFG=END,{version}\n")
    return lines
