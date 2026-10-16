"""硬件告警状态机单测：CPU 高温 / 风扇异常 / 设备断连。

纯逻辑测试：以最小假对象（SimpleNamespace + event 记录）直接驱动
WorkBuddyApp 的三个 _update_*_alert 未绑定方法，不建临时目录、不碰串口，
因此不依赖沙箱/平台能力，可在任何环境稳定运行。

锁定的行为契约（与 M3 路线图一致）：
- 持续 60s 才告警（防瞬时噪声）；恢复走滞回，不抖动；
- 风扇异常必须"CPU 已发热 + 风扇低转"同时成立（冷机停风扇是正常节能）；
- 设备断连只在"曾连接过"后触发（客户端可无设备运行）；
- 阈值改为 0 = 关闭对应规则；阈值变更重置旧防抖计时。
"""

import unittest
from types import SimpleNamespace

from workbuddy.app import App


class _FakeApp:
    """只提供告警状态机用到的最小面：cfg / last / link / health。"""

    def __init__(self, **kw):
        self.last = {}
        self.link = None
        self.events = []
        self._last_tick_at = 0.0
        self.cfg = SimpleNamespace(
            hw_cpu_temp=kw.get("hw_cpu_temp", 85.0),
            hw_fan_min=kw.get("hw_fan_min", 1200),
            hw_dev_off_sec=kw.get("hw_dev_off_sec", 30),
            hw_stale_sec=kw.get("hw_stale_sec", 60),          # 设备静默（链路在无数据）
            hw_loop_stale_sec=kw.get("hw_loop_stale_sec", 30))  # 客户端循环冻结
        self.health = self          # 鸭子类型：event() 记录到 self.events

    def event(self, kind, **fields):
        self.events.append((kind, fields))

    def kinds(self):
        return [k for k, _ in self.events]


class FanAlertTests(unittest.TestCase):
    def setUp(self):
        self.app = _FakeApp()

    def test_fires_only_after_60s_sustained(self):
        self.app.last = {"cputemp": 90, "fan": 800}
        App._update_fan_alert(self.app, 1000)      # 进入异常，未满 60s
        self.assertFalse(self.app._fan_alert["active"])
        App._update_fan_alert(self.app, 1030)      # 30s：仍未满
        self.assertFalse(self.app._fan_alert["active"])
        App._update_fan_alert(self.app, 1061)      # 61s：告警生效
        self.assertTrue(self.app._fan_alert["active"])
        self.assertEqual(self.app.kinds(), ["hw_alert"])
        kind, fields = self.app.events[0]
        self.assertEqual(kind, "hw_alert")
        self.assertEqual(fields.get("sensor"), "fan_stall")
        self.assertEqual(fields.get("temp"), 90)
        self.assertEqual(fields.get("fan"), 800)

    def test_cold_machine_with_stopped_fan_is_normal(self):
        # 低温下风扇停转是正常节能：即使 fan=0 也不告警
        self.app.last = {"cputemp": 45, "fan": 0}
        for t in range(1000, 1200, 30):
            App._update_fan_alert(self.app, t)
        self.assertFalse(self.app._fan_alert["active"])
        self.assertEqual(self.app.events, [])

    def test_warm_threshold_is_cpu_temp_minus_10(self):
        # 默认 hw_cpu_temp=85 → 发热线 75℃：74℃ 不判，76℃ 判
        self.app.last = {"cputemp": 74, "fan": 500}
        App._update_fan_alert(self.app, 1000)
        App._update_fan_alert(self.app, 1061)
        self.assertFalse(self.app._fan_alert["active"])

        self.app.last = {"cputemp": 76, "fan": 500}
        App._update_fan_alert(self.app, 2000)
        App._update_fan_alert(self.app, 2061)
        self.assertTrue(self.app._fan_alert["active"])

    def test_recovers_when_fan_spins_up(self):
        self.app.last = {"cputemp": 90, "fan": 600}
        App._update_fan_alert(self.app, 1000)
        App._update_fan_alert(self.app, 1061)
        self.assertTrue(self.app._fan_alert["active"])
        self.app.last = {"cputemp": 70, "fan": 3000}         # 降温且风扇恢复
        App._update_fan_alert(self.app, 1200)
        self.assertFalse(self.app._fan_alert["active"])
        self.assertEqual(self.app.kinds(), ["hw_alert", "hw_recovered"])

    def test_no_reading_means_no_alert(self):
        # 风扇读不到（None / 缺失）或温度缺失：不误报
        self.app.last = {"cputemp": 95, "fan": None}
        App._update_fan_alert(self.app, 1000)
        App._update_fan_alert(self.app, 1061)
        self.assertFalse(self.app._fan_alert["active"])
        self.app.last = {"fan": 0}                           # 缺温度
        App._update_fan_alert(self.app, 2000)
        App._update_fan_alert(self.app, 2061)
        self.assertFalse(self.app._fan_alert["active"])
        self.assertEqual(self.app.events, [])

    def test_disabled_when_threshold_zero(self):
        app = _FakeApp(hw_fan_min=0)
        app.last = {"cputemp": 95, "fan": 0}
        for t in range(1000, 1200, 30):
            App._update_fan_alert(app, t)
        self.assertFalse(app._fan_alert["active"])
        self.assertEqual(app.events, [])

    def test_threshold_change_resets_debounce(self):
        self.app.last = {"cputemp": 90, "fan": 800}
        App._update_fan_alert(self.app, 1000)
        App._update_fan_alert(self.app, 1050)       # 累计 50s
        self.app.cfg.hw_fan_min = 2000                       # 改阈值：旧计时作废
        App._update_fan_alert(self.app, 1080)       # 距新阈值仅 30s
        self.assertFalse(self.app._fan_alert["active"])
        App._update_fan_alert(self.app, 1141)       # 满 60s 才告警
        self.assertTrue(self.app._fan_alert["active"])


