# coding: utf-8
# copyright by codeskyblue of openATX

from __future__ import print_function

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import requests
import tornado.web
from logzero import logger
from tornado import locks
from tornado.concurrent import run_on_executor
from tornado.ioloop import IOLoop
from tornado.log import enable_pretty_logging

from tools import heartbeat
from apple import device_apple
from apple.idb import idb
from tools.freeport import FreePort
from tools.config import config

HBC_IOS = heartbeat.HeartbeatConnection()
DEVICE_IOS = dict()
FREE_PORT = FreePort("apple")


class CorsMixin(object):
    CORS_ORIGIN = '*'
    CORS_METHODS = 'GET,POST,OPTIONS'
    CORS_CREDENTIALS = True
    CORS_HEADERS = "x-requested-with,authorization"

    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", self.CORS_ORIGIN)
        self.set_header("Access-Control-Allow-Headers", self.CORS_HEADERS)
        self.set_header('Access-Control-Allow-Methods', self.CORS_METHODS)


class AppInstallHandler(CorsMixin, tornado.web.RequestHandler):
    """安装应用"""
    _install_executor = ThreadPoolExecutor(4)
    _download_executor = ThreadPoolExecutor(1)

    def cache_filepath(self, text: str):
        m = hashlib.md5()
        m.update(text.encode('utf-8'))
        return "cache-" + m.hexdigest()

    @run_on_executor(executor="_download_executor")
    def cache_download(self, url: str):
        file_name = self.cache_filepath(url)
        file_dir = "tmp/apple/"
        if not os.path.exists(file_dir):
            os.makedirs(file_dir)
        file_path = file_dir + file_name + ".ipa"

        if os.path.exists(file_path):
            return file_path
        logger.debug("Download %s to %s", url, file_path)

        r = requests.get(url, stream=True)
        r.raise_for_status()

        with open(file_path, "wb") as tfile:
            content_length = int(r.headers.get("content-length", 0))
            if content_length:
                for chunk in r.iter_content(chunk_size=40960):
                    tfile.write(chunk)
            else:
                shutil.copyfileobj(r.raw, tfile)

        return file_path

    @run_on_executor(executor='_install_executor')
    def app_install(self, serial: str, ipa_path: str):
        p = subprocess.Popen(
            ["tidevice", "-u", serial, "install", ipa_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        output = ""
        for line in p.stdout:
            line = line.decode('utf-8')
            logger.debug("%s -- %s", serial[:7], line.strip())
            output += line
        p.wait()
        success = "Complete" in output
        if not success:
            return {"status": 1000, "message": "安装失败:\n%s" % output.split("\n")[-2]}
        return {"status": 0, "message": "安装成功"}

    async def post(self):
        body = json.loads(self.request.body.decode())
        serial = body["serial"]
        url = body["url"]
        try:
            ipa_path = await self.cache_download(url)
            ret = await self.app_install(serial, ipa_path)
            self.write(ret)
        except Exception as e:
            self.write({"status": 1000, "message": "安装错误:\n%s" % str(e)})


class AppUnInstallHandler(CorsMixin, tornado.web.RequestHandler):
    """卸载应用"""
    _uninstall_executor = ThreadPoolExecutor(4)

    @run_on_executor(executor='_uninstall_executor')
    def app_uninstall(self, serial: str, package_name: str):
        p = subprocess.Popen(
            ["tidevice", "-u", serial, "uninstall", package_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        output = ""
        for line in p.stdout:
            line = line.decode('utf-8')
            logger.debug("%s -- %s", serial[:7], line.strip())
            output += line
        success = "Complete" in output

        if not success:
            return {"status": 1000, "message": "卸载失败"}
        return {"status": 0, "message": "卸载成功"}

    async def post(self):
        body = json.loads(self.request.body.decode())
        serial = body["serial"]
        package_name = body["packageName"]
        try:
            ret = await self.app_uninstall(serial, package_name)
            self.write(ret)
        except Exception as e:
            self.write({"status": 1000, "message": "卸载错误:\n%s" % str(e)})


class DeviceScreenshotHandler(CorsMixin, tornado.web.RequestHandler):
    """ 设备截图 """

    async def get(self):
        serial = self.get_argument("serial")
        assert serial
        if serial not in DEVICE_IOS:
            return
        device = DEVICE_IOS[serial]
        try:
            buffer = io.BytesIO()
            device.get_screenshot().convert("RGB").save(buffer, format='JPEG')
            b64data = base64.b64encode(buffer.getvalue())
            res = {
                "type": "jpeg",
                "data": b64data.decode('utf-8'),
            }
            self.write({"status": 0, "message": "获取截图成功", "data": res})
        except EnvironmentError as e:
            self.write({"status": 1000, "message": "获取截图失败: %s" % str(e)})
        except RuntimeError as e:
            self.write({"status": 1000, "message": "获取截图错误: %s" % traceback.format_exc()})


class DeviceHierarchyHandler(CorsMixin, tornado.web.RequestHandler):
    """ 设备控件 """

    async def get(self):
        serial = self.get_argument("serial")
        assert serial
        if serial not in DEVICE_IOS:
            return
        device = DEVICE_IOS[serial]
        try:
            hierachy = device.dump_hierarchy()
            self.write({"status": 0, "message": "获取控件成功", "data": hierachy})
        except Exception as e:
            self.write({"status": 1000, "message": "获取控件失败: %s" % str(e)})


class ColdingHandler(CorsMixin, tornado.web.RequestHandler):
    """ 设备清理 """
    async def post(self):
        serial = self.get_argument('serial')
        assert serial
        logger.info("Receive colding request for %s", serial)
        if serial not in DEVICE_IOS:
            return
        device = DEVICE_IOS[serial]
        device.restart_wda_proxy()
        await device.wda_healthcheck()
        self.write({"status": 0, "message": "冷却成功"})


def make_app():
    setting = {'debug': False}
    return tornado.web.Application([
        (r"/app/install", AppInstallHandler),
        (r"/app/uninstall", AppUnInstallHandler),
        (r"/device/screenshot", DeviceScreenshotHandler),
        (r"/device/hierarchy", DeviceHierarchyHandler),
        (r"/cold", ColdingHandler),
    ], **setting)


async def device_watch():
    """监听苹果设备"""
    async def _device_callback(d: device_apple.WDADevice, status: str):
        """设备状态监听回调"""
        if status == device_apple.status_ready:
            await HBC_IOS.device_update({
                "command": "init",
                "serial": d.serial,
                "agent": d.addrs,
                "properties": await d.properties()
            })
        elif status == device_apple.status_fatal:
            await HBC_IOS.device_update({
                "command": "delete",
                "serial": d.serial
            })
        else:
            logger.error("Unknown status: %s", status)

    lock = locks.Lock()  # WDA launch one by one
    wda_bundle_id = "*%s*" % config.wda_bundle_id
    async for event in idb.track_devices():
        if event.serial.startswith("ffffffffffffffffff"):
            logger.debug("Invalid event: %s", event)
            continue
        logger.debug("Apple Event: %s", event)
        if event.present:
            d = device_apple.WDADevice(event.serial, wda_bundle_id=wda_bundle_id,
                                       free_port=FREE_PORT, lock=lock, callback=_device_callback)
            DEVICE_IOS[event.serial] = d
            d.start()
        else:  # offline
            await DEVICE_IOS[event.serial].stop()
            DEVICE_IOS.pop(event.serial)


async def async_main():
    enable_pretty_logging()
    app = make_app()
    app.listen(config.apple_port)
    # 连接流马
    conn = await heartbeat.heartbeat_connect(config.url, "Apple")
    global HBC_IOS
    HBC_IOS = conn
    time.sleep(1)
    await device_watch()


def start_apple():
    """启动入口"""
    try:
        IOLoop.current().run_sync(async_main)
    except KeyboardInterrupt:
        IOLoop.instance().stop()
        for d in DEVICE_IOS.values():
            d.destroy()

