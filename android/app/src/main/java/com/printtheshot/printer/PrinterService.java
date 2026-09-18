package com.printtheshot.printer;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import androidx.core.app.NotificationCompat;

/**
 * 打印前台服务(后台保活)/ Printing foreground service (background keep-alive)
 * ==============================================================================
 *
 * 中文
 * ----
 * 干什么用:
 *   1) 让 App 退到后台时不被系统回收,蓝牙长连接也就不会断 —— 打印完一张小票
 *      顺手切出去看别的东西,回来还能接着打;
 *   2) 顺带把 {@link LocalPrintBridge} 那个本机 HTTP 桥拉起来(默认开),给
 *      「服务端也跑在这台 Android 上」的部署方式留出口。
 *
 * 什么时候该关:不打印的时候就关掉。前台服务一直挂着费电,而且从 Android 15
 * (API 35) 起,dataSync 类型的前台服务有每日累计时长上限,挂太久会被系统掐掉。
 * 所以它是个「需要时开、用完关」的东西,不是常驻。
 * When to stop it: when you are not printing. A always-on foreground service
 * drains the battery, and from Android 15 (API 35) the dataSync type has a
 * daily cumulative runtime cap, after which the system stops it. It is meant
 * to be started when needed and stopped afterwards, not to run forever.
 *
 * 类型声明 / service type:
 *   打印属于「在本机与外部设备之间搬运数据」,对应 dataSync,所以
 *   AndroidManifest 里声明 `android:foregroundServiceType="dataSync"` ——
 *   Android 14 (API 34) 起,前台服务不声明类型会直接抛异常。
 *   Printing is "moving data between the device and an external device", i.e.
 *   dataSync, hence `android:foregroundServiceType="dataSync"` in the
 *   manifest: from Android 14 (API 34) a foreground service without a
 *   declared type throws.
 *
 * 通知 / notification:
 *   Android 13 (API 33) 起发通知需要 POST_NOTIFICATIONS 权限。用户拒绝的话服务
 *   仍然能跑,只是通知被系统藏起来(Android 会把前台服务的通知降级显示)。所以
 *   拒绝通知权限不该阻断打印 —— 插件里把 notifications 单独返回就是这个意思。
 *   From Android 13 (API 33) notifications need POST_NOTIFICATIONS. If the
 *   user declines, the service still runs; the system just hides the
 *   notification. Declining must therefore never block printing, which is why
 *   the plugin reports `notifications` separately.
 *
 * English
 * -------
 * Two jobs: (1) keep the app process alive while it is in the background so the
 * long-lived Bluetooth link survives, and (2) optionally run
 * {@link LocalPrintBridge} (on by default), the loopback HTTP bridge for
 * deployments where the Python service also runs on this Android device.
 */
public class PrinterService extends Service {

    private static final String TAG = BluetoothPrinter.TAG;

    /** 启动动作 / start action. */
    public static final String ACTION_START = "com.printtheshot.printer.action.START";

    /** 停止动作 / stop action. */
    public static final String ACTION_STOP = "com.printtheshot.printer.action.STOP";

    /**
     * 是否同时拉起本机 HTTP 桥,默认 true /
     * whether to run the loopback HTTP bridge too; defaults to true.
     */
    public static final String EXTRA_HTTP_BRIDGE = "http_bridge";

    /** 通知渠道 ID / notification channel id. */
    private static final String CHANNEL_ID = "print_the_shot";

    /** 通知 ID / notification id. */
    private static final int NOTIFICATION_ID = 1001;

    /** 服务是否在跑 / whether the service is running. */
    private static volatile boolean running = false;

    /** 本机 HTTP 桥,没开时为 null / the loopback HTTP bridge, null when off. */
    private LocalPrintBridge httpBridge;

    /**
     * 服务是否在跑 / whether the service is running.
     *
     * 只是本进程内的标记,跨进程不准 / an in-process flag only; not process-safe.
     */
    public static boolean isRunning() {
        return running;
    }

    /**
     * 起服务 / start the service.
     *
     * @return 是否成功发出启动请求(不代表服务已经跑起来)/
     *         whether the start request was accepted (not a guarantee that the
     *         service is up)
     */
    public static boolean start(Context context) {
        return start(context, true);
    }

