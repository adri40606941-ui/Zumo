package com.zumo.tunnel;

import android.content.Context;
import android.os.ParcelFileDescriptor;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

/**
 * Motor hev-socks5-tunnel.
 *
 * Necesita jniLibs/<abi>/libhev-socks5-tunnel.so
 * (compilá desde https://github.com/heiher/hev-socks5-tunnel ; app de
 * referencia: https://github.com/heiher/sockstun).
 *
 * API nativa:
 *   boolean TProxyStartService(String configPath, int fd)  // bloqueante
 *   boolean TProxyStopService()
 */
public class HevEngine implements VpnEngine {

    private static boolean libLoaded = false;
    static {
        try {
            System.loadLibrary("hev-socks5-tunnel");
            libLoaded = true;
        } catch (Throwable t) {
            Log.e("Zumo", "No se pudo cargar libhev-socks5-tunnel.so", t);
        }
    }

    private static native boolean TProxyStartService(String configPath, int fd);
    private static native boolean TProxyStopService();

    private final Context ctx;

    public HevEngine(Context ctx) { this.ctx = ctx; }

    @Override public String name() { return "hev-socks5-tunnel"; }

    @Override
    public void start(ParcelFileDescriptor tun, String socksAddr, int socksPort, int mtu) throws Exception {
        if (!libLoaded) {
            throw new Exception("Falta libhev-socks5-tunnel.so en jniLibs/<abi>/");
        }
        File cfg = new File(ctx.getCacheDir(), "hev.yaml");
        String yaml =
                "tunnel:\n" +
                "  mtu: " + mtu + "\n" +
                "socks5:\n" +
                "  address: " + socksAddr + "\n" +
                "  port: " + socksPort + "\n" +
                "  udp: 'tcp'\n" +
                "misc:\n" +
                "  log-level: warn\n";
        try (FileOutputStream fo = new FileOutputStream(cfg)) {
            fo.write(yaml.getBytes(StandardCharsets.UTF_8));
        }
        Log.i("Zumo", "hev: arrancando con " + cfg.getAbsolutePath());
        // Bloquea hasta stop().
        TProxyStartService(cfg.getAbsolutePath(), tun.getFd());
    }

    @Override
    public void stop() {
        if (!libLoaded) return;
        try { TProxyStopService(); } catch (Throwable ignored) { }
    }
}
