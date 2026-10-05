package com.zumo.vpn

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.VpnService

/** Al encender el teléfono (o actualizar la app), vuelve a conectar si el cliente la tenía encendida. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val p = Prefs(ctx)
        if (!p.autoStart || !p.wanted) return
        if (VpnService.prepare(ctx) != null) return   // falta el permiso de VPN: se pide al abrir la app
        try { ZumoVpnService.iniciar(ctx) } catch (_: Exception) {}
    }
}
