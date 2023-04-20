import argparse
import json

from tornado import websocket, httpserver, ioloop
import tornado
import tornado.web
import tornado.netutil
import tornado.process

from scrcpy.client import ClientDevice
from scrcpy.constants import sc_control_msg_type

DEVICE_ID = None
H264_HEADER = []
SOCKET_PORT = None
SOCKET_SERVER = None
SOCKET_CLIENT = None


class ScreenWSHandler(websocket.WebSocketHandler):
    """scrcpy投屏"""
    DEVICE_CLIENT_DICT = dict()

    def check_origin(self, origin):
        return True

    def initialize(self):
        self.device_id = DEVICE_ID
        self.device_client = None

    async def open(self):
        # 获取当前连接对应的device_client
        old_device_client = self.DEVICE_CLIENT_DICT.get(self.device_id, None)
        if old_device_client:
            self.device_client = old_device_client
        else:
            self.device_client = self.DEVICE_CLIENT_DICT[self.device_id] = ClientDevice(self.device_id)
        self.device_client.ws_client_list.append(self)
        # 重新启动scrcpy 重新开始任务
        async with self.device_client.device_lock:
            await self.device_client.stop()
            await self.device_client.start()

    async def on_message(self, text_data):
        """receive used to control device"""
        data = json.loads(text_data)
        # touch
        if data['msg_type'] == sc_control_msg_type.SC_CONTROL_MSG_TYPE_INJECT_TOUCH_EVENT:
            await self.device_client.controller.inject_touch_event(x=data['x'], y=data['y'], action=data['action'])
        # scroll
        elif data['msg_type'] == sc_control_msg_type.SC_CONTROL_MSG_TYPE_INJECT_SCROLL_EVENT:
            await self.device_client.controller.inject_scroll_event(x=data['x'], y=data['y'],
                                                                    distance_x=data['distance_x'], distance_y=data['distance_y'])
        # swipe
        elif data['msg_type'] == sc_control_msg_type.SC_CONTROL_MSG_TYPE_INJECT_SWIPE_EVENT:
            await self.device_client.controller.swipe(x=data['x'], y=data['y'], end_x=data['end_x'], end_y=data['end_y'],
                                                      unit=data['unit'], delay=data['delay'])

    def on_connection_close(self):
        self.device_client.ws_client_list.remove(self)


def start_server():
    parser = argparse.ArgumentParser()
    parser.add_argument("-s",
                        "--serial",
                        help="device serial")
    parser.add_argument("-sp",
                        "--server-port",
                        type=int,
                        help="scrcpy server port")
    args = parser.parse_args()
    global DEVICE_ID
    DEVICE_ID = args.serial

    app = tornado.web.Application([
        (r"/screen", ScreenWSHandler),
    ], debug=False)

    http_server = httpserver.HTTPServer(app)
    http_server.listen(args.server_port)
    ioloop.IOLoop.instance().start()


if __name__ == "__main__":
    start_server()
