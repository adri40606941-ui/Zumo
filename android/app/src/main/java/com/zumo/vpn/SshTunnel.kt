package com.zumo.vpn

import com.jcraft.jsch.ChannelDirectTCPIP
import com.jcraft.jsch.JSch
import com.jcraft.jsch.Session
import com.jcraft.jsch.SocketFactory
import com.jcraft.jsch.UIKeyboardInteractive
import com.jcraft.jsch.UserInfo
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
        SshDebug.limpiar()
        CrudoDebug.limpiar()
        JSch.setLogger(object : com.jcraft.jsch.Logger {
            override fun isEnabled(level: Int) = true
            override fun log(level: Int, message: String?) { SshDebug.add(message ?: "") }
        })
        val tr = Transport.connect(cfg, etapa, proteger)
        try {
            etapa("Iniciando sesión")
            val s = JSch().getSession(user, cfg.host, cfg.sshPort)
            s.setPassword(pass)
            s.setConfig("StrictHostKeyChecking", "no")
            // keyboard-interactive primero: en paneles tipo SSHPlus el método "password" se rechaza
            // directo (solo aceptan el desafío PAM), y cada intento rechazado cuenta para el límite
            // "MaxAuthTries" del servidor. Si probamos "password" primero, gastamos un intento de más
            // y el servidor corta con "Too many authentication failures" antes de llegar al método que sí sirve.
            s.setConfig("PreferredAuthentications", "keyboard-interactive,password")
            // Algunos servidores ofrecen cifrados/MAC modernos que JSch no puede instanciar en
            // Android (la clase correspondiente no existe en el proveedor de cifrado del sistema).
            // En vez de descartar ese algoritmo con elegancia, JSch tira un NullPointerException
            // ("Cipher.isCBC() on a null object reference") durante la negociación, cortando la
            // conexión antes de llegar siquiera a pedir usuario/clave. Limitamos la lista a
            // algoritmos clásicos que sí están disponibles en Android para evitar ese choque.
            s.setConfig("cipher.s2c", "aes128-ctr,aes192-ctr,aes256-ctr,aes128-cbc,aes192-cbc,aes256-cbc,3des-cbc")
            s.setConfig("cipher.c2s", "aes128-ctr,aes192-ctr,aes256-ctr,aes128-cbc,aes192-cbc,aes256-cbc,3des-cbc")
            s.setConfig("mac.s2c", "hmac-sha2-256,hmac-sha2-512,hmac-sha1")
            s.setConfig("mac.c2s", "hmac-sha2-256,hmac-sha2-512,hmac-sha1")
            // Muchos paneles (SSHPlus y similares) validan límite de conexiones/vencimiento con PAM
            // usando keyboard-interactive en vez de "password" puro. Sin esto, JSch no responde el
            // desafío y el login falla aunque el usuario/clave sean correctos (HTTP Custom sí lo hace).
            s.setUserInfo(object : UserInfo, UIKeyboardInteractive {
                override fun getPassphrase(): String? = null
                override fun getPassword(): String = pass
                override fun promptPassphrase(message: String?): Boolean = false
                override fun promptPassword(message: String?): Boolean = true
                override fun promptYesNo(message: String?): Boolean = true
                override fun showMessage(message: String?) {}
                override fun promptKeyboardInteractive(
                    destination: String?, name: String?, instruction: String?,
                    prompt: Array<out String>?, echo: BooleanArray?,
                ): Array<String> = Array(prompt?.size ?: 0) { pass }
            })
            s.setSocketFactory(object : SocketFactory {
                override fun createSocket(host: String?, port: Int): Socket = tr.socket
                override fun getInputStream(socket: Socket?): InputStream = tr.input
                override fun getOutputStream(socket: Socket?): OutputStream = Transport.primerCorte(tr.output)
            })
            s.serverAliveInterval = 20000
            s.serverAliveCountMax = 3
            s.connect(25000)
            session = s
        } catch (e: Exception) {
            try { tr.socket.close() } catch (_: Exception) {}
            val msg = e.message ?: e.javaClass.simpleName
            throw Exception(msg, e)
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
