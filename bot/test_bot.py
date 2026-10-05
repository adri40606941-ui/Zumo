#!/usr/bin/env python3
"""Pruebas del bot sin Telegram ni root: .zs, armado del archivo y comandos con una Telegram falsa."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import zs  # noqa: E402

VECTOR_IV = bytes(range(12))
VECTOR_SECRETO = "secreto-de-prueba"
VECTOR_PERFIL = zs.perfil("vps.ejemplo.com", 80, "GET / HTTP/1.1[crlf]Host: x.net[crlf][crlf]", "Mi VPN",
                          "cliente1", "Clave1", "2026-11-05")


def cargar_bot(tmp):
    os.environ.update({"ZUMO_BOT_ENV": f"{tmp}/bot.env", "ZUMO_BOT_JSON": f"{tmp}/bot.json",
                       "ZUMO_DB": f"{tmp}/usuarios.db", "ZUMO_CLAVES": f"{tmp}/claves.db"})
    spec = importlib.util.spec_from_file_location("zumo_bot", os.path.join(AQUI, "zumo-bot.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FalsaTelegram:
    def __init__(self): self.mensajes, self.docs = [], []
    def mensaje(self, chat, texto, md=True): self.mensajes.append(texto)
    def documento(self, chat, nombre, datos, leyenda=""): self.docs.append((nombre, datos, leyenda))


class Pruebas(unittest.TestCase):
    def test_ida_y_vuelta(self):
        blob = zs.cifrar(VECTOR_PERFIL, "x")
        self.assertEqual(zs.descifrar(blob, "x"), VECTOR_PERFIL)
        self.assertEqual(blob[:3], b"ZS1")
        self.assertNotIn(b"vps.ejemplo.com", blob)
        with self.assertRaises(Exception):
            zs.descifrar(blob, "otro")
        alterado = bytearray(blob); alterado[-1] ^= 1
        with self.assertRaises(Exception):
            zs.descifrar(bytes(alterado), "x")

    def test_vector_para_kotlin(self):
        """El mismo vector lo descifra TunelTest/ZsTest.kt: si cambia el formato, hay que tocar los dos."""
        blob = zs.cifrar(VECTOR_PERFIL, VECTOR_SECRETO, VECTOR_IV)
        ruta = os.path.join(AQUI, "..", "android", "app", "src", "test", "resources", "vector.zs.hex")
        with open(ruta) as f:
            self.assertEqual(f.read().strip(), blob.hex())

    def test_bot_crear_y_exportar(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = cargar_bot(tmp)
            open(f"{tmp}/usuarios.db", "w").write("cliente1:2:2026-11-05\n")
            open(f"{tmp}/claves.db", "w").write("cliente1:Clave1\n")
            tg = FalsaTelegram()
            b = bot.Bot(tg, {7}, "sec")
            # no admin
            b.manejar({"chat": {"id": 1}, "from": {"id": 99}, "text": "/ver"})
            self.assertIn("No autorizado", tg.mensajes[-1])
            adm = lambda t: b.manejar({"chat": {"id": 1}, "from": {"id": 7}, "text": t})
            adm("/exportar cliente1")
            self.assertIn("Falta el servidor", tg.mensajes[-1])
            adm("/servidor vps.ejemplo.com 80 Mi VPN")
            adm("/payload\nGET / HTTP/1.1[crlf]Host: x.net[crlf][crlf]")
            adm("/exportar cliente1")
            nombre, datos, _ = tg.docs[-1]
            self.assertEqual(nombre, "Mi_VPN.zs")
            p = zs.descifrar(datos, "sec")
            self.assertEqual((p["user"], p["pass"], p["exp"]), ("cliente1", "Clave1", "2026-11-05"))
            self.assertEqual(p["cfg"]["host"], "vps.ejemplo.com")
            self.assertEqual(p["cfg"]["sshPort"], 80)
            self.assertTrue(p["cfg"]["payload"].startswith("GET / HTTP/1.1[crlf]"))
            adm("/exportar nadie")
            self.assertIn("no está", tg.mensajes[-1])
            adm("/usuarios")
            self.assertIn("cliente1", tg.mensajes[-1])
            adm("/crear a b")
            self.assertIn("Uso", tg.mensajes[-1])

    def test_validaciones(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = cargar_bot(tmp)
            self.assertIn("inválido", bot.crear_usuario("1abc", "x", 5, 1))
            self.assertIn("inválida", bot.crear_usuario("abc", "con espacio", 5, 1))
            self.assertEqual(bot.fecha_cuenta("2026-12-31"), "2027-01-01")


if __name__ == "__main__":
    unittest.main()
