package com.zumo.vpn

import com.jcraft.jsch.ChannelDirectTCPIP
import com.jcraft.jsch.JSch
import com.jcraft.jsch.Session
import com.jcraft.jsch.SocketFactory
import java.io.InputStream
import java.io.OutputStream
import java.net.Socket

/** Una sesión SSH autenticada sobre el transporte (payload / WebSocket). */
class SshTunnel(
    private val cfg: Config, private val user: String, private val pass: String,
    private val etapa: (String) -> Unit = {}, private val proteger: (Socket) -> Unit = {},
) {
    @Volatile var session: Session? = null
        private set

    fun connect() {
        close()
        val tr = Transport.connect(cfg, etapa, proteger)
        try {
            etapa("Iniciando sesión SSH")
            val s = JSch().getSession(user, cfg.host, cfg.sshPort)
            s.setPassword(pass)
            s.setConfig("StrictHostKeyChecking", "no")
            s.setConfig("PreferredAuthentications", "password,keyboard-interactive")
            s.setSocketFactory(object : SocketFactory {
                override fun createSocket(host: String?, port: Int): Socket = tr.socket
                override fun getInputStream(socket: Socket?): InputStream = tr.input
                override fun getOutputStream(socket: Socket?): OutputStream = tr.socket.getOutputStream()
            })
            s.serverAliveInterval = 20000
            s.serverAliveCountMax = 3
            s.connect(25000)
            session = s
        } catch (e: Exception) {
            try { tr.socket.close() } catch (_: Exception) {}
            throw e
        }
    }

    val conectado: Boolean get() = session?.isConnected == true

    fun abrirCanal(host: String, port: Int): ChannelDirectTCPIP? {
        val s = session ?: return null
        if (!s.isConnected) return null
        val ch = s.openChannel("direct-tcpip") as ChannelDirectTCPIP
        ch.setHost(host)
        ch.setPort(port)
        ch.setOrgIPAddress("127.0.0.1")
        ch.setOrgPort(0)
        return ch
    }

    fun close() {
        try { session?.disconnect() } catch (_: Exception) {}
        session = null
    }
}
