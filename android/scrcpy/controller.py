import asyncio
import struct


class Controller:
    def __init__(self, device_client):
        self.device = device_client

    async def empty_control_socket(self, interval=0.02, loop=10):
        for idx in range(loop):
            try:
                await asyncio.wait_for(self.device.control_socket.read(0x10000), timeout=interval)
            except:
                return

    async def inject(self, msg):
        async with self.device.device_lock:
            await self.device.control_socket.write_bytes(msg)

    async def inject_without_lock(self, msg):
        await self.device.control_socket.write_bytes(msg)

    async def inject_touch_event(self, x, y, action=0, touch_id=-1, pressure=0xFFFF, buttons=1 << 0):
        """
        action: android_motionevent_action
        touch_id: touch_id use to distinguish multi touch
        pressure: touch pressure
        buttons: android_motionevent_buttons
        inject_data: lens 28
        """
        if action == 1:
            pressure = 0x0
        msg_type = 2
        x, y = max(x*self.device.resolution[0], 0), max(y*self.device.resolution[1], 0)
        inject_data = struct.pack(">BBqiiHHHi", msg_type, action, touch_id, int(x), int(y),
                                  int(self.device.resolution[0]), int(self.device.resolution[1]), pressure, buttons)
        await self.inject(inject_data)
        return inject_data

    async def inject_scroll_event(self, x, y, distance_x, distance_y, buttons=1 << 0):
        """
        buttons: android_motionevent_buttons
        inject_data: lens 25
        """
        msg_type = 3
        x, y = max(x*self.device.resolution[0], 0), max(y*self.device.resolution[1], 0)
        inject_data = struct.pack(">BiiHHiii", msg_type, int(x), int(y), int(self.device.resolution[0]),
                                  int(self.device.resolution[1]), int(distance_x), int(distance_y), buttons)
        await self.inject(inject_data)
        return inject_data

    async def swipe(self, x, y, end_x, end_y, unit=5, delay=1):
        """
        swipe (x,y) to (end_x, end_y), 匀速移动，每unit个像素点出发一次touch move事件
        """
        x_1, y_1 = x*self.device.resolution[0], y*self.device.resolution[1]
        end_x, end_y = min(end_x*self.device.resolution[0], self.device.resolution[0]), min(end_y*self.device.resolution[1], self.device.resolution[1])
        step = 1
        while True:
            if x_1 > end_x:
                x_1 -= min(x-end_x, unit)
            elif x_1 < end_x:
                x_1 += min(end_x-x_1, unit)
            if y_1 > end_y:
                y_1 -= min(y_1-end_y, unit)
            elif y < end_y:
                y_1 += min(end_y-y_1, unit)
            if x_1 == end_x and y_1 == end_y:
                break
            step += 1
        unit_delay = delay/step
        await self.inject_touch_event(x, y, 0)
        while True:
            if x > end_x:
                x -= min(x-end_x, unit)
            elif x < end_x:
                x += min(end_x-x, unit)
            if y > end_y:
                y -= min(y-end_y, unit)
            elif y < end_y:
                y += min(end_y-y, unit)
            await self.inject_touch_event(x, y, 2)
            await asyncio.sleep(unit_delay)
            if x == end_x and y == end_y:
                await self.inject_touch_event(x, y, 1)
                break

