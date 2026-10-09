"""系统托盘常驻：关闭窗口 ≠ 退出，程序退到通知区域继续跑。

设计要点
--------
1. **逻辑与 GUI 解耦**：菜单项定义、状态机、退出流程都在`TrayController` 里，
   不碰 pystray 也不碰窗口库。这样整套菜单/退出逻辑可以脱离GUI 单测——
   实机托盘在无头环境里既起不来也不可断言，是最难回归的部分。
2. **窗口后端可替换**：优先用 pywebview 开原生窗口（这样"关闭按钮"才归我们管，
   能拦下来改成隐藏）；没装或起不来时降级为「用系统浏览器打开」，
   托盘本身照常工作。
3. **退出必须彻底**：停采集循环 → 关串口 → 停 Web 服务（释放端口）→ 停托盘图标，
   顺序执行并各自兜底，任何一步失败都不能阻断后续清理。

平台差异（托盘后端由 pystray 内部处理）
  · Windows：通知区域图标（pywin32，无需额外运行时）
  · macOS  ：菜单栏图标（pyobjc）
  · Linux  ：需 libappindicator/GTK，装不上时pystray 抛 ImportError
"""
from __future__ import annotations

import sys
import threading
import webbrowser

APP_NAME = "WorkBuddy 桌面状态屏"
APP_ID = "maozi-diy-workbuddy"

_ABOUT_TEXT = (
    f"{APP_NAME}\n"
    "ESP32-S3 触控状态屏上位机客户端\n\n"
    "· 本地设置页与设备推送\n"
    "· AI 会话状态监控\n"
    "· 关闭窗口后驻留托盘继续运行\n\n"
    "设置页：http://127.0.0.1:{port}"
)


def tray_available() -> bool:
    """托盘依赖是否就绪。不可用时上层应退回纯控制台常驻。"""
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except Exception:
        return False


class TrayController:
    """托盘行为控制器（纯逻辑，无 GUI 依赖）。

    宿主需提供两个回调：
      show_window()  —— 显示/恢复主界面
      quit_app()     —— 彻底退出（停循环、释放端口）
    """

    def __init__(self, on_show, on_quit, port: int = 8765,
                 on_refresh=None, on_autostart=None,
                 on_pause=None, on_mute=None, on_page=None):
        self.on_show = on_show
        self.on_quit = on_quit
        self.on_refresh = on_refresh
        self.on_autostart = on_autostart
        # 优化#16：托盘右键增强——暂停采集 / 静音 / 切设备页
        self.on_pause = on_pause
        self.on_mute = on_mute
        self.on_page = on_page
        self.page_count = 0            # 设备页数（>0 才显示切页子菜单）
        self.pause_getter = None       # lambda -> bool，菜单勾选态
        self.port = port
        self.tip_shown = False          # 首次最小化提示只弹一次
        self.visible = True
        self.quitting = False
        self._icon = None

    # ---- 菜单动作（供 GUI 层绑定，也供单测直接调用）----

    def show_window(self):
        """左键单击 / 「显示主界面」：恢复窗口并置前。

        无论是已最小化还是隐藏到托盘，都走同一条恢复路径——
        pystray 只给"单击"事件，窗口当前状态由我们维护。
        """
        self.visible = True
        try:
            self.on_show()
        except Exception:
            pass
        return True

    def hide_window(self):
        """点关闭按钮：只隐藏，不退出。"""
        self.visible = False
        return True

    def toggle_window(self):
        return self.hide_window() if self.visible else self.show_window()

    def refresh(self):
        """「刷新数据」：主动拉一次设备状态并重新渲染页面。"""
        if self.on_refresh:
            try:
                self.on_refresh()
                return True
            except Exception:
                return False
        # 无回调时退化为重开设置页，让页面自己重新取数
        try:
            import webbrowser
            webbrowser.open(self.settings_url())
            return True
        except Exception:
            return False

    def toggle_pause(self):
        """「暂停/恢复采集」：回调返回新的暂停态；无回调时报不支持。"""
        if not self.on_pause:
            return False
        return bool(self.on_pause())

    def paused_state(self) -> bool:
        return bool(self.pause_getter()) if self.pause_getter else False

    def toggle_mute(self):
        if self.on_mute:
            self.on_mute()
            return True
        return False

    def request_page(self, page_1based: int) -> bool:
        if not self.on_page:
            return False
        return bool(self.on_page(page_1based))

    def toggle_autostart(self):
        """「开机自启」开关，返回切换后的状态；失败返回 None 表示不支持。"""
        if self.on_autostart is None:
            return None
        try:
            return bool(self.on_autostart())
        except Exception:
            return None

    def autostart_enabled(self) -> bool:
        try:
            from . import autostart
            return autostart.status()
        except Exception:
            return False

    def about(self):
        return _ABOUT_TEXT.format(port=self.port)

    def show_tip_once(self):
        """首次最小化时提示"程序仍在运行"，避免误以为已关闭。"""
        if self.tip_shown:
            return False
        self.tip_shown = True
        return True

    def quit(self):
        """「退出」：真正结束进程。只允许执行一次，避免重复释放资源。"""
        if self.quitting:
            return False
        self.quitting = True
        try:
            self.on_quit()
        except Exception:
            pass
        return True

    def settings_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def notify(self, title: str, text: str) -> bool:
        """托盘气泡通知（优化#10）：任务完成/出错/等你时主动提醒。

        通知不是关键路径：图标未起/平台不支持/发送失败一律静默返回 False。
        """
        try:
            if self._icon is None:
                return False
            if sys.platform == "darwin":
                self._icon.notify(text, title)
                return True
            if self._icon.has_message:
                self._icon.message = f"{title} {text}".strip()
                return True
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# GUI 层：图标 + 菜单绑定。仅在真正需要托盘时导入 pystray。
# ---------------------------------------------------------------------------

