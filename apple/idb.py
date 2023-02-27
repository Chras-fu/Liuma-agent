import subprocess
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from logzero import logger
from tornado import gen
from tornado.concurrent import run_on_executor
from tidevice._usbmux import Usbmux


DeviceEvent = namedtuple('DeviceEvent', ['present', 'serial'])
um = Usbmux()


class IDBClient:
    executor = ThreadPoolExecutor(4)

    def __init__(self):
        self._lasts = []

    @run_on_executor(executor='executor')
    def list_devices(self):
        return um.device_udid_list()

    def device_info(self, serial):
        lines = self.runcommand("tidevice", "-u", serial, "info").splitlines()
        info = {}
        for line in lines:
            info[line.split(":")[0].strip()] = line.split(":")[-1].strip()
        return info

    @gen.coroutine
    def update(self):
        lasts = self._lasts
        currs = yield self.list_devices()
        gones = set(lasts).difference(currs)  # 离线
        backs = set(currs).difference(lasts)  # 在线
        self._lasts = currs
        raise gen.Return((backs, gones))

    async def track_devices(self):
        while True:
            backs, gones = await self.update()
            for serial in backs:
                yield DeviceEvent(True, serial)

            for serial in gones:
                yield DeviceEvent(False, serial)
            await gen.sleep(1)

    @staticmethod
    def runcommand(*args):
        try:
            output = subprocess.check_output(args)
            return output.strip().decode('utf-8')
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""
        except Exception as e:
            logger.warning("unknown error: %s", e)
            return ""


idb = IDBClient()

