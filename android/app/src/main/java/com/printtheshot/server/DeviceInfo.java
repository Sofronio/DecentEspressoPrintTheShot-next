package com.printtheshot.server;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.List;
import java.util.Locale;

/**
 * 设备网络信息 / device network information
 * ==========================================
 *
 * 中文
 * ----
 * 取本机的局域网地址 —— 用户要把它填进 DE1 插件,把 shot 传到这台平板上,
 * 所以这个地址必须是对的。
 *
 * 为什么不能简单地「连一下 8.8.8.8 看源地址」:
 *
 *   那是 Python 桌面端用的办法,也是踩过坑的地方。**开着 VPN 时它会返回 VPN 的
 *   地址**(例如 100.64.x.x 那种 CGNAT 段),而那个地址在局域网里根本不通 ——
 *   用户照着填,然后怎么都连不上,且完全没有线索。
 *
 *   这里改成遍历网卡并显式**排除隧道接口**(tun/tap/utun/ppp),再按优先级挑:
 *   wlan > eth > 其它。平板上 WiFi 是 wlan0,So 这个顺序就够了。
 *
 * English
 * -------
 * Returns this device's LAN address — the user types it into the DE1 plugin so shots
 * are uploaded to this tablet, so it has to be right.
 *
 * Why not just "connect to 8.8.8.8 and read the source address":
 *
 *   That is what the Python desktop side does, and it is where it went wrong. **With a
 *   VPN up it returns the VPN's address** (a 100.64.x.x CGNAT address, say), which is
 *   not reachable from the LAN at all — the user types it in and then cannot connect,
 *   with nothing to indicate why.
 *
 *   This walks the interfaces and explicitly **excludes tunnel interfaces**
 *   (tun/tap/utun/ppp), then prefers wlan > eth > anything else. On a tablet WiFi is
 *   wlan0, so that ordering is enough.
 */
public final class DeviceInfo {

    private DeviceInfo() {
    }

    /** 看起来像隧道/VPN 的接口名 / interface names that look like tunnels or VPNs. */
    private static boolean isTunnel(String name) {
        String n = name.toLowerCase(Locale.US);
        return n.startsWith("tun") || n.startsWith("tap") || n.startsWith("utun")
                || n.startsWith("ppp") || n.startsWith("ipsec") || n.startsWith("wg")
                || n.contains("rmnet") || n.contains("ccmni");   // 蜂窝数据 / cellular
    }

    private static int rank(String name) {
        String n = name.toLowerCase(Locale.US);
        if (n.startsWith("wlan")) return 0;
        if (n.startsWith("eth")) return 1;
        if (n.startsWith("ap")) return 2;      // 热点 / hotspot
        return 3;
    }

    /**
     * 取局域网 IPv4 地址,取不到返回空串 / the LAN IPv4 address, or "" if there is none.
     *
     * 返回空串而不是 "localhost" 之类的占位:调用方据此决定要不要显示,
     * 显示一个错的地址比不显示更糟。
     * Empty rather than a placeholder like "localhost": the caller decides whether to
     * show anything, and showing a wrong address is worse than showing none.
     */
    public static String lanIp() {
        List<String[]> candidates = new ArrayList<>();   // {name, ip}
        try {
            Enumeration<NetworkInterface> nets = NetworkInterface.getNetworkInterfaces();
            for (NetworkInterface ni : Collections.list(nets)) {
                if (ni == null || !ni.isUp() || ni.isLoopback()) continue;
                String name = ni.getName() == null ? "" : ni.getName();
                if (isTunnel(name)) continue;

                for (InetAddress addr : Collections.list(ni.getInetAddresses())) {
                    if (!(addr instanceof Inet4Address)) continue;
                    if (addr.isLoopbackAddress() || addr.isLinkLocalAddress()) continue;
                    String ip = addr.getHostAddress();
                    if (ip == null || ip.isEmpty()) continue;
                    // 100.64.0.0/10 是运营商级 NAT 段,Tailscale 之类的 VPN 常用;
                    // 真局域网不会用它。
                    // 100.64.0.0/10 is the CGNAT range that VPNs like Tailscale use;
                    // a real LAN does not.
                    if (ip.startsWith("100.")) {
                        String[] p = ip.split("\\.");
                        if (p.length == 4) {
                            try {
                                int second = Integer.parseInt(p[1]);
                                if (second >= 64 && second <= 127) continue;
                            } catch (NumberFormatException ignored) { }
                        }
                    }
                    candidates.add(new String[]{name, ip});
                }
            }
        } catch (Exception e) {
            return "";
        }

        if (candidates.isEmpty()) return "";
        Collections.sort(candidates, (a, b) -> Integer.compare(rank(a[0]), rank(b[0])));
        return candidates.get(0)[1];
    }

    /** 完整地址,便于直接展示 / a full URL, ready to show. */
    public static String lanUrl(int port) {
        String ip = lanIp();
        return ip.isEmpty() ? "" : "http://" + ip + ":" + port;
    }
}
