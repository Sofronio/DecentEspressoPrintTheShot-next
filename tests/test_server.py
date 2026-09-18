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
        cls.proc = subprocess.Popen(
            [sys.executable, "print_the_shot_server.py", "--port", str(PORT)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
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

    def _upload_one(self):
        """上传示例 shot,返回落盘的文件名 / upload the sample shot and return its filename."""
        with open(SAMPLE, "rb") as f:
            payload = f.read()
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
        self.assertTrue(j["version"].startswith("2.1"), j["version"])

    def test_upload_stores_the_shot_and_queues_it_for_printing(self):
        """
        上传是这个服务存在的主要理由:数据要落盘、历史要记上、还要进待打印队列。
        打印由前端那一端完成(它才有打印机),所以服务端这边只入队 —— 队列里
        有没有东西,就是 Auto-print 链路通不通的判据。
        """
        filename = self._upload_one()
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
        self.assertEqual(j["platform"], "mac")   # 测试跑在 macOS 上 / these run on macOS
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
