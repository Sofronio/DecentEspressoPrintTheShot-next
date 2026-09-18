#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CUPS 端到端打印测试 / CUPS end-to-end printing test
==================================================

中文
----
前面那些测试验的是「字节拼得对不对」。这一个验的是**真的能打出来** ——
它建一台指向本地监听的虚拟打印机,把任务真的交给 CUPS,然后接住 CUPS 实际
吐出来的字节,逐字节比对。

为什么值得单独做这件事:`lp` 的参数写错、`-o raw` 没生效、PBM 的头写错,
这些在单元测试里全都能「通过」,因为它们只检查我们这边的字符串。只有让 CUPS
真的跑一遍,才知道系统那一侧收不收。

因为 Linux 的打印适配器与 macOS 共用同一份实现(同一个 lp、同一套参数),
这条测试在 macOS 上跑通,等于同时也覆盖了 Linux 的代码路径。它不是 Linux 的
替代品 —— 发行版差异、CUPS 版本差异还是得在 Linux 上验 —— 但它确实覆盖了
绝大部分我们写的那部分逻辑。

前提 / requirements:
    - 本机有 CUPS(lp / lpadmin / lpstat)
    - 当前用户有权建打印机(通常在 lpadmin 组里)
    条件不满足会自动 skip,而不是失败。

跑法 / run:
    python3 tests/test_cups_e2e.py

English
-------
The other tests check whether the bytes are assembled correctly. This one checks
whether they actually print: it creates a virtual printer pointed at a local
listener, submits a real job to CUPS, catches the bytes CUPS actually emits, and
compares them byte for byte.

Why this deserves its own test: a wrong `lp` flag, a `-o raw` that did not take
effect, a malformed PBM header — all of those pass a unit test, because a unit test
only inspects our own strings. Only a real CUPS round trip shows whether the other
side accepts them.

Because the Linux adapter shares its implementation with macOS (same lp, same
flags), a pass here covers the Linux code path too. It is not a substitute for
testing on Linux — distribution and CUPS-version differences still need real
Linux — but it does cover the part we actually wrote.

Requirements:
    - CUPS present (lp / lpadmin / lpstat)
    - permission to add a printer (normally the lpadmin group)
    Skipped, not failed, when either is missing.
