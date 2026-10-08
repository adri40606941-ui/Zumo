"""El servidor web que reparte la lista de servidores desde la VPS, y cómo la usa el bot."""
import http.client
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import publico  # noqa: E402
import zs  # noqa: E402
from test_bot import FalsaTelegram, cargar_bot  # noqa: E402

TEXTO = "[APP 02]\nhost = h.com\nport = 80\npayload = GET /\n"
SERVIDOR = {"name": "APP 02", "host": "h.com", "port": 80, "payload": "GET /", "tls": False, "sni": ""}


def puerto_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Servidor(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.dir = t.name
        self.datos = zs.cifrar_lista(TEXTO)
        publico.publicar(self.datos, self.dir)
        self.puerto = puerto_libre()
        self.s = publico.servir(self.puerto, self.dir, host="127.0.0.1")
        self.assertIsNotNone(self.s)
        self.addCleanup(lambda: (self.s.shutdown(), self.s.server_close()))

    def pedir(self, metodo, ruta):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=5)
        c.request(metodo, ruta)
        r = c.getresponse()
        cuerpo = r.read()
        c.close()
        return r.status, cuerpo, r

    def test_sirve_la_lista_tal_cual(self):
        st, cuerpo, r = self.pedir("GET", "/servidores.bin")
        self.assertEqual((st, cuerpo), (200, self.datos))
        self.assertEqual(zs.descifrar_lista(cuerpo), TEXTO)
        self.assertEqual(r.getheader("Cache-Control"), "no-cache")

    def test_head_no_manda_cuerpo(self):
        st, cuerpo, r = self.pedir("HEAD", "/servidores.bin")
        self.assertEqual((st, cuerpo), (200, b""))
        self.assertEqual(int(r.getheader("Content-Length")), len(self.datos))

    def test_ignora_el_query(self):
        self.assertEqual(self.pedir("GET", "/servidores.bin?x=1")[0], 200)

    def test_solo_sirve_lo_permitido(self):
        open(os.path.join(self.dir, "secreto.txt"), "w").write("no")
        for ruta in ("/", "/secreto.txt", "/../etc/passwd", "/%2e%2e/etc/passwd", "/servidores.bin/", "/.servidores.tmp"):
            self.assertEqual(self.pedir("GET", ruta)[0], 404, ruta)

    def test_no_acepta_escrituras(self):
        for m in ("POST", "PUT", "DELETE"):
            self.assertEqual(self.pedir(m, "/servidores.bin")[0], 405, m)

    def test_sin_lista_da_404(self):
        os.remove(os.path.join(self.dir, "servidores.bin"))
        self.assertEqual(self.pedir("GET", "/servidores.bin")[0], 404)

    def test_publicar_reemplaza_entero(self):
        nuevo = zs.cifrar_lista(TEXTO + "\n[OTRO]\nhost = x.com\nport = 80\npayload = GET /\n")
        publico.publicar(nuevo, self.dir)
        self.assertEqual(self.pedir("GET", "/servidores.bin")[1], nuevo)
        self.assertEqual(sorted(os.listdir(self.dir)), ["servidores.bin"], "no deja temporales")

    def test_puerto_ocupado_no_tumba_nada(self):
        self.assertIsNone(publico.servir(self.puerto, self.dir, host="127.0.0.1"))


class Https(unittest.TestCase):
    def test_sirve_por_tls_con_certificado_propio(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        cert, key = os.path.join(t.name, "c.pem"), os.path.join(t.name, "k.pem")
        r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=bot.test",
                            "-keyout", key, "-out", cert], capture_output=True)
        if r.returncode:
            self.skipTest("sin openssl")
        datos = zs.cifrar_lista(TEXTO)
        publico.publicar(datos, t.name)
        puerto = puerto_libre()
        s = publico.servir(puerto, t.name, cert, key, host="127.0.0.1")
        self.assertIsNotNone(s)
        self.addCleanup(lambda: (s.shutdown(), s.server_close()))
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        c = http.client.HTTPSConnection("127.0.0.1", puerto, timeout=5, context=ctx)
        c.request("GET", "/servidores.bin")
        resp = c.getresponse()
        self.assertEqual((resp.status, resp.read()), (200, datos))


class BotPublica(unittest.TestCase):
    def armar(self, env):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(f"{tmp.name}/bot.env", "w") as f:
            f.write(env)
        self.bot = cargar_bot(tmp.name)
        self.pub = os.path.join(tmp.name, "publico")
        self.bot.publico.DIR = self.pub
        self.tg = FalsaTelegram()
        self.b = self.bot.Bot(self.tg, {7}, None)

    def test_sin_dominio_todo_sigue_en_github(self):
        self.armar("")
        self.assertEqual(self.b.dominio_lista(), "")
        self.assertFalse(self.b.publicar_en_vps([SERVIDOR]))
        self.assertFalse(os.path.exists(self.pub))
        self.assertEqual(self.b.url_actualizar_app(), "https://raw.githubusercontent.com/adri40606941-ui/Zumo/apk/servidores.bin")

    def test_con_dominio_la_app_prueba_primero_la_vps(self):
        self.armar("ZUMO_DOMINIO=Bot.ZumoServer.com\nGITHUB_REPO=o/r\n")
        self.assertEqual(self.b.dominio_lista(), "bot.zumoserver.com")
        self.assertEqual(self.b.url_actualizar_app(),
                         "https://bot.zumoserver.com/servidores.bin|https://raw.githubusercontent.com/o/r/apk/servidores.bin")

    def test_dominio_raro_se_ignora(self):
        self.armar("ZUMO_DOMINIO=x.com/../;rm -rf\n")
        self.assertEqual(self.b.dominio_lista(), "")

    def test_la_url_manual_manda(self):
        self.armar("ZUMO_DOMINIO=bot.zumoserver.com\nZUMO_URL_ACTUALIZAR=https://otro.com/s.bin\n")
        self.assertEqual(self.b.url_actualizar_app(), "https://otro.com/s.bin")

    def test_publicar_deja_la_lista_cifrada_al_instante(self):
        self.armar("ZUMO_DOMINIO=bot.zumoserver.com\n")
        self.bot.guardar_app([SERVIDOR])
        self.b.publicar_servidores(1)
        with open(os.path.join(self.pub, "servidores.bin"), "rb") as f:
            self.assertIn("[APP 02]", zs.descifrar_lista(f.read()))
        self.assertIn("bot.zumoserver.com", self.tg.mensajes[0])


if __name__ == "__main__":
    unittest.main()
