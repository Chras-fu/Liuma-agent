# coding: utf-8
# copyright by codeskyblue of openATX

import base64
import json
import sys
import subprocess
import time
from functools import partial
import tornado
import wda
import tidevice
from logzero import logger
from tornado import httpclient, locks
from tornado.ioloop import IOLoop
from weditor.web import uidumplib
from tools.config import config
from tools.freeport import FreePort
from apple.idb import idb


status_ready = "ready"
status_fatal = "fatal"


class WDADevice(object):
    def __init__(self, serial: str, wda_bundle_id: str, free_port: FreePort, lock: locks.Lock, callback):
        self._serial = serial
        self._info = None
        self._wda_bundle_id = wda_bundle_id
        self._free_port = free_port
        self._procs = []
        self._wda_proxy_port = None
        self._wda_proxy_proc = None
        self._current_ip = config.host
        self._lock = lock
        self._finished = locks.Event()
        self._stop = locks.Event()
        self._callback = partial(callback, self)
        self.get_info()

    def get_info(self):
        self._info = idb.device_info(self._serial)

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def public_port(self):
        return self._wda_proxy_port

    def __repr__(self):
        return "[{serial}:{name}-{product}]".format(
            serial=self.serial[:5] + ".." + self.serial[-2:],
            name=self._info["DeviceName"], product=self._info["MarketName"])

    def __str__(self):
        return repr(self)

    def start(self):
        """启动wda并保持运行"""
        self._stop.clear()
        IOLoop.current().spawn_callback(self.run_wda_forever)

    async def stop(self):
        """ 结束wda """
        if self._stop.is_set():
            raise RuntimeError(self, "WDADevice is already stopped")
        self._stop.set()  # no need await
        logger.debug("%s waiting for wda stopped ...", self)
        await self._finished.wait()
        logger.debug("%s wda stopped!", self)
        self._finished.clear()

    async def run_wda_forever(self):
        wda_fail_cnt = 0
        while not self._stop.is_set():
            start = time.time()
            ok = await self.run_wda()
            if not ok:
                self.destroy()
                wda_fail_cnt += 1
                if wda_fail_cnt > 3:
                    logger.error("%s Run WDA failed. -_-!", self)
                    break

                if time.time() - start < 3.0:
                    logger.error("%s WDA unable to start", self)
                    break
                logger.warning("%s wda started failed, retry after 10s", self)
                if not await self._sleep(10):
                    break
                continue

            wda_fail_cnt = 0
            logger.info("%s wda lanuched", self._info["DeviceName"])
            await self._callback(status_ready)
            await self.watch_wda_status()

        await self._callback(status_fatal)
        self.destroy()  # destroy twice to make sure no process left
        self._finished.set()  # no need await

    def destroy(self):
        logger.debug("terminate wda processes")
        for p in self._procs:
            p.terminate()
        self._procs = []

    async def _sleep(self, timeout: float):
        """ return false when sleep stopped by _stop(Event) """
        try:
            timeout_timestamp = IOLoop.current().time() + timeout
            await self._stop.wait(timeout_timestamp)  # wired usage
            return False
        except tornado.util.TimeoutError:
            return True

    async def watch_wda_status(self):
        """监控wda状态"""
        fail_cnt = 0
        last_ip = self.device_ip
        while not self._stop.is_set():
            if await self.wda_status():
                if fail_cnt != 0:
                    logger.info("wda ping recovered")
                    fail_cnt = 0
                if last_ip != self.device_ip:
                    last_ip = self.device_ip
                    await self._callback(status_ready)
                await self._sleep(60)
            else:
                fail_cnt += 1
                logger.warning("%s wda ping error: %d", self, fail_cnt)
                if fail_cnt > 3:
                    logger.warning("ping wda fail too many times, restart wda")
                    break
                await self._sleep(10)

        self.destroy()

    @property
    def device_ip(self):
        """ get current device ip """
        if not self.__wda_info:
            return None
        try:
            return self.__wda_info['value']['ios']['ip']
        except IndexError:
            return None

    async def run_wda(self) -> bool:
        """ 启动wda """
        if self._procs:
            self.destroy()

        async with self._lock:
            self._wda_port = self._free_port.get()
            self._mjpeg_port = self._free_port.get()
            # 使用 tidevice 命令启动 wda
            tidevice_cmd = ['tidevice', '-u', self.serial, 'xctest', '-B', self._wda_bundle_id]
            self.run_background(tidevice_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            # 代理接口
            self.run_background(["tidevice", '-u',
                                 self.serial, 'relay', str(self._wda_port), "8100"], silent=True)
            self.run_background(["tidevice", '-u',
                                 self.serial, 'relay',str(self._mjpeg_port), "9100"], silent=True)
            # 转发服务
            self.restart_wda_proxy()
            return await self.wait_until_ready()

    def run_background(self, *args, **kwargs):
        if kwargs.pop("silent", False):
            kwargs['stdout'] = subprocess.DEVNULL
            kwargs['stderr'] = subprocess.DEVNULL
        # logger.debug("exec: %s", subprocess.list2cmdline(args[0]))
        p = subprocess.Popen(*args, **kwargs)
        self._procs.append(p)

    def restart_wda_proxy(self):
        if self._wda_proxy_proc:
            self._wda_proxy_proc.terminate()
        self._wda_proxy_port = self._free_port.get()
        logger.debug("restart wdaproxy with port: %d", self._wda_proxy_port)
        self._wda_proxy_proc = subprocess.Popen([
            sys.executable, "-u", "apple/proxy_wda.py",
            "-p", str(self._wda_proxy_port),
            "--wda-url", "http://localhost:{}".format(self._wda_port),
            "--mjpeg-url", "http://localhost:{}".format(self._mjpeg_port)],
            stdout=subprocess.DEVNULL)  # yapf: disable

    async def wait_until_ready(self, timeout: float = 60.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stop.is_set():
            quited = any([p.poll() is not None for p in self._procs])
            if quited:
                logger.warning("%s process quit %s", self, [(p.pid, p.poll()) for p in self._procs])
                return False
            if await self.wda_status():
                return True
            await self._sleep(1)
        return False

    async def restart_wda(self):
        self.destroy()
        return await self.run_wda()

    @property
    def wda_device_url(self):
        return "http://localhost:{}".format(self._wda_port)

    @property
    def addrs(self):
        def port2addr(port):
            return self._current_ip + ":" + str(port)

        return {
            "url": "http://" + port2addr(config.apple_port),
            "wdaUrl": port2addr(self.public_port)
        }

    async def properties(self):
        return {
                "system": "apple",
                "brand": "Apple",
                "version": self._info["ProductVersion"],
                "model": self._info["MarketName"],
                "name": self._info["DeviceName"],
                "size": await self.wda_screen_size()
            }

    async def wda_status(self):
        try:
            request = httpclient.HTTPRequest(self.wda_device_url + "/status",
                                             connect_timeout=3, request_timeout=15)
            client = httpclient.AsyncHTTPClient()
            resp = await client.fetch(request)
            info = json.loads(resp.body)
            self.__wda_info = info
            return info
        except httpclient.HTTPError as e:
            logger.debug("%s request wda/status error: %s", self, e)
            return None
        except (ConnectionResetError, ConnectionRefusedError):
            logger.debug("%s waiting for wda", self)
            return None
        except Exception as e:
            logger.warning("%s ping wda unknown error: %s %s", self, type(e),e)
            return None

    async def wda_screenshot_ok(self):
        try:
            request = httpclient.HTTPRequest(self.wda_device_url + "/screenshot",
                                             connect_timeout=3, request_timeout=15)
            client = httpclient.AsyncHTTPClient()
            resp = await client.fetch(request)
            data = json.loads(resp.body)
            raw_png_data = base64.b64decode(data['value'])
            png_header = b"\x89PNG\r\n\x1a\n"
            if not raw_png_data.startswith(png_header):
                return False
            return True
        except Exception as e:
            logger.warning("%s wda screenshot error: %s", self, e)
            return False

    async def wda_screen_size(self):
        try:
            await self.wda_home()
            request = httpclient.HTTPRequest(self.wda_device_url + "/window/size",
                                             connect_timeout=3, request_timeout=15)
            client = httpclient.AsyncHTTPClient()
            resp = await client.fetch(request)
            data = json.loads(resp.body)
            return f'{data["value"]["width"]}*{data["value"]["height"]}'
        except Exception as e:
            logger.warning("%s get screen size error: %s", self, e)
            return "unknown"

    async def wda_home(self):
        try:
            request = httpclient.HTTPRequest(self.wda_device_url + "/wda/homescreen", method="POST", body=b'',
                                             connect_timeout=3, request_timeout=15)
            client = httpclient.AsyncHTTPClient()
            await client.fetch(request)
        except Exception as e:
            logger.warning("%s back home error: %s", self, e)

    async def wda_session_ok(self):
        info = await self.wda_status()
        if not info:
            return False
        return True

    async def is_wda_alive(self):
        logger.debug("%s check /status", self)
        if not await self.wda_session_ok():
            return False
        logger.debug("%s check /screenshot", self)
        if not await self.wda_screenshot_ok():
            return False
        return True

    async def wda_healthcheck(self):
        client = httpclient.AsyncHTTPClient()
        if not await self.is_wda_alive():
            logger.warning("%s check failed", self)
            await self._callback(status_fatal)
            if not await self.restart_wda():
                logger.warning("%s wda recover in healthcheck failed", self)
                return
        else:
            logger.debug("%s all check passed", self)
        await client.fetch(self.wda_device_url + "/wda/healthcheck")
        await self._callback(status_ready)

    def get_screenshot(self):
        try:
            client = wda.Client(self.wda_device_url)
            screenshot = client.screenshot(format='pillow')
            return screenshot
        except:
            return tidevice.Device(self._serial).screenshot()

    def dump_hierarchy(self):
        client = wda.Client(self.wda_device_url)
        scale = client.scale
        source = uidumplib.get_ios_hierarchy(client, scale),
        size = client.window_size()
        return {
            "jsonHierarchy": source,
            "windowSize": size,
        }
