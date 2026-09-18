#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Android 打印适配(服务端侧桥接)/ Android printing adapter (server-side bridge)
============================================================================

中文
----
先说清楚一件事:**Android 上通常根本不需要这个模块**。

正常的 Android 部署是 Web UI 跑在 WebView / Capacitor 里,由原生插件直接
通过蓝牙把小票打出来,数据压根不经过 Python 服务端 —— 这条路径见
`android/` 目录与 `web/printer.js`。

那什么时候会用到这里?当服务端本身就跑在 Android 上(例如 Termux、或者把
Python 嵌进 App),此时服务端的 /api/print 需要一个出口把位图交给原生蓝牙层。

两条桥接路径 / two bridge paths:

  1. HTTP 桥(默认)
     原生 App 在本机开一个小端口(默认 127.0.0.1:9100),服务端把打包好的
     ESC/POS 字节 POST 过去,由原生层写进蓝牙 socket。
     好处是完全解耦:服务端不需要知道蓝牙的任何细节。

  2. pyjnius 直调
     如果 Python 跑在 Android 上且装了 pyjnius,直接调 App 暴露的 Java 静态
     方法。少一跳网络,但依赖更重,所以只在 HTTP 桥不可用时才尝试。

English
-------
First, the important part: **Android usually does not need this module at all.**

The normal Android deployment runs the web UI inside a WebView / Capacitor
container and prints over Bluetooth from a native plugin, with the data never
touching the Python service — see the `android/` directory and `web/printer.js`.

So when is this used? When the service itself runs on Android (Termux, or Python
embedded in the app), where /api/print needs an exit to hand the bitmap to the
native Bluetooth layer.

  1. HTTP bridge (default)
     The native app listens on a small local port (127.0.0.1:9100 by default)
     and the service POSTs the packed ESC/POS bytes to it, which the native side
     writes into the Bluetooth socket. Fully decoupled: the service never needs
     to know anything about Bluetooth.

  2. pyjnius direct call
     When Python runs on Android with pyjnius available, it calls a static Java
     method exposed by the app. One network hop fewer, but a heavier dependency,
     so it is only tried when the HTTP bridge is unreachable.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

from . import escpos
from .base import BasePrinter, PrintError

#: 原生桥接服务默认地址 / default address of the native bridge
DEFAULT_BRIDGE = "http://127.0.0.1:9100"


class AndroidPrinter(BasePrinter):
    """Android 打印适配器(桥接到原生蓝牙层)/ Android adapter, bridging to the native Bluetooth layer."""

    platform_id = "android"
    display_name = "Android (Bluetooth ESC/POS)"

    def __init__(self, config=None):
        super().__init__(config)
        self.bridge = (self.config.get("bridge") or DEFAULT_BRIDGE).rstrip("/")
        self.timeout = int(self.config.get("timeout", 20))
        self.feed_lines = int(self.config.get("feed_lines", 3))
        self.do_cut = bool(self.config.get("cut", True))

    # ------------------------------------------------------------------ 能力
    # ------------------------------------------------------------------ capability

    def is_available(self):
        """
        能跑通就视为可用 / available when either bridge path is reachable.

        这里刻意做一次真实的探测而不是只判断平台,因为服务端跑在 Android 上、
        但 App 没起桥接的情况很常见,那时应该老实报「不可用」。
        This probes for real rather than just checking the platform: a service
        running on Android with no bridge up is common, and in that case it
        should honestly report itself unavailable.
        """
        if not sys.platform.startswith("linux"):
            return False
        return self._http_bridge_alive() or self._pyjnius_available()

    def supports_raw(self):
        return True

    def capabilities(self):
        caps = super().capabilities()
        caps.update({
            "bridge": self.bridge,
            "transport": "bridge" if self._http_bridge_alive() else (
                "pyjnius" if self._pyjnius_available() else "none"),
        })
        return caps

    def _http_bridge_alive(self):
        try:
            req = urllib.request.Request(self.bridge + "/ping", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _pyjnius_available(self):
        try:
            from jnius import autoclass  # noqa: F401
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ 枚举
    # ------------------------------------------------------------------ enumeration

    def list_printers(self):
        """
        向原生层要已配对的蓝牙打印机列表 / ask the native layer for paired printers.

        没起桥接时返回空列表,而不是抛错 —— 列表为空本身就是有效信息。
        Returns an empty list rather than raising when no bridge is up: an empty
        list is itself useful information.
        """
        try:
            req = urllib.request.Request(self.bridge + "/printers", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            printers = data.get("printers", data if isinstance(data, list) else [])
            out = []
            for p in printers:
                out.append({
                    "id": p.get("address") or p.get("id"),
                    "name": p.get("name") or p.get("address") or "Bluetooth printer",
                    "status": p.get("status", "paired"),
                    "default": bool(p.get("default")),
                })
            return out
        except Exception:
            return []

    # ------------------------------------------------------------------ 打印
    # ------------------------------------------------------------------ printing

    def print_bitmap(self, bitmap, width, height, printer=None, **kwargs):
        """通过原生层打印 / print via the native layer."""
        self.validate_bitmap(bitmap, width, height)

        payload = escpos.build_print_job(
            bitmap, width, height,
            feed_lines=self.feed_lines, do_cut=self.do_cut,
        )
        target = printer or self.config.get("printer")

        if self._http_bridge_alive():
            return self._print_via_http(payload, target)
        if self._pyjnius_available():
            return self._print_via_pyjnius(payload, target)

        raise PrintError(
            "Android 原生打印桥不可用 / native printing bridge unavailable. "
            "请确认 App 在 %s 上提供了桥接服务 / make sure the app exposes a bridge at %s"
            % (self.bridge, self.bridge),
            "bridge_unavailable",
        )

    def _print_via_http(self, payload, target):
        body = json.dumps({
            "data": base64.b64encode(payload).decode("ascii"),
            "address": target,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.bridge + "/print", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8") or "{}"
            result = json.loads(raw)
        except urllib.error.HTTPError as e:
            raise PrintError("原生打印失败 / native print failed: HTTP %s" % e.code, "print_failed")
        except Exception as e:
            raise PrintError("原生打印失败 / native print failed: %s" % e, "print_failed")

        if not result.get("success", False):
            raise PrintError(
                "原生打印失败 / native print failed: %s"
                % (result.get("message") or "unknown error"),
                "print_failed",
            )
        return self._ok(
            result.get("message") or "打印任务已发送 / print job sent",
            printer=target or result.get("printer"), via="http-bridge",
        )

    def _print_via_pyjnius(self, payload, target):
        try:
            from jnius import autoclass
            bridge = autoclass("com.printtheshot.printer.BluetoothPrinterBridge")
            ok = bridge.printRaw(
                base64.b64encode(payload).decode("ascii"), target or ""
            )
            if not ok:
                raise PrintError("原生打印返回失败 / native layer reported failure", "print_failed")
            return self._ok("打印任务已发送 / print job sent",
                            printer=target, via="pyjnius")
        except PrintError:
            raise
        except Exception as e:
            raise PrintError("pyjnius 调用失败 / pyjnius call failed: %s" % e, "print_failed")


def android_environment_detected():
    """
    判断当前是否跑在 Android 上(不探测桥接)/ whether we are on Android (no bridge probe).

    sys.platform 在 Android 上也是 "linux",所以要靠环境里特有的东西来区分。
    sys.platform is "linux" on Android too, hence the extra environment checks.
    """
    if not sys.platform.startswith("linux"):
        return False
    return hasattr(sys, "getandroidapilevel") or "ANDROID_ROOT" in os.environ
