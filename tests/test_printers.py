#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打印层自测 / Printer layer self-tests
=====================================

中文
----
不需要真实打印机也能跑:这里验证的是「字节布局」这一类可以纯逻辑校验的东西 ——
ESC/POS 指令头、PBM 文件头、BMP 行对齐与自下而上的行序、以及各种长度校验。

之所以重点测这些,是因为它们一旦错了,症状是「打出来一张错位的纸」,
而不是一个报错 —— 在没有打印机的环境里非常难发现。

跑法 / how to run:
    python3 -m unittest discover -s tests -v
    python3 tests/test_printers.py

English
-------
These run without any real printer: they check things that can be verified
purely logically — the ESC/POS command header, the PBM header, BMP row padding
and bottom-up row order, and the various length validations.

That is the point: when any of these is wrong the symptom is a garbled sheet of
paper rather than an exception, which is very hard to notice without hardware.

Run with:
    python3 -m unittest discover -s tests -v
    python3 tests/test_printers.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from printers import escpos, get_printer, platform_id          # noqa: E402
from printers.base import BasePrinter, PrintError              # noqa: E402
from printers.base import bitmap_to_bmp1, bitmap_to_pbm        # noqa: E402


def make_bitmap(width, height, pattern=None):
    """
    造一张测试位图 / build a test bitmap.

    默认图案:每行一个黑点,逐行右移 —— 便于肉眼和断言同时确认行序没错。
    Default pattern: one black dot per row moving right, which makes a wrong row
    order obvious both to the eye and to an assertion.
    """
    bytes_per_row = (width + 7) // 8
    out = bytearray()
    for y in range(height):
        row = bytearray(bytes_per_row)
        if pattern == "solid":
            for i in range(bytes_per_row):
                row[i] = 0xFF
        else:
            x = y % width
            row[x >> 3] = 0x80 >> (x & 7)
        out += row
    return bytes(out)


class TestEscPos(unittest.TestCase):
    """ESC/POS 指令拼装 / ESC/POS command assembly."""

    def test_raster_header_uses_bytes_for_width_and_dots_for_height(self):
        """
        xL/xH 的单位是字节数,yL/yH 的单位是点数 —— 这是最容易写错的地方。
        xL/xH are in BYTES while yL/yH are in DOTS: the single easiest bug here.
        """
        w, h = 16, 4
        data = make_bitmap(w, h)
        cmd = escpos.raster_bitmap(data, w, h)

        self.assertEqual(cmd[0:4], bytes([0x1D, 0x76, 0x30, 0x00]),
                         "must be GS v 0 with m=0")
        self.assertEqual(cmd[4], 2, "xL must be width in BYTES (16/8 = 2)")
        self.assertEqual(cmd[5], 0, "xH must be 0")
        self.assertEqual(cmd[6], 4, "yL must be height in DOTS (4)")
        self.assertEqual(cmd[7], 0, "yH must be 0")
        self.assertEqual(cmd[8:], data, "payload must pass through untouched")

    def test_non_byte_aligned_width_rounds_up(self):
        """宽度不是 8 的整数倍时,每行要补齐到整字节 / width rounds up to whole bytes."""
        w, h = 20, 3
        bytes_per_row = (w + 7) // 8          # 3
        data = make_bitmap(w, h)
        self.assertEqual(len(data), bytes_per_row * h)
        cmd = escpos.raster_bitmap(data, w, h)
        self.assertEqual(cmd[4], 3, "20 dots -> 3 bytes per row")

    def test_large_height_splits_into_two_bytes(self):
        """高度超过 255 时高位字节要接上 / heights above 255 must carry into yH."""
        w, h = 8, 300
        data = make_bitmap(w, h)
        cmd = escpos.raster_bitmap(data, w, h)
        self.assertEqual(cmd[6], 300 & 0xFF, "yL is the low byte")
        self.assertEqual(cmd[7], (300 >> 8) & 0xFF, "yH is the high byte")

    def test_length_mismatch_is_rejected(self):
        """长度不符必须报错,不能默默打出一张错位的纸 / a mismatch must raise, never print garbage."""
        for bad in (b"", b"abc", b"x" * 7, b"x" * 9):
            with self.assertRaises(ValueError):
                escpos.raster_bitmap(bad, 16, 4)

    def test_invalid_size_is_rejected(self):
        with self.assertRaises(ValueError):
            escpos.raster_bitmap(b"", 0, 4)
        with self.assertRaises(ValueError):
            escpos.raster_bitmap(b"", 16, 0)

    def test_print_job_wraps_bitmap_with_reset_and_cut(self):
        w, h = 8, 2
        job = escpos.build_print_job(make_bitmap(w, h), w, h, feed_lines=3, do_cut=True)
        self.assertTrue(job.startswith(bytes([0x1B, 0x40])), "starts with ESC @ (reset)")
        self.assertIn(bytes([0x1D, 0x56, 0x42, 0x00]), job, "contains a partial cut")
        self.assertIn(bytes([0x1B, 0x64, 3]), job, "feeds 3 lines")

    def test_print_job_can_skip_cut(self):
        """便携机常不支持切纸,必须能关掉 / portable units often reject the cut command."""
        job = escpos.build_print_job(make_bitmap(8, 2), 8, 2, do_cut=False)
        self.assertNotIn(bytes([0x1D, 0x56, 0x42, 0x00]), job)


