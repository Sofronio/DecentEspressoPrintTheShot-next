#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一打印接口 / Unified printing interface
=========================================

中文
----
本模块定义平台无关的打印抽象。上层业务代码(HTTP 服务)只调用这里定义的接口,
完全不知道底下是 macOS 的 CUPS、Windows 的 GDI、Linux 的 lp,还是 Android 的
蓝牙 ESC/POS。

所有平台适配器都必须实现 `BasePrinter`,并且遵守同一条数据契约:

    位图 = 1-bit 单色、每行按 MSB-first 打包、行宽向上补齐到 8 的整数倍

这正是 `web/render.js` 里 `canvasToBitmap()` 的输出格式,也是 ESC/POS 的
`GS v 0` 光栅位图指令所需的格式,所以从浏览器到打印机全程不需要重新编码。

English
-------
This module defines the platform-independent printing abstraction. Upper layers
(the HTTP service) only ever call the interface declared here and never need to
know whether the backend is CUPS on macOS, GDI on Windows, lp on Linux, or
Bluetooth ESC/POS on Android.

Every platform adapter must implement `BasePrinter` and honour the same data
contract:

    bitmap = 1-bit monochrome, each row packed MSB-first, row width padded up to
             a whole number of bytes

That is exactly what `canvasToBitmap()` in `web/render.js` produces and exactly
what the ESC/POS `GS v 0` raster-bitmap command wants, so nothing has to be
re-encoded anywhere between the browser and the printer.
"""

from __future__ import annotations


class PrintError(Exception):
    """打印失败 / a printing failure."""

    def __init__(self, message, code="print_error"):
        super().__init__(message)
        self.message = message
        self.code = code


class BasePrinter:
    """
    所有打印适配器的基类 / base class for every printing adapter.

    子类必须实现 / subclasses must implement:
        is_available()            — 当前环境能否使用 / whether usable here
        list_printers()           — 可用打印机列表 / available printers
        print_bitmap(...)         — 实际打印 / actually print
    """

    #: 平台标识,用于日志与 /api/printers 输出 / platform id, for logs and /api/printers
    platform_id = "unknown"
    #: 人类可读名称 / human-readable name
    display_name = "Unknown Printer"

    def __init__(self, config=None):
        #: 适配器配置(打印机名、纸张尺寸、超时等)/ adapter config
        self.config = config or {}

    # ------------------------------------------------------------------ 能力
    # ------------------------------------------------------------------ capability

    def is_available(self):
        """当前环境是否可用 / whether this adapter can be used in this environment."""
        return False

    def supports_raw(self):
        """
        是否支持「原始字节直通」模式(ESC/POS 原样送给打印机,不经驱动栅格化)。
        Whether raw pass-through is supported (ESC/POS bytes go straight to the
        device without driver rasterisation).
        """
        return False

    def capabilities(self):
        """给前端的能力描述 / capability description handed to the front end."""
        return {
            "platform": self.platform_id,
            "display_name": self.display_name,
            "available": self.is_available(),
            "raw": self.supports_raw(),
        }

    # ------------------------------------------------------------------ 枚举
    # ------------------------------------------------------------------ enumeration

    def list_printers(self):
        """
        返回可用打印机列表 / return the list of available printers.

        [{"id": "...", "name": "...", "default": True/False, "status": "..."}]
        """
        return []

    def default_printer(self):
        """默认打印机 id,没有则返回 None / default printer id, or None."""
        for p in self.list_printers():
            if p.get("default"):
                return p["id"]
        printers = self.list_printers()
        return printers[0]["id"] if printers else None

    # ------------------------------------------------------------------ 打印
    # ------------------------------------------------------------------ printing

    def print_bitmap(self, bitmap, width, height, printer=None, **kwargs):
        """
        打印一张 1-bit 位图 / print one 1-bit bitmap.

        参数 / arguments:
            bitmap  (bytes)  MSB-first 打包的 1-bit 数据 / packed 1-bit data
            width   (int)    位图宽度(点)/ bitmap width in dots
            height  (int)    位图高度(点)/ bitmap height in dots
            printer (str)    目标打印机 id,None = 默认 / target printer id

        返回 / returns:
            {"success": bool, "message": str, "printer": str|None}

        失败时抛 PrintError / raises PrintError on failure.
        """
        raise PrintError("print_bitmap() not implemented", "not_implemented")

    # ------------------------------------------------------------------ 工具
    # ------------------------------------------------------------------ helpers

    def _ok(self, message, printer=None, **extra):
        result = {"success": True, "message": message, "printer": printer}
        result.update(extra)
        return result

    @staticmethod
    def validate_bitmap(bitmap, width, height):
        """
        校验位图数据尺寸是否自洽 / sanity-check the bitmap data size.

        期望长度 = ceil(width/8) * height。不符就说明前后端对格式的理解不一致,
        这时候必须直接报错,而不是打出一张错位的图。
        Expected length is ceil(width/8) * height. A mismatch means the two ends
        disagree about the format, and it is better to fail loudly than to print
        a garbled sheet.
        """
        if width <= 0 or height <= 0:
            raise PrintError("位图尺寸非法 / invalid bitmap size: %sx%s" % (width, height), "bad_bitmap")
        expected = ((width + 7) // 8) * height
        actual = len(bitmap)
        if actual != expected:
            raise PrintError(
                "位图数据长度不符 / bitmap length mismatch: "
                "expected %d bytes for %dx%d, got %d" % (expected, width, height, actual),
                "bad_bitmap",
            )
        return True


def bitmap_to_bmp1(bitmap, width, height):
    """
    把 1-bit 位图包成单色 BMP 文件 / wrap a 1-bit bitmap as a 1-bit BMP file.

    仅用于 Windows 的传统兼容路径:旧版是把一张 1-bit BMP 原样塞进打印队列的,
    这个函数复刻那种字节布局,不需要 Pillow。

    Used only by the Windows legacy-compatibility path: the previous version fed a
    1-bit BMP straight into the spooler, and this reproduces that byte layout
    without needing Pillow.

    注意 BMP 的两个坑 / two BMP gotchas:
      - 每行按 4 字节对齐(不是 8)/ rows are padded to 4 bytes, not 8
      - 像素行自下而上 / pixel rows run bottom-to-top
      - 单色 BMP 的调色板里 0=黑、1=白,和直觉相反
        in a 1-bit BMP palette index 0 is black and 1 is white — the opposite of
        what you would guess
    """
    row_bytes_packed = (width + 7) // 8
    row_padded = (row_bytes_packed + 3) & ~3   # 4 字节对齐 / 4-byte alignment
    palette = bytes([0, 0, 0, 0, 255, 255, 255, 0])  # 黑, 白 / black, white
    pixel_offset = 14 + 40 + len(palette)
    image_size = row_padded * height
    file_size = pixel_offset + image_size

    header = bytearray()
    # BITMAPFILEHEADER / BMP file header
    header += b"BM"
    header += file_size.to_bytes(4, "little")
    header += (0).to_bytes(4, "little")
    header += pixel_offset.to_bytes(4, "little")
    # BITMAPINFOHEADER / DIB header
    header += (40).to_bytes(4, "little")
    header += width.to_bytes(4, "little", signed=True)
    header += height.to_bytes(4, "little", signed=True)   # 正数=自下而上 / positive = bottom-up
    header += (1).to_bytes(2, "little")                   # planes
    header += (1).to_bytes(2, "little")                   # bpp
    header += (0).to_bytes(4, "little")                   # 无压缩 / no compression
    header += image_size.to_bytes(4, "little")
    header += (2835).to_bytes(4, "little", signed=True)   # 水平 DPI ≈ 72 / horizontal DPI
    header += (2835).to_bytes(4, "little", signed=True)   # 垂直 DPI ≈ 72 / vertical DPI
    header += (0).to_bytes(4, "little")
    header += (0).to_bytes(4, "little")
    header += palette

    body = bytearray()
    for y in range(height - 1, -1, -1):   # 自下而上 / bottom-up
        start = y * row_bytes_packed
        row = bytes(bitmap[start:start + row_bytes_packed])
        body += row + b"\x00" * (row_padded - row_bytes_packed)
    return bytes(header) + bytes(body)


def bitmap_to_pbm(bitmap, width, height):
    """
    把 1-bit 位图包成 PBM(P4)文件内容 / wrap a 1-bit bitmap as a PBM (P4) file.

    PBM P4 的位序与 ESC/POS 完全一致(MSB-first,每行补齐到整字节),
    所以这里只需要加一个文件头,不用动数据本身。

    PBM P4 uses the same bit order as ESC/POS (MSB-first, rows padded to whole
    bytes), so only a header has to be added — the payload is untouched.

    这也是交给 CUPS 时最省事的格式:PBM 是 CUPS 原生支持的栅格格式之一。
    It is also the easiest format to hand to CUPS, which understands PBM natively.
    """
    header = ("P4\n%d %d\n" % (width, height)).encode("ascii")
    return header + bitmap
