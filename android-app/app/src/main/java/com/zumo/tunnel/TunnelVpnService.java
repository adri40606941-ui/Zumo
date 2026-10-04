package com.zumo.tunnel;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Intent;
import android.net.VpnService;
import android.os.Build;
import android.os.ParcelFileDescriptor;
import android.util.Log;

/**
 * Foreground VpnService that:
 *   1. brings up the SSH-over-payload tunnel and its local SOCKS5 proxy, and
 *   2. establishes a TUN interface for the whole device.
 *
 * The step that copies packets between the TUN interface and the local SOCKS5
 * proxy is a userspace "tun2socks" engine. This is where you plug in a native
 * library (e.g. badvpn-tun2socks or hev-socks5-tunnel) that reads the TUN fd
 * and relays through 127.0.0.1:SOCKS_PORT. That integration point is marked
 * below. Until it is attached, the SOCKS5 proxy at 127.0.0.1:SOCKS_PORT is
 * already usable by apps that support a SOCKS proxy directly.
 */
public class TunnelVpnService extends VpnService implements SshTunnel.Listener {

    public static final String ACTION_START = "com.zumo.tunnel.START";
    public static final String ACTION_STOP  = "com.zumo.tunnel.STOP";
    public static final int    SOCKS_PORT   = 1080;

    public static final String EXTRA_HOST    = "host";
    public static final String EXTRA_PORT    = "port";
    public static final String EXTRA_USER    = "user";
    public static final String EXTRA_PASS    = "pass";
    public static final String EXTRA_PAYLOAD = "payload";
    public static final String EXTRA_ENGINE  = "engine"; // "hev" | "badvpn"

    private static final String TAG = "Zumo";
    private static final String CHANNEL_ID = "zumo_tunnel";
    private static final int    MTU = 1500;

    private final SshTunnel tunnel = new SshTunnel(this);
    private ParcelFileDescriptor vpnInterface;
    private VpnEngine engine;
    private Thread worker;

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopEverything();
            return START_NOT_STICKY;
        }

        final String host = intent.getStringExtra(EXTRA_HOST);
        final int port = intent.getIntExtra(EXTRA_PORT, 22);
        final String user = intent.getStringExtra(EXTRA_USER);
        final String pass = intent.getStringExtra(EXTRA_PASS);
        final String payload = intent.getStringExtra(EXTRA_PAYLOAD);
        final String engineName = intent.getStringExtra(EXTRA_ENGINE);

        engine = "badvpn".equalsIgnoreCase(engineName)
                ? new BadvpnEngine(this)
                : new HevEngine(this);

        startForeground(1, buildNotification("Conectando..."));

        worker = new Thread(new Runnable() {
            @Override public void run() {
                try {
                    tunnel.connect(host, port, user, pass, payload, SOCKS_PORT);
                    establishVpn();
                    runEngine();
                } catch (Exception e) {
                    onLog("Error: " + e.getMessage());
                    Log.e(TAG, "tunnel error", e);
                    stopEverything();
                }
            }
        }, "zumo-tunnel");
        worker.start();

        return START_STICKY;
    }

    private void establishVpn() {
        Builder b = new Builder();
        b.setSession("Zumo");
        b.addAddress("10.8.0.2", 32);
        b.addDnsServer("1.1.1.1");
        b.addRoute("0.0.0.0", 0);
        b.setMtu(MTU);
        // Keep this app's own sockets out of the tunnel to avoid a loop.
        try { b.addDisallowedApplication(getPackageName()); } catch (Exception ignored) { }
        vpnInterface = b.establish();
        onLog("Interfaz VPN levantada (TUN).");
    }

    private void runEngine() throws Exception {
        if (vpnInterface == null) return;
        onLog("Motor: " + engine.name() + ". Enrutando todo por el túnel...");
        updateNotification("Conectado (" + engine.name() + ")");
        // Bloquea hasta stop(): el motor relaya el TUN por el SOCKS5 del SSH.
        engine.start(vpnInterface, "127.0.0.1", SOCKS_PORT, MTU);
    }

    private void stopEverything() {
        if (engine != null) {
            try { engine.stop(); } catch (Throwable ignored) { }
        }
        tunnel.disconnect();
        if (vpnInterface != null) {
            try { vpnInterface.close(); } catch (Exception ignored) { }
            vpnInterface = null;
        }
        if (worker != null) worker.interrupt();
        stopForeground(true);
        stopSelf();
    }

    // ---- SshTunnel.Listener ----
    @Override public void onLog(String line) {
        Log.i(TAG, line);
        Intent i = new Intent("com.zumo.tunnel.LOG");
        i.setPackage(getPackageName());
        i.putExtra("line", line);
        sendBroadcast(i);
    }

    @Override public void onState(boolean connected) {
        Intent i = new Intent("com.zumo.tunnel.STATE");
        i.setPackage(getPackageName());
        i.putExtra("connected", connected);
        sendBroadcast(i);
    }

    // ---- notification plumbing ----
    private Notification buildNotification(String text) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager nm = getSystemService(NotificationManager.class);
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL_ID, "Zumo", NotificationManager.IMPORTANCE_LOW);
            nm.createNotificationChannel(ch);
        }
        Intent stop = new Intent(this, TunnelVpnService.class).setAction(ACTION_STOP);
        PendingIntent pi = PendingIntent.getService(this, 0, stop,
                PendingIntent.FLAG_UPDATE_CURRENT
                        | (Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0));

        return new Notification.Builder(this,
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.O ? CHANNEL_ID : "")
                .setContentTitle("Zumo")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.stat_sys_download_done)
                .addAction(new Notification.Action.Builder(0, "Desconectar", pi).build())
                .setOngoing(true)
                .build();
    }

    private void updateNotification(String text) {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm != null) nm.notify(1, buildNotification(text));
    }

    @Override public void onDestroy() {
        stopEverything();
        super.onDestroy();
    }
}
