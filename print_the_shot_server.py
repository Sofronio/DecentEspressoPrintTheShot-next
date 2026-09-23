#!/usr/bin/env python3
"""
PrintTheShot Next — 数据中转 + 打印调度服务端 / data relay & print dispatch server
=================================================================================

中文
----
画图这件事已经整个搬到浏览器里了(canvas),服务端退化成两件事:

    1. 数据中转 —— 接收 DE1 上传的 shot JSON、存历史、把数据发给前端
    2. 打印调度 —— 接收前端生成的 1-bit 位图,交给当前平台的打印适配器

服务端不再生成任何图片,因此不再需要 Pillow。绘图代码只有一份,在
`web/render.js` 里,macOS 与 Android 共用,两端渲染结果一致。

运行 / run:
    python print_the_shot_server.py                 # 默认 8000 端口 / default port 8000

English
-------
Chart drawing has moved entirely into the browser (canvas), which reduces the
server to two jobs:

    1. Data relay — receive shot JSON from the DE1, persist history, serve it
    2. Print dispatch — take the 1-bit bitmap the front end produced and hand it
       to the printing adapter for this platform

The server produces no images at all any more and therefore no longer needs
Pillow. There is exactly one renderer, `web/render.js`, shared by macOS and
Android so both platforms draw identically.

Run:
    python print_the_shot_server.py                 # default port 8000
"""

import os
import sys
import json
import time
import base64
import hashlib
import threading
import subprocess
import argparse
import http.server
import socketserver
import urllib.parse
import re
from datetime import datetime
from io import BytesIO

# 版本号。**唯一来源** —— 界面标题、Android 的 versionName、发布 tag 都从这里走。
#
# 编号方案:`1.0.0-beta.N` 是 beta 阶段,`1.0.0` 是第一个正式版。
#
# 对应关系:已发布的 **v2.1-beta.1/2/3 → v1.0.0-beta.1/2/3**(旧的 GitHub
# release 也一并改名,历史编号就此统一),而当前这版(在 beta.3 之上还有备份
# 导出、备份导入、真查更新)是 **1.0.0-beta.4**。
#
# 比较靠 _version_key 的预发布段:1.0.0-beta.4 = (1,0,0,0,4),
# 正式版 1.0.0 = (1,0,0,1,0) —— 后者自然排在所有 beta 之后,所以
# 「确认好了再发正式版」这件事不需要额外的代码。
#
# 旧号仍然要能比:还装着 2.1-beta.3 的机器必须知道自己该更新,
# 映射写在 _version_key 里。
#
# The scheme: `1.0.0-beta.N` during the beta phase, `1.0.0` for the first real
# release.
#
# The mapping: the released **v2.1-beta.1/2/3 become v1.0.0-beta.1/2/3** (the old
# GitHub releases are renamed too, so the numbering is uniform from here on), and
# this build — carrying backup export, backup import and a real update check on top
# of beta.3 — is **1.0.0-beta.4**.
#
# The ordering rides on the prerelease segment in _version_key: 1.0.0-beta.4 is
# (1,0,0,0,4) and the final 1.0.0 is (1,0,0,1,0), which sorts after every beta — so
# "confirm it, then cut a real release" needs no extra code.
#
# The version. **Single source** — the UI title, the Android versionName and the
# release tag all come from here.
#
# The scheme: `1.0.0-beta.N` during the beta phase, `1.0.0` for the first real release.
#
# The mapping: the released v2.1-beta.1/2/3 were renamed to v1.0.0-beta.1/2/3 on
# GitHub, and this build — carrying backup export, backup import and a real update
# check on top of beta.3 — is `1.0.0-beta.4`.
#
# The ordering rides on the prerelease segment in _version_key: 1.0.0-beta.4 is
# (1,0,0,0,4) and the final 1.0.0 is (1,0,0,1,0), which sorts after every beta — so
# "confirm it, then cut a real release" needs no extra code.
#
# The old numbers still have to compare correctly: a machine running 2.1-beta.3 must
# know it should update. That mapping lives in _version_key.
VERSION = "1.0.0-beta.4"

# Android 的 versionCode,**独立于 VERSION 单调递增**。
#
# 从前它是从 VERSION 推导的(2.1-beta.3 → 20103),换号之后那条路会出人命:
# 同一公式对 1.0.2 得 10002,**低于所有已装 APK**,Android 会以「降级」为由
# 拒绝安装(INSTALL_FAILED_VERSION_DOWNGRADE),用户只能卸载重装 —— 那正好
# 会清掉他所有的 shot 数据。
#
# 所以它拆出来单独维护:发版时 VERSION 和 VERSION_CODE **一起改**,
# 后者只增不减。当前值 = 上一版 20103 + 1,保证能覆盖现有安装。
#
# Android's versionCode, kept **independent of VERSION** and monotonically
# increasing.
#
# It used to be derived from VERSION (2.1-beta.3 → 20103). After the renumbering that
# path is dangerous: the same formula gives 1.0.2 → 10002, which is **lower than every
# installed APK**, so Android refuses the install as a downgrade
# (INSTALL_FAILED_VERSION_DOWNGRADE) and the user has to uninstall — which is exactly
# what wipes all their shot data.
#
# So it is maintained separately: bump VERSION and VERSION_CODE **together** on every
# release, and the latter only ever goes up. Current value = previous 20103 + 1, so it
# covers existing installs.
VERSION_CODE = 20104

def runtime_data_dir():
    """
    可写的数据目录 / the writable data directory.

    源码运行:用当前工作目录(维持原行为,现有用户的数据不动)。
    打包运行:必须换地方。.app 由 Finder / `open` 启动时,工作目录是 "/",而那是
    只读的 —— 在那里建 shots_data 会直接抛
    `OSError: [Errno 30] Read-only file system: 'shots_data'` 并让进程退出。
    用户看到的是「双击了,什么都没发生」。

    改用各系统约定的用户数据目录。

    Source runs: the current working directory, unchanged, so existing users' data
    stays where it is.
    Packaged runs: somewhere else, necessarily. When a .app is launched from Finder
    or `open`, the working directory is "/", which is read-only — creating
    shots_data there raised `OSError: [Errno 30] Read-only file system` and killed
    the process. All the user saw was "I double-clicked it and nothing happened".

    Falls back to the platform's conventional per-user data location.
    """
    if not getattr(sys, "frozen", False):
        return os.getcwd()
    if sys.platform.startswith("darwin"):
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "PrintTheShot")
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PrintTheShot")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "PrintTheShot")

DATA_DIR = os.path.join(runtime_data_dir(), "shots_data")
# 注意:不再有 shots_images 目录 —— 服务端不生成图片了
# Note: there is no shots_images directory any more; the server draws nothing
PRINT_ENABLED = True
#: 打包版启动时自动打开管理界面 / open the web UI on start for packaged builds
NO_BROWSER = not getattr(sys, "frozen", False)
BEAN_INFO_ENABLED = True
MAX_USERS = 5
# 打印请求体上限。576 点宽 × 最长约 5000 点高 ≈ 360KB 原始位图,base64 后约
# 480KB,所以 8MB 留了很宽的余量,同时仍然能挡住明显异常的请求。
# Cap on the print request body. A 576-dot-wide, ~5000-dot-tall bitmap is about
# 360KB raw and ~480KB in base64, so 8MB leaves plenty of headroom while still
# rejecting anything clearly bogus.
MAX_PRINT_BODY = 8 * 1024 * 1024

# 备份 zip 的上传上限。一条 shot 的 JSON 约 20-40KB,5000 条也就 200MB 上下,
# 但真到那个量级应该先分卷 —— 这里给 64MB,够覆盖正常的一次完整导出,同时挡住
# 明显异常的请求。解压后总量另设上限(见 handle_backup_import),防 zip 炸弹。
# Cap on an uploaded backup zip. One shot's JSON is roughly 20-40KB, so 64MB covers a
# normal full export while still rejecting anything clearly bogus. The *inflated* size
# gets its own cap inside handle_backup_import, which is what stops a zip bomb.
MAX_BACKUP_BODY = 64 * 1024 * 1024
MAX_BACKUP_UNPACKED = 256 * 1024 * 1024
MAX_BACKUP_ENTRY = 8 * 1024 * 1024

# 同时挂在根路径上的 web 资源 / web assets also mounted at the root
ROOT_ASSETS = {
    "/style.css", "/app.js", "/render.js", "/printer.js", "/bt.js",
    "/strings.js", "/index.html", "/render.test.html",
}
received_shots = []
shots_lock = threading.Lock()
print_jobs = []  # 内存打印队列 / in-memory print queue
server_start_time = datetime.now()

# ---------------------------------------------------------------------------
# 资源路径:兼容源码运行与 PyInstaller 打包运行 Resource paths: work both from source and from PyInstaller bundles
# ---------------------------------------------------------------------------
def resource_path(rel):
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, rel)

# 自更新与插件分发的地址 —— 必须指向**本项目自己的仓库**。
#
# 原来这三个都指向 Sofronio/DecentEspressoPrintTheShot(原项目),而那个仓库里
# 是 v1.6。后果不是「偶尔拉错版本」,而是自更新彻底失效:「检查更新」读到的远端
# 版本恒为 1.6,永远判定为「已是最新」,用户永远收不到更新。
#
# 之所以没造成破坏,只是因为 1.6 比当前版本号小,更新按钮保持禁用 —— 万一哪天
# 原仓库的版本号被改大,那就成了「把用户降级到 v1.6」。
#
# These three URLs must point at **this project's own repository**.
#
# They used to point at Sofronio/DecentEspressoPrintTheShot, which holds v1.6. The
# consequence was not "occasionally the wrong version" but a self-update that never
# worked at all: the remote version read as 1.6 forever, so the check always said
# "already up to date" and no update was ever offered.
#
# It caused no damage only because 1.6 sorts below the current version, leaving the
# update button disabled — had that repository's version ever been bumped, this
# would have downgraded users to v1.6.
_REPO = "Sofronio/DecentEspressoPrintTheShot-next"
PLUGIN_GITHUB_URL = f"https://raw.githubusercontent.com/{_REPO}/main/plugin/plugin.tcl"

# 自更新与「检查更新」现在都以**最新 release** 为准,而不是 main 分支。
#
# 为什么改 —— 跟 main 有个要命的窗口期。发布流程是第 2 步改 VERSION、第 6 步才打
# tag,中间那段时间 main 上躺着一个**已改大但还没发布**的版本号。跟 main 的话,
# 这个窗口里每个源码版用户都会被提示「有更新」,而点下去装到的是没发过、没人测过
# 的代码。跟 release 之后,「有更新」永远等于「有一个已发布、测过的版本」。
#
# 另外两端本来就只能跟 release:APK 和打包版都不能原地更新自己,只能把人送到
# release 页面去下载。
#
# Self-update and "check for updates" both follow the **latest release** now, not main.
#
# Why: tracking main has a dangerous window. The release flow bumps VERSION in step 2
# and tags in step 6, so in between, main carries a version number that is **already
# raised but not yet released**. Tracking main would tell every source-mode user there
# is an update, and installing it would give them code that was never released or
# tested. Tracking the release makes "there is an update" always mean "there is a
# released, tested version".
#
# The other two ends could only ever follow releases anyway: neither an APK nor a
# packaged desktop build can replace itself, so all they can do is send the user to the
# release page.
GITHUB_API_RELEASES = f"https://api.github.com/repos/{_REPO}/releases?per_page=30"
RELEASES_PAGE = f"https://github.com/{_REPO}/releases"


def _is_prerelease(v):
    """正式版还是预发布版 / whether a version string is a final release."""
    return _version_key(v)[3] == 0


