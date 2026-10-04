package com.zumo.tunnel;

import android.os.ParcelFileDescriptor;

/**
 * Motor que toma la interfaz TUN y relaya todo su tráfico por el SOCKS5 local
 * del túnel SSH. start() es BLOQUEANTE: corre hasta que se llama stop().
 */
public interface VpnEngine {

    String name();

    void start(ParcelFileDescriptor tun, String socksAddr, int socksPort, int mtu) throws Exception;

    void stop();
}
