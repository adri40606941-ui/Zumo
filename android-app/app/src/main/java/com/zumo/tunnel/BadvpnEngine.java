package com.zumo.tunnel;

import android.content.Context;
import android.net.LocalSocket;
import android.net.LocalSocketAddress;
import android.os.ParcelFileDescriptor;
import android.util.Log;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileDescriptor;
import java.io.InputStreamReader;
import java.util.ArrayList;
import java.util.List;

/**
 * Motor badvpn-tun2socks.
 *
 * Necesita jniLibs/<abi>/libtun2socks.so compilado COMO EJECUTABLE con soporte
 * de --sock-path (parche de Android). Fuente lista para Android:
 * https://github.com/shadowsocks/badvpn  (trae Android.mk y el parche sock-path).
 *
 * Método (igual que shadowsocks-android):
 *   1. se ejecuta libtun2socks.so desde nativeLibraryDir con la config por argv
 *   2. badvpn crea un socket unix en --sock-path
 *   3. le mandamos el fd del TUN por ese socket (ancillary fd)
 */
public class BadvpnEngine implements VpnEngine {

    private final Context ctx;
    private Process proc;

    public BadvpnEngine(Context ctx) { this.ctx = ctx; }

    @Override public String name() { return "badvpn-tun2socks"; }

    @Override
    public void start(ParcelFileDescriptor tun, String socksAddr, int socksPort, int mtu) throws Exception {
        File bin = new File(ctx.getApplicationInfo().nativeLibraryDir, "libtun2socks.so");
        if (!bin.exists()) {
            throw new Exception("Falta libtun2socks.so (badvpn) en jniLibs/<abi>/");
        }
        File sock = new File(ctx.getFilesDir(), "zumo_tun2socks.sock");
        if (sock.exists()) sock.delete();

        List<String> cmd = new ArrayList<>();
        cmd.add(bin.getAbsolutePath());
        cmd.add("--netif-ipaddr");   cmd.add("10.8.0.1");
        cmd.add("--netif-netmask");  cmd.add("255.255.255.0");
        cmd.add("--socks-server-addr"); cmd.add(socksAddr + ":" + socksPort);
        cmd.add("--tunmtu");         cmd.add(String.valueOf(mtu));
        cmd.add("--sock-path");      cmd.add(sock.getAbsolutePath());
        cmd.add("--loglevel");       cmd.add("3");

        Log.i("Zumo", "badvpn: " + cmd);
        proc = new ProcessBuilder(cmd)
                .directory(ctx.getFilesDir())
                .redirectErrorStream(true)
                .start();
        drainToLogcat(proc);

        sendTunFd(tun.getFileDescriptor(), sock);

        // Bloquea mientras el proceso viva.
        proc.waitFor();
    }

    /** Conecta al socket unix de badvpn y le envía el fd del TUN. */
    private void sendTunFd(FileDescriptor fd, File sock) throws Exception {
        Exception last = null;
        for (int i = 0; i < 40; i++) {
            Thread.sleep(250); // esperar a que badvpn cree el socket
            try {
                LocalSocket ls = new LocalSocket();
                ls.connect(new LocalSocketAddress(sock.getAbsolutePath(),
                        LocalSocketAddress.Namespace.FILESYSTEM));
                ls.setFileDescriptorsForSend(new FileDescriptor[]{ fd });
                ls.getOutputStream().write(42);
                ls.getOutputStream().flush();
                ls.close();
                Log.i("Zumo", "badvpn: fd del TUN enviado.");
                return;
            } catch (Exception e) {
                last = e; // reintentar hasta que el socket exista
            }
        }
        throw new Exception("No se pudo pasar el fd a badvpn", last);
    }

    private void drainToLogcat(final Process p) {
        new Thread(() -> {
            try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
                String line;
                while ((line = r.readLine()) != null) Log.i("Zumo", "badvpn| " + line);
            } catch (Exception ignored) { }
        }, "badvpn-log").start();
    }

    @Override
    public void stop() {
        if (proc != null) {
            try { proc.destroy(); } catch (Throwable ignored) { }
            proc = null;
        }
    }
}