class DeviceAlertTests(unittest.TestCase):
    def test_never_connected_means_no_alert(self):
        app = _FakeApp()
        app.link = None
        for t in range(1000, 1200, 10):
            App._update_device_alert(app, t)
        self.assertFalse(app._dev_alert["active"])
        self.assertEqual(app.events, [])

    def test_fires_after_sustained_disconnect(self):
        app = _FakeApp()
        app.link = SimpleNamespace(status="connected:/dev/ttyUSB0")
        App._update_device_alert(app, 1000)         # 标记曾连接
        app.link = SimpleNamespace(status="串口关闭")
        App._update_device_alert(app, 1020)         # 掉线 20s：未满 30s
        self.assertFalse(app._dev_alert["active"])
        App._update_device_alert(app, 1031)         # 掉线 31s：告警
        self.assertTrue(app._dev_alert["active"])
        self.assertEqual(app.kinds(), ["hw_alert"])
        self.assertEqual(app.events[0][0], "hw_alert")
        self.assertEqual(app.events[0][1].get("sensor"), "device_offline")

    def test_recovers_on_reconnect(self):
        app = _FakeApp()
        app.link = SimpleNamespace(status="connected:/dev/ttyUSB0")
        App._update_device_alert(app, 1000)
        app.link = SimpleNamespace(status="closed")
        App._update_device_alert(app, 1031)
        self.assertTrue(app._dev_alert["active"])
        app.link = SimpleNamespace(status="connected:/dev/ttyUSB0")
        App._update_device_alert(app, 1100)
        self.assertFalse(app._dev_alert["active"])
        self.assertEqual(app.kinds(), ["hw_alert", "hw_recovered"])

    def test_disabled_when_threshold_zero(self):
        app = _FakeApp(hw_dev_off_sec=0)
        app.link = SimpleNamespace(status="connected:x")
        App._update_device_alert(app, 1000)
        app.link = SimpleNamespace(status="closed")
        for t in range(1000, 1300, 10):
            App._update_device_alert(app, t)
        self.assertFalse(app._dev_alert["active"])
        self.assertEqual(app.events, [])


class CpuAlertRegressionTests(unittest.TestCase):
    """锁定既有 CPU 高温行为，防止本次改动回归。"""

    def test_fires_after_60s_and_recovers_with_hysteresis(self):
        app = _FakeApp()
        app.last = {"cputemp": 92}
        App._update_hw_alert(app, 1000)
        App._update_hw_alert(app, 1030)
        self.assertFalse(app._hw_alert["active"])
        App._update_hw_alert(app, 1061)
        self.assertTrue(app._hw_alert["active"])
        self.assertEqual(app.kinds(), ["hw_alert"])
        app.last = {"cputemp": 80}                            # 降到阈值-5：滞回未到
        App._update_hw_alert(app, 1200)
        self.assertTrue(app._hw_alert["active"])              # 不恢复（-10℃ 滞后）
        app.last = {"cputemp": 74}                            # 低于阈值-10：恢复
        App._update_hw_alert(app, 1300)
        self.assertFalse(app._hw_alert["active"])
        self.assertEqual(app.kinds(), ["hw_alert", "hw_recovered"])

    def test_no_temp_data_keeps_state_silent(self):
        app = _FakeApp()
        app.last = {}
        App._update_hw_alert(app, 1000)
        App._update_hw_alert(app, 1061)
        self.assertFalse(app._hw_alert["active"])
        self.assertEqual(app.events, [])