    /**
     * 起服务 / start the service.
     *
     * @param httpBridge 是否同时拉起本机 HTTP 桥 / also run the loopback HTTP bridge
     */
    public static boolean start(Context context, boolean httpBridge) {
        if (context == null) {
            return false;
        }
        Intent intent = new Intent(context, PrinterService.class)
                .setAction(ACTION_START)
                .putExtra(EXTRA_HTTP_BRIDGE, httpBridge);
        try {
            // Android 8 (API 26) 起后台不能直接 startService,要用
            // startForegroundService,并且起来之后必须在几秒内 startForeground,
            // 否则系统会抛 ANR 级别的异常。
            // From Android 8 (API 26) a background startService is not allowed;
            // startForegroundService is required, and the service must call
            // startForeground within a few seconds or the system throws.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent);
            } else {
                context.startService(intent);
            }
            return true;
        } catch (Exception e) {
            Log.w(TAG, "启动前台服务失败 / cannot start the foreground service: " + e.getMessage());
            return false;
        }
    }

    /** 停服务 / stop the service. */
    public static void stop(Context context) {
        if (context == null) {
            return;
        }
        try {
            context.stopService(new Intent(context, PrinterService.class));
        } catch (Exception e) {
            Log.w(TAG, "停止前台服务失败 / cannot stop the foreground service: " + e.getMessage());
        }
    }

    // ------------------------------------------------------------------
    // 生命周期 / lifecycle
    // ------------------------------------------------------------------

    @Override
    public void onCreate() {
        super.onCreate();
        running = true;
        // 服务也可能是 App 的第一个入口,顺手把上下文补给蓝牙层 /
        // the service may be the first component to run, so feed the context
        // to the Bluetooth layer here too
        BluetoothPrinter.get().init(this);
        BluetoothPrinterBridge.attach(this);
        createNotificationChannel();
    }

    @Override
    public IBinder onBind(Intent intent) {
        // 不需要绑定 / no binding
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START : intent.getAction();

        if (ACTION_STOP.equals(action)) {
            stopSelf();
            return START_NOT_STICKY;
        }

        startForegroundCompat();

        boolean withBridge = intent == null || intent.getBooleanExtra(EXTRA_HTTP_BRIDGE, true);
        if (withBridge) {
            startHttpBridge();
        } else {
            stopHttpBridge();
        }

        // 被系统回收后自动重启,蓝牙连接才有机会重连 /
        // restart after being killed, so the Bluetooth link can come back
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        stopHttpBridge();
        running = false;
        Log.i(TAG, "前台服务已停止 / foreground service stopped");
        super.onDestroy();
    }

    // ------------------------------------------------------------------
    // 通知 / notification
    // ------------------------------------------------------------------

    /** 建通知渠道 / create the notification channel (API 26+). */
    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager =
                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "PrintTheShot",
                // LOW:常驻通知,不需要提示音,也不要弹出来打扰 /
                // LOW: a quiet persistent notification, no sound, no popup
                NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("PrintTheShot 打印服务 / PrintTheShot printing service");
        channel.setShowBadge(false);
        manager.createNotificationChannel(channel);
    }

    /** 构建常驻通知 / build the persistent notification. */
    private Notification buildNotification() {
        // 点通知回到 App / tapping the notification returns to the app
        Intent launch = getPackageManager().getLaunchIntentForPackage(getPackageName());
        PendingIntent contentIntent = null;
        if (launch != null) {
            int piFlags = PendingIntent.FLAG_UPDATE_CURRENT;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                // Android 12 (API 31) 起必须显式说明 PendingIntent 可变性 /
                // from Android 12 (API 31) mutability must be explicit
                piFlags |= PendingIntent.FLAG_IMMUTABLE;
            }
            contentIntent = PendingIntent.getActivity(this, 0, launch, piFlags);
        }

        NotificationCompat.Builder builder = new NotificationCompat.Builder(this, CHANNEL_ID)
                // 用系统自带的图标,免得为了一个图标再塞一份 drawable 资源。
                //
                // 必须用 **公开** 的系统资源:stat_sys_data_sync 看着更贴切,但它是
                // @hide 的,编译期直接报「找不到符号」。这一点只有拿真的 Android SDK
                // 编译一次才会暴露 —— 用桩代码做检查是发现不了的。
                //
                // A built-in system icon, so no extra drawable has to ship.
                //
                // It must be a **public** system resource: stat_sys_data_sync reads
                // better but is @hide, and referencing it fails to compile with
                // "cannot find symbol" — something only a real Android SDK compile
                // exposes, never a stub-based check.
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setContentTitle("PrintTheShotNext")
                .setContentText("打印服务运行中 / printing service running")
                .setOngoing(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setShowWhen(false);

        if (contentIntent != null) {
            builder.setContentIntent(contentIntent);
        }
        return builder.build();
    }

    /**
     * 进前台 / enter the foreground state.
     *
     * Android 10 (API 29) 起可以显式带上服务类型;Android 14 (API 34) 起类型
     * 必须与 manifest 里声明的一致(dataSync),否则直接抛异常。
     * From Android 10 (API 29) the service type can be passed explicitly, and
     * from Android 14 (API 34) it must match the manifest declaration
     * (dataSync) or the call throws.
     */
    private void startForegroundCompat() {
        Notification notification = buildNotification();
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIFICATION_ID, notification,
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
            } else {
                startForeground(NOTIFICATION_ID, notification);
            }
        } catch (Exception e) {
            // 权限被削、类型没声明之类都会走到这里;打日志但要让它自己冒出去,
            // 免得留下一个「前台服务没起来」的静默失败。
            // Missing permission or an undeclared type lands here; log it and let
            // it propagate rather than fail silently.
            Log.w(TAG, "startForeground 失败 / startForeground failed: " + e.getMessage());
            throw e;
        }
    }

    // ------------------------------------------------------------------
    // 本机 HTTP 桥 / loopback HTTP bridge
    // ------------------------------------------------------------------

    /**
     * 拉起 HTTP 桥 / start the HTTP bridge.
     *
     * 幂等,已经起来就什么都不做 / idempotent: does nothing when already up.
     */
    private void startHttpBridge() {
        if (httpBridge != null) {
            return;
        }
        try {
            LocalPrintBridge bridge = new LocalPrintBridge();
            bridge.start();
            this.httpBridge = bridge;
            Log.i(TAG, "本机打印桥已监听 / local print bridge listening on "
                    + LocalPrintBridge.HOST + ":" + LocalPrintBridge.DEFAULT_PORT);
        } catch (Exception e) {
            // 端口被占之类:不是致命错误,打印主路径(JS 插件)照样能用 /
            // e.g. the port is taken: not fatal, the JS plugin path still works
            Log.w(TAG, "本机打印桥启动失败 / cannot start the local print bridge: " + e.getMessage());
            this.httpBridge = null;
        }
    }

    /** 关掉 HTTP 桥 / stop the HTTP bridge. */
    private void stopHttpBridge() {
        if (httpBridge == null) {
            return;
        }
        httpBridge.stop();
        httpBridge = null;
    }
}
