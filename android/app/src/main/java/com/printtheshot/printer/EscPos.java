package com.printtheshot.printer;

import java.io.ByteArrayOutputStream;
import java.util.Arrays;

/**
 * ESC/POS 指令常量与轻量拼装 / ESC/POS command constants and light assembly helpers
 * ==============================================================================
 *
 * 中文
 * ----
 * 这里放的是热敏小票机通用的 ESC/POS 指令常量,以及几个顺手的小拼装函数。
 *
 * 重要:本项目的正常打印路径**不需要**这里拼装任何东西。
 *   Web UI(web/render.js + 后续的 web/printer.js)在浏览器侧已经把「复位 →
 *   GS v 0 光栅位图 → 走纸 → 切纸」整份字节流拼好了,base64 之后交给原生层;
 *   原生层只负责原样写进蓝牙 socket,一个字节都不改。
 *   Python 服务端(printers/escpos.py)同样是自己拼完整份字节流再交给桥接。
 *   位图相关的按位打包逻辑因此**刻意不在这里重复实现** —— 同一段字节拼装逻辑
 *   只应该存在一处,否则改一处漏一处。
 *
 * 那这些常量给谁用?
 *   1) 调试:想手工往打印机丢一条换行/切纸指令时不用现查表;
 *   2) 扩展:以后要在原生层加「打印自检页」之类的功能时有现成的零件;
 *   3) 可读性:阅读上面两条路径产出的字节流时有个对照表。
 *
 * English
 * -------
 * Common ESC/POS constants for thermal receipt printers, plus a few small
 * assembly helpers.
 *
 * Important: the normal print path in this project does **not** assemble
 * anything here. The web UI (web/render.js plus the forthcoming web/printer.js)
 * already builds the complete byte stream on the browser side — reset →
 * `GS v 0` raster bitmap → feed → cut — and hands it to the native layer as
 * base64. The native layer writes those bytes into the Bluetooth socket
 * verbatim, without touching a single byte. The Python service
 * (printers/escpos.py) likewise builds its own complete job before handing it
 * to a bridge. Bit-level raster packing is therefore deliberately **not**
 * reimplemented here: one piece of byte-assembly logic should live in exactly
 * one place, or it will be fixed in one place and forgotten in the other.
 *
 * So who uses these constants?
 *   1) Debugging — no need to look up the table to send a line feed or a cut;
 *   2) Extension — ready-made parts for e.g. a native self-test page;
 *   3) Readability — a reference table when reading byte streams produced by
 *      the two paths above.
 */
public final class EscPos {

    /** 工具类,不实例化 / utility class, not instantiable. */
    private EscPos() {
    }

    // ------------------------------------------------------------------
    // 基础控制字节 / basic control bytes
    // ------------------------------------------------------------------

    /** ESC 0x1B / ESC, the escape introducer. */
    public static final byte ESC = 0x1B;

    /** GS 0x1D / GS, the group separator introducer. */
    public static final byte GS = 0x1D;

    /** DLE 0x10 / DLE, used by real-time status queries. */
    public static final byte DLE = 0x10;

    /** LF 0x0A 换行 / line feed. */
    public static final byte LF = 0x0A;

    /** CR 0x0D 回车 / carriage return. */
    public static final byte CR = 0x0D;

    /** HT 0x09 水平制表 / horizontal tab. */
    public static final byte HT = 0x09;

    // ------------------------------------------------------------------
    // 对齐 / alignment
    // ------------------------------------------------------------------

    /** 左对齐 / left justification. */
    public static final int ALIGN_LEFT = 0;

    /** 居中 / center justification. */
    public static final int ALIGN_CENTER = 1;

    /** 右对齐 / right justification. */
    public static final int ALIGN_RIGHT = 2;

    // ------------------------------------------------------------------
    // 常用指令序列 / frequently used command sequences
    // ------------------------------------------------------------------

    /** `ESC @` 复位打印机 / reset the printer to its power-on state. */
    public static final byte[] INITIALIZE = {ESC, 0x40};

    /** `LF` 走一行 / feed one line. */
    public static final byte[] FEED_ONE_LINE = {LF};

    /** `GS V 0` 全切 / full cut (may be unsupported on portable units). */
    public static final byte[] CUT_FULL = {GS, 0x56, 0x00};

    /**
     * `GS V 66 0` 部分切纸 / partial cut.
     *
     * 部分切在多数机型上更可靠(留一点不切断,不容易卡纸),所以打印任务默认
     * 用它 —— 与 printers/escpos.py 的默认行为保持一致。
     * Partial cut is more reliable on most hardware (a small tab stays
     * attached, so the paper does not jam), hence it is the default for print
     * jobs — matching the default in printers/escpos.py.
     */
    public static final byte[] CUT_PARTIAL = {GS, 0x56, 0x42, 0x00};

    /**
     * `GS v 0 0` 光栅位图指令头 / raster bitmap command prefix (`GS v 0 m`).
     *
     * 后面还要跟 xL xH yL yH 与位图数据 —— 那部分由 Web / Python 侧负责拼,
     * 这里只留个头做对照。
     * The xL xH yL yH header and the bitmap data follow; the web and Python
     * sides build those. Only the prefix is kept here for reference.
     */
    public static final byte[] RASTER_GS_V_0 = {GS, 0x76, 0x30, 0x00};

