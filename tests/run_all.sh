#!/bin/bash
# PrintTheShot Next — 全量测试 / full test suite
# ==============================================
#
# 中文
# ----
# 依次跑五组测试,任何一组失败就以非零码退出:
#
#   0. 前端 JS 语法检查 + 生成物一致性(几毫秒,挡住「一个括号写错 = 整个界面白屏」,
#      以及「改了源码却忘了重新导出 web/strings.js」)
#   1. tests/test_printers.py           打印层字节布局(纯逻辑)
#   2. tests/test_platform_dispatch.py  平台探测与 Windows 载荷构造
#   3. tests/test_server.py             服务端 HTTP 接口(起真进程)
#   4. tests/test_cups_e2e.py           真实 CUPS 端到端(需要建测试打印机的权限)
#   5. 浏览器测试                       render.js / printer.js / Web UI / APK 路径
#
# 浏览器那两组需要本机装了 Chrome/Chromium;没有的话会明确跳过而不是假装通过。
#
# 用法 / usage:
#   ./tests/run_all.sh
#
# English
# -------
# Runs five suites in turn and exits non-zero if any of them fails:
#
#   0. front-end JS syntax + generated-file consistency (milliseconds; blocks
#      "one bad paren = blank screen" and "edited the source but forgot to
#      re-export web/strings.js")
#   1. tests/test_printers.py           printing-layer byte layout (pure logic)
#   2. tests/test_platform_dispatch.py  platform detection + Windows payload
#   3. tests/test_server.py             server HTTP API (spawns a real process)
#   4. tests/test_cups_e2e.py           real CUPS round trip (needs lpadmin rights)
#   5. browser tests                    render.js / printer.js / UI / APK path
#
# The browser suites need Chrome or Chromium installed; without it they are
# skipped explicitly rather than silently reported as passing.

set -u
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
FAILED=0

hr() { printf '%s\n' "────────────────────────────────────────────────────────────"; }

