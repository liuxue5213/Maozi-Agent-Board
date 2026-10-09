"""天气拉取：Open-Meteo 免费接口（无需密钥），后台线程每 30 分钟刷新。

城市名 → 经纬度（geocoding API）→ 当前温度/天气代码 + 今日高低温。
拉取失败保留上一次数据。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

_GEO = "https://geocoding-api.open-meteo.com/v1/search?name={}&count=1&language=zh&format=json"
_FCST = ("https://api.open-meteo.com/v1/forecast?latitude={}&longitude={}"
         "&current=temperature_2m,weather_code"
         "&daily=temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=1")


class WeatherFetcher:
    def __init__(self, get_city):
        self.get_city = get_city          # 返回城市名的回调（读 profile）
        self.data = {}                    # {"temp","code","hi","lo","city"}
        self._lock = threading.Lock()
        self._geo_cache = {}              # city -> (lat, lon)
        self._last_fetch = 0.0
        self._thread = None
        self._stop = False

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def _loop(self):
        time.sleep(2)   # 等主循环起完
        while not self._stop:
            city = self.get_city() or ""
            if city:
                try:
                    result = self._fetch(city)
                    if result:
                        with self._lock:
                            self.data = result
                except Exception:
                    pass  # 网络失败保留旧值
            time.sleep(1800)   # 30 分钟

    def _fetch(self, city: str) -> dict | None:
        if city not in self._geo_cache:
            url = _GEO.format(urllib.parse.quote(city))
            with urllib.request.urlopen(url, timeout=8) as resp:
                results = json.loads(resp.read()).get("results") or []
            if not results:
                return None
            self._geo_cache[city] = (results[0]["latitude"], results[0]["longitude"])
        lat, lon = self._geo_cache[city]
        url = _FCST.format(lat, lon)
        with urllib.request.urlopen(url, timeout=8) as resp:
            forecast = json.loads(resp.read())
        current = forecast.get("current", {})
        daily = forecast.get("daily", {})
        return {
            "temp": round(float(current["temperature_2m"]), 1),
            "code": int(current.get("weather_code", -1)),
            "hi": round(float(daily["temperature_2m_max"][0])),
            "lo": round(float(daily["temperature_2m_min"][0])),
            "city": city,
        }
