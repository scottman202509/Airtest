import os
import threading
import time
import traceback
import uuid

from selenium.common import WebDriverException
import pychrome

from airtest.core.device import Device
from airtest.core.ios.constant import CAP_METHOD
from airtest.core.settings import Settings as ST
from airtest import aircv
from airtest.core.cv import Template,try_log_screen

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains

import httpx
import logging
logger = logging.getLogger(__name__)
class Chrome(Device):
    def __init__(self, addr="localhost:9222", cap_method=CAP_METHOD.MJPEG, mjpeg_port=None, udid=None, name=None,
                 serialno=None, wda_bundle_id=None):
        super().__init__()
        self.addr = addr
        self._udid = addr
        if not self.addr.startswith("http"):
            url = "http://" + self.addr
        else:
            url = self.addr
        self.browser = pychrome.Browser(url=url)
        tabs = self.browser.list_tab()
        if not tabs:
            raise RuntimeError("No tabs found in Chrome remote debugging")
        self.tab = tabs[0]
        self.tab.start()  # 开启事件监听
        logger.debug(f'开始连接 {self.addr} {self.browser.version(timeout=3)}')
        options = Options()
        options.debugger_address = self.addr
        # options.add_argument("--window-size=500,962")
        # options.add_argument("--force-device-scale-factor=1")
        self.driver = webdriver.Chrome(options=options,keep_alive=True)
        self.actions = ActionChains(self.driver)
        self._size = self.window_size()
        self.display_size = {}
        self.touch_factor = self.driver.execute_script("return window.devicePixelRatio;")

    def get_js_heap(self):
        """获取 JS Heap 使用情况和 DOM 节点数量"""
        metrics = self.driver.execute_cdp_cmd("Performance.getMetrics", {})
        dom = self.driver.execute_cdp_cmd("Memory.getDOMCounters", {})
        js_heap_used = next((m['value'] for m in metrics['metrics'] if m['name'] == "JSHeapUsedSize"), None)
        js_heap_total = next((m['value'] for m in metrics['metrics'] if m['name'] == "JSHeapTotalSize"), None)
        return {
            "JSHeapUsedSize": js_heap_used,
            "JSHeapTotalSize": js_heap_total,
            "DOMNodes": dom.get("nodes"),
            "Documents": dom.get("documents"),
            "EventListeners": dom.get("jsEventListeners")
        }

    def take_heap_snapshot(self, save_path="heap.heapsnapshot"):
        """完整 Heap Snapshot 并保存到文件"""

        snapshot_data = {}

        def on_chunk(chunk, **kwargs):
            snapshot_data[str(uuid.uuid4())] = chunk

        # 绑定事件（注意事件名是完整的）
        self.tab.set_listener(
            "HeapProfiler.addHeapSnapshotChunk",
            on_chunk
        )
        self.tab.HeapProfiler.enable()
        self.tab.HeapProfiler.takeHeapSnapshot(reportProgress=False)
        time_start = time.time()
        size_total = 0
        while True:
            time_current = time.time()
            if time_current - time_start > 600:
                break
            time_start = time_current
            time.sleep(2)
            size_current = len(snapshot_data)
            if size_current == size_total:
                break
            size_total = size_current
        # 合并保存
        with open(save_path, "w") as f:
            f.write("".join(snapshot_data.values()))

    def window_size(self):
        """
        Returns:
            Window size (width, height).
        """
        size = self.driver.execute_script("""
            return {
                width: window.innerWidth,
                height: window.innerHeight
            };
        """)
        return size

    @property
    def display_info(self):
        if not self.display_size:
            self._display_info()
        return self.display_size

    def _display_info(self):
        # Function window_size() return UIKit size, while screenshot() image size is Native Resolution.

        snapshot = self.snapshot()
        height,width  = snapshot.shape[:2]
        self.display_size["width"] = width
        self.display_size["height"] = height

    def snapshot(self, filename=None, quality=10, max_size=None):

        data = self.driver.get_screenshot_as_png()
        # Output cv2 object.
        try:
            screen = aircv.utils.string_2_img(data)
        except:
            # May be black/locked screen or other reason, print exc for debugging.
            traceback.print_exc()
            return None

        # Save as file if needed.
        if filename:
            aircv.imwrite(filename, screen, quality, max_size=max_size)

        return screen

    def exists(self, v,screen = None,threshold = None):
        """
        Check whether given target exists on device screen
        :param v: target to be checked
        :param threshold: default is None
        :return: False if target is not found, otherwise returns the coordinates of the target
        """
        if screen is None:
            screen = self.snapshot(filename=None, quality=ST.SNAPSHOT_QUALITY)
        if screen is None:
            return False
        if threshold:
            v.threshold = threshold
        match_pos = v.match_in(screen)
        return match_pos

    def wait(self, v, timeout=ST.FIND_TIMEOUT, threshold=None, interval=0.5, intervalfunc=None):

        start_time = time.time()
        while True:
            screen = self.snapshot(filename=None, quality=ST.SNAPSHOT_QUALITY)
            if screen is not None:
                if threshold:
                    v.threshold = threshold
                match_pos = v.match_in(screen)
                if match_pos:
                    return match_pos
            if intervalfunc is not None:
                intervalfunc()
            # 超时则raise，未超时则进行下次循环:
            if (time.time() - start_time) > timeout:
                break
            else:
                time.sleep(interval)
        return None

    def find_all(self, v,screen = None):
        if screen is None:
            screen = self.snapshot(quality=ST.SNAPSHOT_QUALITY)
        if screen is None:
            return []
        return v.match_all_in(screen)
    def swipe(self, fpos, tpos, duration=0, delay=None, *args, **kwargs):
        """
        """
        fx, fy = fpos
        tx, ty = tpos
        if not (fx < 1 and fy < 1):
            fx, fy = int(fx / self.touch_factor), int(fy / self.touch_factor)
        if not (tx < 1 and ty < 1):
            tx, ty = int(tx / self.touch_factor), int(ty / self.touch_factor)
        self.driver.execute_script("""
                const fx = arguments[0];
                const fy = arguments[1];
                const tx = arguments[2];
                const ty = arguments[3];

                function fire(type, x, y) {
                    const el = document.elementFromPoint(x, y);
                    if (!el) return;
                    el.dispatchEvent(new MouseEvent(type, {
                        bubbles: true,
                        cancelable: true,
                        clientX: x,
                        clientY: y,
                        buttons: 1
                    }));
                }

                fire('mousedown', fx, fy);
                fire('mousemove', tx, ty);
                fire('mouseup', tx, ty);
            """, fx, fy, tx, ty)
    def get_current_resolution(self):
        w, h = self.display_info["width"], self.display_info["height"]
        return w, h
    def swipe_image(self, v1, v2=None, vector=None, screen = None,**kwargs):
        if isinstance(v1, Template):
            pos1 = self.exists(v1, screen = screen)
        else:
            pos1 = v1
        if not pos1:
            return None, None
        if v2:
            if isinstance(v2, Template):
                pos2 = self.exists(v2, screen = screen)
            else:
                pos2 = v2
            if not pos2:
                return None, None
        elif vector:
            if vector[0] <= 1 and vector[1] <= 1:
                w, h = self.get_current_resolution()
                vector = (int(vector[0] * w), int(vector[1] * h))
            pos2 = (pos1[0] + vector[0], pos1[1] + vector[1])
        else:
            return None, None
        pos1, pos2 = self.swipe(pos1, pos2, **kwargs) or (pos1, pos2)
        return pos1, pos2

    def double_click(self, pos):
        x, y = pos
        if not (x < 1 and y < 1):
            x, y = int(x / self.touch_factor), int(y / self.touch_factor)
        self.actions.move_by_offset(x, y).double_click().perform()

    def double_click_image(self, v, screen = None):
        if isinstance(v, Template):
            pos = self.exists(v, screen = screen)
        else:
            pos = v
        if not pos:
            return None
        pos = self.double_click(pos) or pos
        return pos
    def touch(self, pos, duration=0.01,**kwargs):
        if not pos:
            return
        x, y = pos
        if not (x < 1 and y < 1):
            x, y = int(x / self.touch_factor), int(y / self.touch_factor)
        self.driver.execute_script("""
            const x = arguments[0];
            const y = arguments[1];
            const el = document.elementFromPoint(x, y);
            ['mousedown','mouseup','click'].forEach(type => {
                el.dispatchEvent(new MouseEvent(type, {
                    bubbles: true,
                    clientX: x,
                    clientY: y
                }));
            });
        """, x, y)


    def touch_image(self, v, times=1, screen = None,**kwargs):
        if isinstance(v, Template):
            pos = self.exists(v, screen = screen)
        else:
            pos = v
        if not pos:
            return None
        for _ in range(times):
            # If pos is a relative coordinate, return the converted click coordinates.
            # iOS may all use vertical screen coordinates, so coordinates will not be returned.
            pos = self.touch(pos, **kwargs) or pos
            time.sleep(0.05)
        return pos

    def snapshot_image(self, filename=None, msg="", quality=None, max_size=None):
        if not quality:
            quality = ST.SNAPSHOT_QUALITY
        if not max_size and ST.IMAGE_MAXSIZE:
            max_size = ST.IMAGE_MAXSIZE
        if filename:
            if not os.path.isabs(filename):
                logdir = ST.LOG_DIR or "."
                filename = os.path.join(logdir, filename)
            screen = self.snapshot(filename, quality=quality, max_size=max_size)
            return try_log_screen(screen, quality=quality, max_size=max_size)
        else:
            return try_log_screen(quality=quality, max_size=max_size)

    def is_ready(self):
        try:
            # 最轻量、最快的调用
            _ = self.driver.current_url
            return True
        except WebDriverException:
            return False

    @property
    def uuid(self):
        return self._udid or self.addr