def _make_image():
    """程序化生成托盘图标（◐ 造型），免去额外图片资源文件。"""
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 深色圆底，浅色半圆，呼应设备屏幕的"日夜"意象
    d.ellipse((4, 4, size - 4, size - 4), fill=(14, 58, 56, 255))
    d.pieslice((12, 12, size - 12, size - 12), 90, 270, fill=(167, 139, 250, 255))
    d.ellipse((12, 12, size - 12, size - 12), outline=(110, 226, 220, 255), width=3)
    return img


def _main_menu_items(controller: TrayController, Menu, MenuItem, _show, _quit):
    """托盘主菜单（优化#16 增强）：暂停采集 / 静音 / 切设备页。

    run_tray 与 build_tray 共用，避免两份菜单漂移（历史上就漂移过）。
    """
    def _toggle_pause(*_):
        paused = controller.toggle_pause()
        if controller._icon is not None:
            try:
                controller._icon.title = (
                    f"{APP_NAME}（已暂停采集）" if paused else APP_NAME)
            except Exception:
                pass

    def _page(n):
        def handler(*_):
            if not controller.request_page(n):
                _notify_text(controller, "切换页面失败", "设备未连接或页面号无效")
        return handler

    items = [
        MenuItem("显示主界面", _show, default=True),
        MenuItem("刷新数据", lambda *_: controller.refresh()),
        MenuItem("暂停采集", _toggle_pause,
                 checked=lambda _: controller.paused_state(),
                 enabled=lambda _: controller.on_pause is not None),
        MenuItem("静音", lambda *_: controller.toggle_mute(),
                 enabled=lambda _: controller.on_mute is not None),
        Menu.SEPARATOR,
    ]
    if controller.page_count > 0 and controller.on_page is not None:
        items.append(Menu(
            "切换设备页",
            *[MenuItem(f"第 {n} 页", _page(n))
              for n in range(1, controller.page_count + 1)]))
    items += [
        MenuItem("开机自启", lambda *_: None,
                 checked=lambda _: controller.autostart_enabled(),
                 enabled=lambda _: controller.on_autostart is not None),
        Menu.SEPARATOR,
        MenuItem("关于", lambda *_: _notify_about(controller)),
        MenuItem("退出", _quit),
    ]
    return items


