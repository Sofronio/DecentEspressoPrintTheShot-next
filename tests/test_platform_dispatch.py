#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
平台分派与跨平台可测部分 / Platform dispatch and the cross-platform-testable parts
=================================================================================

中文
----
Windows 和 Linux 的代码没法在本机整体跑起来,但其中相当一部分是**可以**测的,
而且是最容易写错的那部分:

  - 平台探测:sys.platform 到底会挑中哪个适配器
  - Windows 的载荷构造:送进打印队列的字节(ESC/POS / BMP)
  - 在错误的平台上调用必须明确报错,而不是抛一个看不懂的 AttributeError

测不了的部分在文件末尾如实列出来了 —— 写清楚「哪些没验证」比让读者以为
全都验证过要重要得多。

English
-------
The Windows and Linux code cannot be run end to end on this machine, but a good
deal of it — the parts most likely to be wrong — can still be tested:

  - platform detection: which adapter sys.platform actually selects
  - the Windows payload construction: the bytes handed to the spooler
  - calling on the wrong platform must fail clearly rather than raising an opaque
    AttributeError

What cannot be tested is listed explicitly at the end of this file. Being clear
about what is unverified matters more than letting the reader assume everything was.

跑法 / run:
    python3 tests/test_platform_dispatch.py
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import printers                                              # noqa: E402
from printers import NullPrinter, load_printer, platform_id  # noqa: E402
from printers.base import PrintError                         # noqa: E402
from printers.windows_printer import WindowsPrinter          # noqa: E402
from printers.linux_printer import LinuxPrinter              # noqa: E402
from printers.mac_printer import MacPrinter                  # noqa: E402


