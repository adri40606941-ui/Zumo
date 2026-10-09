package com.zumo.vpn

/**
 * Explica en pocas palabras qué error dio la conexión y por qué no conectó. Sin dependencias de Android,
 * para probarlo con pruebas normales. Se parte del texto que deja el servicio en [ultimoError].
 */
object Diagnostico {
    enum class Tipo { BIEN, EN_CURSO, TOKEN, SERVIDOR, INTERNET, OTRO }

    class Aviso(val tipo: Tipo, val titulo: String, val porque: String)

    fun de(conectado: Boolean, conectando: Boolean, etapa: String, error: String): Aviso {
        val e = error.trim()
        if (conectado) return Aviso(Tipo.BIEN, "Sin errores", "La conexión está funcionando.")
        if (e.isEmpty()) {
            return if (conectando) Aviso(Tipo.EN_CURSO, "Conectando…", etapa.ifBlank { "Buscando un servidor que responda." })
            else Aviso(Tipo.BIEN, "Sin errores", "Tocá Conectar para empezar.")
        }
        val m = e.lowercase()
        return when {
            m.contains("token expirado") || m.contains("token") && m.contains("activ") ->
                Aviso(Tipo.TOKEN, "Token expirado", "El servidor no reconoce tu token. Pedí que te lo activen o lo renueven.")
            m.startsWith("error de servidor") || m.contains("ningún servidor respondió") ->
                Aviso(Tipo.SERVIDOR, "Error de servidor", "Ningún servidor respondió. Puede estar caído o bloqueado; probá de nuevo en un rato.")
            m.contains("no se encontró el servidor") ->
                Aviso(Tipo.INTERNET, "Sin internet", "No se pudo llegar al servidor. Revisá tus datos o el WiFi.")
            m.contains("tiempo agotado") ->
                Aviso(Tipo.SERVIDOR, "Servidor sin respuesta", "El servidor no contestó a tiempo. Puede estar saturado o tu red lo bloquea.")
            m.contains("puerto cerrado") ->
                Aviso(Tipo.SERVIDOR, "Puerto bloqueado", "El servidor no acepta conexiones en ese puerto (cerrado o bloqueado por tu red).")
            m.contains("cortó la conexión") ->
                Aviso(Tipo.SERVIDOR, "Conexión cortada", "El servidor cerró la conexión. Se vuelve a intentar solo.")
            m.contains("no se pudo iniciar la vpn") ->
                Aviso(Tipo.OTRO, "No arrancó la VPN", "Android no dejó iniciar la VPN. Aceptá el permiso de VPN y probá de nuevo.")
            else -> Aviso(Tipo.OTRO, "No se pudo conectar", e)
        }
    }
}
