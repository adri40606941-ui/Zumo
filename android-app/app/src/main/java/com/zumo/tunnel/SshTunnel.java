package com.zumo.tunnel;

import com.jcraft.jsch.JSch;
import com.jcraft.jsch.Session;
import com.jcraft.jsch.SocketFactory;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.Properties;

/**
 * Establishes:  TCP -> (write payload) -> SSH handshake -> local SOCKS5 proxy.
 *
 * The local SOCKS5 proxy is created with SSH dynamic port forwarding
 * (JSch setPortForwardingD). Anything pointed at 127.0.0.1:<socksPort> is
 * then forwarded out through the SSH server. The VpnService (TunnelVpnService)
 * is what feeds the device's traffic into that SOCKS proxy.
 */
public class SshTunnel {

    public interface Listener {
        void onLog(String line);
        void onState(boolean connected);
    }

    private final Listener listener;
    private Session session;
    private volatile boolean running = false;

    public SshTunnel(Listener listener) {
        this.listener = listener;
    }

    private void log(String s) {
        if (listener != null) listener.onLog(s);
    }

    /**
     * @param host       SSH host
     * @param port       SSH port
     * @param user       SSH user
     * @param pass       SSH password
     * @param payload    raw payload template (may be empty for a plain SSH connect)
     * @param socksPort  local port to expose the SOCKS5 proxy on
     */
    public synchronized void connect(final String host, final int port,
                                     final String user, final String pass,
                                     final String payload, final int socksPort) throws Exception {
        if (running) return;

        log("Abriendo socket TCP a " + host + ":" + port + " ...");

        // 1) Open the raw socket and write the payload ourselves.
        final Socket raw = new Socket();
        raw.connect(new InetSocketAddress(host, port), 15000);
        raw.setTcpNoDelay(true);

        if (payload != null && !payload.trim().isEmpty()) {
            byte[] bytes = Payload.render(payload, host, port);
            OutputStream os = raw.getOutputStream();
            os.write(bytes);
            os.flush();
            log("Payload enviado (" + bytes.length + " bytes).");

            if (Payload.expectsHttpResponse(payload)) {
                drainHttpResponse(raw.getInputStream());
            }
        } else {
            log("Sin payload: conexión SSH directa.");
        }

        // 2) Hand the already-connected socket to JSch.
        JSch jsch = new JSch();
        session = jsch.getSession(user, host, port);
        session.setPassword(pass);

        session.setSocketFactory(new SocketFactory() {
            @Override public Socket createSocket(String h, int p) { return raw; }
            @Override public InputStream getInputStream(Socket s) throws java.io.IOException { return s.getInputStream(); }
            @Override public OutputStream getOutputStream(Socket s) throws java.io.IOException { return s.getOutputStream(); }
        });

        Properties cfg = new Properties();
        cfg.put("StrictHostKeyChecking", "no");
        cfg.put("PreferredAuthentications", "password,keyboard-interactive");
        session.setConfig(cfg);
        session.setServerAliveInterval(30000);

        log("Negociando SSH ...");
        session.connect(20000);
        log("SSH conectado.");

        // 3) Expose a local SOCKS5 proxy via dynamic forwarding.
        session.setPortForwardingD("127.0.0.1", socksPort);
        log("SOCKS5 local en 127.0.0.1:" + socksPort);

        running = true;
        if (listener != null) listener.onState(true);
    }

    /** Reads and discards the HTTP response headers (up to a blank line). */
    private void drainHttpResponse(InputStream in) {
        try {
            StringBuilder sb = new StringBuilder();
            int c, consecutiveNl = 0;
            while ((c = in.read()) != -1) {
                sb.append((char) c);
                if (c == '\n') {
                    consecutiveNl++;
                    if (consecutiveNl >= 2) break; // blank line -> end of headers
                } else if (c != '\r') {
                    consecutiveNl = 0;
                }
                if (sb.length() > 8192) break;
            }
            String firstLine = sb.toString().split("\r\n", 2)[0];
            log("Respuesta: " + firstLine.trim());
        } catch (Exception e) {
            log("Aviso leyendo respuesta: " + e.getMessage());
        }
    }

    public synchronized void disconnect() {
        running = false;
        if (session != null) {
            try { session.disconnect(); } catch (Exception ignored) { }
            session = null;
        }
        log("Desconectado.");
        if (listener != null) listener.onState(false);
    }

    public boolean isRunning() {
        return running && session != null && session.isConnected();
    }
}
