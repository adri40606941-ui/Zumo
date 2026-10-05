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
    def __init__(self): self.mensajes, self.docs, self.botones, self.ediciones = [], [], [], []
    def mensaje(self, chat, texto, botones=None, md=False):
        self.mensajes.append(texto); self.botones.append(botones); return len(self.mensajes)
    def editar(self, chat, mid, texto, botones=None):
        self.ediciones.append(texto); self.mensajes.append(texto); self.botones.append(botones)
    def responder_cb(self, *a, **k): pass
    def documento(self, chat, nombre, datos, leyenda=""): self.docs.append((nombre, datos, leyenda))

    def datos_botones(self):
        return [d for fila in (self.botones[-1] or []) for _, d in fila]


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
        """El mismo vector lo descifra ZsTest.kt: si cambia el formato, hay que tocar los dos."""
        blob = zs.cifrar(VECTOR_PERFIL, VECTOR_SECRETO, VECTOR_IV)
        ruta = os.path.join(AQUI, "..", "android", "app", "src", "test", "resources", "vector.zs.hex")
        with open(ruta) as f:
            self.assertEqual(f.read().strip(), blob.hex())

    def armar(self, tmp):
        bot = cargar_bot(tmp)
        tg = FalsaTelegram()
        b = bot.Bot(tg, {7}, "sec")
        txt = lambda t: b.manejar({"chat": {"id": 1}, "from": {"id": 7}, "text": t})
        btn = lambda d: b.manejar_cb({"id": "c", "from": {"id": 7}, "data": d, "message": {"chat": {"id": 1}, "message_id": 5}})
        return bot, tg, b, txt, btn

    def test_menu_por_botones_y_exportar(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            open(f"{tmp}/usuarios.db", "w").write("cliente1:2:2026-11-05\n")
            open(f"{tmp}/claves.db", "w").write("cliente1:Clave1\n")
            # un extraño no entra
            b.manejar({"chat": {"id": 1}, "from": {"id": 99}, "text": "hola"})
            self.assertIn("No autorizado", tg.mensajes[-1])
            # cualquier texto abre el menú con botones
            txt("hola")
            self.assertEqual(tg.datos_botones(), ["crear", "lista:0", "srv", "id"])
            # sin servidor no se puede crear ni exportar
            btn("crear")
            self.assertIn("Primero definí el servidor", tg.mensajes[-1])
            btn("x:cliente1")
            self.assertIn("Falta el servidor", tg.mensajes[-1])
            # servidor por botones + texto
            btn("s:host"); txt("vps.ejemplo.com:8080")
            self.assertIn("vps.ejemplo.com", tg.mensajes[-1]); self.assertIn("8080", tg.mensajes[-1])
            btn("s:name"); txt("Mi VPN")
            btn("s:payload"); txt("GET / HTTP/1.1[crlf]Host: x.net[crlf][crlf]")
            btn("s:tls"); self.assertIn("TLS: sí", tg.mensajes[-1])
            btn("s:tls"); self.assertIn("TLS: no", tg.mensajes[-1])
            # lista y ficha del usuario
            btn("lista:0"); self.assertIn("u:cliente1", tg.datos_botones())
            btn("u:cliente1"); self.assertIn("x:cliente1", tg.datos_botones())
            btn("x:cliente1")
            nombre, datos, _ = tg.docs[-1]
            self.assertEqual(nombre, "Mi_VPN.zs")
            p = zs.descifrar(datos, "sec")
            self.assertEqual((p["user"], p["pass"], p["exp"]), ("cliente1", "Clave1", "2026-11-05"))
            self.assertEqual((p["cfg"]["host"], p["cfg"]["sshPort"]), ("vps.ejemplo.com", 8080))
            self.assertTrue(p["cfg"]["payload"].startswith("GET / HTTP/1.1[crlf]"))
            # límite por botón
            llamadas = []
            bot.bash_lib = lambda *a: llamadas.append(a)
            btn("ln:cliente1:3"); self.assertEqual(llamadas[-1], ("zumo_db_set", "cliente1", "2", "3"))

    def test_crear_usuario_paso_a_paso(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            open(f"{tmp}/usuarios.db", "w").write("")
            json.dump({"host": "h.com", "port": 80, "name": "N"}, open(f"{tmp}/bot.json", "w"))
            creados = []
            def falso_crear(u, clave, dias, lim):
                creados.append((u, clave, dias, lim))
                open(f"{tmp}/usuarios.db", "a").write(f"{u}:{lim}:2030-01-01\n")
                bot.clave_guardar(u, clave)
            bot.crear_usuario = falso_crear
            btn("crear")
            txt("1mal"); self.assertIn("inválido", tg.mensajes[-1])
            txt("pepe")
            txt("con espacio"); self.assertIn("inválida", tg.mensajes[-1])
            txt("Clave9")
            self.assertIn("cd:30", tg.datos_botones())
            btn("cd:otro"); txt("abc"); self.assertIn("Escribí un número", tg.mensajes[-1])
            txt("45")
            self.assertIn("cl:2", tg.datos_botones())
            btn("cl:2")
            self.assertEqual(creados, [("pepe", "Clave9", 45, 2)])
            self.assertEqual(tg.docs[-1][0], "N.zs")
            self.assertEqual(zs.descifrar(tg.docs[-1][1], "sec")["user"], "pepe")
            # cancelar limpia el estado
            btn("crear"); btn("menu"); self.assertNotIn(1, b.estado)

    def test_validaciones(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = cargar_bot(tmp)
            self.assertIn("inválido", bot.crear_usuario("1abc", "x", 5, 1))
            self.assertIn("inválida", bot.crear_usuario("abc", "con espacio", 5, 1))
            self.assertEqual(bot.fecha_cuenta("2026-12-31"), "2027-01-01")


if __name__ == "__main__":
    unittest.main()
