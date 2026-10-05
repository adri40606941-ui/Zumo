package hev.htproxy

/** Puente JNI hacia hev-socks5-tunnel (convierte el tráfico del TUN en conexiones SOCKS5). */
class TProxyService {
    external fun TProxyStartService(configPath: String, fd: Int): Boolean
    external fun TProxyStopService(): Boolean
    external fun TProxyIsRunning(): Boolean
    external fun TProxyGetStats(): LongArray

    companion object {
        init {
            System.loadLibrary("hev-socks5-tunnel")
        }
    }
}