"""

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from printers import get_printer                        # noqa: E402
from printers.base import PrintError, bitmap_to_pbm     # noqa: E402

QUEUE = "PTSTestQueue"
PORT = 9155


def cus_available():
    """CUPS 命令行齐全吗 / are the CUPS tools present."""
    return all(shutil.which(x) for x in ("lp", "lpadmin", "lpstat"))


def can_add_printer():
    """有权限建打印机吗 / can we add a printer (dry run)."""
    try:
        r = subprocess.run(
            ["lpadmin", "-p", "__pts_probe__", "-E", "-v", "socket://127.0.0.1:1"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            subprocess.run(["lpadmin", "-x", "__pts_probe__"], capture_output=True, timeout=10)
            return True
    except Exception:
        pass
    return False


def make_test_bitmap(width=64, height=24):
    """
    造一张能人工辨认的测试位图 / build a test bitmap a human can recognise.

    左上角一个 16x8 实心块,之后每行一个右移的点 —— 画出来是「一块正方形加一
    条斜线」。这样万一要人工看图,一眼就知道方向、行序有没有错;而自动比对时
    它就是一段确定的字节。

    A 16x8 solid block in the top-left, then one dot per row shifting right — it
    draws as a square plus a diagonal line. If a human ever has to look at it, the
    orientation and row order are obvious at a glance; for the automated comparison
    it is simply a known byte sequence.
    """
    bytes_per_row = (width + 7) // 8
    # 方块宽度要跟着位图宽度收缩,否则窄位图会越界写
    # The block width has to shrink with the bitmap, or a narrow bitmap writes out of bounds
    block_w = min(16, width)
    block_h = min(8, height)
    rows = []
    for y in range(height):
        row = bytearray(bytes_per_row)
        if y < block_h:
            for x in range(block_w):
                row[x >> 3] |= 0x80 >> (x & 7)
        else:
            x = (y - block_h) * 2
            if x < width:
                row[x >> 3] |= 0x80 >> (x & 7)
        rows.append(bytes(row))
    return b"".join(rows)


class Listener:
    """接收一次连接并把字节收全 / accept one connection and collect the bytes."""

    def __init__(self, port):
        self.port = port
        self.data = bytearray()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", self.port))
            s.listen(1)
            s.settimeout(30)
            self._ready.set()
            conn, _ = s.accept()
            conn.settimeout(8)
            while True:
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                self.data.extend(chunk)
            conn.close()
        except Exception:
            self._ready.set()
        finally:
            s.close()

    def start(self):
        self._thread.start()
        self._ready.wait(timeout=5)
        time.sleep(0.3)     # 给 bind 一点时间 / let the bind settle
        return self

    def finish(self):
        self._thread.join(timeout=40)
        time.sleep(0.3)
        return bytes(self.data)


@unittest.skipUnless(cus_available(), "CUPS 命令行不可用 / CUPS tools unavailable")
@unittest.skipUnless(can_add_printer(), "无权创建打印机 / cannot add a printer")
class CupsEndToEnd(unittest.TestCase):
    """真的把任务交给 CUPS,验它吐出来的字节 / submit to real CUPS, inspect the bytes."""

    @classmethod
    def setUpClass(cls):
        r = subprocess.run(
            ["lpadmin", "-p", QUEUE, "-E", "-v", "socket://127.0.0.1:%d" % PORT],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            raise unittest.SkipTest("无法创建测试打印机 / cannot create the test queue: %s" % r.stderr)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["lpadmin", "-x", QUEUE], capture_output=True, timeout=15)

    def _run_job(self, mode):
        width, height = 64, 24
        bitmap = make_test_bitmap(width, height)
        listener = Listener(PORT).start()
        try:
            result = get_printer().print_bitmap(
                bitmap, width, height, printer=QUEUE, mode=mode)
        except PrintError as e:
            self.fail("打印失败 / printing failed: %s" % e.message)
        received = listener.finish()
        return bitmap, width, height, result, received

    def test_driver_mode_delivers_an_exact_pbm(self):
        """
        driver 模式:CUPS 收到的必须是一份 PBM,而且位图数据逐字节不变 ——
        证明我们没有在头尾多塞东西,也没有在传递中被转码。
        """
        bitmap, width, height, result, received = self._run_job("driver")

        self.assertTrue(result.get("success"), result)
        self.assertTrue(received, "CUPS 应该有内容发出 / CUPS should have emitted something")

        header = b"P4\n%d %d\n" % (width, height)
        self.assertEqual(received[:len(header)], header,
                         "PBM 文件头不对 / wrong PBM header")
        payload = received[len(header):len(header) + len(bitmap)]
        self.assertEqual(payload, bitmap,
                         "位图数据在传输中变了 / the bitmap payload changed in transit")
        self.assertEqual(len(received), len(header) + len(bitmap),
                         "有多余字节 / unexpected extra bytes")

    def test_raw_mode_delivers_an_exact_escpos_job(self):
        """
        raw 模式:字节必须原样直通 —— ESC @ 开头、GS v 0 指令头、然后是位图数据。
        这条同时证明了 `-o raw` 确实生效(否则 CUPS 会去栅格化,收到的东西就不
        是这个了)。
        """
        bitmap, width, height, result, received = self._run_job("raw")

        self.assertTrue(result.get("success"), result)
        bytes_per_row = (width + 7) // 8
        expect = bytes([
            0x1B, 0x40,                                     # ESC @ 复位 / reset
            0x1D, 0x76, 0x30, 0x00,                         # GS v 0 m=0
            bytes_per_row & 0xFF, (bytes_per_row >> 8) & 0xFF,   # xL xH(字节)
            height & 0xFF, (height >> 8) & 0xFF,            # yL yH(点)
        ])
        self.assertEqual(received[:len(expect)], expect,
                         "ESC/POS 指令头不对 / wrong ESC/POS header")
        payload = received[len(expect):len(expect) + len(bitmap)]
        self.assertEqual(payload, bitmap,
                         "位图数据没有原样直通 / the bitmap did not pass through untouched")
        self.assertIn(bytes([0x1D, 0x56, 0x42, 0x00]), received,
                      "缺少切纸指令 / missing the cut command")

    def test_missing_printer_is_reported_rather_than_swallowed(self):
        """
        点名一台不存在的打印机必须报错。CUPS 会把任务静默丢弃,如果不主动检查,
        用户会看到「打印成功」而纸上一片空白 —— 那是最难查的一种失败。
        """
        bitmap = make_test_bitmap(8, 1)
        with self.assertRaises(PrintError) as ctx:
            get_printer().print_bitmap(bitmap, 8, 1, printer="Definitely_Not_A_Printer")
        self.assertEqual(ctx.exception.code, "printer_not_found")
        self.assertIn("Definitely_Not_A_Printer", ctx.exception.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
