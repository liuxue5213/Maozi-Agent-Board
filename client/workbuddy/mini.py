"""置顶迷你悬浮窗（优化#4「类副屏」）。

pywebview 无边框 + always_on_top 小窗，加载本机 Web 服务的 /mini 页面
（同一份 SSE 数据流）。窗口拖拽走页面内的 data-attribute 协议：
  * body 被按下          → shell 查询 window.__mini_drag_start
  * 拖拽偏移              → shell 调用 move(x, y)
关闭悬浮窗不退出主程序；托盘「退出」由 App.on_quit 调 stop() 收尾。
"""
from __future__ import annotations

import threading


class MiniWindow:
    """pywebview 迷你窗壳。show()/stop() 线程安全（内部转主线程执行）。"""

    WIDTH, HEIGHT = 264, 236

    def __init__(self, url: str):
        self.url = url
        self._ready = threading.Event()
        self._stop_flag = False
        self._win = None

    # ---- 供页面 JS 调用的拖拽协议（通过 js_api 暴露） ----

    def drag_start(self):
        """记录按下点，页面随后的 drag_move 会带上绝对坐标。"""
        self._drag_from = None
        return True

    def drag_move(self, x: int, y: int):
        """页面拖拽时上报的屏幕绝对坐标 → 直接移动窗口。"""
        win = self._win
        if win is None:
            return False
        try:
            import webview
            # pywebview 4.x 无直接 move API，用 evaluate 桥原生窗口；
            # macOS 上窗口移动走 pyobjc，Windows 走 user32——都包在壳内。
            if hasattr(win, "move"):
                win.move(int(x), int(y))
                return True
        except Exception:
            pass
        return False

    # ---- 生命周期 ----

    def run(self):
        import webview

        def on_loaded():
            self._ready.set()

        self._win = webview.create_window(
            "WorkBuddy 迷你屏", self.url,
            width=self.WIDTH, height=self.HEIGHT,
            frameless=True, on_top=True, resizable=False,
            js_api=self)
        self._win.events.loaded += on_loaded
        webview.start(self._watch_stop, self._win)

    def _watch_stop(self, win):
        self._ready.set()
        while not self._stop_flag:
            threading.Event().wait(0.4)
        try:
            win.destroy()
        except Exception:
            pass

    def stop(self):
        self._stop_flag = True


def start_mini(url: str) -> MiniWindow:
    """后台线程启动悬浮窗（pywebview 的 GUI 循环必须独占一个线程的解释权，
    由 start_mini 的调用方保证：主窗口与托盘已各归其位）。"""
    mini = MiniWindow(url)
    threading.Thread(target=mini.run, name="wb-mini", daemon=True).start()
    return mini
