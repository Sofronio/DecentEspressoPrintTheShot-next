#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ESC/POS 指令封装 / ESC/POS command helpers
==========================================

中文
----
把 1-bit 位图打包成 ESC/POS 的 `GS v 0` 光栅位图指令。这是热敏小票机的通用
指令,Mac(原始模式)、Linux、Android 蓝牙三条路径共用同一份实现,避免同一段
字节拼装逻辑写三遍、改三处。

指令格式 / command format:

    GS v 0 m xL xH yL yH d1...dk

    1D 76 30 m xL xH yL yH d1...dk

    m  = 0    正常模式 / normal mode
    xL = 宽度低字节,xH = 宽度高字节(单位:字节,不是点)/ width in BYTES, low then high
    yL = 高度低字节,yH = 高度高字节(单位:点)/ height in DOTS, low then high
    d  = 位图数据,MSB-first,每行补齐到整字节
         bitmap data, MSB-first, each row padded to a whole number of bytes

注意 xL/xH 的单位是「字节数」,也就是 ceil(width/8),不是像素宽度 —— 这是
最容易写错的地方。

Note that xL/xH are in BYTES (ceil(width/8)), not in dots. This is the single
easiest thing to get wrong.

English
-------
Packs a 1-bit bitmap into the ESC/POS `GS v 0` raster-bitmap command. This is
the common command set for thermal receipt printers, and macOS (raw mode), Linux
and Android Bluetooth all share this one implementation so the byte layout is
written — and fixed — in exactly one place.
"""

from __future__ import annotations

# 指令常量 / command bytes
GS = 0x1D
ESC = 0x1B

# 对齐 / alignment
ALIGN_LEFT = 0
ALIGN_CENTER = 1
ALIGN_RIGHT = 2


def _le16(value):
    """16 位小端 / 16-bit little-endian."""
    return bytes([value & 0xFF, (value >> 8) & 0xFF])


def initialize():
    """ESC @ — 复位打印机 / reset the printer."""
    return bytes([ESC, 0x40])


def align(mode):
    """ESC a n — 设置对齐 / set justification."""
    return bytes([ESC, 0x61, mode & 0x03])


def feed(lines=1):
    """ESC d n — 走纸 n 行 / feed n lines."""
    return bytes([ESC, 0x64, max(0, min(255, int(lines)))])


def cut(partial=False):
    """
    GS V — 切纸 / cut the paper.

    部分切纸(留一点不切断)在多数机型上更可靠,默认用它。
    Partial cut is more reliable on most hardware, so it is the default.
    """
    if partial:
        return bytes([GS, 0x56, 0x42, 0x00])
    return bytes([GS, 0x56, 0x00])


def raster_bitmap(bitmap, width, height):
    """
    生成 `GS v 0` 光栅位图指令 / build a `GS v 0` raster-bitmap command.

    参数 / arguments:
        bitmap (bytes) MSB-first 打包的 1-bit 数据 / packed 1-bit data
        width  (int)   位图宽度(点)/ width in dots
        height (int)   位图高度(点)/ height in dots

    返回 / returns:
        bytes — 完整的指令序列 / the complete command sequence

    数据已经是 MSB-first 且按行补齐到整字节,所以这里直接拼头部即可,不需要
    再做任何按位搬移。
    The payload is already MSB-first and row-padded to whole bytes, so only the
    header has to be assembled — no bit shuffling is needed here.
    """
    if width <= 0 or height <= 0:
        raise ValueError("位图尺寸非法 / invalid bitmap size: %sx%s" % (width, height))

    width_bytes = (width + 7) // 8
    expected = width_bytes * height
    if len(bitmap) != expected:
        raise ValueError(
            "位图数据长度不符 / bitmap length mismatch: expected %d, got %d"
            % (expected, len(bitmap))
        )

    header = bytes([GS, 0x76, 0x30, 0x00]) + _le16(width_bytes) + _le16(height)
    return header + bytes(bitmap)


def build_print_job(bitmap, width, height, feed_lines=3, do_cut=True):
    """
    组装一份完整的打印任务:复位 → 位图 → 走纸 → 切纸。
    Assemble a complete print job: reset → bitmap → feed → cut.

    切纸指令在部分便携机上不被支持,所以允许关掉。
    Some portable units reject the cut command, hence the switch.
    """
    job = bytearray()
    job += initialize()
    job += raster_bitmap(bitmap, width, height)
    job += feed(feed_lines)
    if do_cut:
        job += cut(partial=True)
    return bytes(job)
