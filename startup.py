# -*- coding: utf-8 -*-
# copyright by Chras-fu of liuma

import multiprocessing
from tools.config import config
from apple.server_apple import start_apple
from android.server_android import start_android
from logzero import logger


__version__ = "1.1.1"


def startup():
    """启动服务"""
    logger.info("当前所属版本号: %s", __version__)
    logger.info("本机设备所属流马项目: %s", config.project)
    logger.info("本机设备所属流马账号: %s", config.owner)
    # 启动设备监听 android和apple独立进程
    processes = []
    if config.enable_android.lower() == "true":
        android_p = multiprocessing.Process(target=start_android)
        android_p.start()
        processes.append(android_p)
    if config.enable_apple.lower() == "true":
        apple_p = multiprocessing.Process(target=start_apple)
        apple_p.start()
        processes.append(apple_p)
    for p in processes:
        p.join()


if __name__ == '__main__':
    startup()