class StaleAlertTests(unittest.TestCase):
    """数据陈旧（链路在但持续无新数据）——与 device_offline 互补。"""

    def _app(self, stale_sec=10):
        app = _FakeApp()
        app.cfg.hw_stale_sec = stale_sec
        app._dev_was_up = True                      # 曾连接过
        return app

    def test_rises_after_sustained_stale(self):
        app = self._app()
        app.link = SimpleNamespace(status="connected:/dev/x")
        App._update_stale_alert(app, 1000)
        app.link = SimpleNamespace(status="stale:/dev/x")   # 链路在但静默
        App._update_stale_alert(app, 1020)          # 20s：未满 30s 阈值
        self.assertFalse(app._stale_alert["active"])
        App._update_stale_alert(app, 1041)          # 41s：告警
        self.assertTrue(app._stale_alert["active"])
        self.assertEqual(app.events[0][1].get("sensor"), "data_stale")

    def test_recovers_when_data_resumes(self):
        app = self._app()
        app.link = SimpleNamespace(status="connected:/dev/x")
        App._update_stale_alert(app, 1000)
        app.link = SimpleNamespace(status="stale:/dev/x")
        App._update_stale_alert(app, 1020)          # 20s：未满阈值
        self.assertFalse(app._stale_alert["active"])
        App._update_stale_alert(app, 1041)          # 41s：告警
        self.assertTrue(app._stale_alert["active"])
        app.link = SimpleNamespace(status="connected:/dev/x")
        App._update_stale_alert(app, 1100)
        self.assertFalse(app._stale_alert["active"])
        self.assertEqual(app.kinds(), ["hw_alert", "hw_recovered"])

    def test_no_alert_when_offline(self):
        """链路彻底丢失归 device_offline 管，stale 不应 active。"""
        app = self._app()
        app.link = SimpleNamespace(status="connected:/dev/x")
        App._update_stale_alert(app, 1000)
        app.link = SimpleNamespace(status="closed")          # 链路丢失
        for t in range(1000, 1300, 10):
            App._update_stale_alert(app, t)
        self.assertFalse(app._stale_alert["active"])


class DeviceVsStaleRegressionTests(unittest.TestCase):
    """P1-2 修正点：stale: 状态必须归数据陈旧，不得误报为「断连」。"""

    def test_stale_status_not_counted_as_offline(self):
        app = _FakeApp(hw_dev_off_sec=5)
        app.cfg.hw_stale_sec = 10
        app.link = SimpleNamespace(status="connected:/dev/x")
        App._update_device_alert(app, 1000)          # 锚定曾连接
        app.link = SimpleNamespace(status="stale:/dev/x")   # 静默但链路在
        for t in range(1000, 1200, 10):
            App._update_device_alert(app, t)
        self.assertFalse(app._dev_alert["active"])  # 不应误报断连
        # 同一状态应触发的是数据陈旧，而非断连
        app.link = SimpleNamespace(status="stale:/dev/x")
        App._update_stale_alert(app, 1000)
        App._update_stale_alert(app, 1041)
        self.assertTrue(app._stale_alert["active"])


