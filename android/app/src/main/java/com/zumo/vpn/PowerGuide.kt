package com.zumo.vpn

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings

/** Ayudas para que el sistema (Tecno HiOS, Xiaomi, etc.) no cierre la VPN en segundo plano. */
object PowerGuide {

    fun sinOptimizar(ctx: Context): Boolean {
        val pm = ctx.getSystemService(Context.POWER_SERVICE) as PowerManager
        return pm.isIgnoringBatteryOptimizations(ctx.packageName)
    }

    /** Muestra el diálogo del sistema para excluir la app de la optimización de batería. */
    fun pedirExclusion(ctx: Context) {
        try {
            ctx.startActivity(
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                    .setData(Uri.parse("package:${ctx.packageName}")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (_: Exception) {
            abrir(ctx, Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
    }

    /** Pantallas de "autoinicio" / "inicio de apps" de cada fabricante; la primera que exista se abre. */
    private val autoinicio = listOf(
        "com.transsion.phonemaster" to "com.cyin.himgr.autostart.AutoStartActivity",
        "com.transsion.phonemaster" to "com.cyin.himgr.MainActivity",
        "com.itel.autostart" to "com.itel.autostart.AutoStartActivity",
        "com.miui.securitycenter" to "com.miui.permcenter.autostart.AutoStartManagementActivity",
        "com.huawei.systemmanager" to "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity",
        "com.coloros.safecenter" to "com.coloros.safecenter.permission.startup.StartupAppListActivity",
        "com.oppo.safe" to "com.oppo.safe.permission.startup.StartupAppListActivity",
        "com.vivo.permissionmanager" to "com.vivo.permissionmanager.activity.BgStartUpManagerActivity",
    )

    fun abrirAutoinicio(ctx: Context): Boolean {
        for ((pkg, cls) in autoinicio) {
            if (abrir(ctx, Intent().setComponent(ComponentName(pkg, cls)))) return true
        }
        return abrirAjustesApp(ctx)
    }

    fun abrirAjustesApp(ctx: Context): Boolean =
        abrir(ctx, Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).setData(Uri.parse("package:${ctx.packageName}")))

    private fun abrir(ctx: Context, i: Intent): Boolean = try {
        ctx.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); true
    } catch (_: Exception) { false }

    val fabricante: String get() = Build.MANUFACTURER.lowercase()
    val esTecno: Boolean get() = fabricante.let { it.contains("tecno") || it.contains("infinix") || it.contains("itel") || it.contains("transsion") }

    val pasos: String
        get() = """
            Para que la VPN no se corte:

            1. Toca "Quitar límite de batería" y elige Permitir.
            2. Toca "Abrir autoinicio" y activa Zumo VPN (inicio automático y en segundo plano).
            3. En las apps recientes, mantén pulsada la tarjeta de Zumo VPN y toca el candado.
            4. Desactiva el ahorro de energía y el ahorro de datos.
            5. Desactiva "Wi-Fi + datos inteligente" si tu teléfono lo tiene.
        """.trimIndent()
}
