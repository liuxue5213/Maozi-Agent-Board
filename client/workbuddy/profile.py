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

PROFILE_PATH = Path.home() / ".workbuddy" / "profile.json"
WIDGET_UNITS = {
    "ai_list": 4, "hw_cards": 4, "clock": 1, "environment": 1,
    "reminder_list": 2, "today_calendar": 2, "alarm": 1,
    "weather": 1, "countdown": 1,
}
DEFAULT_PROFILE = {
    "schema": 1, "version": 1,
    "pages": [
        {"id": "ai", "title": "帽子工作台", "layout": "list", "widgets": ["ai_list"]},
        {"id": "computer", "title": "电脑", "layout": "grid_2x2", "widgets": ["hw_cards"]},
        {"id": "environment", "title": "环境", "layout": "grid_2x2",
         "widgets": ["environment", "clock"]},
    ],
    "reminders": [], "calendar": [], "alarms": [], "apps": [], "countdowns": [],
    "weather": {"city": ""},
    "theme": {"bg": "0B1220", "grad": "0E1B33", "card": "274064", "accent": "22D3EE",
              "text": "F3F7FF", "sub": "A8BDD9", "card_edge": "3E506E", "bright": 90},
}


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


def validate_profile(raw):
    """Return a normalized profile; reject layouts the 480×320 device cannot show."""
    if not isinstance(raw, dict):
        raise ValueError("配置必须是对象")
    pages = raw.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 6:
        raise ValueError("页面数量需为 1–6")
    out = {"schema": 1, "version": int(raw.get("version", 1)), "pages": [],
           "reminders": [], "calendar": [], "alarms": [], "apps": [], "countdowns": [],
    "weather": {"city": ""}, "theme": {}}
    if out["version"] < 1:
        raise ValueError("版本号无效")
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
        if layout not in ("list", "grid_2x2") or not isinstance(widgets, list):
            raise ValueError("页面布局或组件列表无效")
        if not 1 <= len(widgets) <= 4 or any(w not in WIDGET_UNITS for w in widgets):
            raise ValueError("每页需放 1–4 个支持的组件")
        if len(widgets) != len(set(widgets)):
            raise ValueError("同一页面不能重复放置同一组件")
        if any(WIDGET_UNITS[w] == 4 for w in widgets) and len(widgets) != 1:
            raise ValueError("AI 列表和硬件四卡片需独占页面")
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
        out["pages"].append({"id": pid, "title": _label(page.get("title", ""), 24),
                             "layout": layout, "widgets": widgets[:]})

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

    weather = raw.get("weather", {})
    if not isinstance(weather, dict):
        raise ValueError("天气配置格式无效")
    city = str(weather.get("city", "")).strip()
    if city:
        out["weather"]["city"] = _label(city, 20)

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
    def luminance(hex_color):
        values = [int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
        return sum(a * b for a, b in zip(linear, (0.2126, 0.7152, 0.0722)))
    for surface in ("bg", "card"):
        light, dark = sorted((luminance(out["theme"]["text"]), luminance(out["theme"][surface])), reverse=True)
        if (light + 0.05) / (dark + 0.05) < 4.5:
            raise ValueError("主题文字与背景或卡片的对比度不足")
    # 次要文字（标签、单位、说明）对卡片的对比度：低于 3:1 时 20px 正文在
    # 1m 外基本读不出来，属于可用性问题，必须拦住。
    sub_l = luminance(out["theme"]["sub"])
    for surface in ("bg", "card"):
        light, dark = sorted((sub_l, luminance(out["theme"][surface])), reverse=True)
        if (light + 0.05) / (dark + 0.05) < 3.0:
            raise ValueError("主题次要文字与背景的对比度不足，请调亮 sub 或调暗底色")
    # 卡片与背景必须有可辨识的亮度差。深色主题难达 WCAG 3:1 的非文本要求，
    # 因此这里用1.5:1 作为底线：低于此值卡片边界在实机上等于看不见。
    bg_l, card_l = luminance(out["theme"]["bg"]), luminance(out["theme"]["card"])
    if (max(bg_l, card_l) + 0.05) / (min(bg_l, card_l) + 0.05) < 1.5:
        raise ValueError("卡片与背景亮度过近，卡片边界会看不见，请调亮 card 或调暗 bg")
    if len(_payload(out)) > 8192:
        raise ValueError("配置超过 8 KB 设备限制")
    return out


def _payload(profile):
    return json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def load_profile():
    try:
        return validate_profile(json.loads(PROFILE_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return default_profile()


def save_profile(profile):
    profile = validate_profile(profile)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = PROFILE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, PROFILE_PATH)
    return profile


def encode_profile(profile):
    payload = _payload(validate_profile(profile))
    version = profile["version"]
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    lines = [f"CFG=BEGIN,{version},{len(payload)},{crc:08X}\n"]
    for seq, start in enumerate(range(0, len(payload), 90)):
        chunk = base64.b64encode(payload[start:start + 90]).decode("ascii")
        lines.append(f"CFG=DATA,{version},{seq},{chunk}\n")
    lines.append(f"CFG=END,{version}\n")
    return lines