def bitmap(width, height, fill=0x00):
    """长度自洽的位图 / a length-correct bitmap."""
    return bytes([fill]) * (((width + 7) // 8) * height)


class TestPlatformDetection(unittest.TestCase):
    """
    平台探测 / platform detection.

    这块写错了的后果是整条打印链路失效,而且症状取决于跑在哪台机器上 ——
    所以四个平台都要显式验一遍,而不是只验当前这台。
    A mistake here breaks the whole print path, and the symptom depends on which
    machine you are on — so all four platforms are checked explicitly rather than
    only whichever one we happen to be running on.
    """

    def test_mac(self):
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(platform_id(), "mac")
            self.assertIsInstance(load_printer(), MacPrinter)

    def test_linux(self):
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANDROID_ROOT", None)
            self.assertEqual(platform_id(), "linux")
            self.assertIsInstance(load_printer(), LinuxPrinter)

    def test_windows(self):
        for value in ("win32", "win64", "cygwin"):
            with mock.patch.object(sys, "platform", value):
                self.assertEqual(platform_id(), "win", value)
                self.assertIsInstance(load_printer(), WindowsPrinter)

    def test_android_is_distinguished_from_linux(self):
        """
        Android 的 sys.platform 也是 "linux",只能靠环境里特有的东西区分。
        分错了的后果是 Android 上去找一个根本不存在的 CUPS。
        Android also reports sys.platform == "linux" and is told apart only by
        environment markers. Getting this wrong sends Android looking for a CUPS
        that does not exist there.
        """
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch.dict(os.environ, {"ANDROID_ROOT": "/system"}):
            self.assertEqual(platform_id(), "android")
            self.assertEqual(load_printer().platform_id, "android")

    def test_unknown_platform_falls_back_to_null(self):
        """认不出来的平台必须明确地说「没有适配器」,而不是随便挑一个。"""
        with mock.patch.object(sys, "platform", "haiku"):
            self.assertEqual(platform_id(), "unknown")
            self.assertIsInstance(load_printer(), NullPrinter)

    def test_null_printer_refuses_clearly(self):
        with self.assertRaises(PrintError) as ctx:
            NullPrinter().print_bitmap(bitmap(8, 1), 8, 1)
        self.assertEqual(ctx.exception.code, "no_adapter")


class TestLinuxReusesMac(unittest.TestCase):
    """
    Linux 与 macOS 共用同一份实现 / Linux shares its implementation with macOS.

    这不是偷懒,是有意的:CUPS 就是 CUPS,同一套 lp 命令、同一套参数。分成
    两份写,迟早会有一份忘了改。这条断言把这个决定固定下来。
    Not laziness but a decision: CUPS is CUPS — same lp, same flags. Written twice,
    one copy eventually goes stale. This assertion pins the decision down.
    """

    def test_shares_the_cups_implementation(self):
        self.assertTrue(issubclass(LinuxPrinter, MacPrinter))
        # 检查 LinuxPrinter 自己的 __dict__ 里有没有覆盖 —— 这才是「没重写」的
        # 正确判据。用 getattr 取到的是新建的绑定方法对象,`is` 永远为假。
        # Check LinuxPrinter's own __dict__ for an override — that is the right test
        # for "not reimplemented". getattr returns a freshly built bound method, so
        # an `is` comparison is always false.
        for name in ("list_printers", "print_bitmap", "_build_command",
                     "_lp_binary", "_lpstat", "_parse_printer_line"):
            self.assertNotIn(name, LinuxPrinter.__dict__,
                             "%s should not be reimplemented for Linux" % name)

    def test_identifies_itself_as_linux(self):
        p = LinuxPrinter()
        self.assertEqual(p.platform_id, "linux")
        self.assertIn("Linux", p.display_name)
        self.assertTrue(p.supports_raw())


class TestLocalizedPrinterParsing(unittest.TestCase):
    """
    本地化的 lpstat 输出 / localized lpstat output.

    这是真踩过的坑:中文 macOS 上 `lpstat -p` 打的是「打印机XXX闲置…」,原来按
    `"printer "` 前缀匹配的实现一台都找不到,而且不报错。
    A bug actually hit in practice: on a Chinese macOS `lpstat -p` prints
    「打印机XXX闲置…」 and the old `"printer "` prefix match found nothing at all,
    without reporting an error.
    """

    CASES = [
        # (lpstat -p 的一行, 期望的打印机名)
        ("printer Brother_QL_570 is idle.  enabled since Sat", "Brother_QL_570"),
        ("printer Printer_POS_80 is idle.  enabled since Fri 11/ 7", "Printer_POS_80"),
        ("printer HP_LaserJet_P1008 is idle.  enabled since Mon", "HP_LaserJet_P1008"),
        ("打印机Printer_POS_80闲置，启用时间始于六 11/ 8 14:27:21 2025", "Printer_POS_80"),
        ("打印机HP_LaserJet_P1008闲置，启用时间始于六  6/20 17:05:44 2026", "HP_LaserJet_P1008"),
        ("打印机_192_168_0_108_2闲置，启用时间始于一  8/31 06:47:16 2026", "_192_168_0_108_2"),
        ("打印机SPRT_Printer闲置，启用时间始于五 11/ 7 21:45:52 2025", "SPRT_Printer"),
    ]

    def setUp(self):
        self.p = MacPrinter()

    def test_printer_names_parse_in_both_languages(self):
        for line, want in self.CASES:
            self.assertEqual(MacPrinter._parse_printer_line(line), want,
                             "failed on: %s" % line)

    def test_dotted_and_underscored_names_survive(self):
        for name in ("printer.master", "HP-Office_1@host", "a.b.c-d_e"):
            self.assertEqual(
                MacPrinter._parse_printer_line("printer %s is idle." % name), name)

    def test_default_printer_parses_in_both_languages(self):
        self.assertEqual(
            self.p._parse_default("system default destination: Printer_POS_80"),
            "Printer_POS_80")
        # 中文用的是全角冒号,按半角冒号切分会失败
        # The Chinese form uses a full-width colon, so splitting on ':' fails
        self.assertEqual(
            self.p._parse_default("系统默认目的位置：HP_LaserJet_P1008"),
            "HP_LaserJet_P1008")

    def test_empty_output_is_not_a_crash(self):
        self.assertIsNone(self.p._parse_default(""))
        self.assertIsNone(MacPrinter._parse_printer_line(""))
        self.assertIsNone(MacPrinter._parse_printer_line("打印机闲置"))


class TestWindowsPayload(unittest.TestCase):
    """
    Windows 的载荷构造 / the Windows payload construction.

    这是 Windows 适配器里唯一能脱离 Windows 测的部分 —— 也正是最该测的部分,
    因为它决定了纸上印出来什么。真正调 spooler 的那几行测不了,见文件末尾。
    The only part of the Windows adapter testable off Windows, and the part most
    worth testing: it decides what ends up on the paper. The actual spooler calls
    cannot be tested here — see the end of this file.
    """

    def setUp(self):
        self.p = WindowsPrinter()

    def test_escpos_is_the_default_mode(self):
        payload, mode = self.p.build_payload(bitmap(16, 4), 16, 4)
        self.assertEqual(mode, "escpos")
        self.assertEqual(payload[:2], bytes([0x1B, 0x40]), "should start with ESC @")

    def test_escpos_header_is_correct(self):
        """xL/xH 是字节数,yL/yH 是点数 —— 最容易写错的地方。"""
        w, h = 64, 300
        payload, _ = self.p.build_payload(bitmap(w, h), w, h, mode="escpos")
        self.assertEqual(payload[2:6], bytes([0x1D, 0x76, 0x30, 0x00]))
        self.assertEqual(payload[6], 8, "64 dots -> 8 bytes per row")
        self.assertEqual(payload[7], 0)
        self.assertEqual(payload[8], 300 & 0xFF)
        self.assertEqual(payload[9], (300 >> 8) & 0xFF)

    def test_bmp_mode_produces_a_valid_bmp(self):
        w, h = 20, 3        # 20 点 -> 3 字节 -> 补齐到 4 字节 / 20 dots -> 3 bytes -> padded to 4
        payload, mode = self.p.build_payload(bitmap(w, h), w, h, mode="bmp")
        self.assertEqual(mode, "bmp")
        self.assertEqual(payload[:2], b"BM")
        self.assertEqual(int.from_bytes(payload[2:6], "little"), len(payload),
                         "the BMP size field must match the real length")
        # 14 (文件头) + 40 (DIB头) + 8 (调色板) = 62
        self.assertEqual(len(payload) - 62, 4 * h,
                         "BMP rows pad to 4 bytes, not to a byte boundary")

    def test_rejects_a_size_mismatch(self):
        with self.assertRaises(PrintError):
            self.p.build_payload(bitmap(16, 4), 20, 4)

    def test_rejects_bad_dimensions(self):
        for w, h in ((0, 4), (16, 0), (-8, 4)):
            with self.assertRaises(PrintError):
                self.p.build_payload(b"", w, h)

    def test_cut_can_be_disabled(self):
        p = WindowsPrinter({"cut": False})
        payload, _ = p.build_payload(bitmap(8, 2), 8, 2, mode="escpos")
        self.assertNotIn(bytes([0x1D, 0x56, 0x42, 0x00]), payload)


class TestOffPlatformBehaviour(unittest.TestCase):
    """在错误的平台上调用必须明确报错,不能含糊。"""

    def test_windows_adapter_refuses_off_windows(self):
        p = WindowsPrinter()
        self.assertFalse(p.is_available(), "not on Windows, so it must say so")
        self.assertEqual(p.list_printers(), [], "and list nothing rather than raise")
        with self.assertRaises(PrintError) as ctx:
            p.print_bitmap(bitmap(8, 1), 8, 1)
        self.assertEqual(ctx.exception.code, "wrong_platform")

    def test_every_adapter_exposes_the_same_interface(self):
        """接口形状不一致的话,上层就得到处写 if platform。"""
        from printers.android_printer import AndroidPrinter
        for cls in (MacPrinter, LinuxPrinter, WindowsPrinter, AndroidPrinter, NullPrinter):
            p = cls()
            for method in ("is_available", "supports_raw", "capabilities",
                           "list_printers", "default_printer", "print_bitmap"):
                self.assertTrue(callable(getattr(p, method, None)),
                                "%s is missing %s" % (cls.__name__, method))
            caps = p.capabilities()
            for key in ("platform", "display_name", "available", "raw"):
                self.assertIn(key, caps, "%s capabilities lacks %s" % (cls.__name__, key))


# ---------------------------------------------------------------------------
# 本文件**没有**覆盖的部分 / what this file does NOT cover
# ---------------------------------------------------------------------------
# 写在这里而不是留在读者心里猜 —— 「哪些没验证」和「哪些验证了」一样重要。
# Spelled out here rather than left for the reader to guess: what is unverified
# matters as much as what is verified.
#
# Windows:
#   - ctypes.windll.winspool 的全部调用(OpenPrinterW / StartDocPrinterW /
#     StartPagePrinter / WritePrinter / EndDocPrinter / ClosePrinter)。
#     这些符号在 macOS 上根本不存在,连 import 都过不去。
#   - DOC_INFO_1 结构体的字段布局与宽字符(LPWSTR)传递。
#   - EnumPrintersW 的缓冲区协商:先问大小、再分配、再枚举这一段。
#   - "RAW" 数据类型是否被目标打印机驱动接受。这正是 bmp 兼容模式存在的原因:
#     如果某个环境下 escpos 不被接受,还可以退回旧版字节布局。
#   - 分块(65536 字节)写入在大位图上的行为。
#
# Linux:
#   - 测试是在 macOS 的 CUPS 上跑的。CUPS 的 lp/lpstat 命令行在 Linux 上是同一套,
#     CUPS 的 PBM 栅格化路径也是同一套,所以覆盖了绝大部分我们写的逻辑;
#     但各发行版的 CUPS 版本差异、以及某些精简发行版只有 lpr 没有 lp 的情况,
#     仍然需要真的在 Linux 上验。
#   - 树莓派等 ARM 板上 CUPS 需要手动安装,is_available() 会如实返回 False。
#
# Android:
#   - 蓝牙 SPP 的全部真实行为。见 android/README.md 里同样的一份清单。
#
# 没有真实打印机,以上都无法在此环境验证。字节层面的正确性有测试覆盖,
# 纸面上的正确性没有 —— 这个区别很重要。
#
# Windows:
#   - every ctypes.windll.winspool call (OpenPrinterW / StartDocPrinterW /
#     StartPagePrinter / WritePrinter / EndDocPrinter / ClosePrinter). Those
#     symbols do not exist on macOS; the import alone would fail.
#   - the DOC_INFO_1 struct layout and wide-character (LPWSTR) passing.
#   - EnumPrintersW buffer negotiation (size probe, allocate, enumerate).
#   - whether the target driver accepts the "RAW" datatype. That is exactly why
#     the bmp compatibility mode exists: if escpos is rejected somewhere, the old
#     byte layout is still available.
#   - chunked writes (65536 bytes) on very tall bitmaps.
#
# Linux:
#   - tested through CUPS on macOS. lp/lpstat are the same command-line tools on
#     Linux and the PBM rasterisation path is the same, which covers most of what
#     we wrote; but distribution and CUPS-version differences, and minimal distros
#     that ship lpr without lp, still need real Linux.
#   - CUPS has to be installed by hand on ARM boards; is_available() reports False.
#
# Android:
#   - all real Bluetooth SPP behaviour. See the matching list in android/README.md.
#
# None of the above can be verified here without a real printer. Byte-level
# correctness is covered; paper-level correctness is not. The distinction matters.


if __name__ == "__main__":
    unittest.main(verbosity=2)
