package com.zumo.vpn

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.VpnService

class WatchdogReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val p = Prefs(ctx)
        if (!p.wanted) return   // el cliente la apagó: no se reprograma más
        if (!ZumoVpnService.corriendo && VpnService.prepare(ctx) == null) {
            try { ZumoVpnService.iniciar(ctx) } catch (_: Exception) {}
        }
        Watchdog.programar(ctx)   // se re-arma para seguir vigilando
    }
}
