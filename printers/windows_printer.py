#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows 打印适配 / Windows printing adapter
==========================================

中文
----
用纯 ctypes 调用 Windows 打印后台接口(winspool.drv),不依赖 pywin32 ——
和旧版保持一致,免掉一个体积不小、还经常装不上的依赖。

两种打印模式 / two printing modes:

  escpos 模式(默认)
      把位图打包成 ESC/POS 指令,以 "RAW" 数据类型直接入队。
      这是 Windows 上驱动热敏小票机的标准做法:指令不经驱动解释,原样送到
      打印机。也是 Android 蓝牙路径用的同一份字节,两端出纸效果一致。

  bmp 模式(兼容旧版)
      把位图包成一张 1-bit BMP 原样入队,复刻旧版的字节布局。仅在你原来的
      环境确实靠这种方式出纸时才需要。

English
-------
Calls the Windows print spooler (winspool.drv) through plain ctypes with no
pywin32 dependency — same as before, which avoids a hefty dependency that also
often refuses to install.

  escpos mode (default)
      Packs the bitmap into ESC/POS and queues it with the "RAW" datatype. That
      is the standard way to drive a thermal receipt printer on Windows: the
      bytes bypass the driver and reach the device untouched. It is also the
      exact byte stream the Android Bluetooth path uses, so both platforms
      produce the same sheet.

  bmp mode (legacy compatibility)
      Wraps the bitmap as a 1-bit BMP and queues it as-is, reproducing the
      previous byte layout. Only needed if your existing setup really did print
      that way.
