#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
macOS 打印适配 / macOS printing adapter
=======================================

中文
----
通过 CUPS 的命令行工具(lp / lpr / lpstat)驱动打印机。macOS 自带 CUPS,
不需要任何额外的 Python 依赖,也不需要 pyobjc。

两种打印模式 / two printing modes:

  driver 模式(默认)
      把 1-bit 位图写成 PBM(P4)临时文件,交给 `lp`,并指定自定义纸张
      Custom.80x180mm 与 fit-to-page,让 CUPS 的打印机驱动去做栅格化。
      适合在系统里装好了驱动的小票机(和旧版行为一致)。

  raw 模式
      把位图打包成 ESC/POS 指令,用 `lp -o raw` 原样直通给打印机。
      适合打印机在系统里被配置成 Raw 队列、由打印机自己解释指令的场景。
      这条路径和 Android 蓝牙完全一致,所以两端的出纸效果相同。

English
-------
Drives printers through CUPS command-line tools (lp / lpr / lpstat). macOS
ships CUPS, so there are no extra Python dependencies and no pyobjc.

  driver mode (default)
      Writes the 1-bit bitmap to a temporary PBM (P4) file and hands it to `lp`
      with the custom 80x180mm paper and fit-to-page, letting the CUPS printer
      driver do the rasterisation. Right for a thermal printer that has a real
      driver installed, and matches the previous behaviour.

  raw mode
      Packs the bitmap into ESC/POS and passes it straight through with
      `lp -o raw`, for a printer configured as a Raw queue that interprets the
      commands itself. This path is byte-for-byte identical to the Android
      Bluetooth one, so both platforms produce the same sheet.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

from . import escpos
from .base import BasePrinter, PrintError, bitmap_to_pbm

#: 默认纸张。80mm 热敏纸常见规格;用户可在设置里改。
#: Default paper. The usual size for 80mm thermal stock; overridable in settings.
DEFAULT_MEDIA = "Custom.80x180mm"


