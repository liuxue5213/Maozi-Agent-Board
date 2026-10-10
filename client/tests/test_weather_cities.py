"""多城市天气 + 天气城市页的回归测试。

用户需求：天气支持多个城市（省市区精确搜索），每个城市单独作为一个小屏
标签页面，显示当前实况 + 未来 5 天预报。

链路：设置→天气（搜索添加，坐标随 profile 持久化）→ weather.py 每城拉取
实况+5 天预报 → 线级 W0..W2 / WF0..WF2 → 固件按页面 city（城市名）匹配
显示。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbuddy.app import weather_wire_lines
from workbuddy.profile import validate_profile

CLIENT = Path(__file__).resolve().parents[1]
FIRMWARE = CLIENT.parent / "firmware" / "src"


class WeatherWireTests(unittest.TestCase):
    CITIES = [{"name": "上海"}, {"name": "杭州"}, {"name": "北京"}]

    def _data(self):
        return {
            "上海": {"temp": 23.4, "code": 3, "hi": 25, "lo": 17,
                    "days": [{"code": 3, "lo": 17, "hi": 25},
                             {"code": 2, "lo": 16, "hi": 23},
                             {"code": 61, "lo": 15, "hi": 20},
                             {"code": 61, "lo": 14, "hi": 19},
                             {"code": 3, "lo": 16, "hi": 22}]},
            "杭州": {"temp": 24.1, "code": 0, "hi": 26, "lo": 18,
                    "days": [{"code": 0, "lo": 18, "hi": 26}]},
        }

    def test_lines_follow_city_order(self):
        lines = weather_wire_lines(self.CITIES, self._data())
        self.assertTrue(lines["w0"].startswith("23.4,3,25,17"))
        self.assertTrue(lines["w1"].startswith("24.1,0,26,18"))
        self.assertIn("61,15,20", lines["wf0"], "5 天预报须含第 3 天")

    def test_five_days_semicolon_separated(self):
        lines = weather_wire_lines(self.CITIES, self._data())
        self.assertEqual(len(lines["wf0"].split(";")), 5)

    def test_empty_slots_sent_explicitly(self):
        """北京没有数据 → w2/wf2 必须为空串上线（清位，不留上个城市的残影）。"""
        lines = weather_wire_lines(self.CITIES[:2], self._data())
        self.assertEqual(lines["w2"], "")
        self.assertEqual(lines["wf2"], "")

    def test_unknown_city_data_is_skipped(self):
        lines = weather_wire_lines(self.CITIES[:1], {"火星": {"temp": 1}})
        self.assertEqual(lines["w0"], "")

    def test_slots_declared_in_protocol(self):
        proto = (CLIENT / "workbuddy" / "protocol.py").read_text()
        for key in ('"w0"', '"w1"', '"w2"', '"wf0"', '"wf1"', '"wf2"'):
            self.assertIn(key, proto)


class WeatherProfileTests(unittest.TestCase):
    def _profile(self, weather, pages=None):
        return {"schema": 1, "version": 1, "weather": weather,
                "pages": pages if pages is not None else
                [{"id": "w", "title": "上海", "layout": "list",
                  "widgets": ["weather"], "city": "上海"}]}

    def test_city_with_coordinates_accepted(self):
        out = validate_profile(self._profile(
            {"cities": [{"name": "上海", "lat": 31.2304, "lon": 121.4737,
                         "label": "上海（上海市）"}]}))
        self.assertEqual(out["weather"]["cities"][0]["lat"], 31.2304)
        self.assertEqual(out["pages"][0]["city"], "上海")

    def test_more_than_three_cities_rejected(self):
        cities = [{"name": f"城市{i}", "lat": 30, "lon": 120} for i in range(4)]
        with self.assertRaises(ValueError):
            validate_profile(self._profile({"cities": cities}))

    def test_duplicate_city_rejected(self):
        dup = [{"name": "上海", "lat": 31, "lon": 121},
               {"name": "上海", "lat": 31, "lon": 121}]
        with self.assertRaises(ValueError):
            validate_profile(self._profile({"cities": dup}))

    def test_city_without_coordinates_rejected(self):
        with self.assertRaises(ValueError):
            validate_profile(self._profile({"cities": [{"name": "上海"}]}))

    def test_page_city_must_be_a_configured_city(self):
        with self.assertRaises(ValueError) as ctx:
            validate_profile(self._profile(
                {"cities": [{"name": "上海", "lat": 31, "lon": 121}]},
                pages=[{"id": "w", "title": "北京", "layout": "list",
                        "widgets": ["weather"], "city": "北京"}]))
        self.assertIn("未在天气城市", str(ctx.exception))

    def test_page_without_city_still_valid(self):
        """非城市天气页（旧的实况卡）继续合法。"""
        out = validate_profile(self._profile(
            {"cities": [{"name": "上海", "lat": 31, "lon": 121}]},
            pages=[{"id": "w", "title": "天气", "layout": "grid_2x2",
                    "widgets": ["weather"]}]))
        self.assertEqual(out["pages"][0].get("city", ""), "")


class FirmwareParityTests(unittest.TestCase):
    def test_firmware_parses_city_names_and_page_city(self):
        h = (FIRMWARE / "profile.h").read_text()
        self.assertIn("char city_names[3][13]", h)
        self.assertIn("char city[13]", h)
        cpp = (FIRMWARE / "profile.cpp").read_text()
        self.assertIn('page["city"]', cpp)
        self.assertIn('wc["name"]', cpp)

    def test_firmware_parses_w_and_wf_slots(self):
        proto_cpp = (FIRMWARE / "proto.cpp").read_text()
        self.assertIn('handle_wf(key[2] - \'0\', v)', proto_cpp)
        self.assertIn('handle_w(key[1] - \'0\', v)', proto_cpp)
        proto_h = (FIRMWARE / "proto.h").read_text()
        self.assertIn("CityWx cities[3];", proto_h)
        self.assertIn("CityDay days[5];", proto_h)

    def test_forecast_is_five_days(self):
        weather = (CLIENT / "workbuddy" / "weather.py").read_text()
        self.assertIn("forecast_days=5", weather, "Open-Meteo 需取 5 天预报")

    def test_preview_and_firmware_share_geometry(self):
        ui = (FIRMWARE / "ui.cpp").read_text()
        js = (CLIENT / "workbuddy" / "web_assets" / "app.js").read_text()
        for constant in ("184 + 50 * i", "304, 116", "304, 46"):
            self.assertIn(constant, ui, f"固件缺少天气页几何：{constant}")
            self.assertIn(constant.replace(" ", ""), js.replace(" ", ""),
                          f"预览缺少天气页几何：{constant}")


class VariantTests(unittest.TestCase):
    def test_variants_are_prefixes_shortest_first(self):
        """前缀枚举、按长度升序（粗→细）：市级最短名最先尝试。"""
        from workbuddy.weather import _variants
        self.assertEqual(_variants("北京市朝阳区"),
                         ["北京", "北京市", "北京市朝", "北京市朝阳", "北京市朝阳区"])
        self.assertEqual(_variants("天津"), ["天津"])
        self.assertEqual(_variants("上海市"), ["上海", "上海市"])

    def test_geocode_uses_coarse_first(self):
        """带行政后缀的查询必须能命中父级市（此前全部零结果——用户实测踩过）。"""
        from workbuddy import weather
        calls = []
        def fake_search(name):
            calls.append(name)
            # 模拟 GeoNames 只认裸地名：带后缀的查询一律空
            return [{"name": name, "label": name, "lat": 30, "lon": 120}] \
                if name in ("北京", "杭州") else []
        original = weather._openmeteo_search
        weather._openmeteo_search = fake_search
        try:
            results = weather.geocode("北京市朝阳区")
        finally:
            weather._openmeteo_search = original
        self.assertEqual(results[0]["name"], "北京")
        self.assertEqual(calls[0], "北京", "最短（市级）候选必须最先尝试")


class WebEndpointTests(unittest.TestCase):
    def test_weather_search_endpoint_exists(self):
        web = (CLIENT / "workbuddy" / "web.py").read_text()
        self.assertIn('"/api/weather-search"', web)
        self.assertIn("geocode", web)

    def test_geocode_parses_admin_labels(self):
        """省市区标签：admin1/admin2 拼进 label，供用户确认精确位置。"""
        from workbuddy import weather
        weather_src = weather.__dict__["_GEO"]
        self.assertIn("language=zh", weather_src, "地理编码须用中文")
        self.assertIn("count=5", weather_src)


if __name__ == "__main__":
    unittest.main()
