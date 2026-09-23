#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成发布说明 / build the release notes
=====================================

中文
----
把 docs/CHANGELOG.md 和 docs/CHANGELOG_zh.md 里对应版本的两节抽出来,拼成
docs/RELEASE_NOTES.md —— 也就是 GitHub Release 的正文。

为什么需要这一步
----------------
Release 正文和变更日志写的是同一件事,只是读者不同:变更日志是给回头翻历史的人,
Release 正文是给「现在就要下载」的人。两边各手写一份,结局一定是走偏 —— 更常见的
是 Release 正文干脆忘了写:v2.1-beta.2 就发出去过一个**空正文**,而没人发现。

所以变更日志是**单一数据来源**:发版前把 CHANGELOG 对应那节写好,跑这个脚本,
Release 正文就出来了。正文里那两段与版本无关的内容(下载表、macOS 首次放行)
作为常量放在本脚本里,它们每次发版都一样。

跑法 / usage:
    python3 scripts/build_release_notes.py             # 版本号取自服务端源码
    python3 scripts/build_release_notes.py 2.1-beta.3  # 显式指定
    python3 scripts/build_release_notes.py --check     # 只检查是否过期,不写文件

发版顺序 / the release sequence
-------------------------------
    1. 写 CHANGELOG.md 和 CHANGELOG_zh.md 里对应版本的小节
    2. 改 print_the_shot_server.py 的 VERSION —— 版本号的唯一来源
    3. python3 scripts/export_strings.py       重新导出 web/strings.js
    4. python3 scripts/build_release_notes.py  生成 Release 正文
    5. git commit -m "release: vX.Y-beta.N —— <一句话标题>"
    6. git tag -a vX.Y-beta.N -m "..." && git push origin main --follow-tags

第 2 步之后不要在 web/strings.js 里手改版本号:那是生成物,第 3 步会覆盖它。
第 6 步推送 tag 触发 CI 打包发版,CI 发布前会跑一次本脚本的 --check,所以第 4 步
忘了做的话构建会红,而不是发出一个正文对不上的 Release。第 3 步由
tests/run_all.sh 的第 0 步把关。

English
-------
Extracts the matching section from docs/CHANGELOG.md and docs/CHANGELOG_zh.md and
composes docs/RELEASE_NOTES.md — the body of the GitHub Release.

Why this step exists
--------------------
The release body and the changelog say the same thing to two different readers: the
changelog is for looking back, the release body is for someone who wants to download
*now*. Maintaining both by hand guarantees they drift — and more often it means the
release body simply never gets written: v2.1-beta.2 went out with an **empty body**,
and nothing caught it.

So the changelog is the **single source of truth**: write that release's section, run
this script, and the release body appears. The two parts of the body that do not vary
by version (the download table, the macOS first-launch note) live here as constants.

usage:
    python3 scripts/build_release_notes.py             # version from the server source
    python3 scripts/build_release_notes.py 2.1-beta.3  # explicit
    python3 scripts/build_release_notes.py --check     # fail if stale, write nothing

The release sequence
--------------------
    1. Write the release's section in CHANGELOG.md and CHANGELOG_zh.md
    2. Change VERSION in print_the_shot_server.py — the single source of it
    3. python3 scripts/export_strings.py       re-export web/strings.js
    4. python3 scripts/build_release_notes.py  build the release body
    5. git commit -m "release: vX.Y-beta.N — <headline>"
    6. git tag -a vX.Y-beta.N -m "..." && git push origin main --follow-tags

Do not hand-edit the version in web/strings.js after step 2: it is generated, and
step 3 overwrites it. Pushing the tag in step 6 triggers the CI build and release,
and CI runs this script's --check before publishing — so forgetting step 4 fails the
build rather than publishing a release whose body does not match the changelog.
Step 3 is enforced by step 0 of tests/run_all.sh.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "print_the_shot_server.py")
CHANGELOG = os.path.join(ROOT, "docs", "CHANGELOG.md")
CHANGELOG_ZH = os.path.join(ROOT, "docs", "CHANGELOG_zh.md")
OUT = os.path.join(ROOT, "docs", "RELEASE_NOTES.md")

# 与版本无关的两段内容。发版时它们不跟着变,所以不放进 CHANGELOG —— 那会让每一节
# 都重复一遍同样的下载表。
#
# The two version-independent blocks. They do not change between releases, so they are
# not in the CHANGELOG — that would repeat the same download table in every section.
DOWNLOAD_EN = """## Downloads

| Platform | File | Notes |
|---|---|---|
| macOS (Apple Silicon) | `PrintTheShotNext-macos-arm64.zip` | unzip → allow once → double-click |
| macOS (Intel) | `PrintTheShotNext-macos-intel.zip` | same |
| Windows | `PrintTheShotNext-windows-x64.exe` | double-click |
| Linux | `PrintTheShotNext-linux` | `chmod +x` then run |
| Android | `PrintTheShotNext-android.apk` | install — it is its own server and shows its LAN address |

**Every build is a complete server, Android included.** Run any of them and open
<http://localhost:8000> — or, on Android, the app opens its own UI and also shows its LAN
address so the DE1 uploads straight to the tablet. There is no client mode and no
companion machine required.

### macOS: allow it once

```bash
xattr -dr com.apple.quarantine /Applications/PrintTheShot.app
```

or: try to open it once, then **System Settings → Privacy & Security → Open Anyway**.

> ⚠️ Not the same as *"the app is damaged"* — that means the code signature failed to
> validate and cannot be bypassed. `xattr` does not fix it either; you have a build from
> before the signing fix.
"""