def _notify_text(controller: TrayController, title: str, text: str):
    """轻量气泡（菜单动作反馈）。失败静默——菜单动作不该弹异常。"""
    try:
        if controller._icon is not None:
            controller._icon.notify(text, title)
            return
    except Exception:
        pass
    print(f"{title}：{text}")


def run_tray(app, controller: TrayController, icon=None):
    """启动托盘图标并阻塞（pystray 需要在主线程跑）。"""
    import pystray
    from pystray import Menu, MenuItem

    image = icon or _make_image()

    def _show(*_):
        controller.show_window()

    def _quit(*_):
        # 菜单线程里直接退出会留残影，先停图标再走清理
        try:
            if controller._icon is not None:
                controller._icon.stop()
        except Exception:
            pass
        controller.quit()

    menu = Menu(
        *_main_menu_items(controller, Menu, MenuItem, _show, _quit))
    tray = pystray.Icon("workbuddy", image, APP_NAME, menu)
    controller._icon = tray
    tray.run()
    return controller


def build_tray(controller: TrayController, icon=None):
    """构造但不运行托盘图标（供 run_detached 使用）。"""
    import pystray
    from pystray import Menu, MenuItem

    image = icon or _make_image()

    def _show(*_):
        controller.show_window()

    def _quit(*_):
        try:
            if controller._icon is not None:
                controller._icon.stop()
        except Exception:
            pass
        controller.quit()

    menu = Menu(
        *_main_menu_items(controller, Menu, MenuItem, _show, _quit))
    tray = pystray.Icon("workbuddy", image, APP_NAME, menu)
    controller._icon = tray
    return tray


def _notify_about(controller: TrayController):
    """「关于」：优先用原生气泡，失败则退回标准输出。"""
    text = controller.about()
    try:
        if controller._icon is not None and sys.platform == "darwin":
            import pystray
            controller._icon.notify("关于", text)
            return True
        if controller._icon is not None and controller._icon.has_message:
            controller._icon.message = text
            return True
    except Exception:
        pass
    print(text)
    return False


def open_window_blocking(url: str, on_closed=None, title: str = APP_NAME,
                         width: int = 1280, height: int = 900):
    """用 pywebview 开原生窗口并进入事件循环（阻塞）。

    pywebview 缺失时返回 False，由上层退回浏览器模式。
    `closing` 回调把"点关闭按钮"从"退出"改写成"隐藏到托盘"。
    """
    try:
        import webview
    except Exception:
        return False

    state = {"keep": True}

    def on_closing():
        # 返回 False 可拦截关闭；但我们仍要让 pywebview 隐藏窗口，
        # 所以先隐藏再阻止销毁，窗口对象留在托盘后面。
        try:
            state["win"].hide()
        except Exception:
            pass
        if on_closed:
            try:
                on_closed()
            except Exception:
                pass
        return False

    win = webview.create_window(title, url, width=width, height=height,
                                min_size=(900, 640), on_top=False)
    state["win"] = win
    if on_closed is not None:
        try:
            win.events.closing += on_closing
        except Exception:
            pass
    webview.start()
    return True


# ---------------------------------------------------------------------------
# 运行模式编排：采集循环 / 原生窗口 / 托盘图标 三者分处不同线程
# ---------------------------------------------------------------------------

