"""Formato .zs de Zumo VPN (mismo que android/.../Zs.kt).

  "ZS1" + IV(12) + AES-256-GCM(JSON del perfil) + etiqueta(16)
  clave = SHA-256("ZUMO-ZS-1:" + SECRETO)
"""
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIA = b"ZS1"
MAGIA_LISTA = b"ZL1"
SECRETO_POR_DEFECTO = "f14a3636d2aef23c893604756b861ea2"


def _clave(secreto):
    return hashlib.sha256(("ZUMO-ZS-1:" + secreto).encode("utf-8")).digest()


def perfil(host, puerto, payload, nombre, usuario, clave, exp, tls=False, sni=""):
    """Diccionario con el mismo esquema que Perfil.toJson() de la app."""
    return {
        "cfg": {"name": nombre, "host": host, "sshPort": int(puerto),
                "payload": payload, "tls": bool(tls), "sni": sni},
        "user": usuario, "pass": clave, "exp": exp,
    }


def cifrar(p, secreto=SECRETO_POR_DEFECTO, iv=None):
    iv = iv or os.urandom(12)
    datos = json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return MAGIA + iv + AESGCM(_clave(secreto)).encrypt(iv, datos, None)


def descifrar(blob, secreto=SECRETO_POR_DEFECTO):
    if len(blob) < 3 + 12 + 16 or blob[:3] != MAGIA:
        raise ValueError("no es un .zs")
    iv, resto = blob[3:15], blob[15:]
    return json.loads(AESGCM(_clave(secreto)).decrypt(iv, resto, None).decode("utf-8"))


def cifrar_lista(texto, secreto=SECRETO_POR_DEFECTO):
    """Lista de servidores cifrada igual que el APK (assets/servidores.bin, formato ZL1).
    El IV sale del propio texto (SHA-256[:12]) para que la misma lista dé siempre el mismo archivo,
    byte a byte como lo genera Gradle (tarea CifrarServidores). La abre Zs.descifrarLista de la app.
    """
    datos = texto.encode("utf-8")
    iv = hashlib.sha256(datos).digest()[:12]
    return MAGIA_LISTA + iv + AESGCM(_clave(secreto)).encrypt(iv, datos, None)


def descifrar_lista(blob, secreto=SECRETO_POR_DEFECTO):
    if len(blob) < 3 + 12 + 16 or blob[:3] != MAGIA_LISTA:
        raise ValueError("no es una lista ZL1")
    iv, resto = blob[3:15], blob[15:]
    return AESGCM(_clave(secreto)).decrypt(iv, resto, None).decode("utf-8")
