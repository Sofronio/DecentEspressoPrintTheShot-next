#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Linux 打印适配 / Linux printing adapter
=======================================

中文
----
Linux 走 CUPS,和 macOS 完全同源:同一套 lp / lpstat 命令、同样的 PBM 栅格化
路径、同样的 ESC/POS 直通模式。所以这里直接复用 MacPrinter 的实现,只换掉
平台标识与显示名。

差异只有两点 / only two differences:
  - 某些精简发行版只有 lpr 没有 lp,`_lp_binary()` 已经做了回退
  - 树莓派等 ARM 板子上 CUPS 往往要手动装,`is_available()` 会如实返回 False

English
-------
Linux goes through CUPS exactly like macOS: same lp / lpstat tools, same PBM
rasterisation path, same ESC/POS pass-through mode. This adapter therefore
reuses MacPrinter and only swaps the platform id and display name.

Only two things differ: some minimal distributions ship lpr but not lp (handled
by the `_lp_binary()` fallback), and CUPS often has to be installed by hand on
ARM boards such as a Raspberry Pi, where `is_available()` honestly reports False.
"""

from __future__ import annotations

from .mac_printer import MacPrinter


class LinuxPrinter(MacPrinter):
    """Linux(CUPS)打印适配器 / Linux (CUPS) printing adapter."""

    platform_id = "linux"
    display_name = "Linux (CUPS)"
