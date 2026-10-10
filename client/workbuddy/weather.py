"""天气拉取：Open-Meteo 免费接口（无需密钥），后台线程每 30 分钟刷新。

多城市（≤3）：每个城市一次请求同时拿 **当前实况 + 5 天预报**
（daily 含今天共 5 天）。城市坐标在「设置 → 天气」搜索时由地理编码
解析并随 profile 持久化（省市区精确匹配），本模块只在缺坐标时兜底
地理编码，并把结果缓存到 ~/.workbuddy/geocache.json。
拉取失败保留上一次数据。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

_GEO = ("https://geocoding-api.open-meteo.com/v1/search?name={}"
        "&count=5&language=zh&format=json")
_FCST = ("https://api.open-meteo.com/v1/forecast?latitude={}&longitude={}"
         "&current=temperature_2m,weather_code"
         "&daily=weather_code,temperature_2m_max,temperature_2m_min"
         "&timezone=auto&forecast_days=5")
_CACHE = Path.home() / ".workbuddy" / "geocache.json"
MAX_CITIES = 3


# 行政边界字：按它们从右往左切出「父级行政区」候选（天津市河西区 → 天津市）
# 尾缀剥离链：河西区 → 河西（Open-Meteo/GeoNames 只认裸地名）


def _variants(query: str) -> list[str]:
    """中文地址 → 搜索候选：**前缀枚举**，按长度升序（粗→细）。

    GeoNames 只认裸地名：天津市河西区/河西区/北京市朝阳区 这类带行政
    后缀的串全部零结果。而前缀枚举天然按「市级最短优先」排列——
    北京朝阳区 的 2 字前缀就是 北京，一次命中。最短 2 字起。"""
    q = query.strip()
    out, seen = [], set()
    for k in range(2, len(q) + 1):
        v = q[:k]
        if v not in seen:
            seen.add(v)
            out.append(v)
    out.sort(key=len)
    return out


def _openmeteo_search(name: str) -> list[dict]:
    url = _GEO.format(urllib.parse.quote(name))
    with urllib.request.urlopen(url, timeout=8) as resp:
        results = json.loads(resp.read()).get("results") or []
    out = []
    for r in results[:5]:
        admin = "、".join(x for x in (r.get("admin1"), r.get("admin2")) if x)
        label = (f"{r.get('name')}（{admin}，{r.get('country')}）"
                 if admin else f"{r.get('name')}（{r.get('country')}）")
        out.append({"name": r.get("name") or name,
                    "label": label,
                    "lat": round(float(r["latitude"]), 4),
                    "lon": round(float(r["longitude"]), 4)})
    return out


def geocode(query: str) -> list[dict]:
    """省市区搜索：返回 [{name, label, lat, lon}]（label 含省市，供确认）。

    GeoNames 只认裸地名：带行政后缀（天津市河西区/河西区/天津市）的查询
    全部零结果。因此按变体链逐个尝试，父级行政区（天津市）优先于同名
    小地名（河西镇）——天气预报本就是城市网格数据，市级即精确。
    """
    # 粗→细：越短的名字越「市级」，GeoNames 命中质量越高（北京 > 北京市 > 北京朝阳区）
    for variant in sorted(_variants(query), key=len):
        results = _openmeteo_search(variant)
        if results:
            return results
    return []


class WeatherFetcher:
    """多城市天气：每城 {temp, code, hi, lo, days:[{code,lo,hi}×5]}。

    get_cities 回调返回 profile 里的城市列表（含坐标）；坐标缺失的条目
    用 geocache 兜底地理编码。数据按**城市名**键存，客户端组装线级
    W0..W2 / WF0..WF2 时按同一顺序取用。
    """

    def __init__(self, get_cities):
        self.get_cities = get_cities      # 返回城市列表的回调（读 profile）
        self.data = {}                    # name -> {...}
        self._lock = threading.Lock()
        self._geo_cache = self._load_cache()
        self._last_fetch = 0.0
        self._wake = threading.Event()
        self._thread = None
        self._stop = False

    # ---- 地理编码缓存（跨重启） ----

    def _load_cache(self) -> dict:
        try:
            return json.loads(_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_cache(self) -> None:
        try:
            _CACHE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE.write_text(json.dumps(self._geo_cache, ensure_ascii=False),
                              encoding="utf-8")
        except OSError:
            pass

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def kick(self):
        """城市配置刚改过：立刻拉一轮，不等 30 分钟周期。"""
        self._wake.set()

    def _loop(self):
        time.sleep(2)   # 等主循环起完
        while not self._stop:
            self._wake.clear()
            cities = []
            try:
                cities = self.get_cities() or []
            except Exception:
                pass
            if cities:
                for entry in cities[:MAX_CITIES]:
                    name = entry.get("name") or ""
                    try:
                        result = self._fetch(entry)
                        if result:
                            with self._lock:
                                self.data[name] = result
                            print(f"[WX] {name}: {result['temp']}℃ code={result['code']} "
                                  f"{len(result['days'])}天", flush=True)
                        else:
                            print(f"[WX] {name}: 无坐标，跳过", flush=True)
                    except Exception as exc:
                        print(f"[WX] {name} 拉取失败: {exc!r}", flush=True)  # 网络失败保留旧值
            # 30 分钟周期；kick（城市配置刚改过）可随时打断提前拉取
            self._wake.wait(1800)

    def _coord(self, entry: dict):
        """城市坐标：profile 里带的优先，缺失时地理编码并缓存。"""
        try:
            lat, lon = float(entry["lat"]), float(entry["lon"])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
        except (KeyError, TypeError, ValueError):
            pass
        name = entry.get("name") or ""
        if name not in self._geo_cache:
            results = geocode(name)
            if not results:
                return None
            self._geo_cache[name] = (results[0]["lat"], results[0]["lon"])
            self._save_cache()
        return self._geo_cache[name]

    def _fetch(self, entry: dict):
        coord = self._coord(entry)
        if not coord:
            return None
        lat, lon = coord
        url = _FCST.format(lat, lon)
        with urllib.request.urlopen(url, timeout=8) as resp:
            forecast = json.loads(resp.read())
        current = forecast.get("current", {})
        daily = forecast.get("daily", {})
        codes = daily.get("weather_code") or []
        his = daily.get("temperature_2m_max") or []
        los = daily.get("temperature_2m_min") or []
        days = [{"code": int(c), "hi": round(float(h)), "lo": round(float(l))}
                for c, h, l in zip(codes, his, los)][:5]
        return {
            "temp": round(float(current["temperature_2m"]), 1),
            "code": int(current.get("weather_code", -1)),
            "hi": days[0]["hi"] if days else None,
            "lo": days[0]["lo"] if days else None,
            "days": days,
            "city": entry.get("name") or "",
        }
