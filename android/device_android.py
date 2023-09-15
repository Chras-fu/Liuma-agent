# coding: utf-8
# copyright by codeskyblue of openATX

import subprocess
import sys
import traceback
import zipfile

import requests
import uiautomator2
from adbutils import adb as adbclient
from logzero import logger
import apkutils2 as apkutils
from weditor.web import uidumplib

from tools import download
from android.adb import adb
from tools.config import config
from tools.freeport import FreePort


class InitError(Exception):
    """ device init error """


class AndroidDevice(object):
    def __init__(self, serial: str, free_port: FreePort):
        self._free_port = free_port
        self._serial = serial
        self._procs = []
        self._current_ip = config.host
        self._scrcpy_server = None
        self._agent_server = None
        self._input_server = None
        self._device = adbclient.device(serial)

    def __repr__(self):
        return "[" + self._serial + "]"

    @property
    def serial(self):
        return self._serial

    async def run_forever(self):
        try:
            await self.init()
        except Exception as e:
            logger.warning("Init failed: %s", e)

    async def init(self):
        """初始化设备"""
        logger.info("Init device: %s", self._serial)
        self._version = await self.getprop("ro.build.version.release")
        self._init_binaries()
        self._init_apks()
        await adb.shell(self._serial, "/data/local/tmp/atx-agent server --stop")
        await adb.shell(self._serial, "cd /data/local/tmp && ./atx-agent server --nouia -d")
        await self._init_forwards()
        await self.start_server()

    async def open_identify(self):
        await adb.shell(self._serial, "am start -n com.github.uiautomator/.IdentifyActivity -e theme black")

    async def start_server(self):
        """启动scrcpy服务"""
        if self._scrcpy_server:
            self._scrcpy_server.terminate()
        self._scrcpy_server_port = self._free_port.get()
        self._scrcpy_server = subprocess.Popen([
            sys.executable, "-u", "android/proxy_scrcpy.py",
            "-s", self._serial,
            "-sp", str(self._scrcpy_server_port)],
            stdout=sys.stdout)
        logger.info("[%s] scrcpy server start, port %s" % (self._serial, self._scrcpy_server_port))

    def _init_binaries(self):
        """初始化依赖包"""
        d = self._device
        abi = d.getprop('ro.product.cpu.abi')
        abis = (d.getprop('ro.product.cpu.abilist').strip() or abi).split(",")
        abimaps = {
            'armeabi-v7a': 'atx-agent-armv7',
            'arm64-v8a': 'atx-agent-armv7',
            'armeabi': 'atx-agent-armv6',
            'x86': 'atx-agent-386',
        }
        # atx-agent
        okfiles = [abimaps[abi] for abi in abis if abi in abimaps]
        if not okfiles:
            raise InitError("no avaliable abilist", abis)
        logger.debug("%s use atx-agent: %s", self, okfiles[0])
        zipfile_path = download.get_atx_agent_bundle()
        self._push_file(okfiles[0],"/data/local/tmp/atx-agent",zipfile_path=zipfile_path)
        # scrcpy
        scrcpy_zippath = download.get_scrcpy_server()
        self._push_file("scrcpy-server", "/data/local/tmp/scrcpy-server", zipfile_path=scrcpy_zippath)

    def _push_file(self, path: str, dest: str, zipfile_path: str, mode=0o755):
        """上传文件到手机"""
        with zipfile.ZipFile(zipfile_path) as z:
            src_info = z.getinfo(path)
            dest_info = self._device.sync.stat(dest)
            if dest_info.size == src_info.file_size and dest_info.mode & mode == mode:
                logger.debug("%s already pushed %s", self, path)
                return
            with z.open(path) as f:
                self._device.sync.push(f, dest, mode)

    def _init_apks(self):
        whatsinput_apk_path = download.get_whatsinput_apk()
        self._install_apk(whatsinput_apk_path)
        for apk_path in download.get_uiautomator_apks():
            self._install_apk(apk_path)

    def _install_apk(self, path: str):
        assert path, "Invalid %s" % path
        try:
            m = apkutils.APK(path).manifest
            info = self._device.package_info(m.package_name)
            if info and m.version_code == str(info['version_code']) and (
                    m.version_name == info['version_name'] or info['version_name'] == 'null'):
                logger.debug("%s already installed %s", self, path)
            else:
                print(info, ":", m.version_code, m.version_name)
                logger.debug("%s install %s", self, path)
                self._device.install(path)
        except Exception as e:
            traceback.print_exc()
            logger.warning("%s Install apk %s error %s", self, path, e)

    async def _init_forwards(self):
        """代理手机端口"""
        logger.debug("%s forward atx-agent", self)
        if self._agent_server:
            self._agent_server.terminate()
        self._atx_proxy_port, self._agent_server = await self.proxy_device_port(7912)
        logger.debug("%s forward whatsinput", self)
        if self._input_server:
            self._input_server.terminate()
        self._whatsinput_port, self._input_server = await self.proxy_device_port(6677)

    async def adb_forward_to_any(self, remote: str) -> int:
        async for f in adb.forward_list():
            if f.serial == self._serial:
                if f.remote == remote and f.local.startswith("tcp:"):
                    return int(f.local[4:])

        local_port = self._free_port.get()
        await adb.forward(self._serial, 'tcp:{}'.format(local_port), remote)
        return local_port

    async def proxy_device_port(self, device_port: int) -> tuple:
        """ reverse-proxy device:port to *:port """
        local_port = await self.adb_forward_to_any("tcp:" + str(device_port))
        listen_port = self._free_port.get()
        logger.debug("%s proxy port start *:%d -> %d", self, local_port, listen_port)
        server = subprocess.Popen([
            sys.executable, "-u", "android/proxy_port.py",
            "-lp", str(listen_port),
            "-tp", str(local_port)],
            stdout=sys.stdout)
        return listen_port, server

    @property
    def addrs(self):
        def port2addr(port):
            return self._current_ip + ":" + str(port)

        return {
            "url": "http://" + port2addr(config.android_port),
            "atxAgentAddress": port2addr(self._atx_proxy_port),
            "whatsInputAddress": port2addr(self._whatsinput_port),
            "scrcpyServerAddress": port2addr(self._scrcpy_server_port)
        }

    def run_background(self, *args, **kwargs):
        silent = kwargs.pop('silent', False)
        if silent:
            kwargs['stdout'] = subprocess.DEVNULL
            kwargs['stderr'] = subprocess.DEVNULL
        p = subprocess.Popen(*args, **kwargs)
        self._procs.append(p)
        return p

    async def getprop(self, name: str) -> str:
        value = await adb.shell(self._serial, "getprop " + name)
        return value.strip()

    async def getinfo(self, script:str):
        value = await adb.shell(self._serial, script)
        return value.strip()

    async def properties(self):
        brand = await self.getprop("ro.product.brand")
        model = await self.getprop("ro.product.model")
        size = await self.getinfo("wm size")
        return {
            "system": "android",
            "brand": brand,
            "version": self._version,
            "model": model,
            "name": model,
            "size": size.split(": ")[-1],
        }

    async def reset(self):
        self.close()
        await adb.shell(self._serial, "input keyevent HOME")
        await self.init()

    def wait(self):
        for p in self._procs:
            p.wait()

    def close(self):
        for p in self._procs:
            p.terminate()
        self._procs = []

    def get_screenshot(self):
        device = uiautomator2.Device(self._serial)
        screenshot = device.screenshot()
        return screenshot

    def dump_hierarchy(self):
        device = uiautomator2.Device(self._serial)
        current = device.app_current()
        size = device.window_size()
        atx_agent_url = "http://" + self.addrs.get("atxAgentAddress")
        try:
            res = requests.get(atx_agent_url+"/dump/hierarchy")
            page_xml = res.json()["result"]
        except:
            page_xml = device.dump_hierarchy(pretty=True)
        page_json = uidumplib.android_hierarchy_to_json(page_xml.encode('utf-8'))
        return {
            "jsonHierarchy": page_json,
            "activity": current['activity'],
            "packageName": current['package'],
            "windowSize": size,
        }


