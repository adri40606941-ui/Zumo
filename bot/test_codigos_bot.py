"""🔑 Instalar en VPS nueva: códigos de un solo uso (accesos.py) y sus pantallas en el bot."""
import json
import os
import stat
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import accesos  # noqa: E402
from test_bot import FalsaTelegram, cargar_bot  # noqa: E402


class Codigos(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.archivo = os.path.join(t.name, "a.json")
        self.ahora = [5000.0]
        self.ac = accesos.Accesos(self.archivo, reloj=lambda: self.ahora[0])

    def test_formato_del_codigo(self):
        c = self.ac.crear_codigo("panel")
        self.assertRegex(c, r"^[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}$")

    def test_se_acepta_en_minusculas_y_con_espacios(self):
        c = self.ac.crear_codigo("panel")
        pase, tipo = self.ac.canjear("  " + c.lower().replace("-", " ") + "\n")
        self.assertEqual((len(pase), tipo), (32, "panel"))

    def test_canjea_una_sola_vez_y_da_un_pase_distinto_cada_vez(self):
        a, b = self.ac.crear_codigo("panel"), self.ac.crear_codigo("bot")
        pa, _ = self.ac.canjear(a)
        self.assertEqual(self.ac.canjear(a), (None, None))
        pb, tb = self.ac.canjear(b)
        self.assertNotEqual(pa, pb)
        self.assertEqual(tb, "bot")
        self.assertTrue(self.ac.valido(pa) and self.ac.valido(pb))

    def test_vence(self):
        c = self.ac.crear_codigo("panel")
        self.assertEqual(self.ac.codigos_vigentes(), 1)
        self.ahora[0] += accesos.VIDA_CODIGO + 1
        self.assertEqual(self.ac.codigos_vigentes(), 0)
        self.assertEqual(self.ac.canjear(c), (None, None))

    def test_en_disco_solo_hay_hashes_y_el_archivo_es_privado(self):
        c = self.ac.crear_codigo("panel")
        pase, _ = self.ac.canjear(c)
        c2 = self.ac.crear_codigo("bot")
        txt = open(self.archivo).read()
        for secreto in (c.replace("-", ""), c2.replace("-", ""), pase):
            self.assertNotIn(secreto, txt)
        self.assertEqual(stat.S_IMODE(os.stat(self.archivo).st_mode), 0o600)

    def test_pases_invalidos(self):
        for p in ("", None, "x" * 32, "0" * 31, "0" * 32):
            self.assertFalse(self.ac.valido(p), p)

    def test_revocar(self):
        pase, _ = self.ac.canjear(self.ac.crear_codigo("panel"))
        n = self.ac.listar()[0]["id"]
        self.assertTrue(self.ac.revocar(n))
        self.assertFalse(self.ac.valido(pase))
        self.assertFalse(self.ac.revocar(n))

    def test_tipo_desconocido(self):
        with self.assertRaises(ValueError):
            self.ac.crear_codigo("otra-cosa")

    def test_archivo_roto_no_tumba_nada(self):
        with open(self.archivo, "w") as f:
            f.write("{no es json")
        self.assertEqual(self.ac.listar(), [])
        self.assertIsNotNone(self.ac.canjear(self.ac.crear_codigo("panel"))[0])

    def test_los_ids_no_se_repiten_aunque_se_anule(self):
        p1, _ = self.ac.canjear(self.ac.crear_codigo("panel"))
        self.ac.revocar(self.ac.listar()[0]["id"])
        self.ac.canjear(self.ac.crear_codigo("panel"))
        self.assertEqual(self.ac.listar()[0]["id"], 2)


class Pantallas(unittest.TestCase):
    def armar(self, env):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(f"{tmp.name}/bot.env", "w") as f:
            f.write(env)
        self.bot = cargar_bot(tmp.name)
        self.tg = FalsaTelegram()
        self.b = self.bot.Bot(self.tg, {7}, None)
        self.btn = lambda d: self.b.manejar_cb({"id": "c", "from": {"id": 7}, "data": d,
                                                "message": {"chat": {"id": 1}, "message_id": 5}})

    def test_sin_dominio_explica_que_falta(self):
        self.armar("")
        self.btn("inst")
        self.assertIn("ZUMO_DOMINIO", self.tg.mensajes[-1])
        self.assertNotIn("inst_n:panel", self.tg.datos_botones())
        self.btn("inst_n:panel")
        self.assertEqual(self.b.accesos.codigos_vigentes(), 0, "sin dominio no se crean códigos")

    def test_con_dominio_ofrece_codigos(self):
        self.armar("ZUMO_DOMINIO=bot.zumoserver.com\n")
        self.btn("inst")
        self.assertEqual(self.tg.datos_botones(), ["inst_n:panel", "inst_n:bot", "inst_n:actualizar", "inst_l", "menu"])

    def test_el_codigo_sale_con_el_comando_corto(self):
        self.armar("ZUMO_DOMINIO=bot.zumoserver.com\n")
        self.btn("inst_n:panel")
        msg = self.tg.mensajes[-1]
        self.assertIn("curl -fsSL https://bot.zumoserver.com/i | bash", msg)
        codigo = [t for t in msg.split("`") if len(t) == 9 and t[4] == "-"][0]
        self.assertEqual(self.b.accesos.codigos_vigentes(), 1)
        self.assertIsNotNone(self.b.accesos.canjear(codigo)[0])

    def test_codigo_para_pasar_una_vps_vieja_al_dominio(self):
        self.armar("ZUMO_DOMINIO=bot.zumoserver.com\n")
        self.btn("inst_n:actualizar")
        self.assertIn("ya tiene el panel", self.tg.mensajes[-1])
        codigo = [t for t in self.tg.mensajes[-1].split("`") if len(t) == 9 and t[4] == "-"][0]
        self.assertEqual(self.b.accesos.canjear(codigo)[1], "actualizar")

    def test_tipo_raro_no_crea_nada(self):
        self.armar("ZUMO_DOMINIO=bot.zumoserver.com\n")
        self.btn("inst_n:otro")
        self.assertEqual(self.b.accesos.codigos_vigentes(), 0)

    def test_lista_y_anula(self):
        self.armar("ZUMO_DOMINIO=bot.zumoserver.com\n")
        self.btn("inst_l")
        self.assertIn("Todavía no", self.tg.mensajes[-1])
        pase, _ = self.b.accesos.canjear(self.b.accesos.crear_codigo("panel"), "1.2.3.4")
        self.btn("inst_l")
        self.assertIn("#1", self.tg.mensajes[-1])
        self.assertIn("1.2.3.4", self.tg.mensajes[-1])
        self.assertIn("inst_r:1", self.tg.datos_botones())
        self.btn("inst_r:1")
        self.assertIn("inst_rs:1", self.tg.datos_botones())
        self.assertTrue(self.b.accesos.valido(pase), "pedir confirmación no anula todavía")
        self.btn("inst_rs:1")
        self.assertFalse(self.b.accesos.valido(pase))
        self.assertIn("anulado", self.tg.mensajes[-1])

    def test_anular_algo_que_no_existe(self):
        self.armar("ZUMO_DOMINIO=bot.zumoserver.com\n")
        self.btn("inst_rs:9")
        self.assertIn("ya no estaba", self.tg.mensajes[-1])
        self.btn("inst_rs:abc")
        self.btn("inst_r:abc")

    def test_solo_los_admins(self):
        self.armar("ZUMO_DOMINIO=bot.zumoserver.com\n")
        self.b.manejar_cb({"id": "c", "from": {"id": 99}, "data": "inst_n:panel",
                           "message": {"chat": {"id": 1}, "message_id": 5}})
        self.assertEqual(self.b.accesos.codigos_vigentes(), 0)


if __name__ == "__main__":
    unittest.main()