def run_with_tray(app, cfg, use_window: bool = True):
    """托盘常驻模式主入口。

    线程分工（GUI 库的线程约束决定了不能三者都放主线程）：
      · 主线程     —— 原生窗口（pywebview）。无窗口时让给托盘图标。
      · 后台线程 1 —— 采集主循环 `app.run()`（阻塞式）
      · 后台线程 2 —— 托盘图标 pystray `run_detached()`

    退出时按「停图标 → 停采集 → 关串口 → 关 Web 服务」顺序回收，
    保证 8765 端口与串口句柄都被释放。
    """
    import threading
    import time
    import urllib.request

    port = cfg.web_port
    win_holder = {}

    def wait_ready(timeout=8.0):
        """等 Web 服务起来再建窗口，否则会先显示一个连接错误页。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/status", timeout=0.5):
                    return True
            except Exception:
                time.sleep(0.2)
        return False

    def _quit():
        try:
            if ctrl._icon is not None:
                ctrl._icon.stop()
        except Exception:
            pass
        try:
            win_holder.get("win") and win_holder["win"].destroy()
        except Exception:
            pass
        app.stop()

    def _show():
        win = win_holder.get("win")
        if win is None:
            # 降级模式没有窗口，用系统浏览器打开
            try:
                webbrowser.open(ctrl.settings_url())
            except Exception:
                pass
            return
        try:
            win.show()          # 兼容最小化与隐藏两种状态
        except Exception:
            pass
        for attempt in ("restore", "show"):
            try:
                getattr(win, attempt)()
            except Exception:
                continue
        try:
            import webview
            webview.set_window_focus(win)
        except Exception:
            pass

    def _refresh():
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/status", timeout=1.0) as resp:
                resp.read()
        except Exception:
            pass
        # 让页面立即重新取数
        try:
            win = win_holder.get("win")
            if win is not None:
                win.evaluate_js("window.dispatchEvent(new Event('focus'))")
        except Exception:
            pass
        if win_holder.get("win") is None:
            webbrowser.open(ctrl.settings_url())

    def _toggle_autostart():
        from . import autostart
        current = autostart.status()
        autostart.set_enabled(not current)
        return autostart.status()

    ctrl = TrayController(on_show=_show, on_quit=_quit, port=port,
                          on_refresh=_refresh, on_autostart=_toggle_autostart,
                          on_pause=lambda: app.set_paused(not app.paused),
                          on_mute=lambda: app.control.toggle_mute(),
                          on_page=lambda n: app.set_device_page(n))
    ctrl.pause_getter = lambda: app.paused
    ctrl.page_count = len(app.profile.get("pages", []))
    app.on_quit = _quit
    # 优化#10：任务跨入 完成/出错/等你 状态时，托盘发系统通知（不打扰采集循环）
    _NOTICE_TEXT = {"DONE": "已完成", "ERROR": "出错，请查看",
                    "WAIT_INPUT": "等你回复", "ASK": "等你确认"}
    app.on_ai_notice = lambda name, state: ctrl.notify(
        name, _NOTICE_TEXT.get(state, state))

    # 采集循环先跑起来，窗口与托盘随后就位
    worker = threading.Thread(target=app.run, kwargs={"duration": 0.0},
                              name="wb-collect", daemon=True)
    worker.start()
    wait_ready()

    try:
        import webview  # noqa: F401
        has_webview = True
    except Exception:
        has_webview = False

    # 托盘图标常驻：Windows 通知区域 / macOS 菜单栏
    tray_ok = False
    if tray_available():
        try:
            icon = build_tray(ctrl)
            icon.run_detached()   # 后台线程，不与窗口争主线程
            tray_ok = True
        except Exception as exc:
            print(f"托盘图标启动失败（{exc}），以控制台模式继续运行")
    if not tray_ok and not has_webview:
        print("未检测到托盘/窗口依赖，退回控制台常驻模式")

    if has_webview and use_window:
        def _on_closed():
            ctrl.hide_window()
            if ctrl.show_tip_once():
                print("提示：窗口已关闭，程序仍在托盘运行；"
                      "左键单击托盘图标可重新打开，右键菜单可退出。")

        url = ctrl.settings_url()
        try:
            # pywebview 必须在主线程启动
            open_window_blocking(url, on_closed=_on_closed)
        except Exception as exc:
            print(f"窗口启动失败（{exc}），改用浏览器打开")
            webbrowser.open(url)
    else:
        if use_window:
            webbrowser.open(ctrl.settings_url())

    # 没有窗口时主线程阻塞在托盘上；退出会让 app.stop() 把循环停掉
    if not has_webview or not use_window:
        try:
            while not ctrl.quitting:
                time.sleep(0.4)
        except KeyboardInterrupt:
            pass
        finally:
            app.stop()
    worker.join(timeout=3.0)
    return 0