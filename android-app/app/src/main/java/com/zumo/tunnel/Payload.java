package com.zumo.tunnel;

/**
 * Renders a user-supplied payload template into the raw bytes that are written
 * to the socket before the SSH handshake begins.
 *
 * Supported tokens (case-insensitive):
 *   [crlf]        -> \r\n
 *   [cr]          -> \r
 *   [lf]          -> \n
 *   [host]        -> SSH host
 *   [port]        -> SSH port
 *   [host_port]   -> host:port
 *   [ua]          -> a generic User-Agent string
 *   [protocol]    -> "HTTP/1.1"
 *   [crlf][crlf]  -> written literally, so you can terminate the header block
 *
 * This is deliberately a plain text/transport templating step. It does not
 * generate, exploit, or probe anything on its own; it only formats the text
 * the user typed into the payload box.
 */
public final class Payload {

    private static final String DEFAULT_UA =
            "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
          + "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36";

    private Payload() { }

    public static byte[] render(String template, String host, int port) {
        if (template == null) template = "";
        String hostPort = host + ":" + port;

        String out = template
                .replace("[crlf]", "\r\n")
                .replace("[CRLF]", "\r\n")
                .replace("[cr]",   "\r")
                .replace("[CR]",   "\r")
                .replace("[lf]",   "\n")
                .replace("[LF]",   "\n")
                .replace("[host_port]", hostPort)
                .replace("[HOST_PORT]", hostPort)
                .replace("[host]", host)
                .replace("[HOST]", host)
                .replace("[port]", String.valueOf(port))
                .replace("[PORT]", String.valueOf(port))
                .replace("[protocol]", "HTTP/1.1")
                .replace("[PROTOCOL]", "HTTP/1.1")
                .replace("[ua]", DEFAULT_UA)
                .replace("[UA]", DEFAULT_UA);

        // If the user typed a template with no explicit CRLFs, keep newlines as-is.
        return out.getBytes(java.nio.charset.StandardCharsets.ISO_8859_1);
    }

    /** True if the template looks like it expects an HTTP-style response to be drained. */
    public static boolean expectsHttpResponse(String template) {
        if (template == null) return false;
        String t = template.toUpperCase();
        return t.contains("HTTP/") || t.startsWith("GET") || t.startsWith("CONNECT")
                || t.startsWith("POST") || t.contains("[PROTOCOL]");
    }
}
