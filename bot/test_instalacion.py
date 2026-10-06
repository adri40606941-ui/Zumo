#!/usr/bin/env python3
"""Pruebas de los códigos de instalación de un solo uso (servidor real en 127.0.0.1, sin root)."""
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import instalacion  # noqa: E402


class Pruebas(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        d = self.t.name
        os.makedirs(f"{d}/www/sec123")
        with open(f"{d}/www/sec123/install.sh", "w") as f:
            f.write("#!/bin/bash\necho instalador\n")
        with open(f"{d}/centro.env", "w") as f:
            f.write("DOMINIO=d.example\nSECRETO=sec123\n")
        with open(f"{d}/base.url", "w") as f:
            f.write("https://d.example/sec123\n")
        instalacion.CENTRO_ENV, instalacion.BASE_URL, instalacion.PUBLICADO = f"{d}/centro.env", f"{d}/base.url", f"{d}/www"
        self.hora = [1000.0]
        self.cod = instalacion.Codigos(f"{d}/codigos.json", ahora=lambda: self.hora[0])
        self.usos = []
        self.srv = instalacion.hacer_servidor(self.cod, self.usos.append, puerto=0)
        self.puerto = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); self.t.cleanup()

    def pedir(self, c, ruta=None):
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:{self.puerto}" + (ruta or f"/i/{c}/install.sh"), timeout=5)
            return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, ""

    def test_sirve_una_sola_vez(self):
        c = self.cod.crear()
        self.assertEqual(self.pedir(c), (200, "#!/bin/bash\necho instalador\n"))
        self.assertEqual(self.pedir(c)[0], 404)
        self.assertEqual(len(self.usos), 1)

    def test_vence_a_los_15_minutos(self):
        c = self.cod.crear()
        self.hora[0] += 15 * 60 + 1
        self.assertEqual(self.pedir(c)[0], 404)
        self.assertEqual(self.usos, [])

    def test_codigo_falso_o_ruta_rara(self):
        self.cod.crear()
        for ruta in ("/i/" + "0" * 20 + "/install.sh", "/i/zz/install.sh", "/", "/sec123/install.sh", "/i/../x"):
            self.assertEqual(self.pedir(None, ruta)[0], 404, ruta)

    def test_cada_codigo_es_distinto_y_el_archivo_es_privado(self):
        a, b = self.cod.crear(), self.cod.crear()
        self.assertNotEqual(a, b)
        self.assertEqual(len(a), 20)
        self.assertEqual(oct(os.stat(self.cod.ruta).st_mode & 0o777), "0o600")
        self.assertEqual(self.pedir(a)[0], 200)
        self.assertEqual(self.pedir(b)[0], 200)      # gastar uno no gasta el otro

    def test_el_comando_lleva_el_dominio_sin_el_secreto_del_centro(self):
        c = self.cod.crear()
        self.assertEqual(instalacion.comando(c), f"bash <(curl -fsSL https://d.example/i/{c}/install.sh)")


if __name__ == "__main__":
    unittest.main()