class MacPrinter(BasePrinter):
    """macOS(CUPS)打印适配器 / macOS (CUPS) printing adapter."""

    platform_id = "mac"
    display_name = "macOS (CUPS)"

    def __init__(self, config=None):
        super().__init__(config)
        self.media = self.config.get("media") or DEFAULT_MEDIA
        self.timeout = int(self.config.get("timeout", 30))
        # 打印模式:driver / raw,见模块说明 / printing mode, see the module docstring
        self.mode = (self.config.get("mode") or "driver").lower()
        # 送纸后是否走纸再切 / whether to feed and cut after the bitmap
        self.feed_lines = int(self.config.get("feed_lines", 3))
        self.do_cut = bool(self.config.get("cut", True))

    # ------------------------------------------------------------------ 能力
    # ------------------------------------------------------------------ capability

    def _lp_binary(self):
        """找到可用的 lp 或 lpr / locate an usable lp or lpr."""
        return shutil.which("lp") or shutil.which("lpr")

    def is_available(self):
        """有 CUPS 命令行工具即视为可用 / available when the CUPS tools exist."""
        if os.name == "nt":
            return False
        return bool(self._lp_binary()) and bool(shutil.which("lpstat"))

    def supports_raw(self):
        return True

    def capabilities(self):
        caps = super().capabilities()
        caps.update({"mode": self.mode, "media": self.media})
        return caps

    # ------------------------------------------------------------------ 枚举
    # ------------------------------------------------------------------ enumeration

    # 打印机名的合法字符。CUPS 的打印机名按规范只允许这些字符(中文名会被百分号
    # 转义),所以「行里第一段连续的 ASCII」就是打印机名 —— 这条性质让解析不必
    # 依赖界面的语言。
    #
    # The legal characters for a printer name. CUPS restricts names to these (a CJK
    # name gets percent-escaped), so "the first run of ASCII in the line" is the
    # printer name. That property is what makes the parsing language-independent.
    _NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:@+-]*")

    def _lpstat(self, *args):
        """
        跑一次 lpstat / run lpstat.

        顺带固定 LC_ALL=C。这在 macOS 上只能把日期格式变回英文(CUPS 的文案走
        CoreFoundation 本地化,不受 LC_ALL 影响),但聊胜于无,在 Linux 上则是
        完全生效的。

        LC_ALL=C is set as well. On macOS it only reverts the date format — CUPS
        localises its wording through CoreFoundation, which ignores LC_ALL — but it
        costs nothing and on Linux it takes full effect.
        """
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        return subprocess.run(
            ["lpstat"] + list(args), capture_output=True, text=True,
            timeout=10, env=env,
        ).stdout

    @classmethod
    def _parse_printer_line(cls, line):
        """
        从 `lpstat -p` 的一行里取出打印机名 / pull the printer name out of one line.

        必须能同时吃下这两种写法 / both of these must parse:
            printer Brother_QL_570 is idle.  enabled since ...
            打印机Printer_POS_80闲置，启用时间始于六 11/ 8 14:27:21 2025

        第一个 ASCII 片段在英文里是 "printer" 这个单词、在中文里就是名字本身,
        所以:先跳过裸露的 "printer" 单词,再取下一个 ASCII 片段。
        In English the first ASCII run is the word "printer"; in Chinese it is the
        name itself. So skip a bare "printer" token, then take the next run.
        """
        runs = cls._NAME_RE.findall(line)
        if not runs:
            return None
        if runs[0].lower() == "printer" and len(runs) > 1:
            return runs[1]
        return runs[0]

    def _parse_default(self, text):
        """
        从 `lpstat -d` 的输出里取默认打印机 / extract the default from `lpstat -d`.

        取行里**最后**一个 ASCII 片段:
            英文 `system default destination: X`  -> X
            中文 `系统默认目的位置：X`              -> X
        注意中文用的是全角冒号 `：`,不能按半角冒号切分,所以走 ASCII 片段而不是
        字符串切分。

        The **last** ASCII run on the line works for both:
            English `system default destination: X` -> X
            Chinese `系统默认目的位置：X`              -> X
        Note the Chinese form uses a full-width colon, so splitting on ':' does not
        work — hence scanning ASCII runs instead.
        """
        for line in text.splitlines():
            runs = self._NAME_RE.findall(line)
            # 英文那行有 "system"/"default"/"destination" 三个词在名字之前,
            # 取最后一个就对了;中文行只有一个片段。
            # The English line has "system"/"default"/"destination" before the name,
            # so the last run is the right one; the Chinese line has only one.
            if runs:
                return runs[-1]
        return None

    def _parse_status(self, line):
        """
        尽力判断打印机状态 / best-effort printer status.

        只认几种最常见的说法。非英文界面下认不出来时返回 "unknown" —— 界面显示
        「未知」没关系,显示错的状态才有关系。

        Only a few common wordings are recognised. Under a non-English locale an
        unrecognised line reports "unknown": showing "unknown" is fine, showing the
        wrong status is not.
        """
        low = line.lower()
        if "disabled" in low or "已禁用" in line or "停用" in line:
            return "disabled"
        if "printing" in low or "正在打印" in line:
            return "printing"
        if "idle" in low or "闲置" in line or "空闲" in line:
            return "idle"
        return "unknown"

    def list_printers(self):
        """
        枚举 CUPS 里的打印机 / enumerate printers known to CUPS.

        解析刻意写得和界面语言无关 —— 见 _parse_printer_line 与 _parse_default。
        这不是假想的兼容性:在中文 macOS 上,原来按 `"printer "` 前缀匹配的实现
        会一台打印机都找不到,而且是静默找不到,用户只会看到「没有可用打印机」。

        The parsing is deliberately independent of the UI language — see
        _parse_printer_line and _parse_default. This is not hypothetical robustness:
        on a Chinese macOS the previous `"printer "` prefix match found zero
        printers, silently, leaving the user with "no printers available".
        """
        if not shutil.which("lpstat"):
            return []
        printers = []

        try:
            default_name = self._parse_default(self._lpstat("-d"))
        except Exception:
            default_name = None

        try:
            out = self._lpstat("-p")
        except Exception as e:
            raise PrintError("无法执行 lpstat / cannot run lpstat: %s" % e, "cups_unavailable")

        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            name = self._parse_printer_line(line)
            if not name:
                continue
            printers.append({
                "id": name,
                "name": name,
                "status": self._parse_status(line),
                "default": (name == default_name),
            })

        # lpstat -d 报的默认打印机可能没出现在 -p 里,补一条
        # The default may be missing from -p; add it so the UI can still select it
        if default_name and not any(p["id"] == default_name for p in printers):
            printers.insert(0, {
                "id": default_name, "name": default_name,
                "status": "unknown", "default": True,
            })
        return printers

    # ------------------------------------------------------------------ 打印
    # ------------------------------------------------------------------ printing

    def print_bitmap(self, bitmap, width, height, printer=None, **kwargs):
        """打印 1-bit 位图 / print a 1-bit bitmap."""
        if not self.is_available():
            raise PrintError(
                "CUPS 命令行工具不可用(lp / lpstat 缺失)/ CUPS tools unavailable",
                "cups_unavailable",
            )
        self.validate_bitmap(bitmap, width, height)

        target = printer or self.config.get("printer") or self.default_printer()
        if not target:
            raise PrintError(
                "未找到可用打印机,请先在系统设置里添加 / no printer found; "
                "add one in System Settings first",
                "no_printer",
            )

        # 目标打印机必须真实存在,否则 CUPS 会静默地把任务丢掉
        # The target must exist, otherwise CUPS silently drops the job
        known = {p["id"] for p in self.list_printers()}
        if known and target not in known:
            raise PrintError(
                "打印机不存在 / printer not found: %s" % target, "printer_not_found"
            )

        mode = (kwargs.get("mode") or self.mode or "driver").lower()
        if mode == "raw":
            payload, suffix = escpos.build_print_job(
                bitmap, width, height,
                feed_lines=self.feed_lines, do_cut=self.do_cut,
            ), ".bin"
        else:
            payload, suffix = bitmap_to_pbm(bitmap, width, height), ".pbm"

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix="printtheshot_", suffix=suffix, delete=False) as fh:
                fh.write(payload)
                tmp_path = fh.name

            cmd = self._build_command(target, tmp_path, mode)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "").strip()
                raise PrintError(
                    "打印失败 / print failed: %s" % (err or ("exit %d" % result.returncode)),
                    "print_failed",
                )
            return self._ok(
                "打印任务已发送 / print job sent",
                printer=target, mode=mode,
                message_raw=result.stdout.strip(),
            )
        except PrintError:
            raise
        except subprocess.TimeoutExpired:
            raise PrintError("打印超时 / print timed out", "timeout")
        except Exception as e:
            raise PrintError("打印异常 / print error: %s" % e, "print_error")
        finally:
            # 临时文件只在 CUPS 排队期间需要;任务进入队列后即可删除
            # The temp file is only needed while CUPS queues; safe to remove after
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _build_command(self, target, path, mode):
        """拼出 lp 命令行 / assemble the lp command line."""
        binary = shutil.which("lp") or "lpr"
        cmd = [binary, "-d", target]

        if mode == "raw":
            # 原样直通:不做任何驱动栅格化 / pass-through, no driver rasterisation
            cmd += ["-o", "raw"]
        else:
            cmd += [
                "-o", "media=%s" % self.media,
                "-o", "fit-to-page",
                "-o", "margin-top=0",
                "-o", "margin-bottom=0",
                "-o", "margin-left=0",
                "-o", "margin-right=0",
            ]
        cmd.append(path)
        return cmd
