# -*- coding: utf-8 -*-
# copyright by Chras-fu of liuma

import os
import configparser

BASE_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_PATH, "config", "config.ini")


class IniReader:

    def __init__(self, config_ini=CONFIG_PATH):
        if os.path.exists(config_ini):
            self.ini_file = config_ini
        else:
            raise FileNotFoundError('文件不存在！')

    def data(self, section, option):
        config = configparser.ConfigParser()
        config.read(self.ini_file, encoding="utf-8")
        value = config.get(section, option)
        return value

    def option(self, section):
        config = configparser.ConfigParser()
        config.read(self.ini_file, encoding="utf-8")
        options = config.options(section)
        option = {}
        for key in options:
            option[key] = self.data(section, key)
        return option

    def modify(self, section, option, value):
        config = configparser.ConfigParser()
        config.read(self.ini_file, encoding="utf-8")
        config.set(section, option, value)
        config.write(open(self.ini_file, "r+", encoding="utf-8"))


class LMConfig(object):
    """"配置文件"""
    def __init__(self, path=CONFIG_PATH):
        reader = IniReader(path)
        self.url = reader.data("Platform", "url")
        self.host = reader.data("Provider", "host")
        self.android_port = reader.data("Provider", "android-port")
        self.apple_port = reader.data("Provider", "apple-port")
        self.enable_android = reader.data("StartParam", "enable-android")
        self.enable_apple = reader.data("StartParam", "enable-apple")
        self.wda_bundle_id = reader.data("StartParam", "wda-bundle-id")
        self.owner = reader.data("StartParam", "owner")
        self.project = reader.data("StartParam", "project")


config = LMConfig()