"""

from __future__ import annotations

import os
import sys

from . import escpos
from .base import BasePrinter, PrintError, bitmap_to_bmp1


class WindowsPrinter(BasePrinter):
    """Windows(winspool / ctypes)打印适配器 / Windows (winspool, ctypes) adapter."""

    platform_id = "win"
    display_name = "Windows (spooler)"

    def __init__(self, config=None):
        super().__init__(config)
        self.mode = (self.config.get("mode") or "escpos").lower()
        self.feed_lines = int(self.config.get("feed_lines", 3))
        self.do_cut = bool(self.config.get("cut", True))
        self.timeout = int(self.config.get("timeout", 30))

    # ------------------------------------------------------------------ 能力
    # ------------------------------------------------------------------ capability

    def is_available(self):
        """
        不仅看平台字符串,还真的去摸一下 spooler API 在不在。
        Not just the platform string — actually reach for the spooler API.

        只看平台字符串的话,平台判断一旦出错(比如某个没预料到的 Python 构建),
        症状会是打印时抛一个看不懂的 AttributeError。这里主动探一下,问题就变成
        一个清楚的「不可用」。
        Trusting the platform string means a wrong guess surfaces later as an opaque
        AttributeError at print time. Probing here turns it into a clear "unavailable".
        """
        if not (sys.platform.startswith("win") or sys.platform.startswith("cygwin")):
            return False
        try:
            import ctypes
            return bool(ctypes.windll.winspool)
        except Exception:
            return False

    def supports_raw(self):
        return True

    def capabilities(self):
        caps = super().capabilities()
        caps["mode"] = self.mode
        return caps

    # ------------------------------------------------------------------ 枚举
    # ------------------------------------------------------------------ enumeration

    def list_printers(self):
        """
        用 EnumPrintersW 枚举本地打印机 / enumerate local printers via EnumPrintersW.

        PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS = 0x2 | 0x4,
        这样网络打印机也能列出来。
        The flags cover local and network-connected printers.
        """
        if not self.is_available():
            return []
        import ctypes
        from ctypes import wintypes

        winspool = ctypes.windll.winspool
        flags = 0x2 | 0x4          # LOCAL | CONNECTIONS
        level = 4                   # PRINTER_INFO_4:名字 + 服务器
        needed = wintypes.DWORD(0)
        returned = wintypes.DWORD(0)

        # 先问需要多大缓冲 / first ask how big the buffer must be
        winspool.EnumPrintersW(flags, None, level, None, 0,
                               ctypes.byref(needed), ctypes.byref(returned))
        if needed.value == 0:
            return []

        buffer = ctypes.create_string_buffer(needed.value)
        ok = winspool.EnumPrintersW(flags, None, level, buffer, needed.value,
                                    ctypes.byref(needed), ctypes.byref(returned))
        if not ok:
            return []

        class PRINTER_INFO_4(ctypes.Structure):
            _fields_ = [
                ("pPrinterName", wintypes.LPWSTR),
                ("pServerName", wintypes.LPWSTR),
                ("Attributes", wintypes.DWORD),
            ]

        array = ctypes.cast(buffer, ctypes.POINTER(PRINTER_INFO_4))
        default_name = self._default_printer_name()
        printers = []
        for i in range(returned.value):
            item = array[i]
            name = item.pPrinterName or ""
            if not name:
                continue
            printers.append({
                "id": name,
                "name": name,
                "status": "unknown",
                "default": (name == default_name),
            })
        return printers

    def _default_printer_name(self):
        """取默认打印机名 / fetch the default printer name."""
        try:
            import ctypes
            from ctypes import wintypes
            winspool = ctypes.windll.winspool
            name = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if winspool.GetDefaultPrinterW(name, ctypes.byref(size)):
                return name.value
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ 打印
    # ------------------------------------------------------------------ printing

    def build_payload(self, bitmap, width, height, mode=None):
        """
        拼出要送进打印队列的字节 / assemble the bytes that go into the spooler.

        刻意与「调用 Windows API」那部分分开:这一段是纯粹的数据转换,能在任何
        平台上测;下面那段 ctypes 调用只在 Windows 上存在。混在一起写的话,
        可测的那部分也就跟着变成不可测了。

        Deliberately separate from the Windows API calls: this part is pure data
        transformation and can be tested on any platform, while the ctypes section
        below only exists on Windows. Entangled, the testable half stops being
        testable.

        mode:
            escpos(默认)—— ESC/POS 指令,以 RAW 入队
            bmp          —— 1-bit BMP,复刻旧版字节布局
        """
        self.validate_bitmap(bitmap, width, height)
        mode = (mode or self.mode or "escpos").lower()
        if mode == "bmp":
            return bitmap_to_bmp1(bitmap, width, height), mode
        return escpos.build_print_job(
            bitmap, width, height,
            feed_lines=self.feed_lines, do_cut=self.do_cut,
        ), mode

    def print_bitmap(self, bitmap, width, height, printer=None, **kwargs):
        """打印 1-bit 位图 / print a 1-bit bitmap."""
        if not self.is_available():
            raise PrintError("当前不是 Windows / not running on Windows", "wrong_platform")

        payload, mode = self.build_payload(bitmap, width, height, kwargs.get("mode"))

        import ctypes
        from ctypes import wintypes

        target = printer or self.config.get("printer") or self._default_printer_name()
        if not target:
            raise PrintError(
                "未找到可用打印机 / no printer found on this system", "no_printer"
            )

        winspool = ctypes.windll.winspool

        class DOC_INFO_1(ctypes.Structure):
            _fields_ = [
                ("pDocName", wintypes.LPWSTR),
                ("pOutputFile", wintypes.LPWSTR),
                ("pDatatype", wintypes.LPWSTR),
            ]

        handle = wintypes.HANDLE()
        if not winspool.OpenPrinterW(target, ctypes.byref(handle), None):
            raise PrintError(
                "无法打开打印机 / cannot open printer: %s" % target, "printer_not_found"
            )

        try:
            di = DOC_INFO_1()
            di.pDocName = "PrintTheShot"
            # RAW 让字节绕过驱动直抵设备 / RAW sends bytes past the driver to the device
            di.pDatatype = "RAW"
            job_id = winspool.StartDocPrinterW(handle, 1, ctypes.byref(di))
            if job_id == 0:
                raise PrintError("无法创建打印任务 / cannot start print job", "spool_failed")

            try:
                # 按行扫描,无需 DIB 转换 / written row-wise, no DIB conversion needed
                winspool.StartPagePrinter(handle)
                written = wintypes.DWORD(0)
                chunk = 65536
                for offset in range(0, len(payload), chunk):
                    part = payload[offset:offset + chunk]
                    buf = ctypes.create_string_buffer(part)
                    if not winspool.WritePrinter(handle, buf, len(part), ctypes.byref(written)):
                        raise PrintError("写入打印缓冲区失败 / WritePrinter failed", "spool_failed")
                winspool.EndPagePrinter(handle)
            finally:
                winspool.EndDocPrinter(handle)
        finally:
            winspool.ClosePrinter(handle)

        return self._ok("打印任务已发送 / print job sent", printer=target, mode=mode)
