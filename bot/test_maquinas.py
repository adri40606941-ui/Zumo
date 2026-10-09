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

    def test_formato_real_de_free_y_df_con_tres_valores(self):
        # el script imprime "RAM total usada disponible" y "DISCO total usado libre"
        r = mq.parsear_recursos("RAM 1982 842 1010\nDISCO 51200000 18432000 32768000\n")
        self.assertEqual(r["ram"], (842, 1982))
        self.assertEqual(r["disco"], (18000, 50000))
        self.assertEqual(r["disco_libre"], 32000)

    def test_disco_libre_si_df_no_lo_trae(self):
        r = mq.parsear_recursos("DISCO 2048 1024\n")
        self.assertEqual(r["disco_libre"], 1)

    def test_velocidad_de_red(self):
        r = mq.parsear_recursos("RED 1000 400 1250000 150400\n")
        self.assertEqual(r["red"], (1249000, 150000))     # bytes por segundo: baja, sube
        self.assertEqual(mq.parsear_recursos("RED 500 500 100 100\n")["red"], (0, 0))   # contador reiniciado: nunca negativo

    def test_el_script_real_y_el_lector_hablan_el_mismo_formato(self):
        import subprocess
        if not os.path.exists("/proc/net/dev"):
            self.skipTest("sin /proc")
        out = subprocess.run(["sh", "-c", mq.COMANDO_RECURSOS], capture_output=True, text=True, timeout=30).stdout
        r = mq.parsear_recursos(out)
        for clave in ("cpu", "ram", "disco", "disco_libre", "red", "carga"):
            self.assertIsNotNone(r[clave], f"{clave} no se pudo leer de: {out!r}")

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


class PantallaViva(Tienda):
    """La pantalla de una máquina se refresca sola cada 5 minutos, mientras siga abierta."""

    class Bot(mb.MaquinasMixin):
        def __init__(self):
            self.pantallas = []

        def mostrar(self, chat, mid, texto, botones):
            self._viva_soltar(chat, mid)        # igual que el Bot real
            self.pantallas.append((chat, mid, texto))

    def setUp(self):
        super().setUp()
        self.m = mq.agregar("app02", "203.0.113.10", 22, "root", "x", "")
        self.viejo = mq.recursos
        mq.recursos = lambda m: mq.parsear_recursos(
            "CPU 1000 500 1600 550\nCORES 2\nRAM 2000 500 1400\nDISCO 51200000 18432000 32768000\n"
            "RED 0 0 2500000 125000\nCARGA 0.1 0.1 0.1\nACTIVO up 1 day\nSESIONES 3\n")
        self.addCleanup(setattr, mq, "recursos", self.viejo)
        self.b = PantallaViva.Bot()

    def test_muestra_cpu_ram_disco_libre_y_velocidad(self):
        self.b.pantalla_maquina(1, 10, self.m["id"])
        txt = self.b.pantallas[-1][2]
        self.assertIn("CPU", txt)
        self.assertIn("RAM", txt)
        self.assertIn("25%", txt)                       # 500 de 2000 MB
        self.assertIn("libre 31.2 GB de 48.8 GB", txt)
        self.assertIn("⬇ 20.0 Mbps", txt)               # 2.5 MB/s
        self.assertIn("⬆ 1.0 Mbps", txt)                # 125 kB/s
        self.assertIn("cada 5 minutos", txt)

    def test_no_refresca_antes_de_los_5_minutos(self):
        self.b.pantalla_maquina(1, 10, self.m["id"])
        t0 = self.b._vivas()[(1, 10)]["t"]
        self.b.refrescar_vivas(ahora=t0 + 299)
        self.assertEqual(len(self.b.pantallas), 1)

    def test_refresca_a_los_5_minutos_y_sigue_viva(self):
        self.b.pantalla_maquina(1, 10, self.m["id"])
        t0 = self.b._vivas()[(1, 10)]["t"]
        self.b.refrescar_vivas(ahora=t0 + 300)
        self.assertEqual(len(self.b.pantallas), 2)
        self.assertIn((1, 10), self.b._vivas())

    def test_si_el_mensaje_pasa_a_otra_pantalla_deja_de_refrescar(self):
        self.b.pantalla_maquina(1, 10, self.m["id"])
        self.b.mostrar(1, 10, "🛡 Zumo VPN", [])        # el usuario tocó otro botón en ese mensaje
        self.assertNotIn((1, 10), self.b._vivas())
        t0 = 10 ** 9
        self.b.refrescar_vivas(ahora=t0)
        self.assertEqual(len(self.b.pantallas), 2)

    def test_deja_de_refrescar_a_las_6_horas(self):
        self.b.pantalla_maquina(1, 10, self.m["id"])
        v = self.b._vivas()[(1, 10)]
        self.b.refrescar_vivas(ahora=v["inicio"] + mb.VIDA_MAX_SEG + 1)
        self.assertNotIn((1, 10), self.b._vivas())
        self.assertEqual(len(self.b.pantallas), 1)

    def test_el_refresco_no_estira_la_vida_maxima(self):
        self.b.pantalla_maquina(1, 10, self.m["id"])
        inicio = self.b._vivas()[(1, 10)]["inicio"]
        self.b.refrescar_vivas(ahora=inicio + 300)
        self.assertEqual(self.b._vivas()[(1, 10)]["inicio"], inicio)

    def test_sin_mensaje_no_se_registra(self):
        self.b.pantalla_maquina(1, None, self.m["id"])
        self.assertEqual(self.b._vivas(), {})


if __name__ == "__main__":
    unittest.main()
