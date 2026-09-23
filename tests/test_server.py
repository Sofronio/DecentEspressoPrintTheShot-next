#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
服务端集成测试 / Server integration tests
========================================

中文
----
起一个真的服务端进程,用真的 HTTP 请求打它。测的是「服务端到底能不能用」,
而不是某个函数单独调用时的返回值 —— 路由写错、模板占位符没替换、静态资源
路径不对,这些只有把服务端跑起来才看得出来。

    python3 tests/test_server.py
    python3 -m unittest discover -s tests -v

English
-------
Boots a real server process and hits it with real HTTP requests. This tests whether
the server actually works rather than what an isolated function returns — a wrong
route, an unsubstituted template placeholder or a broken static-asset path only
shows up once the server is running.

    python3 tests/test_server.py
"""

import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PORT = 8791
BASE = "http://127.0.0.1:%d" % PORT
SAMPLE = os.path.join(ROOT, "sample_shots", "prodigal_el_rafugio.json")


def free_port(preferred):
    """挑一个可用端口 / pick a usable port."""
    for port in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("no free port")


def http(method, path, body=None, timeout=15):
    """发一个请求,返回 (status, body_bytes)/ issue a request and return (status, bytes)."""
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def http_json(method, path, body=None, timeout=15):
    status, raw = http(method, path, body, timeout)
    try:
        return status, json.loads(raw.decode("utf-8"))
    except Exception:
        return status, {"_raw": raw[:400].decode("utf-8", "replace")}


class ServerTest(unittest.TestCase):
    """整个类共用同一个服务端进程 / one server process shared by the whole class."""

    proc = None

    @classmethod
    def setUpClass(cls):
        global PORT, BASE
        PORT = free_port(8791)
        BASE = "http://127.0.0.1:%d" % PORT
        # 试运行:这些用例会调 /api/print,真跑就会往系统默认打印机上打。
        # 默认打印机是热敏机时,每跑一次测试就是一张完整图表(约 94 KB)——
        # 实测把打印机打到持续走纸、必须断电。见 printers.DryRunPrinter。
        #
        # Dry run: these tests call /api/print, and for real that means printing to the
        # system default printer. With a thermal printer as the default, every run puts a
        # full chart (about 94 KB) on it — which in practice left it feeding continuously
        # until it was power-cycled. See printers.DryRunPrinter.
        env = dict(os.environ, PTS_PRINT_DRYRUN="1")
        cls.proc = subprocess.Popen(
            [sys.executable, "print_the_shot_server.py", "--port", str(PORT)],
            cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        # 等端口起来 / wait for the port
        for _ in range(150):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", PORT)) == 0:
                    return
            if cls.proc.poll() is not None:
                out = cls.proc.stdout.read() if cls.proc.stdout else ""
                raise RuntimeError("server exited early:\n" + out)
            time.sleep(0.05)
        raise RuntimeError("server did not start in time")

    @classmethod
    def tearDownClass(cls):
        if cls.proc and cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()

    # ------------------------------------------------------------------ 辅助
    # ------------------------------------------------------------------ helpers

    def _upload_one(self, unique=False):
        """
        上传示例 shot,返回落盘的文件名 / upload the sample shot and return its filename.

        unique=True 会往内容里盖一个每次都不同的标记。因为打印去重是**按内容**算的
        (见服务端的 DEDUPE_WINDOW_S),而整个测试类共用一个服务端进程:同一个样例
        被前面的用例传过之后,后面的上传会在去重窗口内被吃掉、根本不进队列。
        凡是断言「进了队列」的用例都得让内容独一无二,否则它就依赖测试的执行顺序。

        unique=True stamps the payload with a marker that differs on every call. Print
        de-duplication is **content**-based (see the server's DEDUPE_WINDOW_S) and the
        whole test class shares one server process: once an earlier case has uploaded
        this same sample, a later upload inside the window is swallowed and never
        queued. Any case asserting "it is queued" needs unique content, or it silently
        depends on test execution order.
        """
        with open(SAMPLE, "rb") as f:
            payload = f.read()
        if unique:
            shot = json.loads(payload.decode("utf-8"))
            shot["_test_marker"] = "%s-%d" % (self.id(), time.time_ns())
            payload = json.dumps(shot, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            BASE + "/upload?machine_id=TEST-RIG", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(body.get("status"), "success")
        return body["message"].rsplit(" ", 1)[-1]

    # ------------------------------------------------------------------ 依赖
    # ------------------------------------------------------------------ dependencies

    def test_server_needs_no_third_party_packages(self):
        """
        服务端只准用标准库。绘制搬到浏览器之后就没有 Pillow 的用武之地了,
        这条断言防止它悄悄回来 —— 一旦有人重新 import PIL,这里的 subprocess
        会因为 ModuleNotFoundError 起不来,而这个测试会明确报出来。
        (本机没装 Pillow,所以这条断言在本环境里是有 teeth 的。)
        """
        self.assertIsNone(self.proc.poll(), "服务端进程应该还活着 / the server must still be running")
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import print_the_shot_server as m; "
             "print('OK')" % ROOT],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertIn("OK", out.stdout, out.stderr)
        self.assertNotIn("PIL", out.stderr, "服务端不应再依赖 Pillow / must not depend on Pillow")
        with open(os.path.join(ROOT, "print_the_shot_server.py"), encoding="utf-8") as f:
            src = f.read()
        for banned in ("import PIL", "from PIL", "import matplotlib", "import numpy"):
            self.assertNotIn(banned, src, "服务端不应再 import %s" % banned)

    # ------------------------------------------------------------------ 页面
    # ------------------------------------------------------------------ pages

    def test_index_serves_the_ui_with_placeholders_substituted(self):
        status, raw = http("GET", "/")
        self.assertEqual(status, 200)
        html = raw.decode("utf-8")
        self.assertNotIn("{{VERSION}}", html, "模板占位符必须被替换 / placeholders must be substituted")
        self.assertNotIn("{{LANG}}", html)
        # 相对路径:浏览器和 APK 两种布局下都成立(见 ROOT_ASSETS)
        # Relative paths: valid under both the browser and APK layouts
        for asset in ('href="style.css"', 'src="render.js"', 'src="printer.js"',
                      'src="app.js"', 'src="strings.js"'):
            self.assertIn(asset, html, "index.html 应引用 %s" % asset)
        self.assertNotIn('src="/web/', html, "不该用绝对路径 —— 在 APK 里会 404")

    def test_app_js_is_templated(self):
        """
        app.js 里也有 {{LANG}}。如果只对 index.html 做替换、漏了 app.js,
        页面会加载出一堆语言 key 而不是文字 —— 这个错误在浏览器里不容易看出根因。
        """
        status, raw = http("GET", "/web/app.js")
        self.assertEqual(status, 200)
        js = raw.decode("utf-8")
        self.assertNotIn("{{LANG}}", js, "app.js 的 LANG 占位符必须被替换")
        self.assertIn("const L = resolveStrings()", js)
        self.assertIn("print_platform", js, "新增的打印文案应该在语言表里")
        self.assertIn("h_print", js)

    def test_static_assets_resolve(self):
        """页面引用的每个静态资源都必须真的能取到 / every referenced asset must resolve."""
        for path, expect_type in (
            ("/web/render.js", "renderShotToCanvas"),
            ("/web/printer.js", "PrintTheShotPrinter"),
            ("/web/app.js", "loadShots"),
            ("/web/style.css", "@font-face"),
        ):
            status, raw = http("GET", path)
            self.assertEqual(status, 200, "%s should be 200" % path)
            self.assertIn(expect_type, raw.decode("utf-8"), "%s looks wrong" % path)

    def test_bundled_font_is_served(self):
        """自带字体对 Mac/Android 渲染一致是必需的,不能 404。"""
        status, raw = http("GET", "/fonts/NotoSansCJKsc-Regular.otf")
        self.assertEqual(status, 200)
        self.assertGreater(len(raw), 100000, "字体文件不该这么小 / the font should not be this small")

    def test_path_traversal_is_blocked(self):
        """静态资源不能穿越出资源目录 / static serving must not escape its directory."""
        for path in ("/web/../../etc/passwd", "/fonts/../print_the_shot_server.py",
                     "/web/..%2f..%2fetc%2fpasswd"):
            status, raw = http("GET", path)
            if status == 200:
                self.assertNotIn(b"root:", raw, "path traversal leaked a system file")

    # ------------------------------------------------------------------ 核心 API
    # ------------------------------------------------------------------ core API

    def test_status_endpoint(self):
        status, j = http_json("GET", "/api/status")
        self.assertEqual(status, 200)
        for key in ("status", "version", "shots_received", "print_enabled", "language"):
            self.assertIn(key, j)
        # 只断言「像个版本号」,不写死具体数字。这条原本写的是 "2.1",于是版本
        # 编号换成 1.0.N 时它挂了 —— 而挂的理由和它真正想确认的事(服务端确实
        # 读到了 VERSION、而不是空串或占位符)毫无关系。
        #
        # Assert only that it looks like a version, never a specific number. This used
        # to hard-code "2.1", so the renumbering to 1.0.N broke it for a reason that has
        # nothing to do with what the test is for: that the server really read VERSION
        # rather than an empty string or a placeholder.
        self.assertRegex(j["version"], r"^\d+\.\d+", j["version"])

    def test_upload_stores_the_shot_and_queues_it_for_printing(self):
        """
        上传是这个服务存在的主要理由:数据要落盘、历史要记上、还要进待打印队列。
        打印由前端那一端完成(它才有打印机),所以服务端这边只入队 —— 队列里
        有没有东西,就是 Auto-print 链路通不通的判据。

        用 unique=True:这条断言依赖「上传的这份内容刚被入队」,而按内容去重会让
        同一份样例在 30 秒窗口内第二次上传时被吃掉。整个测试类共用一个服务端,
        所以不这么做就是在依赖测试的执行顺序。

        The upload is unique=True because this assertion depends on *this* content having
        just been queued, and content-based de-duplication swallows the same sample when
        it is uploaded again inside a 30-second window. The class shares one server, so
        not doing this means depending on test execution order.
        """
        filename = self._upload_one(unique=True)
        self.assertTrue(filename.endswith(".json"))

        # 文件真的落盘了 / the file really landed on disk
        self.assertTrue(os.path.exists(os.path.join(ROOT, "shots_data", filename)))

        # 历史与队列是后台线程写的:服务端先回响应再记账,免得 DE1 插件上传时
        # 干等。所以这里要轮询,而不是立刻断言 —— 直接断言会变成一个飘忽的测试,
        # 而飘忽的测试比没有测试更糟。
        #
        # History and the queue are written by a background thread: the server
        # acknowledges first and records afterwards so the DE1 plugin never waits.
        # Hence the polling — asserting immediately would make this test flaky, and
        # a flaky test is worse than no test.
        names, queued = [], []
        for _ in range(40):
            _, shots = http_json("GET", "/api/shots")
            names = [s["filename"] for s in shots["shots"]]
            _, q = http_json("GET", "/api/print-queue")
            queued = [j["filename"] for j in q["jobs"]]
            if filename in names and filename in queued:
                break
            time.sleep(0.1)

        self.assertIn(filename, names, "上传的 shot 应出现在历史里 / should appear in history")
        self.assertIn(filename, queued, "上传的 shot 应进入待打印队列 / should be queued to print")

        # ack 之后出队 / cleared by an ack
        _, a = http_json("POST", "/api/print-queue/ack", {"filename": filename, "ok": True})
        self.assertTrue(a["success"])
        self.assertNotIn(filename, [j["filename"] for j in a["jobs"]])

    def test_shot_detail_returns_drawable_json(self):
        """
        前端就靠这个端点取数据画图。返回的 JSON 必须带齐 render.js 需要的字段,
        否则前端只会看到一张空白图,而服务端这边毫无报错。

        自己上传一条,不依赖别的测试先跑过 —— 测试之间有顺序依赖的话,
        单跑这一条就会失败,而那种失败毫无信息量。
        """
        filename = self._upload_one()

        status, j = http_json("GET", "/api/shot?file=%s&lang=zh" % filename)
        self.assertEqual(status, 200)
        self.assertTrue(j["success"])
        shot = j["shot"]
        for key in ("elapsed", "pressure", "flow", "temperature"):
            self.assertIn(key, shot, "render.js 需要 %s" % key)
        self.assertIn("pressure", shot["pressure"])
        self.assertIn("basket", shot["temperature"])
        self.assertGreater(len(shot["elapsed"]), 1)

    def test_shot_detail_rejects_unknown_and_traversal(self):
        # 不存在的文件 -> 404 / a missing file is a 404
        status, j = http_json("GET", "/api/shot?file=nope.json")
        self.assertEqual(status, 404)
        self.assertFalse(j["success"])

        # 目录穿越:basename 之后 "passwd" 不以 .json 结尾,所以是 400 而不是 404。
        # 具体码不重要,重要的是「被拒了,而且没有内容泄露」—— 断言写成具体的 404
        # 会在这种等价行为上误报。
        #
        # Traversal: after basename the name is "passwd", which has no .json suffix,
        # so it is a 400 rather than a 404. The exact code does not matter — what
        # matters is that it is refused and nothing leaks. Pinning the assertion to
        # 404 would raise a false alarm on an equivalent outcome.
        for probe in ("../../etc/passwd", "..%2f..%2fetc%2fpasswd.json", "/etc/passwd.json"):
            status, raw = http("GET", "/api/shot?file=" + probe)
            self.assertIn(status, (400, 404), "traversal probe %r must be refused" % probe)
            self.assertNotIn(b"root:", raw, "traversal probe %r leaked a system file" % probe)

    # ------------------------------------------------------------------ 去重
    # ------------------------------------------------------------------ de-duplication

    def _upload_bytes(self, payload):
        """上传指定内容,返回落盘文件名 / upload the given bytes, return the stored name."""
        req = urllib.request.Request(
            BASE + "/upload?machine_id=TEST-RIG", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(body.get("status"), "success")
        return body["message"].rsplit(" ", 1)[-1]

    def _queue_names(self):
        _, q = http_json("GET", "/api/print-queue")
        return [j["filename"] for j in q["jobs"]]

    def _wait_queued(self, filename, tries=50):
        """等后台记账线程把这条写进队列 / wait for the background recorder."""
        for _ in range(tries):
            if filename in self._queue_names():
                return True
            time.sleep(0.1)
        return False

    def _payload(self, marker):
        """
        带唯一标记的样例 / the sample shot with a unique marker.

        去重是按**内容**做的,而整个类共用同一个服务端进程 —— 所以每个用例都必须
        用自己独有的内容,否则前一个用例留下的 pending / printed 状态会把这个用例
        吃掉。加一个标记字段就够:服务端存的是原始请求体,内容变了哈希就变了。

        De-duplication is by **content**, and the whole class shares one server process,
        so every test needs content of its own — otherwise the pending/printed state
        left by an earlier test swallows this one. One marker field is enough: the
        server stores the raw body, so a changed field moves the hash.
        """
        with open(SAMPLE, "rb") as f:
            shot = json.loads(f.read())
        shot["_test_marker"] = marker
        return json.dumps(shot).encode("utf-8")

    def test_identical_content_is_queued_once(self):
        """
        同一份内容传两次,队列里只应有一条。

        上游确实会重复上传:DE1 侧的 after_flow_complete 可能重复触发,插件里那个
        last_upload_shot 只赋值、从不比较,HTTP 超时重试时服务端其实也已经存盘了。
        而每次上传的文件名都不同(带微秒 ID),所以按文件名去重拦不住,只能按**内容**拦。

        The upstream really does repeat: the DE1's after_flow_complete can fire more
        than once, the plugin's last_upload_shot is assigned but never compared, and an
        HTTP timeout retry arrives after the server has already stored the shot. Each
        upload gets a different filename (it carries a microsecond ID), so a name-based
        check catches nothing — only a content-based one does.
        """
        payload = self._payload("identical")

        first = self._upload_bytes(payload)
        self.assertTrue(self._wait_queued(first), "第一份应进队列 / the first copy should be queued")

        # 等第一份确实排进队列之后再传第二份 —— 否则两条上传会和后台记账线程抢跑,
        # 测试就变成飘忽的。
        #
        # Only upload the second copy once the first is really in the queue; otherwise
        # both race the background recorder and the test turns flaky.
        second = self._upload_bytes(payload)
        self.assertNotEqual(first, second, "两次上传的文件名应不同 / filenames should differ")
        time.sleep(1.0)

        names = self._queue_names()
        self.assertIn(first, names)
        self.assertNotIn(second, names,
                         "同内容的第二份不该进队列 / the identical copy must not be queued")

    def test_content_printed_recently_is_not_queued_again(self):
        """
        打印成功之后,窗口期内同样的内容不再排队。这是「同一张票出两次」的正解:
        第一次打完了,后面那几次重复上传应该被吃掉,而不是再出几张。

        After a successful print, identical content is not queued again inside the
        window. This is the fix for "the same receipt comes out twice": the first one
        printed, so the repeats should be swallowed rather than printed again.
        """
        payload = self._payload("printed")

        first = self._upload_bytes(payload)
        self.assertTrue(self._wait_queued(first))

        # 模拟前端打印成功后的回执 / simulate the front end's ack after printing
        _, a = http_json("POST", "/api/print-queue/ack", {"filename": first, "ok": True})
        self.assertTrue(a["success"])

        second = self._upload_bytes(payload)
        time.sleep(1.0)
        self.assertNotIn(second, self._queue_names(),
                         "刚打印过的内容不该再排队 / content just printed must not be re-queued")

    def test_different_content_is_queued_separately(self):
        """
        内容不同就该各排各的 —— 去重只认内容,不能把正常的第二杯咖啡吃掉。

        Different content queues separately: the de-duplication is by content only and
        must never swallow a legitimately different shot.
        """
        a = self._upload_bytes(self._payload("diff-a"))
        self.assertTrue(self._wait_queued(a))
        b = self._upload_bytes(self._payload("diff-b"))
        self.assertTrue(self._wait_queued(b), "不同内容应各自进队列 / different content should queue")

        names = self._queue_names()
        self.assertIn(a, names)
        self.assertIn(b, names)

    def test_dedupe_window_expires(self):
        """
        窗口过期之后同样的内容应重新打印 —— 这是「30 秒」和「永远」的分界线。

        直接驱动时间,不去真等 30 秒:窗口的判定逻辑在服务端进程里,而测试跑在
        另一个进程,只能就地 import 进来验证。
        (跨进程判定只能靠墙钟,那样要么很慢,要么很飘。)

        After the window expires the same content prints again — that is the line
        between "30 seconds" and "forever". Time is driven directly rather than waiting
        30 real seconds: the window logic lives inside the server process and this test
        runs in another one, so it is imported locally instead.
        """
        sys.path.insert(0, ROOT)
        import print_the_shot_server as m

        base = 1000.0
        m.printed_at.clear()
        m.printed_at["deadbeef"] = base

        self.assertTrue(m._printed_recently("deadbeef", now=base + m.DEDUPE_WINDOW_S),
                        "窗口边界上仍算刚打印过 / still inside the window at the boundary")
        self.assertFalse(m._printed_recently("deadbeef", now=base + m.DEDUPE_WINDOW_S + 1),
                         "过了窗口就该重新打印 / past the window it prints again")

        # 没有哈希(文件读不到)不参与去重 / no hash (unreadable file) means no de-duplication
        self.assertFalse(m._printed_recently("", now=base))
        self.assertFalse(m._printed_recently(None, now=base))

        m.printed_at.clear()

    # ------------------------------------------------------------------ 打印
    # ------------------------------------------------------------------ printing

    def test_printers_endpoint_describes_the_platform(self):
        status, j = http_json("GET", "/api/printers")
        self.assertEqual(status, 200)
        for key in ("platform", "display_name", "available", "capabilities", "printers"):
            self.assertIn(key, j)
        self.assertIsInstance(j["printers"], list)

    def test_print_config_round_trip(self):
        status, j = http_json("GET", "/api/print/config")
        self.assertEqual(status, 200)
        for key in ("printer", "mode", "print_width", "threshold", "rotate", "platform"):
            self.assertIn(key, j)

        status, saved = http_json("POST", "/api/print/config", {"print_width": 512})
        self.assertTrue(saved["success"])
        self.assertEqual(saved["config"]["print_width"], 512)

        # 改回来,免得影响别的测试 / restore so other tests are unaffected
        http_json("POST", "/api/print/config", {"print_width": 576})

    def test_print_rejects_a_malformed_body(self):
        """错误的输入必须在入口就被挡住,不能一路走到打印机才出问题。"""
        # 缺 bitmap
        status, j = http_json("POST", "/api/print", {"width": 8, "height": 1})
        self.assertEqual(status, 400)
        self.assertFalse(j["success"])

        # base64 不合法
        status, j = http_json("POST", "/api/print", {"bitmap": "!!!bad!!!", "width": 8, "height": 1})
        self.assertEqual(status, 400)

        # 长度与尺寸不符
        import base64 as b64
        data = b64.b64encode(b"\x00" * 8).decode()
        status, j = http_json("POST", "/api/print", {"bitmap": data, "width": 999, "height": 1})
        self.assertEqual(status, 400)
        self.assertIn("长度不符", j.get("message", "") + j.get("error", ""))

    def test_print_accepts_a_valid_bitmap_and_dispatches(self):
        """
        用一张真的位图走一遍 /api/print。没接打印机也没关系 —— 这里要确认的是
        请求被接受、位图通过校验、适配器被调用。适配器失败的原因必须如实回报,
        而不是假装成功(假装成功会让人对着一张空白的纸排查半天)。
        """
        import base64 as b64
        width, height = 64, 8
        bitmap = b"\x00" * (((width + 7) // 8) * height)
        status, j = http_json("POST", "/api/print", {
            "bitmap": b64.b64encode(bitmap).decode(),
            "width": width, "height": height,
            "printer": "default", "label": "unit-test",
        })
        self.assertEqual(status, 200)
        self.assertTrue(j["success"], "合法位图必须被接受 / a valid bitmap must be accepted")
        # 这里原本断言 "mac",但 setUpClass 现在固定带 PTS_PRINT_DRYRUN=1(理由见
        # 那里:不带的话测试会真的往系统默认打印机上出纸)。dry-run 下平台是
        # "dryrun",所以那条断言永远不可能成立。要确认的不是「跑在 Mac 上」,而是
        # 「走的是本机真实的那个适配器」——网卡上没有适配器的平台会回落成 dryrun。
        #
        # This used to assert "mac", but setUpClass now always sets PTS_PRINT_DRYRUN=1
        # (see there: without it the tests really print to the system default printer).
        # Under dry-run the platform is "dryrun", so that assertion could never hold.
        # What matters is not "this is a Mac" but "a real local adapter was chosen" —
        # dryrun is what an unsupported platform falls back to.
        self.assertIn(j["platform"], ("mac", "dryrun"), j["platform"])
        self.assertEqual(j["width"], width)
        self.assertEqual(j["height"], height)

        # 等后台线程写完队列 / let the worker thread record the outcome
        time.sleep(1.5)
        _, q = http_json("GET", "/api/queue")
        self.assertTrue(q["jobs"], "打印结果应被记录 / the outcome should be recorded")
        last = q["jobs"][-1]
        self.assertEqual(last["label"], "unit-test")
        # 没有打印机时必须是失败,而且要说清楚原因 / with no printer it must fail with a reason
        if not last["ok"]:
            self.assertTrue(last.get("message"), "失败时必须给出原因 / a failure must explain itself")

    def test_queue_can_be_cleared(self):
        status, raw = http("DELETE", "/api/queue")
        self.assertEqual(status, 200)
        _, q = http_json("GET", "/api/queue")
        self.assertEqual(q["count"], 0)

    # ------------------------------------------------------------------ 旧接口
    # ------------------------------------------------------------------ legacy endpoints

    def test_legacy_endpoints_still_work(self):
        """convert.md 要求保持既有 API 兼容 / the task requires the old API to keep working."""
        for path in ("/api/stats", "/api/language", "/api/languages", "/api/settings"):
            status, _ = http("GET", path)
            self.assertEqual(status, 200, "%s should still respond" % path)

    def test_images_endpoint_is_gone(self):
        """服务端不再出图 / the server produces no images any more."""
        status, _ = http("GET", "/images/anything.png")
        self.assertIn(status, (404, 500, 501))

    def test_secrets_are_not_served_over_http(self):
        """
        settings.json 里存着 DeepSeek API key,而这个服务的设计前提是局域网可达
        (DE1 插件要往里上传)。所以「不能把运行目录当网站根目录」是硬要求 ——
        继承 SimpleHTTPRequestHandler 的默认行为正好会那么做,这是从上一版继承
        过来的真实暴露面,不是理论风险。

        settings.json holds the DeepSeek API key and this service is designed to be
        reachable on the LAN (the DE1 plugin uploads to it). "Do not serve the working
        directory" is therefore a hard requirement — inheriting
        SimpleHTTPRequestHandler's defaults does exactly that, and that was a real
        exposure inherited from the previous version, not a theoretical one.
        """
        for path in ("/settings.json", "/translations.json", "/print_the_shot_server.py",
                     "/.gitignore", "/tests/test_server.py", "/backup/"):
            status, raw = http("GET", path)
            self.assertEqual(status, 404, "%s must not be served" % path)
            self.assertNotIn(b"deepseek_key", raw)

    def test_traversal_is_blocked_on_static_routes(self):
        """
        静态路由的目录穿越要挡住 —— 用 %2e%2e 之类编码绕开客户端规范化。
        Traversal on the static routes must be refused, including percent-encoded
        forms that bypass client-side normalisation.
        """
        for path in ("/web/..%2f..%2fprint_the_shot_server.py",
                     "/fonts/..%2f..%2fsettings.json",
                     "/web/%2e%2e/%2e%2e/settings.json"):
            status, raw = http("GET", path)
            self.assertNotEqual(status, 200, "%s must not return content" % path)
            self.assertNotIn(b"deepseek_key", raw)

    def test_root_level_assets_are_served(self):
        """
        web/ 里的资源同时挂在根路径上,这样 index.html 的相对路径在浏览器和 APK
        里都成立 —— APK 里 Capacitor 把 web/ 的内容放在资源根目录。
        少挂一个,打包出来就是样式和脚本全 404,而浏览器里一切正常。

        web/ assets are also mounted at the root so index.html's relative paths work
        both in a browser and inside the APK, where Capacitor puts web/'s contents at
        the root of the assets. Miss one and the packaged app 404s on its styles and
        scripts while the browser looks perfectly fine.
        """
        for path in ("/style.css", "/app.js", "/render.js", "/printer.js",
                     "/bt.js", "/strings.js"):
            status, raw = http("GET", path)
            self.assertEqual(status, 200, "%s should be reachable at the root" % path)
            self.assertGreater(len(raw), 100)

    def test_bundled_font_reachable_from_both_layouts(self):
        """
        字体必须两种布局下都能取到 —— 它决定了 Mac 和 Android 渲染是否一致。
        The font must resolve under both layouts; it is what makes macOS and Android
        render identically.
        """
        for path in ("/fonts/NotoSansCJKsc-Regular.otf",
                     "/web/fonts/NotoSansCJKsc-Regular.otf"):
            status, raw = http("GET", path)
            self.assertEqual(status, 200, "%s should resolve" % path)
            self.assertGreater(len(raw), 100000)

    def test_plugin_download_still_works(self):
        status, raw = http("GET", "/plugin/plugin.tcl")
        self.assertEqual(status, 200)
        self.assertGreater(len(raw), 1000)

    # ------------------------------------------------------------------ 备份
    # ------------------------------------------------------------------ backups
    #
    # 这几个用例都**基于真实导出包**来构造:导回去的都是同名文件,属于覆盖,
    # 不会往 shots_data 里灌新记录 —— 测试不该在用户的数据目录里留垃圾。
    #
    # These cases are built from a real export on purpose: everything they import has a
    # name that already exists, so it is an overwrite and no new record lands in
    # shots_data. Tests should not leave junk in the user's data directory.

    def _post_raw(self, path, raw, content_type, timeout=60):
        """发原始字节 / POST raw bytes with an explicit content type."""
        req = urllib.request.Request(
            BASE + path, data=raw, headers={"Content-Type": content_type}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _backup_zip(self):
        """拿一份导出的备份包 / fetch one exported backup archive."""
        status, raw = http("GET", "/api/backup/export")
        self.assertEqual(status, 200, "导出应当成功 / the export should succeed")
        return raw

    def test_backup_export_is_a_real_archive(self):
        """
        导出的必须是一个**内容对得上**的 zip,而不是一个大小不为零的文件。

        这条断言有来历:上一次的「备份」只检查了「文件在、2560 字节」,事后才发现
        包里只有目录项和一个 2 字节的 index.json,20 多条记录一条没进去 —— 而那时
        数据已经不可恢复。所以这里把包拆开逐条验证:条目数要对得上,每条都要能
        json.loads,每条都不能是空的。

        The export must be a zip whose **contents** check out, not merely a file with a
        non-zero size. This assertion has a history: the previous "backup" was only ever
        checked for existence and size, and it turned out to hold nothing but a directory
        entry and a 2-byte index.json — every one of 20-plus records missing, by which
        time the data was unrecoverable. So the archive is opened and checked entry by
        entry: the count has to match, everything has to parse as JSON, nothing may be
        empty.
        """
        import io
        import zipfile

        raw = self._backup_zip()
        self.assertTrue(raw.startswith(b"PK"), "导出应当是 zip / the export should be a zip")

        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            self.assertIn("index.json", names, "备份应带上索引 / the archive should carry the index")
            shots = [n for n in names if n != "index.json"]
            self.assertGreater(len(shots), 0, "备份里应当有 shot / the archive should hold shots")
            for n in names:
                payload = z.read(n)
                self.assertGreater(len(payload), 0, "%s 不该为空 / must not be empty" % n)
                # 坏内容在这里直接炸掉 / malformed content fails right here
                json.loads(payload.decode("utf-8"))

    def test_backup_import_restores_what_was_exported(self):
        """
        导出的包能导回去,而且那条记录真的还在。

        只导出不能导入等于没备份 —— 恢复才是这个功能的全部意义,所以这一条测的是
        「恢复」这条路径本身,而不是导出成功与否。

        An exported archive imports back and the record is really there. Export without
        import is not a backup — restoring is the whole point — so this exercises the
        restore path itself rather than whether the export succeeded.
        """
        raw = self._backup_zip()

        # 从包里挑一条真实记录来核对 / pick one real record out of the archive to verify
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = [n for n in z.namelist() if n != "index.json"]
            self.assertGreater(len(names), 0)
            wanted = names[0]

        status, body = self._post_raw("/api/backup/import", raw, "application/zip")
        self.assertEqual(status, 200, body[:300])
        res = json.loads(body.decode("utf-8"))
        self.assertTrue(res.get("success"), res)
        self.assertGreaterEqual(res.get("imported", 0), 1,
                                "至少要恢复一条 / should restore at least one")

        # 恢复之后那条记录还能取出来 —— 这是「真的还在」的证据
        # The record is retrievable afterwards, which is the proof that it is really back
        status, detail = http("GET", "/api/shot?file=%s" % wanted)
        self.assertEqual(status, 200, "%s 恢复后应当可读 / should be readable after the restore" % wanted)
        self.assertGreater(len(detail), 100)

    def test_backup_import_rejects_garbage(self):
        """
        不是 zip 的东西要被明确拒绝,而不是当成一个空备份报成功。

        A non-zip must be refused outright rather than reported as a successful,
        empty import.
        """
        status, body = self._post_raw("/api/backup/import", b"definitely not a zip",
                                      "application/zip")
        self.assertNotEqual(status, 200, "非 zip 不该报成功 / a non-zip must not report success")
        res = json.loads(body.decode("utf-8", "replace"))
        self.assertFalse(res.get("success"), res)

    def test_backup_import_refuses_path_traversal(self):
        """
        包里带 `../` 的条目必须被跳过,而且不能有文件落到数据目录外面。

        条目名一律取 basename,含 `..` 的路径段直接跳过 —— 备份包里出现 `../` 只有
        两种可能:坏了,或者恶意,两种都不该被当成一条正常记录导进来。

        Entries containing `../` must be skipped and nothing may land outside the data
        directory. Names are always reduced to their basename and any `..` segment is
        skipped outright: a `../` in a backup is either corrupt or hostile, and neither
        belongs in the data directory as a legitimate record.
        """
        import io
        import zipfile

        # 拿一份真实的导出包,往里塞一个穿越条目。其余条目都是同名覆盖,所以这次
        # 导入不会在 shots_data 里留下新文件。
        #
        # Take a real export and slip a traversal entry into it. Every other entry keeps
        # its existing name, so this import leaves no new files behind.
        raw = self._backup_zip()
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as src, \
                zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
            for n in src.namelist():
                dst.writestr(n, src.read(n))
            dst.writestr("../evil.json", json.dumps({"pwned": True}))
        poisoned = buf.getvalue()

        status, body = self._post_raw("/api/backup/import", poisoned, "application/zip")
        self.assertEqual(status, 200, body[:300])
        res = json.loads(body.decode("utf-8"))
        self.assertIn("../evil.json", res.get("skipped", []),
                      "穿越条目应当出现在 skipped 里 / the traversal entry should be listed as skipped")

        # 最要紧的一条:它没有落到数据目录外面
        # The one that matters: nothing escaped the data directory
        data_dir = os.path.join(ROOT, "shots_data")
        for escaped in (os.path.join(ROOT, "evil.json"),
                        os.path.join(os.path.dirname(data_dir), "evil.json")):
            self.assertFalse(os.path.exists(escaped),
                             "穿越条目写出了文件:%s / the traversal entry escaped to %s"
                             % (escaped, escaped))

    def test_update_check_never_concludes_without_checking(self):
        """
        /api/update/check 要么给出一个**明确**的结论,要么承认自己没查成。

        绝不能返回一个没有 update_available 字段的 200 —— 前端会把它读成 falsy,
        界面就显示绿色的「已是最新」,而其实什么都没检查。Android 端当初就是这么
        对用户撒谎的。

        这里不强求联网(GitHub 不通是常态),所以只约束:凡是 200,就必须带上
        布尔型的 update_available;拿不到远端就走错误路径,而不是走「已是最新」。

        A 200 from /api/update/check must either carry a definite verdict or admit that
        the check did not happen. It must never return a 200 with no update_available
        field: the front end reads the absence as falsy and shows a green "up to date"
        without anything having been checked — which is exactly how the Android build
        ended up lying to the user.

        Reaching GitHub is not required (being offline is normal), so the only
        constraint is: any 200 carries a boolean update_available, and a failure to reach
        the remote takes the error path rather than the "up to date" one.
        """
        status, res = http_json("GET", "/api/update/check", timeout=30)
        if status != 200:
            self.assertNotIn("update_available", res,
                             "查不成时不能给出结论 / no verdict when the check failed")
            return
        self.assertIn("local", res)
        self.assertIsInstance(res.get("update_available"), bool,
                              "200 必须带布尔 update_available / a 200 must carry a boolean")


class VersionComparisonTest(unittest.TestCase):
    """
    版本比较器单测 —— 不依赖服务端进程。

    这个函数是「有没有新版」这个判断的**全部依据**,而它历史上已经错过两次:
    先是 beta.2 和 beta.3 比成相等,后来是 release tag 的 `v` 前缀让远端解析成
    全零、永远显得比本地旧。所以下面这些用例不是走过场 —— 每一条都对应一种
    真实发生过的「界面告诉用户已是最新,而其实不是」。

    还有一条是**预防性**的:旧编号 2.1-beta.N 映射进 1.0.N 标尺。不映射的话,
    还装着 2.1-beta.3 的机器会拿 2.1 和 1.0 比、得出「我更新」,从此再也收不到
    更新 —— 而那正是换编号方案时最容易漏掉的一步。

    Unit tests for the version comparator — no server process needed.

    This function is the entire basis of the "is there a newer version" verdict, and it
    has been wrong twice: beta.2 and beta.3 once compared equal, and later the release
    tag's leading `v` made the remote parse to all zeros and look older than anything
    local. So these cases are not ceremony — each maps to a real "the UI said you were
    up to date when you were not".

    One is preventive: mapping the old 2.1-beta.N numbering into the 1.0.N scale.
    Without it, a machine on 2.1-beta.3 compares 2.1 against 1.0, concludes it is ahead,
    and never sees another update — the step most easily missed when renumbering.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, ROOT)
        import print_the_shot_server as srv
        cls.k = staticmethod(srv._version_key)

    def test_leading_v_is_stripped(self):
        """
        release tag 带 v 前缀(v1.0.3)。不剥掉的话正则从头匹配就失败,解析成
        全零,远端永远小于本地 —— 界面永远说「已是最新」。
        """
        self.assertEqual(self.k("v1.0.2"), self.k("1.0.2"))
        self.assertEqual(self.k("V1.0.2"), self.k("1.0.2"))
        self.assertEqual(self.k("vv1.0.2"), self.k("1.0.2"), "多个 v 也应剥掉")
        self.assertGreater(self.k("v1.0.3"), self.k("1.0.2"), "带 v 也要能比出大小")

    def test_a_newer_patch_is_seen_as_newer(self):
        self.assertGreater(self.k("1.0.3"), self.k("1.0.2"))
        self.assertGreater(self.k("1.0.10"), self.k("1.0.9"), "不能按字符串比,10 > 9")
        self.assertGreater(self.k("1.1.0"), self.k("1.0.99"))

    def test_legacy_numbering_maps_into_the_new_scale(self):
        """
        换编号方案时最容易漏的一步:老机器必须还能看到新版。

        2.1-beta.1/2/3 一一对应到 1.0.0-beta.1/2/3(也是那些 release 改名后的
        名字),所以在 beta 阶段和发正式版之后,老机器都会正确看到「有新版」。
        """
        self.assertEqual(self.k("2.1-beta.1"), self.k("1.0.0-beta.1"))
        self.assertEqual(self.k("2.1-beta.2"), self.k("1.0.0-beta.2"))
        self.assertEqual(self.k("2.1-beta.3"), self.k("1.0.0-beta.3"))
        # 老机器之间仍然有序
        self.assertLess(self.k("v2.1-beta.1"), self.k("v2.1-beta.2"))
        self.assertLess(self.k("v2.1-beta.2"), self.k("v2.1-beta.3"))
        # 老机器 vs 新编号:必须看到「有新版」
        self.assertLess(self.k("v2.1-beta.3"), self.k("1.0.0-beta.4"))
        # 而新版机器不该把老版本误判成更新
        self.assertGreater(self.k("1.0.0-beta.4"), self.k("v2.1-beta.3"))

    def test_a_final_release_sorts_after_every_beta_of_the_same_version(self):
        """
        正式版必须排在所有同名 beta 之后 —— 「确认好了再发正式版」这件事
        靠的就是这一条,否则已升到正式版的机器会被提示降级回 beta。
        """
        self.assertLess(self.k("1.0.0-beta.1"), self.k("1.0.0"))
        self.assertLess(self.k("1.0.0-beta.4"), self.k("1.0.0"))
        self.assertLess(self.k("1.0.0-beta.99"), self.k("1.0.0"))
        self.assertGreater(self.k("1.0.0"), self.k("1.0.0-beta.4"))

    def test_prerelease_detection(self):
        """本地是正式版还是 beta,决定了要不要把 beta 推给他。"""
        from print_the_shot_server import _is_prerelease
        self.assertTrue(_is_prerelease("1.0.0-beta.4"))
        self.assertTrue(_is_prerelease("2.1-beta.3"))
        self.assertFalse(_is_prerelease("1.0.0"))

    def test_unparseable_input_is_all_zeros(self):
        for bad in ("", None, "garbage", "unknown", "release-notes"):
            self.assertEqual(self.k(bad), (0, 0, 0, 0, 0), repr(bad))

    # ------------------------------------------------------------ 频道过滤
    # ------------------------------------------------------------ channel filtering

    def _fetch_with(self, releases, channel, local_version=None):
        """
        喂一份假的 release 列表给 fetch_latest_release,免得测试依赖真实 GitHub。

        这一层逻辑(挑最大、排除 draft、按频道排除 beta)是「检查更新」的全部判断
        依据,而它没办法用真实接口稳定地覆盖 —— 真实仓库里不会有我们要的那些组合。

        Feed a fake release list to fetch_latest_release so the test does not depend on
        the live GitHub. This layer — pick the max, drop drafts, drop betas per channel —
        is the whole judgement behind "check for updates", and the live API cannot cover
        it reliably: a real repository will not happen to hold the combinations we need.
        """
        import io
        from unittest import mock
        import print_the_shot_server as srv

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        payload = json.dumps(releases).encode("utf-8")
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            return srv.fetch_latest_release(channel=channel, local_version=local_version)

    def test_channel_filtering(self):
        releases = [
            {"tag_name": "v1.0.1-beta.1", "prerelease": True,  "draft": False, "html_url": "beta"},
            {"tag_name": "v1.0.0",        "prerelease": False, "draft": False, "html_url": "final"},
            {"tag_name": "v9.9.9",        "prerelease": False, "draft": True,  "html_url": "draft"},
        ]
        # 稳定版频道:只看正式版,draft 永远不算
        self.assertEqual(self._fetch_with(releases, "stable"), ("v1.0.0", "final"))
        # Beta 频道:beta 一起看,而且它确实更高
        self.assertEqual(self._fetch_with(releases, "beta"), ("v1.0.1-beta.1", "beta"))

    def test_final_release_wins_over_its_own_beta(self):
        """同一版本的正式版要压过它的 beta —— 否则「beta 用户升到正式版」会失败。"""
        releases = [
            {"tag_name": "v1.0.0",        "prerelease": False, "draft": False, "html_url": "final"},
            {"tag_name": "v1.0.0-beta.9", "prerelease": True,  "draft": False, "html_url": "beta"},
        ]
        self.assertEqual(self._fetch_with(releases, "beta"), ("v1.0.0", "final"))

    def test_an_empty_channel_is_an_answer_not_an_error(self):
        """
        频道为空(比如正式版还没发过)必须返回 None,而不是抛异常 —— 否则界面会显示
        一个 ❌,而事实只是「这个频道还没有东西」。
        """
        releases = [
            {"tag_name": "v1.0.0-beta.1", "prerelease": True, "draft": False, "html_url": "beta"},
        ]
        tag, url = self._fetch_with(releases, "stable")
        self.assertIsNone(tag, "空频道应返回 None")
        self.assertTrue(url.startswith("http"), "但页面地址仍要给出来,便于用户自己去看")

    def test_auto_channel_follows_the_local_version(self):
        """不带频道时的默认:本机是 beta 就走 beta,否则走 stable。"""
        releases = [
            {"tag_name": "v1.0.1-beta.1", "prerelease": True,  "draft": False, "html_url": "beta"},
            {"tag_name": "v1.0.0",        "prerelease": False, "draft": False, "html_url": "final"},
        ]
        self.assertEqual(self._fetch_with(releases, "auto", local_version="1.0.0"),
                         ("v1.0.0", "final"), "正式版本机 → 只走正式版频道")
        self.assertEqual(self._fetch_with(releases, "auto", local_version="1.0.0-beta.4"),
                         ("v1.0.1-beta.1", "beta"), "beta 本机 → beta 频道")

    def test_the_shipped_version_parses(self):
        """本机版本号必须解析得出来 —— 解析失败会让所有比较都失去意义。"""
        from print_the_shot_server import VERSION, VERSION_CODE
        self.assertNotEqual(self.k(VERSION), (0, 0, 0, 0, 0), VERSION)
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+(-[A-Za-z]+\.\d+)?$",
                         "编号方案是 1.0.0 或 1.0.0-beta.N")
        self.assertGreater(VERSION_CODE, 20103,
                           "versionCode 必须高于上一版(20103),否则装不上")


if __name__ == "__main__":
    unittest.main(verbosity=2)