# ---------------------------------------------------------------- 0. 语法
# 前端 JS 的语法检查。看着多余,但它挡住的是一整类灾难:一个括号写错,
# 整个脚本不执行,而浏览器只会往控制台里丢一行别人不会去看的 SyntaxError ——
# 用户看到的是白屏,排查起来要从「页面为什么是白的」一路倒推回来。
#
# A syntax check over the front-end JS. It looks redundant but it blocks a whole
# class of disaster: one unbalanced paren and the entire script never runs, while
# the browser only drops a SyntaxError into a console nobody reads. The user sees a
# blank page and has to reason backwards from "why is it white".
hr
echo "0/5  前端脚本:语法 + 生成物一致性 / front-end: syntax + generated files"
hr
SYNTAX_FAILED=0
for f in web/*.js; do
  if node --check "$f" 2>/dev/null; then
    echo "  ✅ $f"
  else
    echo "  ❌ $f"
    node --check "$f" 2>&1 | head -6
    SYNTAX_FAILED=1
  fi
done
if [ "$SYNTAX_FAILED" -ne 0 ]; then
  echo "❌ 前端脚本有语法错误,后面的浏览器测试不用跑了 / syntax errors; skipping browser tests"
  exit 1
fi

# 生成的文案表必须与源码一致。改了 VERSION 或改了文案却忘了重新导出,在这里挡住;
# 「生成的产物被人手改过」也在这里露馅 —— 这正是它要防的事,因为手改当下看不出
# 区别,直到下次重新导出把改动无声冲掉。
#
# The generated string table must match the source. Bumping VERSION, or editing a
# string, without re-exporting is caught here — as is a generated file that was
# hand-edited. That is the whole point: a hand-edit looks fine until the next
# regeneration silently discards it.
if ! python3 scripts/export_strings.py --check; then
  echo "   ➜ 重新导出 / re-run: python3 scripts/export_strings.py"
  exit 1
fi

# ---------------------------------------------------------------- 1. 打印层
run_py() {
  local label="$1"; shift
  hr
  echo "$label"
  hr
  if python3 "$@" 2>&1 | grep -v "ResourceWarning\|_warnings.warn\|tracemalloc" | tail -4; then
    echo "✅ $label — 通过 / OK"
  else
    echo "❌ $label — 失败 / FAILED"
    FAILED=1
  fi
}

run_py "1/5  打印层(字节布局)/ printing layer (byte layout)" tests/test_printers.py
run_py "2/5  平台分派 / platform dispatch"                 tests/test_platform_dispatch.py
run_py "3/5  服务端接口 / server API"                      tests/test_server.py

# ---------------------------------------------------------------- 4. CUPS 端到端
# 真的把任务交给 CUPS,验它吐出来的字节。需要建一台测试打印机的权限,
# 没有就自动 skip —— 不会假装通过。
# Submits a real job to CUPS and inspects the bytes. Needs permission to add a
# test queue; skipped (not silently passed) without it.
run_py "4/5  CUPS 端到端 / CUPS end-to-end"                tests/test_cups_e2e.py

# ---------------------------------------------------------------- 3. 浏览器
hr
echo "5/5  浏览器(render.js / printer.js / Web UI / APK 路径)"
hr

CHROME_FOUND=0
for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
         "/Applications/Chromium.app/Contents/MacOS/Chromium" \
         "$(command -v google-chrome 2>/dev/null)" \
         "$(command -v chromium 2>/dev/null)"; do
  [ -n "$c" ] && [ -x "$c" ] && CHROME_FOUND=1 && break
done

if [ "$CHROME_FOUND" -eq 0 ]; then
  echo "⚠️  未找到 Chrome/Chromium,跳过浏览器测试 / no Chrome found; skipping browser tests"
  echo "    (这不是通过 —— 浏览器相关的代码路径本轮未经验证)"
  echo "    (this is not a pass: the browser code paths went unverified this run)"
elif ! command -v node >/dev/null 2>&1; then
  echo "⚠️  未找到 node,跳过浏览器测试 / no node found; skipping browser tests"
else
  # 起一个服务端供测试页访问 / boot a server for the test pages to talk to
  #
  # PTS_PRINT_DRYRUN 是必须的:web_test.html 里那句「真的发一次 /api/print」在
  # 配了打印机的机器上会**真的打印**。默认打印机若是热敏机,每跑一次测试就灌进去
  # 一张完整图表(约 94 KB),足以把它打懵到持续走纸 —— 实测如此,最后靠断电才停。
  #
  # PTS_PRINT_DRYRUN is not optional: the "really POST /api/print" step in web_test.html
  # **really prints** on a machine that has a printer. With a thermal printer as the
  # default, every test run pushes a full chart (about 94 KB) at it — enough to leave it
  # feeding continuously, which is exactly what happened; it took a power cycle to stop.
  PORT=8780
  PTS_PRINT_DRYRUN=1 python3 print_the_shot_server.py --port "$PORT" >/tmp/pts_test_server.log 2>&1 &
  SERVER_PID=$!
  trap 'kill $SERVER_PID 2>/dev/null' EXIT

  for _ in $(seq 1 60); do
    python3 -c "import socket,sys; sys.exit(0 if socket.socket().connect_ex(('127.0.0.1',$PORT))==0 else 1)" && break
    sleep 0.25
  done

  # 准备一条数据,否则界面测试没有卡片可测
  # put one shot in place, otherwise the UI test has no card to test
  curl -s -X POST "http://localhost:$PORT/upload?machine_id=TEST-RIG" \
       -H 'Content-Type: application/json' \
       --data-binary @sample_shots/prodigal_el_rafugio.json >/dev/null

  for page in /tests/web_test.html /tests/ui_test.html /tests/apk_sim.html; do
    echo ""
    echo "── $page"
    if node tests/run_web_tests.mjs "http://localhost:$PORT" "$page"; then
      echo "✅ $page 通过 / passed"
    else
      echo "❌ $page 失败 / FAILED"
      FAILED=1
    fi
  done

  kill $SERVER_PID 2>/dev/null
  trap - EXIT
fi

hr
if [ "$FAILED" -eq 0 ]; then
  echo "🎉 全部测试通过 / all tests passed"
else
  echo "❌ 有测试失败 / some tests FAILED"
fi
hr
exit $FAILED