    /**
     * `DLE EOT n` 实时状态查询 / real-time status request (`DLE EOT n`).
     *
     * n=1 打印机状态,n=4 纸张状态。部分机型不响应,查询前请留好超时。
     * n=1 is printer status, n=4 is paper status. Some units do not answer,
     * so always leave a timeout when querying.
     */
    public static byte[] realtimeStatus(int n) {
        return new byte[]{DLE, 0x04, (byte) (n & 0x0F)};
    }

    // ------------------------------------------------------------------
    // 拼装小工具 / small assembly helpers
    // ------------------------------------------------------------------

    /**
     * `ESC a n` 设置对齐 / set justification.
     *
     * @param mode {@link #ALIGN_LEFT} / {@link #ALIGN_CENTER} / {@link #ALIGN_RIGHT}
     */
    public static byte[] align(int mode) {
        return new byte[]{ESC, 0x61, (byte) (mode & 0x03)};
    }

    /**
     * `ESC d n` 走纸 n 行 / feed n lines.
     *
     * @param lines 行数,自动夹到 0..255 / line count, clamped to 0..255
     */
    public static byte[] feed(int lines) {
        int n = lines < 0 ? 0 : (lines > 255 ? 255 : lines);
        return new byte[]{ESC, 0x64, (byte) n};
    }

    /**
     * `ESC 3 n` 设置行间距 / set line spacing to n dots.
     *
     * @param dots 点数,自动夹到 0..255 / dot count, clamped to 0..255
     */
    public static byte[] lineSpacing(int dots) {
        int n = dots < 0 ? 0 : (dots > 255 ? 255 : dots);
        return new byte[]{ESC, 0x33, (byte) n};
    }

    /**
     * `ESC E n` 粗体开关 / turn emphasized (bold) mode on or off.
     */
    public static byte[] bold(boolean on) {
        return new byte[]{ESC, 0x45, (byte) (on ? 1 : 0)};
    }

    /**
     * `ESC - n` 下划线开关 / turn underline on or off.
     */
    public static byte[] underline(boolean on) {
        return new byte[]{ESC, 0x2D, (byte) (on ? 1 : 0)};
    }

    /**
     * `GS ! n` 字符倍宽倍高 / set character width and height multipliers.
     *
     * @param width  1..8 倍宽 / width multiplier 1..8
     * @param height 1..8 倍高 / height multiplier 1..8
     */
    public static byte[] textSize(int width, int height) {
        int w = clamp(width, 1, 8) - 1;
        int h = clamp(height, 1, 8) - 1;
        return new byte[]{GS, 0x21, (byte) ((w << 4) | h)};
    }

    /**
     * 纯 ASCII 文本 + 换行 / plain ASCII text followed by a line feed.
     *
     * 只做 ASCII。中文字符必须走位图路径 —— 那正是 Web UI 的做法(整张图以
     * `GS v 0` 光栅位图输出),所以这里不需要代码页/编码转换,也就不需要引入
     * 任何字符集依赖。
     * ASCII only. CJK characters must take the bitmap path — which is exactly
     * what the web UI does (the whole chart goes out as a `GS v 0` raster
     * bitmap), so no code-page or encoding conversion is needed here, and no
     * charset dependency has to be pulled in.
     */
    public static byte[] textLine(String ascii) {
        return text(ascii, true);
    }

    /**
     * 纯 ASCII 文本 / plain ASCII text.
     *
     * @param ascii    文本,非 ASCII 字符会被替换成 '?' /
     *                 text; non-ASCII characters are replaced with '?'
     * @param newline  是否追加换行 / whether to append a line feed
     */
    public static byte[] text(String ascii, boolean newline) {
        String s = ascii == null ? "" : ascii;
        ByteArrayOutputStream out = new ByteArrayOutputStream(s.length() + 1);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            out.write(c < 0x80 ? c : '?');
        }
        if (newline) {
            out.write(LF);
        }
        return out.toByteArray();
    }

    /**
     * 把几段指令接起来 / concatenate several command chunks.
     *
     * 只做拼接,不做任何解释或校验 —— 调用方给什么就写什么。
     * Pure concatenation: no interpretation, no validation.
     */
    public static byte[] concat(byte[]... chunks) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        if (chunks != null) {
            for (byte[] chunk : chunks) {
                if (chunk != null && chunk.length > 0) {
                    out.write(chunk, 0, chunk.length);
                }
            }
        }
        return out.toByteArray();
    }

    /**
     * 一行三列的简单排版 / a simple three-column line.
     *
     * 左右两部分靠空格顶开,中间留给空白;打印机是等宽字体,视觉上能对齐。
     * The left and right parts are pushed apart with spaces; receipt printers
     * use a fixed-width font, so this lines up visually.
     */
    public static byte[] twoColumnLine(String left, String right, int columns) {
        String l = left == null ? "" : left;
        String r = right == null ? "" : right;
        int width = columns <= 0 ? 32 : columns;
        int pad = width - l.length() - r.length();
        StringBuilder sb = new StringBuilder(l);
        if (pad > 0) {
            char[] spaces = new char[pad];
            Arrays.fill(spaces, ' ');
            sb.append(spaces);
        } else {
            sb.append(' ');
        }
        sb.append(r);
        return textLine(sb.toString());
    }

    private static int clamp(int value, int min, int max) {
        return value < min ? min : (value > max ? max : value);
    }
}
