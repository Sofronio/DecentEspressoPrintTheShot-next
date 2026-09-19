#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出界面文案表 / export the UI string table
==========================================

中文
----
把 print_the_shot_server.py 里的 LANGUAGES 表导出成 web/strings.js。

为什么需要这一步
----------------
桌面浏览器上,服务端会把 app.js 里的 {{LANG}} 占位符替换成语言表,前端不需要
自带文案。但 **APK 里的文件是原样复制的**,没有任何服务端参与替换 —— 占位符会
原样留在 app.js 里,而 JSON.parse('{{LANG}}') 在第一行就抛异常,整个脚本加载
失败,界面白屏。

所以前端必须自带一份文案兜底。这份表就是从服务端那份导出来的 —— **单一数据
来源**:改文案只改 print_the_shot_server.py,然后跑这个脚本重新导出,不要两边
各改一份。两边各维护一份的结局一定是走偏,而走偏的表现是「界面上冒出一堆 key」。

跑法 / usage:
    python3 scripts/export_strings.py

English
-------
Exports the LANGUAGES table from print_the_shot_server.py into web/strings.js.

Why this step exists
--------------------
In a desktop browser the server substitutes the {{LANG}} placeholder inside app.js,
so the front end needs no strings of its own. But **files inside the APK are copied
verbatim** — nothing substitutes anything, the placeholder survives in app.js, and
JSON.parse('{{LANG}}') throws on the first line, killing the whole script and leaving
a blank screen.

So the front end has to ship a fallback table. It is generated from the server's own
table, which keeps **one source of truth**: edit print_the_shot_server.py, run this,
done. Maintaining two hand-written copies guarantees they drift, and drifting shows
up as raw keys leaking into the UI.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "print_the_shot_server.py")
OUT = os.path.join(ROOT, "web", "strings.js")

HEADER = '''/**
 * PrintTheShot — 内置文案表 / built-in string table
 * =================================================
 *
 * 中文
 * ----
 * 这份表是界面文案的**底线**,不是全部。
 *
 * 桌面浏览器里,index.html 和 app.js 由 Python 服务端发出去,服务端会把
 * {{LANG}} 占位符替换成当前语言的完整文案表(含 AI 新增的自定义语言)——
 * 那条路径下这份表基本用不上。
 *
 * 但在 Android 上,Web UI 是**打包进 APK** 的,文件原样复制,没有任何服务端
 * 参与替换,{{LANG}} 会原样留在 app.js 里。而 JSON.parse('{{LANG}}') 在第一行
 * 就抛异常,整个脚本加载失败 —— 界面直接白屏,且不报任何看得见的错。
 *
 * 所以界面文案必须自带一份。服务端注入的表如果存在,会覆盖在这份之上。
 *
 * English
 * -------
 * This table is the **floor** for UI strings, not the whole story.
 *
 * In a desktop browser, index.html and app.js are served by the Python service,
 * which substitutes the {{LANG}} placeholder with the full table for the current
 * language (including custom AI-translated ones) — on that path this table is
 * mostly unused.
 *
 * On Android, though, the web UI is **bundled inside the APK**. The files are
 * copied verbatim, nothing substitutes anything, and {{LANG}} stays in app.js —
 * where JSON.parse('{{LANG}}') throws on the very first line, the whole script
 * fails to load, and the app shows a blank screen with no visible error.
 *
 * So the strings have to ship with the UI. Any server-injected table is layered
 * on top of this one.
 *
 * 由 scripts/export_strings.py 从 print_the_shot_server.py 的 LANGUAGES 表导出,
 * 改文案请改那边再重新导出 —— 不要两边各改一份。
 * Generated from the LANGUAGES table in print_the_shot_server.py. Edit there and
 * re-run the exporter rather than keeping two hand-maintained copies.
 */

'''


def load_languages():
    """
    从服务端源码里取出 LANGUAGES 字典 / pull the LANGUAGES dict out of the server source.

    刻意用 exec 解析源码而不是 import 模块:导入服务端会连带执行模块级的初始化,
    而这个脚本只是想要一张字符串表。exec 一段纯字面量更轻,也不会把服务端的
    副作用带进来。
    Parsing the source with exec rather than importing the module is deliberate:
    importing the server runs its module-level initialisation, and this script only
    wants a table of strings. exec'ing a pure literal is lighter and carries none of
    the server's side effects.
    """
    with open(SERVER, encoding="utf-8") as f:
        src = f.read()
    start = src.index("LANGUAGES = {")
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    else:
        raise SystemExit("❌ 找不到 LANGUAGES 表的结尾 / could not find the end of LANGUAGES")

    ns = {}
    exec(src[start:end], ns)
    return ns["LANGUAGES"]


def read_version():
    """从服务端源码里取 VERSION / read VERSION out of the server source."""
    with open(SERVER, encoding="utf-8") as f:
        for line in f:
            m = re.match(r'VERSION\s*=\s*"([^"]+)"', line.strip())
            if m:
                return m.group(1)
    raise SystemExit("❌ 找不到 VERSION / could not find VERSION")


def main():
    languages = load_languages()
    # 只导出真正的文案,__code / __languages 这些是运行时附加的
    # Export the strings only; __code / __languages are runtime additions
    clean = lambda d: {k: v for k, v in d.items() if not k.startswith("__")}  # noqa: E731

    missing = [c for c in ("en", "zh") if c not in languages]
    if missing:
        raise SystemExit("❌ 缺少语言 / missing languages: %s" % ", ".join(missing))

    payload = {"en": clean(languages["en"]), "zh": clean(languages["zh"])}
    version = read_version()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(HEADER)
        # 版本号也要一起导出。index.html 里写的是 <title>...v{{VERSION}}</title>,
        # 由服务端替换 —— 而打包进 APK 时没人替换,标题里就会留着字面的
        # "{{VERSION}}"。带上版本号,前端就能自己把它补上。
        #
        # The version goes out too. index.html has <title>...v{{VERSION}}</title> and
        # the server substitutes it — but nothing does when the files are bundled into
        # the APK, leaving a literal "{{VERSION}}" in the title. Shipping the version
        # lets the front end fill it in itself.
        f.write("window.PTS_VERSION = %s;\n\n" % json.dumps(version, ensure_ascii=False))
        f.write("window.PTS_STRINGS = ")
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write(";\n")

    print("已写入 / written: %s" % os.path.relpath(OUT, ROOT))
    for code, table in payload.items():
        print("  %s: %d 条 / entries" % (code, len(table)))

    # 两套语言的键必须一一对应,少一个键在界面上就是一个原始 key
    # The two languages must have identical key sets; a missing key shows up as a raw
    # key in the UI
    only_en = set(payload["en"]) - set(payload["zh"])
    only_zh = set(payload["zh"]) - set(payload["en"])
    if only_en or only_zh:
        print("  ⚠️  键不一致 / key mismatch:")
        if only_en:
            print("     只在 en 里 / en only:", ", ".join(sorted(only_en)))
        if only_zh:
            print("     只在 zh 里 / zh only:", ", ".join(sorted(only_zh)))
    else:
        print("  ✅ 中英键一致 / en and zh have the same keys")


if __name__ == "__main__":
    main()
