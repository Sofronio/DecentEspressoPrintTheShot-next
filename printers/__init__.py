#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打印适配器加载 / printing adapter loading
========================================

中文
----
按运行平台挑一个打印适配器装载,上层只拿到一个 BasePrinter 实例,
不用关心底下是哪个平台。

    from printers import get_printer
    printer = get_printer()
    printer.print_bitmap(bitmap, 576, 1200)

加载顺序 / loading order:
    darwin  → MacPrinter      (CUPS:lp / lpstat)
    win32   → WindowsPrinter  (ctypes GDI,无 pywin32 依赖)
    linux   → LinuxPrinter    (CUPS,与 Mac 同源)
    其他    → NullPrinter     (明确报错,而不是静默失败)

Android 比较特殊:Web UI 通常跑在 WebView 里、由原生插件直接通过蓝牙打印,
压根不经过 Python 服务端。这里的 AndroidPrinter 是给「服务端确实跑在
Android 上」这种部署方式(例如 Termux / 嵌入式 Python)准备的桥接实现。

English
-------
Picks a printing adapter for the running platform and hands the caller a single
`BasePrinter` instance, so nothing above this layer needs to know which platform
is underneath.

    from printers import get_printer
    printer = get_printer()
    printer.print_bitmap(bitmap, 576, 1200)

Android is a special case: the web UI normally runs inside a WebView and prints
directly over Bluetooth through a native plugin, never touching the Python
service. The AndroidPrinter here is a bridge for deployments where the service
really does run on Android (Termux, an embedded Python, and so on).
"""

from __future__ import annotations

import os
import platform
import sys

from .base import BasePrinter, PrintError, bitmap_to_pbm  # noqa: F401  (re-export)

__all__ = [
    "BasePrinter",
    "PrintError",
    "bitmap_to_pbm",
    "NullPrinter",
    "get_printer",
    "load_printer",
    "platform_id",
]


class NullPrinter(BasePrinter):
    """
    没有可用适配器时的占位实现 / placeholder used when no adapter applies.

    它的存在是为了「明确失败」:打印接口会返回一条清楚的错误,而不是假装成功
    或者抛一个看不懂的异常。
    It exists so failures are explicit: the print endpoint returns a clear error
    instead of pretending to succeed or raising something unreadable.
    """

    platform_id = "null"
    display_name = "No printer support"

    def is_available(self):
        return False

    def list_printers(self):
        return []

    def print_bitmap(self, bitmap, width, height, printer=None, **kwargs):
        raise PrintError(
            "当前平台没有可用的打印适配器 / no printing adapter available on %s"
            % platform.system(),
            "no_adapter",
        )


class DryRunPrinter(BasePrinter):
    """
    试运行适配器:照常校验,然后什么都不做 / dry-run adapter: validate, then do nothing.

    为什么需要它 —— 这条是踩出来的
    -----------------------------
    测试套件里有一句「真的发一次 /api/print」,注释写着「没有打印机没关系」。这个
    前提只在**没配打印机的机器上**成立:在一台默认打印机就是热敏机的 Mac 上跑测试,
    每一次都往那台机器灌一张完整图表(约 94 KB)。缓冲小的热敏机顶不住这种连续数据,
    会错位、然后持续走纸 —— 实测把打印机打到必须断电才停。

    测试需要的其实是一个**会成功、但不碰硬件**的适配器:接口形状照样验证(请求被
    接受、位图通过校验),而纸一张都不出。

    Why it exists — learned the hard way
    -----------------------------------
    The test suite contains a "really POST /api/print" step whose comment says "no
    printer is attached, that is fine". That only holds on a machine with no printer
    configured: run the tests on a Mac whose default printer is a thermal one and every
    run pushes a full chart (about 94 KB) at it. A thermal printer with a small buffer
    cannot absorb that, loses alignment and feeds paper continuously — in practice it had
    to be power-cycled to stop.

    What the tests need is an adapter that **succeeds without touching hardware**: the
    interface shape is still exercised (the request is accepted, the bitmap is
    validated), and not a sheet comes out.
    """

    platform_id = "dryrun"
    display_name = "Dry run (no printing)"

    def is_available(self):
        return True

    def list_printers(self):
        return [{
            "id": "dryrun",
            "name": "Dry run",
            "address": "dryrun",
            "paired": True,
            "default": True,
        }]

    def print_bitmap(self, bitmap, width, height, printer=None, **kwargs):
        # 校验照做 ——「尺寸不符必须被拒」那条测试依赖它
        # Validation still runs: the "a size mismatch must be rejected" test needs it
        self.validate_bitmap(bitmap, width, height)
        return self._ok("试运行,未实际打印 / dry run: nothing was sent to a printer",
                        printer=printer or "dryrun", mode="dryrun")


#: 设了这个环境变量就换成试运行适配器,纸一张都不出 /
#: set this environment variable to swap in the dry-run adapter; nothing prints
DRYRUN_ENV = "PTS_PRINT_DRYRUN"


def platform_id():
    """把 sys.platform / platform.system() 归一成一个短标识 / normalise the platform to a short id."""
    if sys.platform.startswith("darwin"):
        return "mac"
    # Cygwin 上跑的 Python 报的是 "cygwin",但底下是货真价实的 Windows,
    # ctypes.windll 可用,所以归到 win 分支。
    # Python running under Cygwin reports "cygwin" but sits on real Windows with a
    # working ctypes.windll, so it belongs in the win branch.
    if sys.platform.startswith("win") or sys.platform.startswith("cygwin"):
        return "win"
    if sys.platform.startswith("linux"):
        # Android 的 sys.platform 也是 "linux",靠 android 属性区分
        # Android also reports "linux"; the android attribute disambiguates it
        if hasattr(sys, "getandroidapilevel") or "ANDROID_ROOT" in __import__("os").environ:
            return "android"
        return "linux"
    return "unknown"


def load_printer(config=None):
    """
    按平台装载适配器 / load the adapter for this platform.

    返回 BasePrinter 实例。没有匹配的平台时返回 NullPrinter,由调用方决定
    如何提示用户。
    Returns a BasePrinter instance. When nothing matches, a NullPrinter comes
    back and the caller decides how to tell the user.

    环境变量 PTS_PRINT_DRYRUN 一旦设置,直接返回 DryRunPrinter —— 测试与被脚本
    驱动的验证都靠它,免得「跑测试」等于「真的往默认打印机灌一张图」。见
    DryRunPrinter 的说明。
    With PTS_PRINT_DRYRUN set, a DryRunPrinter comes back instead — that is what keeps
    "run the tests" from meaning "push a chart at the default printer". See DryRunPrinter.
    """
    if os.environ.get(DRYRUN_ENV):
        return DryRunPrinter(config)

    pid = platform_id()
    try:
        if pid == "mac":
            from .mac_printer import MacPrinter
            return MacPrinter(config)
        if pid == "win":
            from .windows_printer import WindowsPrinter
            return WindowsPrinter(config)
        if pid == "linux":
            from .linux_printer import LinuxPrinter
            return LinuxPrinter(config)
        if pid == "android":
            from .android_printer import AndroidPrinter
            return AndroidPrinter(config)
    except ImportError:
        # 适配器模块缺失 / the adapter module is missing
        return NullPrinter(config)
    return NullPrinter(config)


#: 进程级单例,服务端启动时装载一次 / process-wide singleton, loaded once at startup
_printer = None


def get_printer(config=None, reload=False):
    """取进程级适配器单例 / get the process-wide adapter singleton."""
    global _printer
    if _printer is None or reload:
        _printer = load_printer(config)
    return _printer