DOWNLOAD_ZH = """## 下载

| 平台 | 文件 | 说明 |
|---|---|---|
| macOS(Apple Silicon) | `PrintTheShotNext-macos-arm64.zip` | 解压 → 放行一次 → 双击 |
| macOS(Intel) | `PrintTheShotNext-macos-intel.zip` | 同上 |
| Windows | `PrintTheShotNext-windows-x64.exe` | 双击 |
| Linux | `PrintTheShotNext-linux` | `chmod +x` 后运行 |
| Android | `PrintTheShotNext-android.apk` | 装上即可 —— 它自己就是服务端,会显示本机地址 |

**每一个构建都是完整的服务端,Android 版也是。** 运行之后打开
<http://localhost:8000> —— Android 上应用会打开自己的界面,并且显示本机的局域网
地址,DE1 直接把数据传到平板即可。没有客户端模式,也不需要另一台机器配合。

### macOS:首次启动前放行一次

```bash
xattr -dr com.apple.quarantine /Applications/PrintTheShot.app
```

或者:先双击一次让它报错,然后到 **系统设置 → 隐私与安全性 → 点「仍要打开」**。

> ⚠️ 这和*「已损坏」*不是一回事 —— 后者是签名校验失败,绕不过去,`xattr` 也修不好;
> 那是签名修复之前的构建,换用当前版本。
"""


def read_version():
    """从服务端源码里取 VERSION / read VERSION out of the server source."""
    with open(SERVER, encoding="utf-8") as f:
        for line in f:
            m = re.match(r'VERSION\s*=\s*"([^"]+)"', line.strip())
            if m:
                return m.group(1)
    raise SystemExit("❌ 在 %s 里找不到 VERSION / could not find VERSION" % SERVER)


def extract_section(path, version):
    """
    取出 '## <version>' 那一节,到下一个 '## ' 为止。

    Pull out the '## <version>' section, up to the next '## '.
    """
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    heading = "## %s" % version
    start = None
    for i, line in enumerate(lines):
        if line.strip() == heading:
            start = i + 1
            break

    if start is None:
        raise SystemExit(
            "❌ %s 里没有 '%s' 这一节 —— 先补上再发版 / no '%s' section in %s — write it first"
            % (os.path.relpath(path, ROOT), heading, heading, os.path.relpath(path, ROOT))
        )

    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break

    # 标题和正文之间、正文和下一节之间的空行都去掉,拼接处才不会出现三四行空白。
    # Trim the blank lines around the section so the seams do not collect three or four
    # blank lines each.
    body = "\n".join(lines[start:end]).strip("\n")
    if not body:
        raise SystemExit(
            "❌ %s 的 '%s' 一节是空的 / the '%s' section in %s is empty"
            % (os.path.relpath(path, ROOT), heading, heading, os.path.relpath(path, ROOT))
        )
    return body


def build(version):
    en = extract_section(CHANGELOG, version)
    zh = extract_section(CHANGELOG_ZH, version)

    return (
        "# PrintTheShot Next v%(v)s\n\n"
        "---\n\n"
        "# English\n\n"
        "%(en)s\n\n"
        "%(dl_en)s\n"
        "---\n\n"
        "# 中文\n\n"
        "%(zh)s\n\n"
        "%(dl_zh)s"
        % {"v": version, "en": en, "zh": zh, "dl_en": DOWNLOAD_EN, "dl_zh": DOWNLOAD_ZH}
    )


def main():
    argv = [a for a in sys.argv[1:]]
    check = "--check" in argv
    argv = [a for a in argv if not a.startswith("-")]

    if len(argv) > 1:
        raise SystemExit("用法 / usage: build_release_notes.py [version] [--check]")

    version = argv[0] if argv else read_version()
    text = build(version)

    if check:
        current = ""
        if os.path.exists(OUT):
            with open(OUT, encoding="utf-8") as f:
                current = f.read()
        if current == text:
            print("✅ %s 与 CHANGELOG 一致 / up to date: %s" % (os.path.relpath(OUT, ROOT), version))
            return
        raise SystemExit(
            "❌ %s 与 CHANGELOG 的 %s 一节对不上 —— 跑一次不带 --check 的 / %s does not match "
            "the %s section — run without --check" % (os.path.relpath(OUT, ROOT), version,
                                                      os.path.relpath(OUT, ROOT), version)
        )

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)

    print("已写入 / written: %s" % os.path.relpath(OUT, ROOT))
    print("  版本 / version: %s" % version)
    print("  %d 字符 / chars" % len(text))


if __name__ == "__main__":
    main()
