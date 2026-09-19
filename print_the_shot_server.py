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
import threading
import subprocess
import argparse
import http.server
import socketserver
import urllib.parse
import re
from datetime import datetime
from io import BytesIO

VERSION = "2.1-next.1"


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

WEB_INDEX = resource_path(os.path.join("web", "index.html"))
PLUGIN_TCL = resource_path(os.path.join("plugin", "plugin.tcl"))  # bundle内(只读)
PLUGIN_GITHUB_URL = "https://raw.githubusercontent.com/Sofronio/DecentEspressoPrintTheShot/main/plugin/plugin.tcl"
RAW_SERVER_URL = "https://raw.githubusercontent.com/Sofronio/DecentEspressoPrintTheShot/main/print_the_shot_server.py"
GITHUB_ZIP_URL = "https://codeload.github.com/Sofronio/DecentEspressoPrintTheShot/zip/refs/heads/main"


def _version_key(v):
    """
    把版本号解析成可比较的元组 / parse a version string into something comparable.

    原来只取 major.minor,于是 '2.0-beta.2' 和 '2.0-beta.3' 都变成 (2, 0),
    比较结果相等 —— 「检查更新」会告诉 beta.2 的用户「已是最新」,而实际上不是。
    预发布版本更新得越勤,这个 bug 越致命。

        '2.0-beta.2' -> (2, 0, 0, 0, 2)
        '2.0-beta.3' -> (2, 0, 0, 0, 3)     比上面大
        '2.0'        -> (2, 0, 0, 1, 0)     正式版排在所有同名预发布之后
        '2.1-next.1' -> (2, 1, 0, 0, 1)

    第四位是「是否正式版」的哨兵:0 = 预发布,1 = 正式版。

    The old version took only major.minor, so '2.0-beta.2' and '2.0-beta.3' both
    became (2, 0) and compared equal — "check for updates" told beta.2 users they
    were already current when they were not.

    The prerelease number is now part of the comparison. The fourth element is a
    sentinel: 0 for a prerelease, 1 for a final release.
    """
    import re
    v = (v or "").strip()
    m = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", v)
    if not m:
        return (0, 0, 0, 0, 0)
    major, minor = int(m.group(1)), int(m.group(2))
    patch = int(m.group(3) or 0)
    pre = re.search(r"(alpha|beta|rc|next|pre)[.\-]?(\d+)", v, re.I)
    if pre:
        return (major, minor, patch, 0, int(pre.group(2)))
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
        "plugin_step1": "1. Download plugin.tcl and copy to DE1 tablet:",
        "plugin_step2": "/de1plus/plugins/print_the_shot/plugin.tcl",
        "plugin_step3": "2. Restart DE1App, plugin auto-loads",
        "plugin_step4": "3. Set Server URL to this machine's IP:8000",
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
        "btn_update_service": "Update service from GitHub (auto backup)",
        "update_check": "Local {local} · Remote {remote}",
        "update_ok": "Up to date",
        "update_avail": "Update available",
        "update_note": "Auto-backup to backup/ before updating; restart the server after update; packaged builds can't self-update.",
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
        "plugin_step1": "1. 下载 plugin.tcl 并复制到DE1平板:",
        "plugin_step2": "/de1plus/plugins/print_the_shot/plugin.tcl",
        "plugin_step3": "2. 重启DE1App,插件自动加载",
        "plugin_step4": "3. 插件服务器地址填本机IP:8000",
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
        "btn_update_service": "从 GitHub 更新服务(自动备份)",
        "update_check": "当前 {local} · 远程 {remote}",
        "update_ok": "已是最新",
        "update_avail": "有更新可用",
        "update_note": "更新前自动备份到 backup/ 目录;更新后请重启服务器;打包版不支持在线更新。",
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
pending_prints = []      # [{"filename":..., "queued": "...", "attempts": n}]
pending_lock = threading.Lock()
MAX_PENDING = 50


def queue_print_job(filename):
    """把一条 shot 挂进待打印队列 / add a shot to the pending-print queue."""
    with pending_lock:
        if any(j["filename"] == filename for j in pending_prints):
            return
        pending_prints.append({
            "filename": filename,
            "queued": datetime.now().strftime("%H:%M:%S"),
            "attempts": 0,
        })
        # 队列不设上限的话,一台关着的机器能让它无限涨下去
        # Without a cap, a machine that is switched off lets this grow forever
        if len(pending_prints) > MAX_PENDING:
            del pending_prints[:-MAX_PENDING]


def take_pending_prints():
    """取出待打印队列(不移除,等客户端 ack)/ read the queue without removing."""
    with pending_lock:
        return [dict(j) for j in pending_prints]


def ack_pending_print(filename, ok=True):
    """客户端打印完成后确认,把任务摘出队列 / acknowledge a finished job."""
    with pending_lock:
        for i, job in enumerate(pending_prints):
            if job["filename"] == filename:
                if ok:
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
        """GET /api/update/check — 对比本地与GitHub远端版本"""
        import urllib.request, re
        try:
            req = urllib.request.Request(RAW_SERVER_URL, headers={"User-Agent": "PrintTheShotBeta"})
            with urllib.request.urlopen(req, timeout=15) as r:
                content = r.read().decode("utf-8", "replace")
            m = re.search(r'VERSION\s*=\s*"([^"]+)"', content)
            remote = m.group(1) if m else "unknown"
            self._send_json({
                "local": VERSION,
                "remote": remote,
                "update_available": remote != "unknown" and _version_key(remote) > _version_key(VERSION),
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def handle_update(self):
        """POST /api/update — 从GitHub更新整个服务(自动备份);打包版不支持"""
        global current_language
        if getattr(sys, "frozen", False):
            msg = ("打包版本不支持在线更新,请下载新安装包" if current_language == "zh"
                   else "Packaged build can't self-update — download the new installer")
            self._send_json({"success": False, "message": msg})
            return
        ok, msg = perform_update(GITHUB_ZIP_URL, os.getcwd(), current_language)
        self._send_json({"success": ok, "message": msg}, 200 if ok else 500)

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

        if name.endswith((".js", ".html", ".css")):
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

    def _extract_multipart_json(self, post_data, content_type):
        """极简multipart解析:取出第一个文件字段的JSON内容"""
        import re
        boundary = re.search(r"boundary=([^;]+)", content_type).group(1).strip('"')
        parts = post_data.split(("--" + boundary).encode())
        for part in parts:
            if b"filename=" in part[:400]:
                header_end = part.find(b"\r\n\r\n")
                if header_end > 0:
                    body = part[header_end + 4:]
                    body = body.replace(b"\r\n--", b"").rstrip()
                    return json.loads(body.decode("utf-8"))
        raise ValueError("No file part found in multipart data")

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
            queue_print_job(filename)
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
