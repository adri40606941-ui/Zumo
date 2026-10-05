package com.zumo.vpn

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.SystemClock

/**
 * Vuelve a encender la VPN si el sistema mató el proceso mientras el cliente la quería encendida.
 * Se re-arma solo cada vez que se dispara, así sigue vigilando mientras la VPN deba estar prendida.
 */
object Watchdog {
    private const val CADA_MS = 30_000L   // cada 30s: en HiOS (Tecno/Infinix/itel) el sistema puede matar el proceso entero, no solo el servicio, así que cuanto más seguido se revise, menos tiempo queda desconectado

    private fun pendiente(ctx: Context): PendingIntent {
        val i = Intent(ctx, WatchdogReceiver::class.java)
        return PendingIntent.getBroadcast(ctx, 0, i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    fun programar(ctx: Context) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val t = SystemClock.elapsedRealtime() + CADA_MS
        try {
            am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, t, pendiente(ctx))
        } catch (_: Exception) {
        }
    }

    fun cancelar(ctx: Context) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        try { am.cancel(pendiente(ctx)) } catch (_: Exception) {}
    }
}