class TestPbm(unittest.TestCase):
    """PBM(P4)输出 / PBM (P4) output."""

    def test_header_and_payload(self):
        w, h = 16, 4
        data = make_bitmap(w, h)
        pbm = bitmap_to_pbm(data, w, h)
        self.assertTrue(pbm.startswith(b"P4\n16 4\n"), "P4 magic and dimensions")
        self.assertEqual(pbm[len(b"P4\n16 4\n"):], data, "payload is untouched")


class TestBmp1(unittest.TestCase):
    """1-bit BMP 输出 / 1-bit BMP output."""

    def test_file_header_and_size(self):
        w, h = 16, 4
        data = make_bitmap(w, h)
        bmp = bitmap_to_bmp1(data, w, h)
        self.assertEqual(bmp[:2], b"BM", "BM magic")
        self.assertEqual(int.from_bytes(bmp[2:6], "little"), len(bmp),
                         "the size field must match the real file length")

    def test_rows_are_padded_to_four_bytes(self):
        """
        BMP 每行按 4 字节对齐(不是 8)—— 漏掉这一步图会整体斜掉。
        BMP rows pad to 4 bytes, not 8; missing this skews the whole image.
        """
        w, h = 20, 3
        data = make_bitmap(w, h)
        bmp = bitmap_to_bmp1(data, w, h)
        packed = (w + 7) // 8               # 3
        padded = (packed + 3) & ~3          # 4
        self.assertEqual(padded, 4)
        body = bmp[14 + 40 + 8:]
        self.assertEqual(len(body), padded * h)
        # 第一行应是源数据的最后一行(BMP 自下而上)
        # the first row must be the source's last row (BMP is bottom-up)
        first = data[(h - 1) * packed:(h - 1) * packed + packed]
        self.assertEqual(body[:packed], first, "BMP pixel rows run bottom-to-top")
        self.assertEqual(body[packed:padded], b"\x00", "row padding is zeroed")


class TestBasePrinterValidation(unittest.TestCase):
    """接口层校验 / interface-level validation."""

    def test_validate_bitmap_accepts_correct_size(self):
        p = BasePrinter()
        self.assertTrue(p.validate_bitmap(make_bitmap(16, 4), 16, 4))

    def test_validate_bitmap_rejects_wrong_size(self):
        p = BasePrinter()
        with self.assertRaises(PrintError):
            p.validate_bitmap(make_bitmap(16, 4), 20, 4)

    def test_validate_bitmap_rejects_bad_dimensions(self):
        p = BasePrinter()
        for w, h in ((0, 4), (16, 0), (-1, 4), (16, -1)):
            with self.assertRaises(PrintError):
                p.validate_bitmap(b"", w, h)

    def test_base_printer_print_is_not_implemented(self):
        """基类不能假装能打印 / the base class must not pretend it can print."""
        with self.assertRaises(PrintError):
            BasePrinter().print_bitmap(b"", 8, 1)


class TestAdapterLoading(unittest.TestCase):
    """适配器装载 / adapter loading."""

    def test_platform_id_is_recognised(self):
        self.assertIn(platform_id(), ("mac", "win", "linux", "android", "unknown"))

    def test_get_printer_returns_singleton(self):
        self.assertIs(get_printer(), get_printer(), "must be a process-wide singleton")

    def test_adapter_exposes_the_full_interface(self):
        """不管装载到哪个平台,接口形状必须一致 / whatever loads must expose the same shape."""
        p = get_printer()
        for method in ("is_available", "list_printers", "print_bitmap", "capabilities"):
            self.assertTrue(callable(getattr(p, method)), "%s must be callable" % method)
        caps = p.capabilities()
        for key in ("platform", "display_name", "available", "raw"):
            self.assertIn(key, caps)

    def test_list_printers_does_not_raise_when_there_is_no_printer(self):
        """
        没装打印机时应该返回空列表,而不是抛错 —— 服务端启动时就会调它。
        Returning an empty list (not raising) matters: startup calls this.
        """
        printers = get_printer().list_printers()
        self.assertIsInstance(printers, list)
        for entry in printers:
            for key in ("id", "name", "status", "default"):
                self.assertIn(key, entry)


if __name__ == "__main__":
    unittest.main(verbosity=2)
