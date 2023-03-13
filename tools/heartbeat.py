# coding: utf-8
# copyright by codeskyblue of openATX

import collections
import json
import urllib
from asyncio import sleep
from collections import defaultdict

from logzero import logger
from tornado.ioloop import IOLoop
from tornado.queues import Queue
from tornado import websocket
from tornado import gen, httpclient
from tools.config import config


DEVICES = dict()


async def heartbeat_connect(server_url, system):
    if server_url.startswith("https"):
        addr = server_url.replace("/", "").replace("https:", "wss://")
    else:
        addr = server_url.replace("/", "").replace("http:", "ws://")
    ws_url = f"{addr}/websocket/heartbeat?project={urllib.parse.quote(config.project)}&owner={config.owner}"
    hbc = HeartbeatConnection(ws_url, system)
    await hbc.open()
    return hbc


class SafeWebSocket(websocket.WebSocketClientConnection):
    async def write_message(self, message, binary=False):
        if isinstance(message, dict):
            message = json.dumps(message)
        return await super().write_message(message)


class HeartbeatConnection(object):
    """心跳连接"""

    def __init__(self, ws_url=None, system=None):
        self._server_ws_url = ws_url
        self._system = system
        self._queue = Queue()
        self._db = defaultdict(dict)

    async def open(self):
        self._ws = await self.connect()
        IOLoop.current().spawn_callback(self._drain_ws_message)
        IOLoop.current().spawn_callback(self._drain_queue)
        IOLoop.current().spawn_callback(self._ping)

    async def _ping(self):
        while True:
            try:
                self._ws.ping(bytes(0))
            except:
                pass
            await sleep(30)

    async def _drain_queue(self):
        while True:
            message = await self._queue.get()
            if message is None:
                logger.info("Resent messages: %s", self._db)
                for v in self._db.values():
                    await self._ws.write_message(v)
                continue

            if 'serial' in message:  # ping消息不包含在裡面
                serial = message['serial']
                update_recursive(self._db, {serial: message})
            self._queue.task_done()

            if self._ws:
                try:
                    await self._ws.write_message(message)
                    logger.debug("websocket send: %s", message)
                except TypeError as e:
                    logger.info("websocket write_message error: %s", e)

    async def _drain_ws_message(self):
        while True:
            message = await self._ws.read_message()
            logger.info("WS receive message: %s", message)
            if message is None:  # 连接关闭重新连接
                self._ws = None
                logger.warning("WS closed")
                self._ws = await self.connect()
                await self._queue.put(None)
            elif message.startswith("cold@"): # 冷却设备
                serial = message[5:]
                try:
                    await self.cold_device(message[5:])
                    logger.info(f"{self._system} device:{serial} cold success")
                except Exception as e:
                    logger.info(f"{self._system} device:{serial} cold error: {str(e)}")

    async def cold_device(self, serial):
        if serial not in DEVICES:
            return
        device = DEVICES[serial]
        if self._system == "Android":
            await device.reset()
            await self.device_update({
                "command": "init",
                "serial": serial,
                "agent": device.addrs,
                "properties": await device.properties()
            })
        else:
            device.restart_wda_proxy()
            await device.wda_healthcheck()

    async def connect(self, cnt=0):
        while cnt < 30:
            try:
                ws = await self._connect()
                return ws
            except Exception as e:
                cnt += 1
                logger.warning("WS connect error: %s, reconnect after %ds", str(e), cnt+1)
                await gen.sleep(cnt+1)
        else:
            logger.warning("连接流马失败 请检查平台地址、项目名称以及用户账号是否配置正确")

    async def _connect(self):
        request = httpclient.HTTPRequest(self._server_ws_url, validate_cert=False)
        ws = await websocket.websocket_connect(request, connect_timeout=None)
        ws.__class__ = SafeWebSocket
        msg = await ws.read_message()
        logger.info(f"{self._system} AgentURL: http://{config.host}:"
                    f"{str(config.android_port if self._system=='Android' else config.apple_port)}")
        logger.info(f"{self._system} AgentId: {msg}")
        return ws

    async def device_update(self, data: dict):
        await self._queue.put(data)


def update_recursive(d: dict, u: dict) -> dict:
    for k, v in u.items():
        if isinstance(v, collections.Mapping):
            d[k] = update_recursive(d.get(k) or {}, v)
        else:
            d[k] = v
    return d

