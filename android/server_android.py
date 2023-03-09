# coding: utf-8
# copyright by codeskyblue of openATX
import base64
import hashlib
import io
import json
import os
import re
import shutil
import time
import traceback
import requests
import tornado.web
import tornado.websocket
from tornado.concurrent import run_on_executor
from tornado.ioloop import IOLoop
from adbutils import adb as adbclient, errors
from tornado.log import enable_pretty_logging
from concurrent.futures import ThreadPoolExecutor
from logzero import logger
from android.adb import adb
from android.device_android import AndroidDevice
from tools.freeport import FreePort
from tools.config import config
from tools.download import get_all
from tools.heartbeat import heartbeat_connect, HeartbeatConnection, DEVICES


HBC_ANDROID = HeartbeatConnection()
FREE_PORT = FreePort("android")


class CorsMixin(object):
    CORS_ORIGIN = '*'
    CORS_METHODS = '*'
    CORS_CREDENTIALS = True
    CORS_HEADERS = "*"

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
        file_dir = "tmp/android/"
        if not os.path.exists(file_dir):
            os.makedirs(file_dir)
        file_path = file_dir+file_name

        if os.path.exists(file_path):
            return file_path
        logger.debug("Download %s to %s", url, file_path)

        tmp_path = file_path + ".tmp"
        r = requests.get(url, stream=True)
        r.raise_for_status()

        with open(tmp_path, "wb") as tfile:
            content_length = int(r.headers.get("content-length", 0))
            if content_length:
                for chunk in r.iter_content(chunk_size=40960):
                    tfile.write(chunk)
            else:
                shutil.copyfileobj(r.raw, tfile)

        os.rename(tmp_path, file_path)
        return file_path

    @run_on_executor(executor='_install_executor')
    def app_install_url(self, serial: str, apk_path: str):
        device = adbclient.device(serial)
        try:
            # 推送到手机
            dst = "/data/local/tmp/tmp-%d.apk" % int(time.time() * 1000)
            device.sync.push(apk_path, dst)
            # 调用pm install安装
            device.install_remote(dst)
        except errors.AdbInstallError as e:
            return {
                "status": 1000,
                "message": "安装失败: \n%s" % e.output
            }
        return {
            "status": 0,
            "message": "安装成功"
        }

    async def post(self):
        body = json.loads(self.request.body.decode())
        serial = body["serial"]
        url = body["url"]
        try:
            apk_path = await self.cache_download(url)
            ret = await self.app_install_url(serial, apk_path)
            self.write(ret)
        except Exception as e:
            self.write({"status": 1000, "message": "安装错误:\n%s" % str(e)})


class AppUninstallHandler(CorsMixin, tornado.web.RequestHandler):
    """卸载应用"""
    _uninstall_executor = ThreadPoolExecutor(4)

    @run_on_executor(executor='_uninstall_executor')
    def app_uninstall(self, serial:str, package_name: str):
        device = adbclient.device(serial)
        output = device.uninstall(package_name)
        if "Success" not in output:
            return {
                "status": 1000,
                "message": "卸载失败"
            }
        return {
            "status": 0,
            "message": "卸载成功"
        }

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
        if serial not in DEVICES:
            return
        device = DEVICES[serial]
        try:
            buffer = io.BytesIO()
            device.get_screenshot().convert("RGB").save(buffer, format='JPEG')
            b64data = base64.b64encode(buffer.getvalue())
            res = {
                "type": "jpeg",
                "encoding": "base64",
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
        if serial not in DEVICES:
            return
        device = DEVICES[serial]
        try:
            hierachy = device.dump_hierarchy()
            self.write({"status": 0, "message": "获取控件成功", "data": hierachy})
        except Exception as e:
            self.write({"status": 1000, "message": "获取控件失败: %s" % str(e)})


def make_app():
    setting = {'debug': False}
    app = tornado.web.Application([
        (r"/app/install", AppInstallHandler),
        (r"/app/uninstall", AppUninstallHandler),
        (r"/device/screenshot", DeviceScreenshotHandler),
        (r"/device/hierarchy", DeviceHierarchyHandler),
        # (r"/cold", ColdingHandler),
    ], **setting)
    return app


async def device_watch():
    """监听安卓设备"""
    async for event in adb.track_devices():
        if re.match(r"(\d+)\.(\d+)\.(\d+)\.(\d+):(\d+)", event.serial):
            logger.debug("Skip remote device: %s", event)
            continue
        logger.debug("Android Event: %s", event)
        serial = event.serial
        if event.present:
            try:
                device = AndroidDevice(event.serial, FREE_PORT)
                await device.init()
                await device.open_identify()
                DEVICES[serial] = device
                await HBC_ANDROID.device_update({
                    "command": "init",
                    "serial": serial,
                    "agent": device.addrs,
                    "properties": await device.properties()
                })
                logger.info("Device:%s is ready", event.serial)
            except RuntimeError:
                logger.warning("Device:%s initialize failed", event.serial)
            except Exception as e:
                logger.error("Unknown error: %s", e)
                import traceback
                traceback.print_exc()
        else:
            if serial in DEVICES:
                DEVICES[serial].close()
                DEVICES.pop(serial, None)

            await HBC_ANDROID.device_update({
                "command": "delete",
                "serial": serial
            })


async def async_main():
    enable_pretty_logging()
    app = make_app()
    app.listen(config.android_port)
    get_all()  # 下载安卓所有依赖包
    time.sleep(1)
    # 连接流马
    conn = await heartbeat_connect(config.url, "Android")
    global HBC_ANDROID
    HBC_ANDROID = conn
    await device_watch()


def start_android():
    """启动入口"""
    try:
        IOLoop.current().run_sync(async_main)
    except KeyboardInterrupt:
        logger.info("Interrupt catched")

