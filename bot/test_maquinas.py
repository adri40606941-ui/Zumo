import os
import shutil
import stat
import tempfile
import unittest

import maquinas as mq
import maquinas_bot as mb


class Tienda(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.viejos = (mq.DIR, mq.ARCHIVO, mq.CLAVE)
        mq.DIR = self.d
        mq.ARCHIVO = os.path.join(self.d, "maquinas.enc")
        mq.CLAVE = os.path.join(self.d, "maquinas.key")
        self.addCleanup(self._restaurar)

    def _restaurar(self):
        mq.DIR, mq.ARCHIVO, mq.CLAVE = self.viejos


class Cifrado(Tienda):
    def test_ida_y_vuelta_y_no_queda_en_claro(self):
        m = mq.agregar("app02", "203.0.113.10", 22, "root", "Secreta123", "SHA256:abc")
        self.assertEqual(mq.buscar(m["id"])["clave"], "Secreta123")
        with open(mq.ARCHIVO, "rb") as f:
            self.assertNotIn(b"Secreta123", f.read())
        self.assertNotIn(b"203.0.113.10", open(mq.ARCHIVO, "rb").read())

    def test_permisos_de_la_clave(self):
        mq.agregar("app02", "203.0.113.10", 22, "root", "x", "")
        modo = stat.S_IMODE(os.stat(mq.CLAVE).st_mode)
        self.assertEqual(modo, 0o600)

    def test_quitar_borra_la_maquina(self):
        a = mq.agregar("a", "10.0.0.1", 22, "root", "x", "")
        b = mq.agregar("b", "10.0.0.2", 22, "root", "y", "")
        mq.quitar(a["id"])
        self.assertEqual([m["id"] for m in mq.cargar()], [b["id"]])

    def test_sin_archivo_la_lista_esta_vacia(self):
        self.assertEqual(mq.cargar(), [])


class Recursos(unittest.TestCase):
    def test_parsea_todo(self):
        salida = "\n".join([
            "CPU 1000 500 1600 550",      # total +600, ocioso +50 -> 91.7 %
            "CORES 2",
            "RAM 4000 1024 2900",
            "DISCO 51200 18432 32768",
            "CARGA 0.21 0.18 0.15",
            "ACTIVO up 5 days, 3 hours",
            "SESIONES 14",
        ])
        r = mq.parsear_recursos(salida)
        self.assertAlmostEqual(r["cpu"], 91.7)
        self.assertEqual(r["cores"], 2)
        self.assertEqual(r["ram"], (1024, 4000))
        self.assertEqual(r["disco"], (18, 50))
        self.assertEqual(r["carga"], "0.21 · 0.18 · 0.15")
        self.assertEqual(r["activo"], "up 5 days, 3 hours")
        self.assertEqual(r["sesiones"], 14)

    def test_salida_rota_no_rompe(self):
        r = mq.parsear_recursos("basura\nCPU x y\nRAM 1\n")
        self.assertIsNone(r["cpu"])
        self.assertIsNone(r["ram"])


class Protocolos(Tienda):
    def setUp(self):
        super().setUp()
        self.m = mq.agregar("app02", "203.0.113.10", 22, "root", "x", "")
        self.llamadas = []
        self.estado = ""
        self.viejo = mq.correr

        def falso(m, cmd, timeout=20):
            self.llamadas.append(cmd)
            return self.estado if "is-active" in cmd else "FIN=0\n"
        mq.correr = falso
        self.addCleanup(setattr, mq, "correr", self.viejo)

    def test_estado_por_grupo(self):
        self.estado = "\n".join([
            "pdirect-80 active", "udpgw-7300 inactive", "hcr-server no",
            "bhttp-server active", "bhttp-shim active", "bhttp-v2 no",
        ])
        got = dict(mq.estado_protocolos(self.m))
        self.assertEqual(got["PDirect (WebSocket 80)"], "activo")
        self.assertEqual(got["BadVPN (UDPGW 7300)"], "inactivo")
        self.assertEqual(got["HCR Server"], "no instalado")
        self.assertEqual(got["BHTTP"], "activo")
        self.assertEqual(got["BHTTP v2"], "no instalado")

    def test_parcial_si_falta_una_unidad(self):
        self.estado = "bhttp-server active\nbhttp-shim inactive\nbhttp-v2 no\n"
        got = dict(mq.estado_protocolos(self.m))
        self.assertEqual(got["BHTTP"], "parcial")

    def test_prender_solo_lo_instalado(self):
        self.estado = "bhttp-server inactive\nbhttp-shim no\n"
        mq.cambiar_protocolo(self.m, "BHTTP", encender=True)
        cmd = self.llamadas[-1]
        self.assertIn("systemctl enable --now bhttp-server", cmd)
        self.assertNotIn("bhttp-shim", cmd.split("FIN")[0].split("enable --now")[1])

    def test_apagar_en_orden_inverso(self):
        self.estado = "bhttp-server active\nbhttp-shim active\n"
        mq.cambiar_protocolo(self.m, "BHTTP", encender=False)
        self.assertIn("systemctl disable --now bhttp-shim bhttp-server", self.llamadas[-1])

    def test_no_instalado_no_se_puede_prender(self):
        self.estado = "pdirect-80 no\n"
        with self.assertRaises(mq.ErrorMaquina):
            mq.cambiar_protocolo(self.m, "PDirect (WebSocket 80)", encender=True)


class Flujo(Tienda):
    """Agregar una VPS: la contraseña se borra del chat y no aparece en ningún mensaje."""

    class Tg:
        def __init__(self):
            self.enviados, self.borrados = [], []

        def mensaje(self, chat, texto, botones=None, md=False):
            self.enviados.append(texto)

        def borrar(self, chat, mid):
            self.borrados.append(mid)

    class Bot(mb.MaquinasMixin):
        def __init__(self):
            self.tg = Flujo.Tg()
            self.estado = {}

        def pedir(self, chat, texto, paso, **datos):
            self.estado[chat] = {"paso": paso, **datos}

        def borrar_entrada(self, chat, e):
            if e.get("_mid"):
                self.tg.borrar(chat, e["_mid"])

        CANC_MAQ = [[("✖ Cancelar", "maq")]]

    def setUp(self):
        super().setUp()
        self.bot = Flujo.Bot()
        self.viejo_probar, self.viejo_agregar = mq.probar, mq.agregar
        mq.probar = lambda host, puerto, usuario, clave: "SHA256:huella"
        mq.agregar = lambda *a: {"id": "x1", "nombre": a[0]}
        self.addCleanup(setattr, mq, "probar", self.viejo_probar)
        self.addCleanup(setattr, mq, "agregar", self.viejo_agregar)

    def test_contrasena_se_borra_y_no_se_muestra(self):
        b, chat = self.bot, 1
        b.pedir(chat, "x", "m_nombre")
        for paso, valor in [("m_nombre", "app02"), ("m_host", "203.0.113.10"),
                            ("m_puerto", "22"), ("m_usuario", "root")]:
            b.estado[chat]["_mid"] = 100
            self.assertTrue(b.texto_maquinas(chat, b.estado[chat], b.estado[chat]["paso"], valor))
        b.estado[chat]["_mid"] = 555
        self.assertTrue(b.texto_maquinas(chat, b.estado[chat], "m_clave", "Secreta123"))
        self.assertIn(555, b.tg.borrados)
        self.assertFalse(any("Secreta123" in t for t in b.tg.enviados))
        self.assertNotIn(chat, b.estado)

    def test_rechaza_host_invalido(self):
        b = self.bot
        b.pedir(1, "x", "m_host")
        self.assertTrue(b.texto_maquinas(1, b.estado[1], "m_host", "no es un host!!"))
        self.assertIn("m_host", b.estado[1]["paso"])


if __name__ == "__main__":
    unittest.main()
