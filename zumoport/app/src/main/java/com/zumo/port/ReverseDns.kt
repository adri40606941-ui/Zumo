package com.zumo.port

import java.net.InetAddress

/**
 * DNS inverso (PTR) de una sola IP: el nombre que el dueño de esa IP haya configurado para ella, si hay alguno.
 * No sirve para saber "qué dominios usan esta IP" en una IP compartida (un CDN, un hosting compartido): el PTR
 * es un solo campo que fija quien administra la IP, no una lista de los dominios que apuntan a ella.
 */
object ReverseDns {
    fun resolver(ip: String, lookup: (String) -> String = { InetAddress.getByName(it).canonicalHostName }): String? {
        return try {
            val h = lookup(ip).trim()
            if (h.isBlank() || h == ip || Objetivos.ipv4(h) != null) null else h.trimEnd('.')
        } catch (_: Exception) {
            null
        }
    }
}