class FrozenAlertTests(unittest.TestCase):
    """数据冻结（客户端采集循环自身停滞，telemetry_age 持续增长）。

    与 StaleAlertTests（设备静默：链路在但小屏不说话）互补——冻结管的是
    循环卡死/崩溃：界面全部读数停在旧值，必须告警而非让用户误以为实时。
    """

    def test_fires_only_after_threshold_plus_15s_sustain(self):
        app = _FakeApp()
        app._last_tick_at = 1000.0
        App._update_frozen_alert(app, 1020)      # age=20 < 30：未越阈
        self.assertFalse(app._frozen_alert["active"])
        App._update_frozen_alert(app, 1031)      # age=31 越阈，但持续仅 0s
        self.assertFalse(app._frozen_alert["active"])
        App._update_frozen_alert(app, 1044)      # 越阈后持续 13s：仍未满 15s
        self.assertFalse(app._frozen_alert["active"])
        App._update_frozen_alert(app, 1046)      # 越阈后持续 15s：告警
        self.assertTrue(app._frozen_alert["active"])
        self.assertEqual(app.kinds(), ["hw_alert"])
        self.assertEqual(app.events[0][1].get("sensor"), "frozen")
        self.assertGreaterEqual(app.events[0][1].get("age"), 30)

    def test_fresh_tick_keeps_quiet(self):
        app = _FakeApp()
        app._last_tick_at = 1000.0
        for t in (1002, 1005, 1010, 1020):       # 采集正常：age 始终很小
            App._update_frozen_alert(app, t)
        self.assertFalse(app._frozen_alert["active"])
        self.assertEqual(app.events, [])

    def test_recovers_with_hysteresis(self):
        app = _FakeApp()
        app._last_tick_at = 1000.0
        App._update_frozen_alert(app, 1031)   # 越阈，since=1031
        App._update_frozen_alert(app, 1046)   # 持续 15s → 告警
        self.assertTrue(app._frozen_alert["active"])
        app._last_tick_at = 2000.0                # 循环恢复：age 变负/很小
        App._update_frozen_alert(app, 2001)
        self.assertFalse(app._frozen_alert["active"])
        self.assertEqual(app.kinds(), ["hw_alert", "hw_recovered"])

    def test_never_sampled_keeps_quiet(self):
        app = _FakeApp()                          # _last_tick_at = 0.0（启动阶段）
        App._update_frozen_alert(app, 5000)
        App._update_frozen_alert(app, 9000)
        self.assertFalse(app._frozen_alert["active"])
        self.assertEqual(app.events, [])

    def test_disabled_when_threshold_zero(self):
        app = _FakeApp(hw_loop_stale_sec=0)
        app._last_tick_at = 1000.0
        for t in range(1000, 1200, 10):
            App._update_frozen_alert(app, t)
        self.assertFalse(app._frozen_alert["active"])
        self.assertEqual(app.events, [])

    def test_threshold_change_resets_debounce(self):
        app = _FakeApp()
        app._last_tick_at = 1000.0
        App._update_frozen_alert(app, 1031)       # 越阈，since=1031
        app.cfg.hw_loop_stale_sec = 60            # 改阈值：旧计时作废
        App._update_frozen_alert(app, 1040)       # 新阈值下 age=40 < 60：未越限
        self.assertFalse(app._frozen_alert["active"])
        App._update_frozen_alert(app, 1075)       # age=75 越限，since=1075
        App._update_frozen_alert(app, 1091)       # 越限后持续 16s：告警
        self.assertTrue(app._frozen_alert["active"])


class FrozenWatchdogThreadTests(unittest.TestCase):
    """看门狗线程契约：必须独立驱动 _update_frozen_alert，并能响应 _stop 退出。

    这是"采集循环死亡时告警仍能触发"的架构保证——状态机在循环内则同生共死。
    """

    def test_watchdog_drives_alert_and_exits_on_stop(self):
        import threading
        app = _FakeApp()
        app._last_tick_at = 1000.0
        app._stop = False
        app._frozen_interval = 0.01      # 测试缩小周期，避免等满 5s
        calls = []

        def fake_update(now):
            calls.append(now)
            app._stop = True          # 首次调用后即请求退出，保证测试确定性

        app._update_frozen_alert = fake_update
        th = threading.Thread(target=App._frozen_watchdog_loop, args=(app,),
                              daemon=True)
        th.start()
        th.join(timeout=3.0)
        self.assertFalse(th.is_alive())            # 收到 _stop 后必须退出
        self.assertGreaterEqual(len(calls), 1)     # 至少驱动了一次

    def test_watchdog_survives_alert_exceptions(self):
        import threading
        app = _FakeApp()
        app._stop = False
        app._frozen_interval = 0.01
        calls = []

        def boom(now):
            calls.append(now)
            if len(calls) >= 2:
                app._stop = True       # 第二次异常后退出，证明循环越过异常继续
            raise RuntimeError("watchdog must not die")

        app._update_frozen_alert = boom
        th = threading.Thread(target=App._frozen_watchdog_loop, args=(app,),
                              daemon=True)
        th.start()
        th.join(timeout=3.0)
        self.assertFalse(th.is_alive())
        self.assertGreaterEqual(len(calls), 2)     # 异常被吞，循环继续


if __name__ == "__main__":
    unittest.main()