def fetch_latest_release(channel="auto", local_version=None, timeout=15):
    """
    取 GitHub 上**本机应该看到的**最新 release,返回 (tag, 页面地址)。

    为什么是列表而不是 /releases/latest
    -----------------------------------
    GitHub 对那个端点的定义是「最新的**非 pre-release、非 draft** 的 release」,
    一个 pre-release 都没有时直接 **404**。而本项目在 beta 阶段**每个 release
    都标 pre-release** —— 用 latest 的话检查更新会整个失效,而且失效方式很隐蔽:
    返回 404 → 被我们当成「查询失败」→ 界面显示一个 ❌,而不是「有新版」。

    自己从列表里挑最大还有个额外好处:**按版本号排序,而不是按发布日期**。
    于是「发布顺序必须等于版本号顺序」这个脆弱前提就不需要了。

    频道
    ----
    界面上有「检查更新(稳定版)」和「检查更新(Beta版)」两个按钮,对应这里的
    channel:

        stable —— 只在正式版里挑。用稳定版的人不该被推去装 beta。
        beta   —— 正式版和 beta 一起挑。装着 beta 的人要能升到正式版。
        auto   —— 不带参数时的默认:频道跟着本机版本走(本机是 beta 就走 beta,
                  否则走 stable)。没有界面在用,是给直接调接口的人一个合理默认。

    某个频道下一个 release 都没有(比如正式版还没发过)时返回 (None, 页面地址),
    而不是抛异常 —— 「这个频道还没有东西」是一个正常答案,不是查询失败。
    网络/权限问题才抛异常。

    Channel
    -------
    The UI has two buttons — "check for updates (stable)" and "(beta)" — which map to
    this `channel`:

        stable — final releases only. Someone on a stable build should not be pushed
                 onto a beta.
        beta   — finals and betas together. A machine on a beta has to be able to move
                 up to the final.
        auto   — the default when no channel is given: follow the local version (a beta
                 build checks the beta channel, otherwise stable). Nothing in the UI
                 uses it; it is a sensible default for anyone calling the endpoint
                 directly.

    When a channel holds no releases at all (no stable cut yet, say) this returns
    (None, page) rather than raising — "this channel is empty" is an answer, not a
    lookup failure. Only network or permission problems raise.

    Returns the newest release **in that channel** as (tag, html_url).

    Why the list rather than /releases/latest
    -----------------------------------------
    GitHub defines that endpoint as "the most recent **non-prerelease, non-draft**
    release" and answers **404** when there is no such release. This project flags
    *every* release as a pre-release during the beta phase, so latest would break
    update checking outright — and break it quietly: a 404 becomes our "lookup failed"
    path, showing a ❌ rather than "there is a new version".

    Picking the maximum ourselves also has a bonus: the ordering is **by version, not
    by publish date**, so the fragile "release order must match version order"
    precondition disappears.

    Why filter by the local version
    -------------------------------
    Someone already on a final release should not be pushed onto a beta. So a final
    local version picks among final releases only, while a beta local version considers
    both (it has to be able to move up to the final one).

    失败时抛异常 —— 调用方负责把它变成一句诚实的错误,而不是一个结论。
    Raises on failure; callers turn that into an honest error rather than a verdict.
    """
    import urllib.request, json as _json
    local = local_version or VERSION
    req = urllib.request.Request(GITHUB_API_RELEASES, headers={
        # GitHub API 不带 User-Agent 会直接 403,不是可选项
        # The GitHub API answers 403 without a User-Agent; it is not optional.
        "User-Agent": "PrintTheShotNext/" + VERSION,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        releases = _json.loads(r.read().decode("utf-8", "replace"))

    if channel == "beta":
        want_prerelease = True
    elif channel == "stable":
        want_prerelease = False
    else:                       # auto:频道跟着本机版本走
        want_prerelease = _is_prerelease(local)

    best_tag, best_url = None, RELEASES_PAGE
    for rel in releases:
        if rel.get("draft"):
            continue
        if rel.get("prerelease") and not want_prerelease:
            continue
        tag = rel.get("tag_name", "")
        if not tag:
            continue
        if best_tag is None or _version_key(tag) > _version_key(best_tag):
            best_tag, best_url = tag, rel.get("html_url", RELEASES_PAGE)

    # 频道为空不算错误 —— 调用方据此说「这个频道还没有版本」,而不是报一个查不到的错
    # An empty channel is not an error; callers say "nothing in this channel yet"
    # rather than reporting a lookup failure.
    return best_tag, best_url


WEB_INDEX = resource_path(os.path.join("web", "index.html"))
PLUGIN_TCL = resource_path(os.path.join("plugin", "plugin.tcl"))  # bundle内(只读)


def _version_key(v):
    """
    把版本号解析成可比较的元组 / parse a version string into something comparable.

    原来只取 major.minor,于是 '2.0-beta.2' 和 '2.0-beta.3' 都变成 (2, 0),
    比较结果相等 —— 「检查更新」会告诉 beta.2 的用户「已是最新」,而实际上不是。
    预发布版本更新得越勤,这个 bug 越致命。

        '1.0.0-beta.3' -> (1, 0, 0, 0, 3)
        '1.0.0-beta.4' -> (1, 0, 0, 0, 4)   比上面大
        '1.0.0'        -> (1, 0, 0, 1, 0)   正式版排在所有同名 beta 之后
        '2.1-beta.3'   -> (1, 0, 0, 0, 3)   旧编号,映射成 1.0.0-beta.3

    第四位是「是否正式版」的哨兵:0 = 预发布,1 = 正式版。

    The old version took only major.minor, so '2.0-beta.2' and '2.0-beta.3' both
    became (2, 0) and compared equal — "check for updates" told beta.2 users they
    were already current when they were not.

    The prerelease number is now part of the comparison. The fourth element is a
    sentinel: 0 for a prerelease, 1 for a final release.
    """
    import re
    v = (v or "").strip()

    # release tag 带 v 前缀(v1.0.0-beta.3),而 re.match 是从头匹配的 —— 不剥掉的话
    # 它直接解析失败、落到 (0,0,0,0,0),于是**远端永远显示得比本地旧**,界面永远说
    # 「已是最新」。这跟上面 docstring 里那段历史是同一类错误。
    #
    # Release tags carry a leading v (v1.0.0-beta.3) and re.match anchors at the start,
    # so without stripping it the tag fails to parse and lands on (0,0,0,0,0) — making
    # the remote look **older than anything local**, so the UI would say "up to date"
    # forever. Same family as the history in the docstring above.
    v = v.lstrip("vV")

    m = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", v)
    if not m:
        return (0, 0, 0, 0, 0)
    major, minor = int(m.group(1)), int(m.group(2))
    patch = int(m.group(3) or 0)
    pre = re.search(r"(alpha|beta|rc|next|pre)[.\-]?(\d+)", v, re.I)
    if pre:
        n = int(pre.group(2))
        # 旧编号时代(2.1-beta.N)映射成 1.0.0-beta.N —— 也就是那些 release
        # 改名之后的名字,一一对应。
        #
        # 不映射的话,还装着 2.1-beta.3 的机器会拿 2.1 和 1.0 比,得出「我更新」,
        # 从此再也收不到更新 —— 那正是这个函数当初要修的那类「谎报已是最新」。
        #
        # Versions from the old numbering (2.1-beta.N) map to 1.0.0-beta.N — exactly
        # the names those releases were renamed to, one for one.
        #
        # Without that, a machine on 2.1-beta.3 compares 2.1 against 1.0, concludes it
        # is ahead, and never sees another update — the same "up to date" lie this
        # function exists to prevent.
        if major == 2:
            return (1, 0, 0, 0, n)
        return (major, minor, patch, 0, n)
    return (major, minor, patch, 1, 0)

def perform_update(zip_url, base_dir, lang="zh"):
    """从GitHub仓库ZIP更新整个服务:下载→校验→备份→替换。
    返回 (success, message)。base_dir = 服务器运行目录(源码模式=仓库根)。"""
    import urllib.request, zipfile, io, shutil
    try:
        req = urllib.request.Request(zip_url, headers={"User-Agent": "PrintTheShotBeta"})
        with urllib.request.urlopen(req, timeout=120) as r:
            zdata = r.read()
        zf = zipfile.ZipFile(io.BytesIO(zdata))
        names = zf.namelist()
        root = names[0].split("/")[0] if names else ""

        def get(rel):
            for n in names:
                if n == f"{root}/{rel}" or n.endswith("/" + rel):
                    return zf.read(n)
            return None

        new_server = get("print_the_shot_server.py")
        new_web = get("web/index.html")
        new_plugin = get("plugin/plugin.tcl")
        # Web UI 现在拆成了几个文件,更新时必须一起去,否则会出现新旧版本混搭
        # The web UI is split across several files now and they must move together,
        # otherwise the server ends up running a mix of old and new front-end code
        new_assets = {}
        for rel in ("web/app.js", "web/render.js", "web/printer.js",
                    "web/style.css", "web/strings.js"):
            blob = get(rel)
            if blob:
                new_assets[rel] = blob
        # 校验 Validation
        if not new_server or b"def main()" not in new_server or b"PrintTheShot" not in new_server:
            return False, ("下载内容异常,已取消" if lang == "zh" else "Downloaded content invalid, aborted")
        if not new_web or b"{{LANG}}" not in new_web:
            return False, ("web模板异常,已取消" if lang == "zh" else "Web template invalid, aborted")

        # 备份(排除fonts:体积大且极少变更) Backup (excluding fonts: large and rarely changes)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(base_dir, "backup", ts)
        os.makedirs(backup_dir, exist_ok=True)
        for rel in ("print_the_shot_server.py", "web/index.html",
                    "web/app.js", "web/render.js", "web/printer.js",
                    "web/style.css", "web/strings.js"):
            src = os.path.join(base_dir, rel)
            if os.path.exists(src):
                dst = os.path.join(backup_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        runtime_plugin = os.path.join(base_dir, "plugin", "plugin.tcl")  # 与base_dir一致,避免测试误写真实路径
        if os.path.exists(runtime_plugin):
            os.makedirs(os.path.join(backup_dir, "plugin"), exist_ok=True)
            shutil.copy2(runtime_plugin, os.path.join(backup_dir, "plugin", "plugin.tcl"))

        # 写入新文件 Write new files
        with open(os.path.join(base_dir, "print_the_shot_server.py"), "wb") as f:
            f.write(new_server)
        with open(os.path.join(base_dir, "web", "index.html"), "wb") as f:
            f.write(new_web)
        for rel, blob in new_assets.items():
            dst = os.path.join(base_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(blob)
        if new_plugin and b"print_the_shot" in new_plugin:
            with open(runtime_plugin, "wb") as f:
                f.write(new_plugin)
        msg = (f"更新完成,备份在 backup/{ts}/,请重启服务器生效" if lang == "zh"
               else f"Update complete — backup at backup/{ts}/, restart the server to apply")
        print(f"🔄 {msg}")
        return True, msg
    except Exception as e:
        return False, (f"更新失败: {e}" if lang == "zh" else f"Update failed: {e}")

def plugin_runtime_path():
    """插件运行时路径:优先 CWD/plugin/(可写,支持GitHub更新);
    打包环境下首次运行从bundle复制过去。"""
    runtime_dir = os.path.join(runtime_data_dir(), "plugin")
    os.makedirs(runtime_dir, exist_ok=True)
    runtime_path = os.path.join(runtime_dir, "plugin.tcl")
    if not os.path.exists(runtime_path) and os.path.exists(PLUGIN_TCL):
        import shutil
        shutil.copy2(PLUGIN_TCL, runtime_path)
    return runtime_path

# ---------------------------------------------------------------------------
# 多语言 / i18n i18n / Localization
# ---------------------------------------------------------------------------
LANGUAGES = {
    "en": {
        "server_title": "PrintTheShot Next Server v{VERSION}",
        "status_running": "Running",
        "start_time": "Start Time",
        "shots_received": "Shots Received",
        "active_users": "Active Users",
        "max_users": "Max Users",
        "print_enabled": "Printing Enabled",
        "bean_info_enabled": "Bean Info Enabled",
        "print_queue": "Print Queue",
        "enable_print": "Enable Printing",
        "disable_print": "Disable Printing",
        "enable_bean_info": "Enable Bean Info",
        "disable_bean_info": "Disable Bean Info",
        "refresh": "Refresh",
        "clear_queue": "Clear Queue",
        "queue_empty": "Queue is empty",
        "data_upload": "Data Upload",
        "drag_drop": "Drag and drop JSON file here or click to select",
        "recent_data": "Recently Received Data",
        "no_data": "No data available",
        "print": "Print",
        "print_job_sent": "Print job sent",
        "plugin_download": "Download DE1 Plugin",
        "plugin_instructions": "Plugin Installation",
        # 序号由页面上的 <ol> 自动排,文案里不要再写「1.」/ The <ol> numbers these.
        "plugin_step1": "Download plugin.tcl and copy it to the DE1 tablet: /de1plus/plugins/print_the_shot/plugin.tcl",
        "plugin_step2": "If there is no print_the_shot folder, create one inside the plugins folder",
        "plugin_step3": "Restart DE1App — the plugin loads automatically",
        # 插件的地址和路径是两个字段,但写在同一行 —— 它们是连着做的两件事,拆成
        # 两条只是把列表拉长。中间用空格隔开,免得被读成一个值。
        #
        # 空格用的是 U+00A0(不换行空格)而不是普通空格:HTML 会把连续空格压成
        # 一个,写三个普通空格在这里显示出来只有一个。  不会被压缩。
        #
        # The plugin has two fields here, but they go on one line: they are two things
        # done in one go, and splitting them only lengthens the list. A gap separates
        # them so they do not read as a single value.
        #
        # The gap is U+00A0, not ordinary spaces: HTML collapses runs of spaces into
        # one, so three of them would render as a single space.   is not collapsed.
        "plugin_step4": "Server address {HOST}   Path: upload",
        "plugin_step4_fallback": "this machine's IP:8000",
        "language": "Language",
        "queue_status": "Queue Status: {count} jobs",
        "chart_pressure": "Pressure (Bar)",
        "chart_flow": "Flow Rate (g/s)",
        "chart_water_flow": "Water Flow",
        "chart_coffee_flow": "Coffee Flow",
        "chart_temperature": "Temp (°C)",
        "chart_time": "Time (s)",
        "chart_date_time": "Date&Time",
        "chart_profile": "Profile",
        "chart_extraction": "Extraction",
        "chart_grinder_temp": "Grind&Temp",
        "chart_in_weight": "In",
        "chart_out_weight": "Out",
        "chart_shot_time": "Time",
        "chart_grind_setting": "Grind",
        "chart_initial_temp": "Temp",
        "chart_unknown_profile": "Unknown Profile",
        "chart_na": "N/A",
        "chart_bean_info": "Bean Info",
        "chart_profile_info": "Profile Info",
        "chart_tasting_note": "Tasting Note",
        "h_print": "Printing",
        "print_platform": "Platform",
        "print_transport": "Transport",
        "print_printer": "Printer",
        "print_default": "System default",
        "print_mode": "Mode",
        "print_width": "Width",
        "print_saved": "Print settings saved",
        "btn_shutdown": "Stop service",
        "shutdown_confirm": "Stop the PrintTheShot service? The web interface will become unavailable and you will need to start the app again.",
        "shutdown_ok": "Service stopped",
        "shutdown_failed": "Could not stop the service",
        "shutdown_denied": "Shutdown is not allowed from this address",
        "shutdown_sent": "Stopping… this page will stop responding.",
        "upload_failed": "Upload failed — check the server address and that the server is running.",
        "lan_address_label": "Device address",
        "lan_address_hint": "Use this as the server address in the DE1 plugin",
        "keepalive_label": "Background keep-alive",
        "keepalive_hint": "Keeps the server and printing alive while another app is in the foreground",
        "keepalive_on": "Running",
        "keepalive_off": "Off",
        "keepalive_turn_on": "Turn on",
        "keepalive_turn_off": "Turn off",
        "setup_title": "Connect to the server",
        "setup_hint": "Enter the address of the PrintTheShot server on your network (the machine running print_the_shot_server.py).",
        "setup_connect": "Connect",
        "setup_required": "Please enter an address",
        "setup_testing": "Testing the connection…",
        "setup_ok": "Connected — server v{v}",
        "setup_failed": "Could not reach the server.",
        "setup_prompt": "Server address (host:port)",
        "bt_title": "Bluetooth printer",
        "bt_status_label": "Connection",
        "bt_request": "Grant permission",
        "bt_refresh": "Refresh list",
        "bt_disconnect": "Disconnect",
        "bt_test": "Test print",
        "bt_connected": "Connected",
        "bt_saved_not_connected": "Selected, not connected",
        "bt_none": "No printer selected",
        "bt_requesting": "Requesting permission…",
        "bt_granted": "Permission granted",
        "bt_denied": "Permission denied — grant Bluetooth access in Android settings",
        "bt_scanning": "Loading paired devices…",
        "bt_empty": "No paired printers yet. Pair the printer in Android Settings → Bluetooth first.",
        "bt_empty_hint": "If the printer is already paired, Bluetooth permission is probably missing.",
        "bt_unpaired": "not paired",
        "bt_connect": "Connect",
        "bt_reconnect": "Reconnect",
        "bt_connecting": "Connecting…",
        "bt_connect_ok": "Connected",
        "bt_connect_fail": "Connection failed",
        "bt_android_only": "Bluetooth printing is Android-only",
        "bt_testing": "Sending a test page…",
        "bt_test_ok": "Test page sent — check the printer",
        "bt_test_fail": "Test print failed",
        "view_large": "Click to view large image",
        "machine_id": "",
        "print_control": "Print Control",
        "upload_success": "Upload successful",
        "print_disabled": "Printing disabled",
        "all_dates": "All dates",
        "latest_n": "Latest 9",
        "show_latest_9": "Showing the latest 9 shots. Use date filter for the full day.",
        "capped_note": "Too many shots — showing latest 100. Please filter by date.",
        "h_stats": "Statistics",
        "stat_total": "Total Shots",
        "stat_dates": "Days",
        "stat_machines": "Machines",
        "stat_avg_size": "Avg Data Size",
        "stat_prints": "Prints (session)",
        "stat_profiles": "Top Profiles",
        "stat_per_date": "By Date",
        "stat_older": "{n} earlier days",
        "stat_avg_day": "avg {n}/day",
        "stat_beans": "Bean Distribution",
        "prev_day": "Prev day",
        "next_day": "Next day",
        "plugin_local": "Download plugin (local · matches this version)",
        "plugin_github": "Download from GitHub (latest)",
        "plugin_txt": "Download TXT (for Bluetooth send)",
        "per_page": "per page",
        "total_n": "Total {n}",
        "plugin_note": "The local plugin matches this server version; the GitHub one may be newer.",
        "h_ai_settings": "AI Translation and Languages",
        "btn_save_key": "Save API Key",
        "btn_check_balance": "Check Balance",
        "ai_enabled_label": "Enable AI translation for bean info (timeout falls back to original text)",
        "h_languages": "Languages:",
        "btn_add_lang": "Add Language",
        "ai_key_hint": "enter API key",
        "ai_key_saved": "API key saved",
        "ai_toggled": "AI translation setting saved",
        "ai_lang_hint": "language name required",
        "btn_translate": "Translate this chart with AI",
        "ai_translating": "Translating UI strings via AI, ~10-30s...",
        "t_title": "Translate this chart into...",
        "stat_ai_balance": "AI Balance",
        "translate_done": "Translated and re-rendered",
        "update_title": "Service Update",
        "btn_check_update": "Check for updates",
        "btn_check_stable": "Check (stable)",
        "btn_check_beta": "Check (beta)",
        "update_channel_empty": "Nothing released in this channel yet",
        "update_channel_stable": "stable",
        "update_channel_beta": "beta",
        "btn_update_service": "Update service from GitHub (auto backup)",
        "update_check": "Local {local} · Remote {remote}",
        "update_ok": "Up to date",
        "update_avail": "Update available",
        "update_note": "Auto-backup to backup/ before updating; restart the server after update; packaged builds can't self-update.",
        "update_unsupported": "This build updates by installing a new APK — online update is not available.",
        "btn_update_apk": "Download the new APK",
        "update_apk_hint": "Updates come as a new APK. Check for updates to see if one is out.",
        "btn_update_installer": "Get the new installer",
        "update_installer_hint": "This packaged build cannot update itself. Check for updates, then download the new installer.",
        "h_backup": "Backup & restore",
        "btn_backup_export": "Export backup",
        "btn_backup_import": "Restore from backup",
        "backup_note": "Export downloads a ZIP of all shot records. Importing overwrites records with the same filename — it is a restore, not a merge.",
    },
    "zh": {
        "server_title": "PrintTheShot Next 服务器 v{VERSION}",
        "status_running": "运行中",
        "start_time": "启动时间",
        "shots_received": "接收数据",
        "active_users": "并发用户",
        "max_users": "最大用户",
        "print_enabled": "打印已启用",
        "bean_info_enabled": "豆子信息已启用",
        "print_queue": "打印队列",
        "enable_print": "启用打印",
        "disable_print": "禁用打印",
        "enable_bean_info": "启用豆子信息",
        "disable_bean_info": "禁用豆子信息",
        "refresh": "刷新",
        "clear_queue": "清空队列",
        "queue_empty": "队列为空",
        "data_upload": "数据上传",
        "drag_drop": "拖放JSON文件到这里或点击选择",
        "recent_data": "最近接收的数据",
        "no_data": "暂无数据",
        "print": "打印",
        "print_job_sent": "打印任务已发送",
        "plugin_download": "下载DE1插件",
        "plugin_instructions": "插件安装",
        # 序号由页面上的 <ol> 自动排,文案里不要再写「1.」/ The <ol> numbers these.
        "plugin_step1": "下载 plugin.tcl 并复制到 DE1 平板: /de1plus/plugins/print_the_shot/plugin.tcl",
        "plugin_step2": "如果没有 print_the_shot 文件夹,请在 plugins 文件夹内新建 print_the_shot 文件夹",
        "plugin_step3": "重启 DE1App,插件自动加载",
        # 插件的地址和路径是两个字段,但写在同一行 —— 它们是连着做的两件事,拆成
        # 两条只是把列表拉长。中间留一段间隔,免得被读成一个值。
        #
        # 间隔用的是 U+00A0(不换行空格)而不是普通空格:HTML 会把连续空格压成
        # 一个,写三个普通空格在这里显示出来只有一个。不换行空格不会被压缩。
        #
        # The plugin has two fields here, but they go on one line: they are two things
        # done in one go, and splitting them only lengthens the list. A gap separates
        # them so they do not read as a single value.
        #
        # The gap is U+00A0, not ordinary spaces: HTML collapses runs of spaces into
        # one, so three of them would render as a single space. U+00A0 is not collapsed.
        "plugin_step4": "服务器地址填 {HOST}   路径Path填写 upload",
        "plugin_step4_fallback": "本机IP:8000",
        "language": "语言",
        "queue_status": "打印队列: {count} 个任务",
        "chart_pressure": "压力 (巴)",
        "chart_flow": "流速 (克/秒)",
        "chart_water_flow": "水流流速",
        "chart_coffee_flow": "咖啡流速",
        "chart_temperature": "温度 (°C)",
        "chart_time": "时间 (秒)",
        "chart_date_time": "日期时间",
        "chart_profile": "冲煮方案",
        "chart_extraction": "萃取参数",
        "chart_grinder_temp": "研磨与温度",
        "chart_in_weight": "咖啡粉",
        "chart_out_weight": "咖啡液",
        "chart_shot_time": "时间",
        "chart_grind_setting": "研磨度",
        "chart_initial_temp": "温度",
        "chart_unknown_profile": "未知方案",
        "chart_na": "未记录",
        "chart_bean_info": "咖啡豆信息",
        "chart_profile_info": "冲煮方案信息",
        "chart_tasting_note": "品鉴感受",
        "h_print": "打印",
        "print_platform": "平台",
        "print_transport": "通道",
        "print_printer": "打印机",
        "print_default": "系统默认",
        "print_mode": "模式",
        "print_width": "宽度",
        "print_saved": "打印设置已保存",
        "btn_shutdown": "停止服务",
        "shutdown_confirm": "确定要停止 PrintTheShot 服务吗?管理界面将不可用,需要重新启动应用才能恢复。",
        "shutdown_ok": "服务已停止",
        "shutdown_failed": "停止失败",
        "shutdown_denied": "不允许从该地址停止服务",
        "shutdown_sent": "正在停止…本页面将不再响应。",
        "upload_failed": "上传失败 —— 请检查服务端地址,以及服务端是否在运行。",
        "lan_address_label": "本机地址",
        "lan_address_hint": "把它填进 DE1 插件的服务端地址",
        "keepalive_label": "后台常驻",
        "keepalive_hint": "让服务端和打印在别的应用位于前台时继续工作",
        "keepalive_on": "运行中",
        "keepalive_off": "已关闭",
        "keepalive_turn_on": "开启",
        "keepalive_turn_off": "关闭",
        "setup_title": "连接服务端",
        "setup_hint": "填写局域网里 PrintTheShot 服务端的地址(也就是跑 print_the_shot_server.py 的那台机器)。",
        "setup_connect": "连接",
        "setup_required": "请填写地址",
        "setup_testing": "正在测试连接…",
        "setup_ok": "已连接 — 服务端 v{v}",
        "setup_failed": "连不上服务端。",
        "setup_prompt": "服务端地址(主机:端口)",
        "bt_title": "蓝牙打印机",
        "bt_status_label": "连接状态",
        "bt_request": "申请权限",
        "bt_refresh": "刷新列表",
        "bt_disconnect": "断开连接",
        "bt_test": "测试打印",
        "bt_connected": "已连接",
        "bt_saved_not_connected": "已选择,未连接",
        "bt_none": "尚未选择打印机",
        "bt_requesting": "正在申请权限…",
        "bt_granted": "权限已授予",
        "bt_denied": "权限被拒绝 —— 请到 Android 设置里授予蓝牙权限",
        "bt_scanning": "正在读取已配对设备…",
        "bt_empty": "还没有配对的打印机。请先在 Android 设置 → 蓝牙里配对打印机。",
        "bt_empty_hint": "如果打印机已经配对过了,那多半是蓝牙权限没给。",
        "bt_unpaired": "未配对",
        "bt_connect": "连接",
        "bt_reconnect": "重新连接",
        "bt_connecting": "正在连接…",
        "bt_connect_ok": "已连接",
        "bt_connect_fail": "连接失败",
        "bt_android_only": "蓝牙打印仅在 Android 上可用",
        "bt_testing": "正在发送测试页…",
        "bt_test_ok": "测试页已发送 —— 看看打印机",
        "bt_test_fail": "测试打印失败",
        "view_large": "点击查看大图",
        "machine_id": "",
        "print_control": "打印控制",
        "upload_success": "上传成功",
        "print_disabled": "打印已禁用",
        "all_dates": "全部日期",
        "latest_n": "最新 9 条",
        "show_latest_9": "显示最新 9 条,按日期筛选可查看当日全部",
        "capped_note": "数据较多,仅显示最近 100 条,建议按日期筛选",
        "h_stats": "统计数据",
        "stat_total": "总接收数据",
        "stat_dates": "数据天数",
        "stat_machines": "机器数",
        "stat_avg_size": "平均数据大小",
        "stat_prints": "打印次数(本次运行)",
        "stat_profiles": "Top 冲煮方案",
        "stat_per_date": "按日期分布",
        "stat_older": "更早 {n} 天",
        "stat_avg_day": "日均 {n}",
        "stat_beans": "豆子分布",
        "prev_day": "前一天",
        "next_day": "后一天",
        "plugin_local": "下载插件(本地·匹配当前版本)",
        "plugin_github": "从 GitHub 下载(最新)",
        "plugin_txt": "下载 TXT 版(蓝牙发送)",
        "per_page": "每页",
        "total_n": "共 {n} 条",
        "plugin_note": "本地插件与当前服务器版本匹配;GitHub 上的可能更新。",
        "h_ai_settings": "AI 翻译与语言",
        "btn_save_key": "保存 API Key",
        "btn_check_balance": "查余额",
        "ai_enabled_label": "启用 AI 翻译豆子信息(超时自动回退原文)",
        "h_languages": "语言:",
        "btn_add_lang": "新增语言",
        "ai_key_hint": "请输入 API Key",
        "ai_key_saved": "API Key 已保存",
        "ai_toggled": "AI 翻译设置已保存",
        "ai_lang_hint": "请输入语言名称",
        "btn_translate": "AI 翻译本条曲线",
        "ai_translating": "AI 正在翻译界面文案,约需 10-30 秒...",
        "t_title": "翻译本条曲线为...",
        "stat_ai_balance": "AI 余额",
        "translate_done": "已翻译并重新渲染",
        "update_title": "服务更新",
        "btn_check_update": "检查更新",
        "btn_check_stable": "检查更新(稳定版)",
        "btn_check_beta": "检查更新(Beta版)",
        "update_channel_empty": "这个频道还没有发布过版本",
        "update_channel_stable": "稳定版",
        "update_channel_beta": "Beta版",
        "btn_update_service": "从 GitHub 更新服务(自动备份)",
        "update_check": "当前 {local} · 远程 {remote}",
        "update_ok": "已是最新",
        "update_avail": "有更新可用",
        "update_note": "更新前自动备份到 backup/ 目录;更新后请重启服务器;打包版不支持在线更新。",
        "update_unsupported": "本版通过安装新 APK 更新,不支持在线更新。",
        "btn_update_apk": "下载新 APK",
        "update_apk_hint": "更新通过安装新 APK 完成。点「检查更新」看看有没有新版。",
        "btn_update_installer": "下载新安装包",
        "update_installer_hint": "打包版不能自动更新。点「检查更新」看看有没有新版,然后去发布页面下载安装包。",
        "h_backup": "备份与恢复",
        "btn_backup_export": "导出备份",
        "btn_backup_import": "从备份恢复",
        "backup_note": "导出会下载一个包含全部 shot 记录的 ZIP。导入会按文件名覆盖同名记录 —— 是恢复,不是合并。",
    },
}

current_language = "en"   # 默认英文,可在网页右上角切换 / default English; switchable at the top-right of the web UI

# 豆子/方案中英名映射:展示层翻译,存储保持原始名称
# Bean/profile name mapping (zh<->en): translated at the display layer; storage keeps the original
PROFILE_TRANSLATIONS = {
    "温和香甜": "Gentle & Sweet", "甜甜萃": "Sweet", "自适应": "Adaptive",
    "长萃": "Allongé", "绽放": "Blooming", "涡轮": "Turbo", "经典浓缩": "Classic",
    "伦敦之王": "Londinium", "克雷米纳": "Cremina", "高提取": "High Extraction",
}
BEAN_TRANSLATIONS = {
    "哥伦比亚·乌伊拉 卡图拉/卡斯蒂略 · 日晒": "Colombia Huila Caturra/Castillo · Natural",
    "哥伦比亚 鲁比·奇罗索III · 厌氧水洗": "Colombia Rubí Chiroso III · Anaerobic Washed",
    "哥伦比亚 迪纳斯蒂亚瑰夏 · 厌氧水洗": "Colombia Dinastía Gesha · Anaerobic Washed",
    "肯尼亚·涅里 卡利鲁尼AA SL28/鲁伊鲁11 · 水洗": "Kenya Nyeri Kaliluni AA SL28/Ruiru 11 · Washed",
    "埃塞俄比亚·科科塞 · 日晒": "Ethiopia Kokose · Natural",
    "洪都拉斯 戈沙·拉萨尔瓦赫瑰夏 · 水洗": "Honduras Gosha La Salvaje Gesha · Washed",
}

def display_name(name, mapping):
    """按当前界面语言翻译名称;静态表 + AI翻译缓存;未知名称原样返回
    Translate a name for the current UI language: static map first, then the AI translation cache"""
    if not name:
        return name
    if current_language == "en":
        t = mapping.get(name)
        if t:
            return t
    elif current_language == "zh":
        t = {v: k for k, v in mapping.items()}.get(name)
        if t:
            return t
    # 自定义语言或静态表未命中:查AI翻译缓存(仅显示,不改记录)
    cached = translation_cache.get(current_language, {}).get(name)
    if cached:
        return cached
    return name

def get_text(key):
    return LANGUAGES.get(current_language, LANGUAGES["en"]).get(key, key)

# ---------------------------------------------------------------------------
# AI 翻译设置(DeepSeek):设置持久化 + 自定义语言 + 翻译缓存
# AI translation settings (DeepSeek): persisted settings + custom languages + translation cache
# ---------------------------------------------------------------------------
# 和 DATA_DIR 同理:打包后不能依赖工作目录,否则从 Finder 启动时写不进去,
# 用户会看到「设置保存了但下次打开又没了」。
# Same reasoning as DATA_DIR: a packaged app cannot rely on the working directory,
# or settings silently fail to persist when launched from Finder.
SETTINGS_FILE = os.path.join(runtime_data_dir(), "settings.json")
TRANSLATION_CACHE_FILE = os.path.join(runtime_data_dir(), "translations.json")
DEEPSEEK_API = "https://api.deepseek.com/chat/completions"
DEEPSEEK_BALANCE = "https://api.deepseek.com/user/balance"
AI_TIMEOUT = 8          # 单条翻译超时 / single-translation timeout (s)
AI_TIMEOUT_BATCH = 30   # 批量UI文案翻译超时 / batch UI-strings timeout (s)

settings = {"deepseek_key": "", "ai_enabled": False, "languages": {}}
translation_cache = {}  # {"zh": {"原文": "译文"}}  / per-language cache

def load_settings():
    """启动时加载设置与自定义语言 / load settings and custom languages at startup"""
    global settings, translation_cache
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings.update(json.load(f))
    except Exception as e:
        print(f"⚠️ settings.json 读取失败: {e}")
    try:
        if os.path.exists(TRANSLATION_CACHE_FILE):
            with open(TRANSLATION_CACHE_FILE, "r", encoding="utf-8") as f:
                translation_cache = json.load(f)
    except Exception:
        translation_cache = {}
    # 把自定义语言合并进 LANGUAGES,渲染/注入直接可用
    for code, info in settings.get("languages", {}).items():
        if info.get("strings"):
            LANGUAGES[code] = info["strings"]

def save_settings():
    """持久化设置(settings.json 含 API key,已在 .gitignore)"""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"⚠️ 设置保存失败: {e}")

def save_translation_cache():
    try:
        with open(TRANSLATION_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(translation_cache, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

def ai_call(messages, timeout=AI_TIMEOUT):
    """调用 DeepSeek,返回响应文本;失败抛异常 / call DeepSeek, returns text"""
    import urllib.request
    key = settings.get("deepseek_key", "")
    if not key:
        raise RuntimeError("no api key")
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 2000,
    }).encode("utf-8")
    req = urllib.request.Request(DEEPSEEK_API, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp["choices"][0]["message"]["content"].strip()

def clean_bean_text(text):
    """清理豆子信息文本:去掉连字符/间隔符等无效信息,保留数字连字符与单词内连字符
    Clean bean-info text: strip hyphens/separators, keep numeric & word-internal hyphens"""
    if not text:
        return text
    t = str(text)
    t = t.replace("·", " ").replace("–", " ").replace("—", " ")   # 间隔符去掉
    t = t.replace("\u2011", "-")                                    # 恢复受保护的连字符
    t = re.sub(r"(?<=\d)-(?=\d)", "\u2011", t)                    # 数字区间(3-4)保护
    t = t.replace("-", " ")                                          # 其余连字符去掉
    t = t.replace("\u2011", "-")
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"^[,，;；\s]+|[,，;；\s]+$", "", t)
    return t.strip()

def ai_translate(text, target_lang, allow_api=True):
    """把豆子信息翻译成目标语言;缓存优先,静态表快速路径,失败返回原文
    Translate bean info into the target language: cache first, static-map fast path, original on failure.
    allow_api=False 时只查缓存/静态表(视图语言切换用,避免API风暴)"""
    if not text or not text.strip():
        return text
    # 静态表快速路径(演示豆子直接命中,不花token)
    if target_lang == "en":
        t = BEAN_TRANSLATIONS.get(text)
        if t:
            return t
    elif target_lang == "zh":
        t = {v: k for k, v in BEAN_TRANSLATIONS.items()}.get(text)
        if t:
            return t
    # 缓存
    cached = translation_cache.get(target_lang, {}).get(text)
    if cached:
        return cached
    if not allow_api or not settings.get("deepseek_key") or not settings.get("ai_enabled"):
        return text
    # 用语言显示名而非内部代码(如 Français 而不是 lang1),AI才能正确理解
    # use the display name (e.g. Français) instead of the internal code (lang1)
    lang_name = {"en": "English", "zh": "Simplified Chinese"}.get(
        target_lang, settings.get("languages", {}).get(target_lang, {}).get("name", target_lang))
    try:
        out = ai_call([
            {"role": "system",
             "content": f"You translate the following coffee-related text into {lang_name}: "
                        f"1) brew profile names, 2) bean origin/processing, 3) flavor notes, 4) roast levels. "
                        f"Keep brand names, farm names and technical terms intact. Be CONCISE and abbreviate where "
                        f"natural (the text must fit a narrow chart column). Return ONLY the translation."},
            {"role": "user", "content": text},
        ], timeout=AI_TIMEOUT)
        out = clean_bean_text(out)  # 去掉连字符等无效信息 / strip hyphens & stray separators
        translation_cache.setdefault(target_lang, {})[text] = out
        save_translation_cache()
        return out
    except Exception as e:
        print(f"⚠️ AI 翻译失败(使用原文): {e}")
        return text  # 降级:原文,打印不受影响 / fallback: original text

def translate_shot_for_display(shot_data, target_lang, allow_api=True):
    """
    把一条 shot 的展示文案翻成目标语言(豆子信息、方案名),返回新的数据副本。
    Translate a shot's display text into the target language and return a copy.

    这是原来 rerender_shot_in_lang 的替代品:过去这个函数会「翻译 + 重渲染成
    PNG」,现在渲染在浏览器里发生,服务端只负责把翻译好的数据发出去。职责反而
    更单一了 —— 也就更容易测。

    This replaces the old rerender_shot_in_lang, which used to translate and then
    re-render a PNG. Rendering now happens in the browser, so the server only has
    to hand over translated data. The responsibility is narrower, and easier to
    test as a result.
    """
    shot_data = json.loads(json.dumps(shot_data))  # 深拷贝,不改调用方的对象 / deep copy, never mutate the caller's object

    profile = dict(shot_data.get("profile", {}))
    if profile.get("title"):
        profile["title"] = clean_bean_text(ai_translate(str(profile["title"]), target_lang, allow_api))
        shot_data["profile"] = profile

    bean_meta = shot_data.get("meta", {}).get("bean", {}) or {}
    if bean_meta:
        bean_meta = dict(bean_meta)
        bean_meta["type"] = clean_bean_text(ai_translate(str(bean_meta.get("type", "")), target_lang, allow_api))
        bean_meta["notes"] = clean_bean_text(ai_translate(str(bean_meta.get("notes", "")), target_lang, allow_api))
        if bean_meta.get("roast_level"):
            bean_meta["roast_level"] = clean_bean_text(ai_translate(str(bean_meta["roast_level"]), target_lang, allow_api))
        shot_data.setdefault("meta", {})["bean"] = bean_meta

    return shot_data

# ---------------------------------------------------------------------------
# 打印调度 / Print dispatch
# ---------------------------------------------------------------------------
# 服务端不再自己画图,也不再自己拼打印命令:它只把前端送来的 1-bit 位图转交给
# 当前平台的打印适配器。适配器在启动时按 sys.platform 装载一次。
#
# The server no longer draws anything and no longer builds print commands itself:
# it simply hands the 1-bit bitmap from the front end to the adapter for this
# platform. The adapter is loaded once at startup based on sys.platform.
from printers import PrintError, get_printer, platform_id  # noqa: E402

#: 打印相关设置的默认值 / defaults for the print settings
PRINT_DEFAULTS = {
    "printer": "",           # 打印机 id,空 = 系统默认 / printer id, empty = system default
    "mode": "",              # driver / raw / escpos / bmp(空 = 适配器默认)
    "media": "Custom.80x180mm",
    "print_width": 576,      # 打印点数宽度(80mm 机常见 576)/ print width in dots
    "paper_width_mm": 80,
    "feed_lines": 3,
    "cut": True,
    "threshold": 200,        # 二值化阈值 / binarisation threshold
    "rotate": True,
}

def print_config():
    """当前打印配置(默认值 + 已保存的设置)/ current print config (defaults + saved)."""
    cfg = dict(PRINT_DEFAULTS)
    cfg.update(settings.get("print", {}) or {})
    adapter = get_printer()
    cfg["platform"] = adapter.platform_id
    cfg["platform_name"] = adapter.display_name
    cfg["available"] = adapter.is_available()
    cfg["raw_supported"] = adapter.supports_raw()
    return cfg

def refresh_printer(config=None):
    """重新装载打印适配器(设置改变后调用)/ reload the adapter after a settings change."""
    return get_printer(config, reload=True)

# ---------------------------------------------------------------------------
# 待打印队列 / pending-print queue
# ---------------------------------------------------------------------------
# 绘制搬到浏览器之后,「服务端渲染完就打印」这条链路断了 —— 服务端手里没有图。
# 于是改成:上传到达时把文件名挂进这个队列,由持有打印机的那一端(Mac 上的浏览器
# 或 Android WebView)来取,渲染完再 POST /api/print。打印完成后客户端调
# /api/print-queue/ack 把它摘掉,避免重复打印。
#
# Now that drawing lives in the browser, "render then print" on the server side is
# gone — the server simply has no image. Instead an arriving upload is queued by
# filename and claimed by whichever end owns the printer (a browser on macOS or an
# Android WebView), which renders it and POSTs to /api/print. The client then acks
# to remove it, so nothing prints twice.
pending_prints = []      # [{"filename":..., "hash":..., "queued": "...", "attempts": n}]
pending_lock = threading.Lock()
MAX_PENDING = 50

# 打印去重窗口(秒)/ the print de-duplication window, in seconds
# 一份**内容相同**的 shot 在这么久之内不再打印第二次。
# A shot with identical **content** is not printed again within this window.
DEDUPE_WINDOW_S = 30

# 内容哈希 → 最近一次打印成功的时刻 / content hash → when it was last printed
printed_at = {}

def _shot_content_hash(filename):
    """
    按内容算哈希 / hash the stored shot's content.

    为什么按内容而不是按文件名去重:每次上传都会生成一个新文件名(带微秒 ID),
    所以同一个 shot 传三次就是三个不同的名字,按名字拦不住。而上游确实会重复
    上传 —— DE1 侧的 after_flow_complete 可能重复触发,插件里那个
    last_upload_shot 只赋值、从不比较,HTTP 超时重试时服务端其实也已经存盘了。
    结果就是同一张票出好几次。

    读不到文件就返回 None —— 拿不到内容就不参与去重,宁可多打一次,也不要因为
    算不出哈希而漏打。

    Why content rather than filename: every upload gets a fresh filename (it carries a
    microsecond ID), so one shot uploaded three times is three different names and a
    name-based check catches nothing. And the upstream really does repeat — the DE1's
    after_flow_complete can fire more than once, the plugin's last_upload_shot is
    assigned but never compared, and an HTTP timeout retry arrives after the server
    already stored the shot. The result is the same receipt coming out several times.

    Returns None when the file cannot be read: no content, no de-duplication.
    Printing twice beats silently skipping.
    """
    try:
        with open(os.path.join(DATA_DIR, os.path.basename(filename)), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None

def _printed_recently(h, now=None):
    """这份内容刚刚已经打过了吗 / has this content just been printed?"""
    if not h:
        return False
    when = printed_at.get(h)
    return when is not None and ((now or time.time()) - when) <= DEDUPE_WINDOW_S

def _pending_has_hash(h):
    """
    这份内容已经在队列里等着了吗 / is this content already waiting in the queue?

    「已经打印过」拦不住**同时**到达的那几次重复上传:它们在第一份打印完成之前就
    都进来了,那时 printed_at 里还没有东西。要在这里按内容再拦一道,队列才不会
    一下子排进三份一样的。

    "Already printed" does nothing about duplicates that arrive *at the same time*:
    they all land before the first one finishes printing, so printed_at is still empty.
    Checking the queue by content as well is what keeps three identical copies from
    piling up in the first place.
    """
    return bool(h) and any(j.get("hash") == h for j in pending_prints)

def _prune_printed(now=None):
    """
    清掉过期的哈希 / drop hashes whose window has passed.

    不清理的话,这个字典会随运行时间一直长 —— 它按内容哈希累积,和 shot 数量同阶。
    只在记录时顺手扫一遍,不值得为它单开一个线程。

    Without this the dict grows for as long as the process lives — one entry per
    distinct shot. Swept opportunistically on write; not worth its own thread.
    """
    now = now or time.time()
    for h in [h for h, t in printed_at.items() if now - t > DEDUPE_WINDOW_S]:
        del printed_at[h]

def queue_print_job(filename, machine_id=""):
    """
    把一条 shot 挂进待打印队列 / add a shot to the pending-print queue.

    machine_id 跟着任务走 / machine_id rides along on the job.

    它不在 shot 文件里 —— 那份是上传上来的原始 JSON。机器名是服务端在存盘时记进
    索引的,所以要打印的那一端只能从这里拿到。不带的话,票上永远是 UNKNOWN,而
    界面上显示的却是 de1xl。

    It is not in the shot file — that is the uploaded JSON verbatim. The machine name is
    recorded by the server, in the index, at save time, so the end that prints can only
    get it from here. Without it the receipt says UNKNOWN while the UI says de1xl.

    返回是否真的入了队 / returns whether it actually joined the queue.
    被去重吃掉时返回 False —— 调用方靠它决定要不要打日志,否则每次重复上传都会
    打印一句「已加入待打印队列」,而它根本没进队列。
    False when de-duplication swallowed it: the caller uses this to decide whether to
    log, or every duplicate upload claims to have been queued when it was not.
    """
    with pending_lock:
        if any(j["filename"] == filename for j in pending_prints):
            return False

        # 哈希在入队时算一次、存进任务里。之后判断「这份打过没有」就只是查表 ——
        # 否则每次轮询队列都要把 50 个文件重新读一遍再算一遍 SHA-256。
        # 文件写入后不再改变(重复上传是**新文件名**),所以算一次就够了。
        #
        # Hashed once, at queue time, and kept on the job. Afterwards "was this
        # printed?" is a lookup — otherwise every queue poll would re-read and
        # re-hash up to 50 files. A stored file never changes (a re-upload is a
        # *new* filename), so one pass is enough.
        h = _shot_content_hash(filename)

        # 同一份内容刚打过就别再排队了 —— 上游重复上传的那几次在这里被吸收掉。
        #
        # Identical content that was just printed is not queued at all: this is
        # where the upstream's repeated uploads get absorbed.
        if _printed_recently(h) or _pending_has_hash(h):
            print(f"⏭️  内容相同,跳过 / same content, skipping: {filename}")
            return False

        pending_prints.append({
            "filename": filename,
            "hash": h or "",
            "machine_id": machine_id or "",
            "queued": datetime.now().strftime("%H:%M:%S"),
            "attempts": 0,
        })
        # 队列不设上限的话,一台关着的机器能让它无限涨下去
        # Without a cap, a machine that is switched off lets this grow forever
        if len(pending_prints) > MAX_PENDING:
            del pending_prints[:-MAX_PENDING]
        return True

def take_pending_prints():
    """取出待打印队列(不移除,等客户端 ack)/ read the queue without removing."""
    with pending_lock:
        now = time.time()
        # 第一份打成功之后,同内容的其余副本还排在队列里,但它们已经不该再打了 ——
        # 前端每打一个任务就重新拉一次队列,正是靠这里把它们滤掉。
        #
        # After the first copy prints, the remaining identical copies are still queued
        # but must not print. The front end re-fetches per job, and this is the filter
        # that removes them.
        return [dict(j) for j in pending_prints if not _printed_recently(j.get("hash"), now)]

def ack_pending_print(filename, ok=True):
    """客户端打印完成后确认,把任务摘出队列 / acknowledge a finished job."""
    with pending_lock:
        for i, job in enumerate(pending_prints):
            if job["filename"] == filename:
                if ok:
                    # 记下「这份内容刚打过」。同内容的其余副本下一轮就不在队列里了。
                    #
                    # Record that this content has just printed; the remaining
                    # identical copies drop out of the queue on the next poll.
                    h = job.get("hash")
                    if h:
                        printed_at[h] = time.time()
                        _prune_printed()
                    del pending_prints[i]
                else:
                    job["attempts"] = job.get("attempts", 0) + 1
                    # 失败重试几次后放弃,免得一条坏数据把队列堵死
                    # Give up after a few tries so one bad record cannot jam the queue
                    if job["attempts"] >= 3:
                        del pending_prints[i]
                return True
    return False

def record_print_job(label, width, height, printer, mode, ok, message=""):
    """把一次打印记进内存队列,供界面显示 / record a print for the UI."""
    job = {
        "label": label, "width": width, "height": height,
        "printer": printer, "mode": mode or "",
        "time": datetime.now().strftime("%H:%M:%S"),
        "ok": ok, "message": message,
    }
    with shots_lock:
        print_jobs.append(job)
        if len(print_jobs) > 20:
            del print_jobs[:-20]

def print_bitmap(bitmap, width, height, printer=None, **kwargs):
    """
    调用当前平台的适配器打印 / print through this platform's adapter.

    返回 {"success": bool, "message": str, ...};失败时把 PrintError 的原因
    原样带出来,方便前端提示。
    Returns a result dict; on failure the PrintError reason is passed through so
    the front end can show something useful.
    """
    adapter = get_printer()
    try:
        return adapter.print_bitmap(bitmap, width, height, printer=printer, **kwargs)
    except PrintError as e:
        return {"success": False, "message": e.message, "code": e.code}
    except Exception as e:
        return {"success": False, "message": str(e), "code": "unexpected"}

# ---------------------------------------------------------------------------
# 停止服务 / stopping the service
# ---------------------------------------------------------------------------
# 打包版(.app)没有 Dock 图标、没有窗口、没有终端,用户需要一个能停掉它的入口 ——
# 放在 Web UI 里最自然,因为他本来就在那儿。
#
# ⚠️ 这个端点**不限来源 IP**。服务本身监听局域网(DE1 插件要往这儿上传),所以
# 同一网段内的任何设备都能关掉它。这是刻意的选择:部署在自家局域网里的小工具,
# 方便比防篡改重要。如果要收紧,把 _allow_shutdown 改成只放行 127.0.0.1 即可。
#
# ⚠️ This endpoint is **not restricted by source IP**. The service listens on the LAN
# by design (the DE1 plugin uploads to it), so any device on the same network can
# stop it. That is a deliberate choice: for a small tool on a home network, being
# convenient beats being tamper-proof. To tighten it, restrict _allow_shutdown to
# 127.0.0.1.
_shutdown_hook = None

def show_stop_button():
    """
    界面上要不要显示「停止服务」按钮 / whether the web UI should offer a stop button.

    只有一种情况需要:**macOS 的打包版**。

    因为只有它没有任何别的停止方式 —— 它是 LSUIElement,不进 Dock、没有窗口、
    双击启动也没有终端,用户想停掉它的话除了命令行以外别无他法。

    其他情况都有现成的办法,按钮是多余的:
      - Windows 打包版:console=True 会开一个控制台窗口,关掉就行
      - Linux:通常就是在终端里跑的,Ctrl+C
      - 各平台的源码运行:同上,终端就在边上

    如果你把服务跑在无终端的环境里(比如 Linux 上 nohup / systemd),把这里改成
    直接 return True 即可。

    Exactly one case needs it: **a packaged macOS build.**

    That is the only configuration with no other way to stop it — it is a
    LSUIElement, so it is not in the Dock, has no window, and double-clicking it opens
    no terminal.

    Everywhere else already has an obvious way, and the button is redundant:
      - packaged Windows: console=True gives it a console window you can close
      - Linux: it is normally run from a terminal; Ctrl+C
      - source runs anywhere: same, the terminal is right there

    If you run it somewhere without a terminal (nohup or systemd on Linux), make this
    return True unconditionally.
    """
    return getattr(sys, "frozen", False) and sys.platform == "darwin"

def _allow_shutdown(_client_ip):
    """
    是否允许来自该地址的停止请求 / whether a shutdown request from this address is allowed.

    目前一律允许 —— 想收紧就在这里按 IP 判断,不用改别处。
    Currently always allowed. Tighten here rather than anywhere else.
    """
    return True

def request_shutdown(delay=0.4):
    """
    请求停止服务 / ask the server to stop.

    延迟一下再真的停,好让 HTTP 响应先发出去 —— 否则用户看到的是「连接被重置」,
    而不是「已停止」。
    Delayed slightly so the HTTP response gets out first; otherwise the user sees a
    connection reset instead of a confirmation.
    """
    if _shutdown_hook is None:
        return False
    threading.Timer(delay, _shutdown_hook).start()
    return True

# ---------------------------------------------------------------------------
# HTTP 服务器 HTTP Server
# ---------------------------------------------------------------------------
class PrintTheShotHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")

    # ---------- 工具 ---------- ---------- Helpers ----------
    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path, content_type, as_attachment=False, download_name=None):
        if not os.path.exists(path):
            self.send_error(404, "Not found")
            return
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-type", content_type)
        if as_attachment:
            name = download_name or os.path.basename(path)
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- GET ----------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self.show_index()
        elif path == "/api/status":
            self.send_api_status()
        elif path == "/api/queue":
            self.send_queue_status()
        elif path == "/api/shots":
            self.send_shots_list()
        elif path == "/api/stats":
            self.send_stats()
        elif path == "/api/update/check":
            self.check_update()
        elif path == "/api/settings/ai":
            self.send_ai_settings()
        elif path == "/api/ai/balance":
            self.send_ai_balance()
        elif path == "/api/languages":
            self.send_languages()
        elif path == "/api/settings":
            self._send_json({
                "bean_info_enabled": BEAN_INFO_ENABLED,
                "print_enabled": PRINT_ENABLED,
                "max_users": MAX_USERS,
            })
        elif path == "/api/printers":
            self.send_printers()
        elif path == "/api/print/config":
            self.send_print_config()
        elif path == "/api/print-queue":
            self._send_json({"jobs": take_pending_prints()})
        elif path.startswith("/api/shot"):
            self.send_shot_detail()
        elif path in ROOT_ASSETS:
            # 根路径也发一份,和 APK 里的布局保持一致。
            #
            # Capacitor 把 web/ 的内容放在 APK 资源根目录,所以 WebView 里
            # style.css 在 /style.css;而浏览器里我们把它挂在 /web/ 下。如果不
            # 统一,同一份 index.html 就只能在其中一个环境里工作 —— 而「打包进
            # APK 之后样式和脚本全 404」正是这么来的。
            #
            # Serving these at the root too keeps the layout identical to the APK.
            #
            # Capacitor puts web/'s contents at the root of the APK assets, so inside
            # the WebView style.css lives at /style.css, whereas in a browser we
            # mounted it under /web/. Leaving them different means one index.html can
            # only work in one of the two environments — and that is exactly how you
            # end up with "styles and scripts all 404 once it is packaged".
            self._serve_static("/web/" + path.lstrip("/"), "web")
        elif path.startswith("/web/"):
            self._serve_static(path, "web")
        elif path.startswith("/fonts/"):
            # 字体放在 web/fonts/ 下 —— 这样 Capacitor 打包 APK 时会一并带上。
            # 放在仓库根的话只有 web/ 会被复制进包,App 里中文会回退到系统字体,
            # 「Mac 与 Android 渲染一致」就没了。
            #
            # The font lives under web/fonts/ so Capacitor bundles it into the APK.
            # Kept at the repository root, only web/ gets copied and the app falls
            # back to the system font, which breaks "macOS and Android render the
            # same".
            self._serve_static(path, os.path.join("web", "fonts"))
        elif path == "/plugin/plugin.tcl":
            self._serve_file(plugin_runtime_path(), "application/x-tcl", as_attachment=True)
        elif path == "/plugin/plugin.tcl.txt":
            # TXT版:蓝牙发送时安卓端常拒绝无扩展名/.tcl文件,tcl.txt可正常传输 TXT version: Android often rejects extension-less/.tcl files over Bluetooth; tcl.txt transfers fine
            self._serve_file(plugin_runtime_path(), "text/plain", as_attachment=True,
                             download_name="tcl.txt")
        elif path == "/api/backup/export":
            self.send_backup_zip()
        elif path.startswith("/download/json/"):
            name = os.path.basename(path)
            self._serve_file(os.path.join(DATA_DIR, name), "application/json", as_attachment=True)
        elif path == "/api/language":
            self._send_json({"language": current_language})
        elif path.startswith("/tests/") and path.endswith(".html"):
            # 浏览器测试页(tests/*.html)必须能被访问 —— 它们就是给无头浏览器
            # 打开用的。只放行 .html:同目录下的 .py 是服务端测试代码,跟出图
            # 无关,没有理由暴露。
            #
            # The browser test pages (tests/*.html) must be reachable: a headless
            # browser opens them. Only .html is allowed — the .py files beside them
            # are server tests with nothing to do with rendering and no reason to be
            # exposed.
            self._serve_static(path, "tests")
        elif path.startswith("/shots_data/"):
            # 出图页需要直接取原始 JSON(见 tests/mockup.html)。这些内容本来
            # 就能从 /api/shot 拿到,所以不算新增暴露面。
            #
            # 单独一个方法而不是走 _serve_static:DATA_DIR 是相对当前工作目录的
            # (服务端其它地方也都按 cwd 读写它),而 _serve_static 是按脚本所在
            # 目录解析资源的。两者在 cwd ≠ 脚本目录时会指向不同地方。
            #
            # The mockup page fetches raw JSON directly (see tests/mockup.html).
            # These are already reachable through /api/shot, so this adds no exposure.
            #
            # A separate method rather than _serve_static: DATA_DIR is relative to the
            # working directory (as everywhere else in this server), while
            # _serve_static resolves against the script directory. With cwd != script
            # dir the two would point at different places.
            self._serve_data_file(path)
        else:
            # 不落到 SimpleHTTPRequestHandler 的默认实现。
            #
            # 它的行为是「把当前工作目录当网站根目录」,于是 run 目录下的任何文件
            # 都能被取走 —— 包括 settings.json(里面有 DeepSeek API key)、
            # translations.json、服务器源码。而这个服务的设计前提是**局域网可访问**
            # (DE1 插件要往这儿上传),所以「局域网内谁都能读走 API key」是个真实
            # 的暴露面,不是理论问题。
            #
            # 改成白名单:上面没有显式匹配的路径一律 404。
            #
            # Do NOT fall through to SimpleHTTPRequestHandler's default. It treats the
            # current working directory as the document root, so any file next to the
            # server can be fetched — including settings.json (which holds the DeepSeek
            # API key), translations.json and the server source. And this service is
            # designed to be reachable on the LAN (the DE1 plugin uploads to it), so
            # "anyone on the network can read your API key" is a real exposure, not a
            # theoretical one.
            #
            # Whitelist instead: anything not matched above is a 404.
            self.send_error(404, "Not found")

    # ---------- POST ----------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/upload":
            self.handle_upload()
        elif path == "/api/print":
            self.handle_print_bitmap()
        elif path == "/api/print/config":
            self.save_print_config()
        elif path == "/api/print-queue/ack":
            self.ack_print_queue()
        elif path == "/api/language":
            self.handle_language_change()
        elif path == "/api/settings/beaninfo":
            self.handle_beaninfo_setting()
        elif path == "/api/settings/print":
            self.handle_print_setting()
        elif path == "/api/plugin/update":
            self.handle_plugin_update()
        elif path == "/api/update":
            self.handle_update()
        elif path == "/api/backup/import":
            self.handle_backup_import()
        elif path == "/api/settings/ai":
            self.save_ai_settings()
        elif path == "/api/languages":
            self.add_language()
        elif path == "/api/languages/delete":
            self.delete_language()
        elif path == "/api/translate/shot":
            self.handle_translate_shot()
        elif path == "/api/shutdown":
            self.handle_shutdown()
        else:
            self.send_error(404, "Endpoint not found")

    # ---------- DELETE ----------
    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/queue":
            print_jobs.clear()
            self._send_json({"success": True, "message": "Queue cleared"})
        else:
            self.send_error(404, "Endpoint not found")

    # ---------- 页面 ---------- ---------- Pages ----------
    def show_index(self):
        try:
            with open(WEB_INDEX, "r", encoding="utf-8") as f:
                html = render_template(f.read())
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error(500, f"Template error: {e}")

    # ---------- API ----------
    def send_api_status(self):
        self._send_json({
            "status": get_text("status_running"),
            "version": VERSION,
            "start_time": server_start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "shots_received": len(received_shots),
            "active_users": len(threading.enumerate()) - 1,
            "max_users": MAX_USERS,
            "print_enabled": PRINT_ENABLED,
            "bean_info_enabled": BEAN_INFO_ENABLED,
            "language": current_language,
            # 前端据此决定要不要显示「停止服务」按钮(见 show_stop_button)
            # The front end uses this to decide whether to offer the stop button
            "show_stop_button": show_stop_button(),
            # 第二个更新按钮的职责,和 /api/update/check 返回的是同一个词。放在这里
            # 是因为**界面一加载就要知道该写什么文案**,而不是等用户点了「检查更新」
            # 才知道。Android 由它自己的服务端回 "apk"。
            #
            # What the second update button is for, the same word /api/update/check
            # returns. It is here because **the UI has to label the button as soon as it
            # loads**, not only after the user clicks "check for updates". Android answers
            # "apk" from its own server.
            "update_via": "installer" if getattr(sys, "frozen", False) else "self",
        })

    def send_queue_status(self):
        with shots_lock:
            jobs = [dict(j) for j in print_jobs]
        self._send_json({"count": len(jobs), "jobs": jobs})

    def send_shots_list(self):
        """GET /api/shots[?date=YYYY-MM-DD]
        默认:最近100条(UI取前9);指定日期:当日全部(上限500);用于日期切换"""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        date_str = q.get("date", [""])[0].replace("-", "")
        with shots_lock:
            shots = list(reversed(received_shots))
        dates = sorted({s.get("timestamp", "")[:8] for s in shots}, reverse=True)
        dates_fmt = [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in dates]
        if date_str:
            shots = [s for s in shots if s.get("timestamp", "")[:8] == date_str][:500]
        else:
            shots = shots[:100]
        # 展示层翻译:卡片标题按界面语言显示,存储保持原始名称
        for s in shots:
            s["bean"] = display_name(s.get("bean", ""), BEAN_TRANSLATIONS)
            s["profile"] = display_name(s.get("profile", ""), PROFILE_TRANSLATIONS)
        # 这里过去会在后台把「图表语言和界面语言不一致」的 shot 重新渲染一遍 PNG。
        # 渲染搬到浏览器之后,这条逻辑没有存在意义了:前端拿到数据自己画,语言切换
        # 立刻生效,不需要服务端做任何事,也不需要缓存图片。
        #
        # This used to re-render PNGs in the background whenever a shot's chart
        # language differed from the UI language. With rendering in the browser that
        # whole mechanism disappears: the front end draws from data, so switching
        # language takes effect immediately and no images have to be cached.
        self._send_json({
            "shots": shots,
            "dates": dates_fmt,
            "lang": current_language,
            "pending_prints": [j["filename"] for j in take_pending_prints()],
        })

    def send_stats(self):
        """GET /api/stats — 统计数据(总数据/天数/机器/Top方案/日期分布)"""
        import collections
        with shots_lock:
            shots = list(received_shots)
        total = len(shots)
        per_date = collections.Counter(s.get("timestamp", "")[:8] for s in shots)
        profiles = collections.Counter(s.get("profile", "unknown") for s in shots)
        beans = collections.Counter(s.get("bean", "未知") for s in shots)
        machines = len({s.get("machine_id") for s in shots})
        avg_size = int(sum(s.get("data_size", 0) for s in shots) / total) if total else 0
        # 日期分布只展示最近7天,更早的合并为一行,避免列过长 Date distribution shows only the latest 7 days; older ones merge into one row to avoid a long list
        per_date_list = [{"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}", "count": c}
                         for d, c in sorted(per_date.items(), reverse=True)]
        recent, older = per_date_list[:7], per_date_list[7:]
        self._send_json({
            "total_shots": total,
            "total_dates": len(per_date),
            "machines": machines,
            "avg_data_size": avg_size,
            "prints_session": len(print_jobs),
            "per_date": recent,
            "older_days": len(older),
            "older_count": sum(x["count"] for x in older),
            "top_profiles": [{"name": display_name(n, PROFILE_TRANSLATIONS), "count": c}
                             for n, c in profiles.most_common(5)],
            "top_beans": [{"name": display_name(n, BEAN_TRANSLATIONS), "count": c}
                          for n, c in beans.most_common(6)],
        })

    def handle_language_change(self):
        global current_language
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            lang = data.get("language", "zh")
            if lang in LANGUAGES:
                current_language = lang
            self._send_json({"success": True, "language": current_language})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def handle_beaninfo_setting(self):
        global BEAN_INFO_ENABLED
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if "enabled" in data:
                BEAN_INFO_ENABLED = bool(data["enabled"])
            self._send_json({"success": True, "bean_info_enabled": BEAN_INFO_ENABLED})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def handle_print_setting(self):
        global PRINT_ENABLED
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if "enabled" in data:
                PRINT_ENABLED = bool(data["enabled"])
            self._send_json({"success": True, "print_enabled": PRINT_ENABLED})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def check_update(self):
        """
        GET /api/update/check — 查 GitHub 上最新的 release,和本机版本比。

        数据源是 release 而不是 main 分支,理由见 GITHUB_API_LATEST 上面那段:
        跟 main 会把「已改版本号但还没发布」的代码推给用户。

        返回里带 `update_via`,前端据此决定第二个按钮干什么:

            self      —— 源码运行,能原地更新。按钮 = 从 GitHub 更新服务
            installer —— 打包版,不能原地更新自己。按钮 = 去下载新安装包
            (Android 由它自己的服务端回 apk,按钮 = 下载新 APK)

        三种情况下**前台界面走同一条逻辑**,差别只在服务端这一句话里。

        Checks the latest GitHub release against the local version. The source is the
        release rather than main for the reason given above GITHUB_API_LATEST: tracking
        main hands users code whose version was bumped but never released.

        The response carries `update_via`, which is what the front end uses to decide
        what the second button does:

            self      — source mode, can update in place. Button = update from GitHub
            installer — packaged build, cannot replace itself. Button = get the installer
            (Android answers `apk` from its own server: button = download the APK)

        All three take the same path through the UI; the whole difference is this one
        word from the server.
        """
        try:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            channel = q.get("channel", ["auto"])[0]
            if channel not in ("auto", "stable", "beta"):
                channel = "auto"
            tag, page = fetch_latest_release(channel=channel)

            # 频道里一个版本都没有(比如正式版还没发过)—— 这是一个**答案**,不是错误。
            # 说清楚它,而不是报一个「查询失败」,更不是报「已是最新」:后者会让用户
            # 以为稳定版就是当前这个 beta,那是假的。
            #
            # An empty channel (no stable cut yet, say) is an **answer**, not an error.
            # Say so, rather than reporting a failed lookup — and certainly not "up to
            # date", which would imply the stable release is this beta, and that is
            # false.
            if not tag:
                zh = current_language == "zh"
                self._send_json({
                    "success": True,
                    "local": VERSION,
                    "remote": "",
                    "channel": channel,
                    "update_available": False,
                    "release_url": page,
                    "update_via": "installer" if getattr(sys, "frozen", False) else "self",
                    "message": ("这个频道还没有发布过版本" if zh
                                else "Nothing has been released in this channel yet"),
                })
                return

            remote = tag
            # success 是给前端的「这次真的查了」信号。没有它,前端只能靠
            # update_available 的真假去猜,而「查了、是最新」和「压根没查」
            # 在那个字段上长得一模一样 —— Android 端就是这么显示成绿色的
            # 「已是最新」的。
            #
            # success tells the front end "this check really happened". Without it the
            # front end can only infer from update_available, and "checked, up to date"
            # looks exactly like "never checked" on that field — which is how the
            # Android build came to show a green "up to date" without checking at all.
            self._send_json({
                "success": True,
                "local": VERSION,
                "remote": remote,
                "channel": channel,
                "update_available": _version_key(remote) > _version_key(VERSION),
                "release_url": page,
                "update_via": "installer" if getattr(sys, "frozen", False) else "self",
            })
        except Exception as e:
            # 查不到就说查不到,绝不退化成「已是最新」—— 那是这个端点最早的毛病。
            # A failed check says so and never degrades into "up to date" — that was
            # this endpoint's original sin.
            self._send_json({"success": False, "error": str(e),
                             "release_url": RELEASES_PAGE}, 500)

    def handle_update(self):
        """
        POST /api/update — 安装**最新 release**,而不是 main 分支。

        和 /api/update/check 用同一个数据源:检查说哪个版本,装的就是哪个版本。
        两者不一致的话,界面会承诺一个版本、装出另一个 —— 那比「不能更新」还糟。

        打包版拒绝,并把 release 页面一并给出去。从前只回一句「请下载新安装包」,
        没有链接,用户只能自己去搜。

        Installs the **latest release**, not main — the same source /api/update/check
        uses, so the version the check reported is the version installed. A mismatch
        would promise one version and deliver another, which is worse than not updating
        at all.

        A packaged build refuses and hands back the release page. It used to answer only
        "download the new installer", with no link, leaving the user to go searching.
        """
        global current_language
        zh = current_language == "zh"
        if getattr(sys, "frozen", False):
            msg = ("打包版本不支持在线更新,请到发布页面下载新安装包" if zh
                   else "Packaged build can't self-update — get the new installer from the release page")
            self._send_json({"success": False, "message": msg,
                             "release_url": RELEASES_PAGE})
            return
        # 装**界面刚查过的那个频道**,而不是自己另选一个。检查说哪个版本,装的就是
        # 哪个版本 —— 两处不一致的话,界面会承诺一个版本、装出另一个。
        #
        # Install what the UI just checked, not a channel chosen independently here. The
        # version the check reported must be the version installed; otherwise the UI
        # promises one version and delivers another.
        channel = "auto"
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length:
                channel = json.loads(self.rfile.read(length).decode("utf-8")).get("channel", "auto")
        except Exception:
            pass          # 没带 body 就用默认 / no body means the default
        if channel not in ("auto", "stable", "beta"):
            channel = "auto"

        try:
            tag, page = fetch_latest_release(channel=channel)
            if not tag:
                raise ValueError("这个频道还没有发布过版本 / nothing released in this channel yet")
        except Exception as e:
            self._send_json({
                "success": False,
                "message": ("查询最新版本失败:" if zh else "Could not look up the latest version: ") + str(e),
                "release_url": RELEASES_PAGE,
            }, 500)
            return
        # tag 的 zip 根目录名和 main 的不同(仓库名-1.0.3 vs 仓库名-main),但
        # perform_update 的根目录是**从包里现取**的,所以这里不用额外处理。
        #
        # A tag zip has a different root folder than main's (repo-1.0.3 vs repo-main),
        # but perform_update reads the root out of the archive itself, so nothing extra
        # is needed here.
        zip_url = f"https://codeload.github.com/{_REPO}/zip/refs/tags/{tag}"
        ok, msg = perform_update(zip_url, os.getcwd(), current_language)
        self._send_json({"success": ok, "message": msg, "release_url": page},
                        200 if ok else 500)

    # ---------- AI 翻译设置 / AI translation settings ----------
    def send_ai_settings(self):
        """GET /api/settings/ai — 当前AI设置(不返回key本身)"""
        self._send_json({
            "key_set": bool(settings.get("deepseek_key")),
            "ai_enabled": bool(settings.get("ai_enabled")),
        })

    def save_ai_settings(self):
        """POST /api/settings/ai {key?, enabled?} — 保存key/开关"""
        global settings
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if "key" in data and data["key"] is not None:
                settings["deepseek_key"] = data["key"].strip()
            if "enabled" in data:
                settings["ai_enabled"] = bool(data["enabled"])
            save_settings()
            self._send_json({"success": True, "key_set": bool(settings["deepseek_key"]),
                             "ai_enabled": settings["ai_enabled"]})
        except Exception as e:
            self._send_json({"success": False, "message": str(e)}, 500)

    def send_ai_balance(self):
        """GET /api/ai/balance — DeepSeek 余额(兼作连接测试)"""
        import urllib.request
        key = settings.get("deepseek_key", "")
        if not key:
            self._send_json({"success": False, "message": "no api key"})
            return
        try:
            req = urllib.request.Request(DEEPSEEK_BALANCE, method="GET",
                                         headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as r:
                resp = json.loads(r.read().decode("utf-8"))
            infos = resp.get("balance_infos", [])
            total = sum(float(i.get("total_balance", 0)) for i in infos)
            currency = infos[0].get("currency", "CNY") if infos else "CNY"
            self._send_json({"success": True, "balance": round(total, 2), "currency": currency})
        except Exception as e:
            self._send_json({"success": False, "message": f"balance check failed: {e}"})

    def send_languages(self):
        """GET /api/languages — 可用语言列表(内置 + 自定义)"""
        langs = [{"code": "en", "name": "English", "builtin": True},
                 {"code": "zh", "name": "中文", "builtin": True}]
        for code, info in settings.get("languages", {}).items():
            langs.append({"code": code, "name": info.get("name", code), "builtin": False})
        self._send_json({"languages": langs})

    def add_language(self):
        """POST /api/languages {name} — 用DeepSeek把UI文案翻译成新语言并启用(代码自动生成)
        Add a custom language by plain-text name: translate all UI strings via DeepSeek"""
        global settings
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            name = data.get("name", "").strip()
            if not name:
                self._send_json({"success": False, "message": "language name required"})
                return
            # 代码自动生成(内部使用,用户无需关心) / auto-generate internal code
            code = data.get("code", "").strip().lower()
            if not code or not re.match(r"^[a-z0-9]{2,8}$", code) or code in ("en", "zh"):
                n = 1
                while f"lang{n}" in settings.get("languages", {}) or f"lang{n}" in ("en", "zh"):
                    n += 1
                code = f"lang{n}"
            if not settings.get("deepseek_key"):
                self._send_json({"success": False, "message": "DeepSeek API key required first"})
                return
            # 批量翻译EN文案 → JSON(AI理解语言名称,任意写法均可)
            prompt = ("Translate the following JSON object of UI strings into " + name +
                      ". Keep {placeholders} intact. Be CONCISE and abbreviate where natural "
                      "(labels must fit a narrow chart column). "
                      "Return ONLY valid JSON with the same keys.")
            out = ai_call([{"role": "system", "content": prompt},
                           {"role": "user", "content": json.dumps(LANGUAGES["en"], ensure_ascii=False)}],
                          timeout=AI_TIMEOUT_BATCH)
            import re as _re
            m = _re.search(r"\{.*\}", out, _re.S)
            strings = json.loads(m.group(0)) if m else json.loads(out)
            settings.setdefault("languages", {})[code] = {"name": name, "strings": strings}
            save_settings()
            LANGUAGES[code] = strings  # 立即生效
            self._send_json({"success": True, "message": f"language {name} ({code}) added",
                             "strings_count": len(strings)})
        except Exception as e:
            self._send_json({"success": False, "message": f"add language failed: {e}"}, 500)

    def handle_shutdown(self):
        """
        POST /api/shutdown — 停止服务 / stop the service.

        先回响应、再真的停(见 request_shutdown 的说明)。
        Answer first, then actually stop (see request_shutdown).
        """
        client = self.client_address[0] if self.client_address else "?"
        if not _allow_shutdown(client):
            self._send_json({"success": False,
                             "message": get_text("shutdown_denied")}, 403)
            return
        ok = request_shutdown()
        print("🛑 收到停止请求 / shutdown requested from %s" % client)
        self._send_json({
            "success": ok,
            "message": get_text("shutdown_ok") if ok else get_text("shutdown_failed"),
            "client": client,
        })

    def handle_translate_shot(self):
        """
        POST /api/translate/shot {filename, lang?, allow_api?}
        用 DeepSeek 把一条 shot 的豆子信息翻译成目标语言,并把结果写回数据文件。

        这里过去会「翻译 + 重渲染 PNG」,现在只翻译 —— 渲染在浏览器里发生,而
        且语言切换不需要重新出图,前端拿到翻译好的数据重画就行,比等一张新 PNG
        快得多。

        Translate a shot's bean info with DeepSeek and write the result back into
        the data file.

        This used to translate and re-render a PNG. It now only translates:
        rendering happens in the browser, and switching language no longer needs a
        new image — the front end just redraws from data, which is far quicker
        than waiting on a fresh PNG.
        """
        global current_language
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            filename = os.path.basename(data.get("filename", ""))
            target_lang = data.get("lang") or current_language
            if target_lang not in LANGUAGES:
                self._send_json({"success": False, "message": f"language '{target_lang}' not available"})
                return
            filepath = os.path.join(DATA_DIR, filename)
            if not os.path.exists(filepath):
                self._send_json({"success": False, "message": "shot not found"})
                return
            if not settings.get("deepseek_key") or not settings.get("ai_enabled"):
                self._send_json({"success": False, "message": "AI translation not enabled"})
                return

            with open(filepath, "r", encoding="utf-8") as f:
                shot_data = json.load(f)
            allow_api = data.get("allow_api", True)  # 大图语言切换仅用缓存 / lightbox switching uses cache only
            translated = translate_shot_for_display(shot_data, target_lang, allow_api=allow_api)

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(translated, f, ensure_ascii=False, indent=2)

            with shots_lock:
                for s in received_shots:
                    if s.get("filename") == filename:
                        s["data_lang"] = target_lang
            persist_index()
            self._send_json({
                "success": True,
                "message": "translated",
                "lang": target_lang,
                "shot": translated,
            })
        except Exception as e:
            self._send_json({"success": False, "message": str(e)}, 500)

    def delete_language(self):
        """DELETE /api/languages/delete {code} — 移除自定义语言"""
        global settings
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            code = data.get("code", "")
            langs = settings.get("languages", {})
            if code in langs:
                del langs[code]
                LANGUAGES.pop(code, None)
                save_settings()
            self._send_json({"success": True})
        except Exception as e:
            self._send_json({"success": False, "message": str(e)}, 500)

    def handle_plugin_update(self):
        """从GitHub拉取最新plugin.tcl,先本地备份再覆盖"""
        global current_language
        import urllib.request
        try:
            req = urllib.request.Request(PLUGIN_GITHUB_URL, headers={"User-Agent": "PrintTheShotBeta"})
            with urllib.request.urlopen(req, timeout=20) as r:
                new_data = r.read()
            # 基本校验:必须含插件标识,否则视为下载异常 Basic validation: must contain the plugin marker, else treat as a bad download
            if len(new_data) < 500 or b"print_the_shot" not in new_data:
                self._send_json({"success": False, "message": "下载内容异常,已取消"})
                return
            runtime_path = plugin_runtime_path()
            # 备份当前插件 Back up the current plugin
            backup = runtime_path + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            if os.path.exists(runtime_path):
                import shutil
                shutil.copy2(runtime_path, backup)
            # 写入新插件 Write new plugin
            with open(runtime_path, "wb") as f:
                f.write(new_data)
            msg = f"插件已更新(旧版备份: {os.path.basename(backup)})"
            if current_language == "en":
                msg = f"Plugin updated (backup: {os.path.basename(backup)})"
            print(f"🔄 {msg}")
            self._send_json({"success": True, "message": msg})
        except Exception as e:
            msg = f"更新失败: {e}" if current_language == "zh" else f"Update failed: {e}"
            self._send_json({"success": False, "message": msg}, 500)

    # ---------- 打印 ---------- ---------- Printing ----------
    def handle_print_bitmap(self):
        """
        POST /api/print — 接收前端生成的 1-bit 位图并打印 / take the 1-bit bitmap
        the front end produced and print it.

        Body: {"bitmap": "<base64>", "width": 576, "height": 2242,
               "printer": "default"|"<id>", "mode": "driver"|"raw", "label": "..."}

        服务端不关心位图是怎么画出来的,也不做任何图像处理 —— 收到什么就转交
        给平台适配器。这正是「绘制与打印彻底分离」的落点:服务端这一侧只有
        base64 解码和一次转发。

        The server neither knows nor cares how the bitmap was drawn, and it applies
        no image processing: whatever arrives goes straight to the platform adapter.
        That is where "rendering and printing are fully separated" actually lands —
        on this side there is only a base64 decode and a hand-off.
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                self._send_json({"success": False, "message": "空请求体 / empty body"}, 400)
                return
            if length > MAX_PRINT_BODY:
                self._send_json({
                    "success": False,
                    "message": "位图过大 / bitmap too large: %d bytes (limit %d)"
                               % (length, MAX_PRINT_BODY),
                }, 413)
                return

            data = json.loads(self.rfile.read(length).decode("utf-8"))
            bitmap_b64 = data.get("bitmap") or ""
            if not bitmap_b64:
                self._send_json({"success": False, "message": "缺少 bitmap 字段 / missing 'bitmap'"}, 400)
                return

            try:
                bitmap = base64.b64decode(bitmap_b64, validate=True)
            except Exception as e:
                self._send_json({"success": False, "message": "bitmap base64 解码失败 / bad base64: %s" % e}, 400)
                return

            width = int(data.get("width") or 0)
            height = int(data.get("height") or 0)
            printer = data.get("printer") or None
            if printer in ("default", "", None):
                printer = None
            mode = data.get("mode") or None
            label = str(data.get("label") or "")[:120]

            # 长度自洽性先校验一遍:与其打出一张错位的纸,不如直接报错
            # Check the length first: better a clear error than a garbled sheet
            try:
                get_printer().validate_bitmap(bitmap, width, height)
            except PrintError as e:
                self._send_json({"success": False, "message": e.message, "code": e.code}, 400)
                return

            kwargs = {}
            if mode:
                kwargs["mode"] = mode

            # 打印会阻塞(要等 CUPS / 蓝牙把任务收下),所以放到线程里做,
            # 先回一个受理响应,不让浏览器干等。
            #
            # Printing blocks while CUPS or Bluetooth accepts the job, so it runs
            # off-thread and the browser gets an immediate acknowledgement.
            def _run():
                result = print_bitmap(bitmap, width, height, printer=printer, **kwargs)
                record_print_job(label, width, height, printer, mode,
                                 bool(result.get("success")), result.get("message", ""))

            threading.Thread(target=_run, daemon=True).start()

            adapter = get_printer()
            self._send_json({
                "success": True,
                "message": get_text("print_job_sent"),
                "platform": adapter.platform_id,
                "printer": printer or "default",
                "width": width,
                "height": height,
            })
        except PrintError as e:
            self._send_json({"success": False, "message": e.message, "code": e.code}, 500)
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def send_printers(self):
        """GET /api/printers — 当前平台的可用打印机 / printers available on this platform."""
        adapter = get_printer()
        try:
            printers = adapter.list_printers()
            error = None
        except PrintError as e:
            printers, error = [], e.message
        except Exception as e:
            printers, error = [], str(e)
        self._send_json({
            "platform": adapter.platform_id,
            "display_name": adapter.display_name,
            "available": adapter.is_available(),
            "capabilities": adapter.capabilities(),
            "printers": printers,
            "error": error,
        })

    def send_print_config(self):
        """GET /api/print/config — 打印配置(纸张、模式、默认打印机)/ print settings."""
        self._send_json(print_config())

    def ack_print_queue(self):
        """
        POST /api/print-queue/ack {filename, ok}
        客户端打印完成后回执,把任务摘出待打印队列。
        Client acknowledges a finished job so it leaves the pending queue.
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception as e:
            self._send_json({"success": False, "message": "请求体解析失败 / bad body: %s" % e}, 400)
            return
        filename = os.path.basename(str(data.get("filename") or ""))
        if not filename:
            self._send_json({"success": False, "message": "缺少 filename / missing 'filename'"}, 400)
            return
        removed = ack_pending_print(filename, ok=bool(data.get("ok", True)))
        self._send_json({"success": True, "removed": removed, "jobs": take_pending_prints()})

    def save_print_config(self):
        """POST /api/print/config — 保存打印配置 / persist print settings."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception as e:
            self._send_json({"success": False, "message": "请求体解析失败 / bad body: %s" % e}, 400)
            return

        allowed = {"printer", "mode", "media", "print_width", "paper_width_mm",
                   "feed_lines", "cut", "threshold", "rotate"}
        changed = {}
        for key in allowed:
            if key in data:
                changed[key] = data[key]
        settings["print"] = {**settings.get("print", {}), **changed}
        save_settings()
        # 配置变了要重新装载适配器,否则改纸张/模式不会生效
        # Reload the adapter so paper/mode changes actually take effect
        refresh_printer(settings["print"])
        self._send_json({"success": True, "config": print_config()})

    def send_backup_zip(self):
        """
        GET /api/backup/export —— 把 shot 数据打包成一个 zip 下载。

        为什么要这个 —— 一次真实的教训
        ------------------------------
        原本的「备份」只存在于**服务更新**流程里:点更新时顺手存一份到 backup/。
        用户看不到、也调不到,而它能救的场景只有一个。真正会丢数据的是
        「卸载重装」(Android 卸载会清掉应用私有目录)和「换设备」—— 那两件事都
        发生在 App 之外,App 没有任何机会先备份自己。

        所以备份必须是**用户能主动导出、随时能导回**的东西。

        顺带一条纪律:导出完要验证**内容**。这一版就是因为只看了「文件存在、
        大小不为零」,事后才发现包里空无一物。(见 /api/backup/import)
        """
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            count = 0
            if os.path.isdir(DATA_DIR):
                for name in sorted(os.listdir(DATA_DIR)):
                    full = os.path.join(DATA_DIR, name)
                    if not os.path.isfile(full) or not name.endswith(".json"):
                        continue
                    z.write(full, name)
                    count += 1
        payload = buf.getvalue()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition",
                         'attachment; filename="printtheshot_backup_%s.zip"' % ts)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        print("💾 已导出备份 / backup exported:%d 个文件,%.1f KB"
              % (count, len(payload) / 1024.0))

    def handle_backup_import(self):
        """
        POST /api/backup/import —— 收下一个备份 zip,把里面的 shot 恢复回 DATA_DIR。

        语义是**恢复**,不是合并:同名文件直接覆盖。这正是「导回」该有的样子 ——
        用户拿着备份回来,期望的是回到导出时的样子,而不是新旧混在一起。

        三条纪律:
        - 条目名一律取 basename。备份包里出现 `../` 说明它要么是坏的、要么是恶意
          的,两种都不该落盘。
        - 跳过 index.json —— 它是生成物,导入后由 load_history() 扫描目录重建,
          比信任包里那份更可靠。
        - 不触发打印、不入队。恢复历史不是新数据,不该出纸。

        Semantics are **restore**, not merge: a file with the same name is overwritten.
        That is what "import my backup" should mean — the user expects to get back what
        they exported, not a blend of old and new.

        Three rules: entry names are always reduced to their basename (a `../` inside a
        backup is either corrupt or hostile, and neither belongs on the disk); index.json
        is skipped because it is derived and load_history() rebuilds it more reliably by
        scanning; and nothing is printed or queued, because restoring history is not new
        data.
        """
        import io
        import zipfile

        try:
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                self._send_json({"success": False, "message": "空请求 / empty body"}, 400)
                return
            if length > MAX_BACKUP_BODY:
                self._send_json({
                    "success": False,
                    "message": "备份包过大 / backup too large (max %dMB)"
                               % (MAX_BACKUP_BODY // (1024 * 1024)),
                }, 413)
                return
            body = self.rfile.read(length)

            if "multipart/form-data" in content_type:
                body = self._extract_multipart_bytes(body, content_type)

            imported, imported_bytes, skipped, unpacked = 0, 0, [], 0
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    entry = info.filename.replace("\\", "/")
                    # 带 ".." 的条目直接跳过,而不是安静地取个 basename 收下 ——
                    # 包里出现 `../` 只有两种可能:坏了,或者恶意。两种都不该被
                    # 当作一条正常记录导进来,用户也该在「跳过」里看到它。
                    #
                    # Entries containing ".." are skipped rather than quietly taken under
                    # their basename: a `../` in an archive is either corrupt or hostile,
                    # and neither should come in as a legitimate record. The user should
                    # see it listed as skipped.
                    if ".." in entry.split("/"):
                        skipped.append(info.filename)
                        continue
                    # 仍然只取 basename 落盘:这是最后一道关口,不指望上面那行。
                    # Still write under the basename: this is the last gate before the
                    # disk, and it does not rely on the check above.
                    name = os.path.basename(entry)
                    if not name.endswith(".json") or name == "index.json":
                        skipped.append(info.filename)
                        continue
                    # 按**实际读出的字节数**设限,而不是信 zip 头里声明的 file_size ——
                    # 声明值可以撒谎,读出来的不会。(zip 炸弹正是靠撒谎的那个数。)
                    #
                    # Cap on the bytes actually read, not on the declared file_size: the
                    # header can lie, the bytes cannot. (A zip bomb is exactly a lie in
                    # that header.)
                    with z.open(info) as fp:
                        raw = fp.read(MAX_BACKUP_ENTRY + 1)
                    if (len(raw) > MAX_BACKUP_ENTRY
                            or unpacked + len(raw) > MAX_BACKUP_UNPACKED):
                        skipped.append(info.filename)
                        continue
                    unpacked += len(raw)
                    try:
                        shot = json.loads(raw.decode("utf-8"))
                    except Exception:
                        # 坏条目跳过而不是整体失败:一个坏文件不该让其余 199 条白导
                        # Skip the bad entry instead of failing the whole import: one
                        # malformed file should not cost the other 199.
                        skipped.append(info.filename)
                        continue
                    with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
                        json.dump(shot, f, ensure_ascii=False, indent=2)
                    imported += 1
                    imported_bytes += os.path.getsize(os.path.join(DATA_DIR, name))

            # 索引重建交给 load_history():它 index.json 优先、目录扫描兜底,比在这里
            # 手工拼一条更可靠 —— 何况我们刚跳过包里的 index.json,本来就该重建。
            #
            # Let load_history() rebuild the index: it prefers index.json and falls back
            # to scanning the directory, which beats assembling entries by hand — and
            # since the archive's own index.json was just skipped, a rebuild is due.
            load_history()

            # 备份纪律:验证**内容**,而不是「文件生成了」。这两行打印实际落盘的条数
            # 和字节数 —— 数字不对就是没导成,别让它看起来像成了。
            #
            # Backup discipline: verify the *content*, not that "a file appeared". These
            # two lines report what actually landed; wrong numbers mean the import did
            # not work, and nothing should suggest otherwise.
            print("📥 已导入备份 / backup imported:%d 条(%.1f KB),跳过 %d 条"
                  % (imported, imported_bytes / 1024.0, len(skipped)))
            for name in skipped:
                print("   跳过 / skipped: %s" % name)

            zh = current_language == "zh"
            msg = ("已恢复 %d 条记录" % imported) if zh else ("Restored %d shot(s)" % imported)
            if skipped:
                msg += (",跳过 %d 条" % len(skipped)) if zh else (", skipped %d" % len(skipped))
            self._send_json({
                "success": True,
                "imported": imported,
                "skipped": skipped,
                "message": msg,
            })

        except zipfile.BadZipFile:
            self._send_json({
                "success": False,
                "message": ("这不是有效的备份包(不是 zip 文件)" if current_language == "zh"
                            else "Not a valid backup archive (not a zip file)"),
            }, 400)
        except Exception as e:
            self._send_json({"success": False, "message": str(e)}, 500)

    def send_shot_detail(self):
        """
        GET /api/shot?file=<filename>&lang=<code>
        返回一条 shot 的完整 JSON,按界面语言翻译好豆子/方案文案 —— 前端拿去
        用 Canvas 画图。这是「服务端不再出图」之后,前端取数据的入口。

        Returns one shot's full JSON with bean/profile text translated for the
        current UI language, for the front end to draw on a canvas. This is the
        data entry point now that the server no longer produces images.
        """
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        filename = os.path.basename(q.get("file", [""])[0])
        lang = q.get("lang", [current_language])[0]
        if not filename or not filename.endswith(".json"):
            self._send_json({"success": False, "message": "缺少 file 参数 / missing 'file'"}, 400)
            return
        filepath = os.path.join(DATA_DIR, filename)
        if not os.path.exists(filepath):
            self._send_json({"success": False, "message": "文件不存在 / not found"}, 404)
            return
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                shot_data = json.load(f)
        except Exception as e:
            self._send_json({"success": False, "message": "读取失败 / read failed: %s" % e}, 500)
            return

        # 按需翻译:AI 翻译可能很慢,所以默认只查缓存;带 ?ai=1 才允许调 API
        # Translate on demand: AI calls can be slow, so cache-only by default and
        # ?ai=1 opts into hitting the API
        allow_api = q.get("ai", ["0"])[0] == "1"
        if lang and lang != "zh":
            try:
                shot_data = translate_shot_for_display(shot_data, lang, allow_api=allow_api)
            except Exception as e:
                print("⚠️ 翻译失败,返回原文 / translation failed, returning original: %s" % e)

        machine_id = q.get("machine_id", [""])[0]
        if machine_id:
            shot_data.setdefault("_display", {})["machine_id"] = machine_id
        self._send_json({
            "success": True, "filename": filename, "lang": lang, "shot": shot_data,
        })

    def _serve_data_file(self, path):
        """
        提供 DATA_DIR 下的文件 / serve a file from DATA_DIR.

        路径按 basename 处理:这里只放 JSON 数据文件,没有任何层级结构,
        所以 basename 既够用,又天然挡住了目录穿越。

        basename-only: this directory holds flat JSON data with no nesting, so
        basename is both sufficient and inherently traversal-proof.
        """
        name = os.path.basename(path)
        if not name or not name.endswith(".json"):
            self.send_error(404, "Not found")
            return
        self._serve_file(os.path.join(os.path.abspath(DATA_DIR), name),
                         "application/json; charset=utf-8")

    def _serve_static(self, path, subdir):
        """
        从资源目录里取静态文件(web/ 与 fonts/)。
        路径做 basename 处理,避免 ../ 之类穿越到资源目录之外。

        web/ 下的文本文件会走一遍模板替换:app.js 里也有 {{LANG}} 占位符(语言
        文案表在那里被解析),不替换的话页面加载出来全是 key 而不是文字。

        Serve static files (web/ and fonts/) out of the resource directory, using
        basename to stop any ../ traversal escaping it.

        Text files under web/ go through template substitution: app.js also carries
        a {{LANG}} placeholder (that is where the string table gets parsed), and
        without substitution the page would show raw keys instead of words.
        """
        rel = path.split("/", 2)[2] if path.count("/") >= 2 else ""
        if not rel:
            self.send_error(404, "Not found")
            return

        # 支持子路径(如 /web/fonts/x.otf),但必须挡住目录穿越:
        # 规范化之后一定要还在资源目录里面,否则 ../ 就能读到任意文件。
        #
        # Sub-paths are supported (/web/fonts/x.otf) but traversal must be stopped:
        # after normalisation the result still has to sit inside the resource
        # directory, or ../ reads any file on disk.
        base = os.path.realpath(resource_path(subdir))
        full = os.path.realpath(os.path.join(base, rel))
        if full != base and not full.startswith(base + os.sep):
            self.send_error(403, "Forbidden")
            return

        name = os.path.basename(full)

        # 模板替换只对 web/ 下的文件做。
        #
        # 原来对任何 .html/.js/.css 都做,于是 /tests/ 下的测试页也被替换 ——
        # 包括测试页里**作为断言内容**出现的字面量。结果是断言在发送途中被悄悄
        # 改写,测出来的东西和写的不是一回事。这种"测试被服务端改坏"的问题极难
        # 排查,因为文件本身看起来完全正常。
        #
        # Substitution happens for files under web/ only.
        #
        # It used to run for any .html/.js/.css, which included the test pages under
        # /tests/ — and their placeholder literals are assertion *content*. The
        # assertions were silently rewritten in flight, so they no longer tested what
        # they said. That kind of corruption is very hard to trace, because the file
        # on disk looks perfectly correct.
        if subdir == "web" and name.endswith((".js", ".html", ".css")):
            try:
                with open(full, "r", encoding="utf-8") as f:
                    text = f.read()
                if "{{LANG}}" in text or "{{VERSION}}" in text:
                    body = render_template(text).encode("utf-8")
                    ctype = {
                        ".js": "application/javascript; charset=utf-8",
                        ".html": "text/html; charset=utf-8",
                        ".css": "text/css; charset=utf-8",
                    }[os.path.splitext(name)[1].lower()]
                    self.send_response(200)
                    self.send_header("Content-type", ctype)
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            except Exception as e:
                self.send_error(500, "Template error: %s" % e)
                return
        ctype = "application/octet-stream"
        ext = os.path.splitext(name)[1].lower()
        ctype = {
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".html": "text/html; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".otf": "font/otf",
            ".ttf": "font/ttf",
            ".woff": "font/woff",
            ".woff2": "font/woff2",
            ".svg": "image/svg+xml",
        }.get(ext, ctype)
        self._serve_file(full, ctype)

    # ---------- 上传 ---------- ---------- Upload ----------
    def handle_upload(self):
        global current_language
        try:
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(length)

            if "multipart/form-data" in content_type:
                shot_data = self._extract_multipart_json(post_data, content_type)
            elif "application/json" in content_type:
                shot_data = json.loads(post_data.decode("utf-8"))
            else:
                shot_data = json.loads(post_data.decode("utf-8"))

            parsed = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed.query)
            machine_id = q.get("machine_id", ["UNKNOWN"])[0]

            shot_id = time.time_ns() // 1000  # 微秒级唯一ID,避免同秒上传撞车 microsecond-unique ID to avoid same-second filename collisions
            filename = f"shot_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{shot_id}.json"
            filepath = os.path.join(DATA_DIR, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(shot_data, f, ensure_ascii=False, indent=2)

            # 先应答,再后台渲染+打印 Respond first, then render + print in the background
            self._send_json({
                "status": "success", "id": shot_id,
                "message": f"Shot data received and saved as {filename}",
                "auto_printed": PRINT_ENABLED,
            })

            threading.Thread(target=self._process_shot,
                             args=(filepath, filename, shot_id, machine_id, len(post_data)),
                             daemon=True).start()

        except Exception as e:
            self.send_error(400, f"Upload error: {e}")

    def _extract_multipart_bytes(self, post_data, content_type):
        """极简multipart解析:取出第一个文件字段的**原始字节**。

        和 _extract_multipart_json 是同一件事,区别只在于全程不解码 —— 备份包是
        zip(二进制),先 decode 成 str 会把内容毁掉。

        The minimal multipart parse: pull the raw bytes of the first file field. Same job
        as _extract_multipart_json except nothing is ever decoded — a backup is a zip
        (binary), and decoding it to str first would corrupt it.
        """
        import re
        m = re.search(r"boundary=([^;]+)", content_type)
        if not m:
            raise ValueError("multipart 缺少 boundary / no boundary in content-type")
        marker = b"--" + m.group(1).strip('"').encode()
        for part in post_data.split(marker):
            if b"filename=" not in part[:400]:
                continue
            header_end = part.find(b"\r\n\r\n")
            if header_end <= 0:
                continue
            body = part[header_end + 4:]
            # 结尾那个 CRLF 是 boundary 前的分隔符,不算内容,精确去掉两个字节。
            # 不能用 rstrip():二进制内容末尾真的以 CRLF 结束时会被误删。
            #
            # The trailing CRLF separates the content from the boundary and is not part
            # of it — drop exactly those two bytes. rstrip() is wrong here: binary
            # content that genuinely ends in CRLF would lose it.
            if body.endswith(b"\r\n"):
                body = body[:-2]
            return body
        raise ValueError("No file part found in multipart data")

    def _extract_multipart_json(self, post_data, content_type):
        """极简multipart解析:取出第一个文件字段的JSON内容"""
        return json.loads(self._extract_multipart_bytes(post_data, content_type).decode("utf-8"))

    def _process_shot(self, filepath, filename, shot_id, machine_id, data_size):
        global current_language
        with open(filepath, "r", encoding="utf-8") as f:
            shot_data = json.load(f)

        original_bean = shot_data.get("meta", {}).get("bean", {}).get("type", "未知")
        # 上传时仅用缓存/静态表翻译(不调AI API;主动翻译请用卡片🌐或大图chips)
        # upload-time translation is cache/static-map only (no AI calls; use 🌐/chips for on-demand)
        lang = current_language
        if lang in LANGUAGES:
            bean_meta = shot_data.get("meta", {}).get("bean", {})
            if bean_meta and (lang != "zh" or not re.search("[一-鿿]", str(bean_meta.get("type", "")))):
                bean_meta = dict(bean_meta)
                bean_meta["type"] = clean_bean_text(ai_translate(str(bean_meta.get("type", "")), lang, allow_api=False))
                bean_meta["notes"] = clean_bean_text(ai_translate(str(bean_meta.get("notes", "")), lang, allow_api=False))
                if bean_meta.get("roast_level"):
                    bean_meta["roast_level"] = clean_bean_text(ai_translate(str(bean_meta["roast_level"]), lang, allow_api=False))
                shot_data.setdefault("meta", {})["bean"] = bean_meta
            profile = dict(shot_data.get("profile", {}))
            if profile.get("title") and (lang != "zh" or not re.search("[一-鿿]", str(profile["title"]))):
                profile["title"] = clean_bean_text(ai_translate(str(profile["title"]), lang, allow_api=False))
                shot_data["profile"] = profile

        # 服务端不再画图:把展示用的翻译写回文件,前端取数据时直接就能画。
        # 这样翻译只做一次,而不是每个客户端各翻译一遍。
        #
        # The server draws nothing now. Display translations are written back into
        # the file so any client can just fetch and draw — translation happens
        # once instead of once per client.
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(shot_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("⚠️ 翻译结果写回失败 / failed to write back translation: %s" % e)

        shot_info = {
            "data_lang": lang,        # 数据当前是什么语言(旧字段 chart_lang 的含义变了)
                                      # which language the stored data is in (the old
                                      # chart_lang field, now meaning something different)
            "id": shot_id,
            "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "filename": filename,
            "data_size": data_size,
            "clock": shot_data.get("clock", "unknown"),
            "profile": shot_data.get("profile", {}).get("title", "unknown"),
            "machine_id": machine_id,
            "bean": original_bean,  # 记录存原始语言,显示层翻译 / record keeps the original; display translates
        }
        with shots_lock:
            received_shots.append(shot_info)
            if len(received_shots) > 5000:
                del received_shots[:-1000]
        persist_index()  # 锁外调用,避免与内部锁死锁;持久化历史,重启不丢 called outside the lock to avoid a deadlock with the inner lock; persists history across restarts

        if PRINT_ENABLED:
            # 渲染搬到了浏览器,所以「收到就自动打印」这件事也交给持有打印机的那
            # 一端:这里只把任务挂进待打印队列,由前端(浏览器 / Android WebView)
            # 取走后渲染 + 打印,打完再回来确认。
            #
            # Rendering moved into the browser, so "print on arrival" moves to
            # whichever end owns the printer too: this only queues the job. The
            # front end (browser or Android WebView) picks it up, renders and
            # prints, then comes back to acknowledge.
            # 把机器名一起带上 —— 它只在这个索引里,打印那一端拿不到别的来源
            # Carry the machine name; the index is the only place it exists
            if queue_print_job(filename, shot_info.get("machine_id", "")):
                print("🖨️ 已加入待打印队列 / queued for printing: %s" % filename)

# ---------------------------------------------------------------------------
# 入口 Entry
# ---------------------------------------------------------------------------
def render_template(text):
    """
    替换 Web UI 模板里的 {{VERSION}} 与 {{LANG}} 占位符。

    这两个占位符同时出现在 index.html 和 app.js 里(index.html 用 VERSION,
    app.js 用 LANG),所以两边共用这一个函数,免得哪天只改了一处、另一处又坏掉。

    Substitute the {{VERSION}} and {{LANG}} placeholders in the web UI templates.

    Both placeholders appear in index.html and app.js (VERSION in the page, LANG in
    the script), so both go through this one function — otherwise a change to one
    call site quietly leaves the other broken.
    """
    text = text.replace("{{VERSION}}", VERSION)

    lang_json = dict(LANGUAGES.get(current_language, LANGUAGES["en"]))
    # 附带当前语言码与可用语言列表(供切换器动态渲染)
    # attach the current code and the available languages for the switcher
    lang_json["__code"] = current_language
    lang_json["__version"] = VERSION
    lang_json["__languages"] = [{"code": "en", "name": "English"}, {"code": "zh", "name": "中文"}] + [
        {"code": c, "name": i.get("name", c)}
        for c, i in settings.get("languages", {}).items()]

    blob = json.dumps(lang_json, ensure_ascii=False)
    # 防单引号破坏 JS 字符串(can't 之类)/ keep apostrophes from breaking the JS string
    blob = blob.replace("'", "&#39;")
    blob = blob.replace("{VERSION}", VERSION)
    return text.replace("{{LANG}}", blob)

def setup_packaged_logging():
    """
    打包版把输出同时写进日志文件 / also write output to a log file in packaged builds.

    为什么需要:PyInstaller 在 macOS 上用了 argv_emulation,打包后的进程 stdout
    是断开的 —— 双击启动没有终端,启动横幅、报错、异常堆栈全都无处可去。用户看到的
    只有「双击了,没反应」,而我们这边连一句线索都拿不到。

    这里把 stdout/stderr 接到数据目录下的 server.log(仍然保留原有的 stdout,
    所以从终端运行时行为不变)。

    Why this exists: on macOS PyInstaller uses argv_emulation, and the packaged
    process has no working stdout — launching from Finder means no terminal, so the
    startup banner, warnings and tracebacks all go nowhere. The user sees "I
    double-clicked and nothing happened" and we get not one clue.

    This tees stdout/stderr into server.log in the data directory (the original
    stdout is kept, so behaviour from a terminal is unchanged).

    返回日志路径,失败时返回 None / returns the log path, or None.
    """
    if not getattr(sys, "frozen", False):
        return None
    try:
        log_path = os.path.join(runtime_data_dir(), "server.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        # 每次启动新开一份,同时保留上一份 —— 崩溃之后重启,上一次的记录不能丢
        # A fresh file each start, keeping the previous one: after a crash and a
        # restart, the record of the crash must not be overwritten
        if os.path.exists(log_path):
            try:
                os.replace(log_path, log_path + ".1")
            except OSError:
                pass

        class _Tee:
            """同时写日志和原 stdout / write to the log and to the original stdout."""
            def __init__(self, *streams):
                self._streams = [x for x in streams if x is not None]

            def write(self, data):
                for st in self._streams:
                    try:
                        st.write(data)
                    except Exception:
                        pass
                return len(data)

            def flush(self):
                for st in self._streams:
                    try:
                        st.flush()
                    except Exception:
                        pass

            def isatty(self):
                # 让 argparse 等按「不是终端」处理,避免依赖终端宽度
                # So argparse and friends treat it as "not a terminal"
                return False

        fh = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = _Tee(fh, sys.__stdout__)
        sys.stderr = _Tee(fh, sys.__stderr__)
        print(f"📝 日志 / log: {log_path}")
        return log_path
    except Exception:
        return None

def ensure_directories():
    # 只有数据目录了 —— 不再有图片目录,因为服务端不画图
    # Only the data directory remains: there is no image directory any more
    # because the server draws nothing.
    os.makedirs(DATA_DIR, exist_ok=True)

def persist_index():
    """把历史列表写入 shots_data/index.json(重启后恢复用)"""
    try:
        with shots_lock:
            with open(os.path.join(DATA_DIR, "index.json"), "w", encoding="utf-8") as f:
                json.dump(received_shots, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 持久化失败 / Persist failed: {e}")

def load_history():
    """启动时恢复历史:index.json 优先,再扫描目录兜底(崩溃恢复)"""
    global received_shots
    restored = []
    index_path = os.path.join(DATA_DIR, "index.json")
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                restored = json.load(f)
        except Exception as e:
            print(f"⚠️ index.json 读取失败,将重建: {e}")
            restored = []

    # 目录扫描:补上索引里没有的文件(机器ID无法从文件恢复,标 UNKNOWN) Directory scan: fill in files missing from the index (machine ID can't be recovered from files, marked UNKNOWN)
    known = {s.get("filename") for s in restored}
    try:
        for fn in sorted(os.listdir(DATA_DIR)):
            if not fn.endswith(".json") or fn == "index.json" or fn in known:
                continue
            fp = os.path.join(DATA_DIR, fn)
            profile, data_size, clock, data = "unknown", 0, "unknown", None
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                profile = data.get("profile", {}).get("title", "unknown")
                data_size = os.path.getsize(fp)
                clock = data.get("clock", "unknown")
            except Exception:
                pass
            ts = fn.replace("shot_", "").split("_")
            timestamp = ts[0] + "_" + ts[1] if len(ts) >= 2 else ""
            bean = data.get("meta", {}).get("bean", {}).get("type", "未知") if data else "未知"
            restored.append({
                "id": 0, "timestamp": timestamp, "filename": fn,
                "data_size": data_size, "clock": clock,
                "profile": profile, "machine_id": "UNKNOWN",
                "data_lang": "",  # 未知:首次查看按界面语言重译 / unknown; retranslated on first view
                "bean": bean,
            })
    except FileNotFoundError:
        pass

    # 补齐旧索引条目缺失的 bean 字段(解析JSON文件,一次性) Backfill the bean field for old index entries (parse JSON files, one-time)
    for s in restored:
        if "bean" not in s:
            try:
                with open(os.path.join(DATA_DIR, s.get("filename", "")), "r", encoding="utf-8") as f:
                    d = json.load(f)
                s["bean"] = d.get("meta", {}).get("bean", {}).get("type", "未知")
            except Exception:
                s["bean"] = "未知"

    restored.sort(key=lambda s: s.get("timestamp", ""), reverse=True)
    with shots_lock:
        received_shots = restored[:5000]
    persist_index()

def print_server_info(port):
    import socket
    hostname = socket.gethostname()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "localhost"
    adapter = get_printer()
    printers = []
    try:
        printers = adapter.list_printers()
    except Exception:
        pass
    default_printer = next((p["name"] for p in printers if p.get("default")),
                           printers[0]["name"] if printers else "—")

    print("")
    print("🍳 " + "=" * 62)
    print(f"🍳           PrintTheShot Next v{VERSION}")
    print("🍳 " + "=" * 62)
    print(f"🍳  管理界面 / Web UI:   http://localhost:{port}")
    print(f"🍳  局域网访问 / LAN:    http://{local_ip}:{port}")
    print(f"🍳  上传端点 / Upload:   http://{local_ip}:{port}/upload")
    print(f"🍳  数据目录 / Data:     {os.path.abspath(DATA_DIR)}")
    print(f"🍳  打印平台 / Platform: {adapter.display_name} ({adapter.platform_id})")
    print(f"🍳  打印功能 / Print:    {'启用 / on' if PRINT_ENABLED else '禁用 / off'}")
    print(f"🍳  打印机数 / Printers: {len(printers)}  默认 / default: {default_printer}")
    if not adapter.is_available():
        print("🍳  ⚠️  未检测到可用的打印适配器 / no usable printing adapter detected")
    print(f"🍳  启动时间 / Started:  {server_start_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 打包版由双击启动:没有终端、没有窗口、也不显示 Dock 图标,用户看不到上面这
    # 一屏横幅,无从判断「到底跑起来没有」。自动打开一次管理界面,让他立刻看到结果。
    # 只在打包版这么做,用 --no-browser 可以关掉。
    #
    # A packaged app is started by double-click: no terminal, no window, and no Dock
    # icon, so none of the banner above is visible and there is no way to tell whether
    # it came up at all. Open the web UI once so the user immediately sees the result.
    # Packaged builds only; --no-browser turns it off.
    if not NO_BROWSER:
        try:
            import webbrowser
            webbrowser.open("http://localhost:%d" % port)
        except Exception as e:
            print("⚠️ 无法自动打开浏览器 / could not open a browser: %s" % e)
    print("🍳  绘制在浏览器完成,服务端不出图 / rendering happens in the browser")
    print("🍳  Ctrl+C 停止 / Stop")
    print("🍳 " + "=" * 62)

def main():
    global PRINT_ENABLED, NO_BROWSER, _shutdown_hook
    parser = argparse.ArgumentParser(
        description="PrintTheShot Next — data relay + print dispatch "
                    "(rendering happens in the browser)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-print", action="store_true",
                        help="启动时禁用自动打印 / disable auto-printing at startup")
    parser.add_argument("--list-printers", action="store_true",
                        help="列出当前平台的打印机后退出 / list printers and exit")
    parser.add_argument("--print-mode", choices=["driver", "raw", "escpos", "bmp"],
                        help="覆盖打印模式 / override the printing mode")
    parser.add_argument("--no-browser", action="store_true",
                        help="启动时不自动打开管理界面 / do not open the web UI on start")
    args = parser.parse_args()

    if args.list_printers:
        adapter = get_printer()
        print("平台 / platform: %s (%s)" % (adapter.display_name, adapter.platform_id))
        print("可用 / available: %s" % adapter.is_available())
        for p in adapter.list_printers():
            print("  %s %s [%s]" % ("*" if p.get("default") else " ", p["name"], p["status"]))
        sys.exit(0)

    if args.no_print:
        PRINT_ENABLED = False
    if args.no_browser:
        NO_BROWSER = True

    # 尽早接上日志:任何后续的报错都要能被记下来
    # Attach the log as early as possible so any later failure is recorded
    setup_packaged_logging()

    ensure_directories()
    load_settings()   # 加载AI设置与自定义语言 / load AI settings & custom languages
    load_history()    # 恢复历史数据(重启不丢)/ restore history so restarts lose nothing

    # 用保存下来的打印设置装载适配器 / load the adapter with the persisted print settings
    printer_config = dict(settings.get("print", {}) or {})
    if args.print_mode:
        printer_config["mode"] = args.print_mode
    refresh_printer(printer_config)

    print_server_info(args.port)

    class ReuseTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with ReuseTCPServer(("", args.port), PrintTheShotHandler) as httpd:
        # 把停止钩子接到这个 server 实例上,Web UI 的「停止服务」按钮走的就是它
        # Wire the shutdown hook to this server instance; the web UI's stop button
        # goes through it
        _shutdown_hook = httpd.shutdown

        print(f"✅ 服务器启动成功 / Server started on port {args.port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        print("\n🛑 服务器已停止 / Server stopped")

if __name__ == "__main__":
    main()
