"""Las dos opciones del bot para compilar la app: en GitHub o en la VPS (con una Telegram falsa)."""
import os
import sys
import tempfile
import time
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
from test_bot import FalsaTelegram, cargar_bot  # noqa: E402

CLAVE = ("/etc/zumo/firma/zumo.jks", "secreta")
SERVIDOR = {"name": "APP 02", "host": "h.com", "port": 80, "payload": "GET /", "tls": False, "sni": ""}


class Base(unittest.TestCase):
    def armar(self, instalado=True, clave=CLAVE, env=""):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(f"{tmp.name}/bot.env", "w") as f:
            f.write(env)
        self.bot = cargar_bot(tmp.name)
        self.tg = FalsaTelegram()
        self.b = self.bot.Bot(self.tg, {7}, None)
        cv, centro = self.bot.compilar_vps, self.bot.centro
        viejos = (cv.faltantes, cv.commit_info, cv.compilar, cv.sincronizar, cv.REPO, centro.respaldo.clave_firma)
        self.addCleanup(lambda: (setattr(cv, "faltantes", viejos[0]), setattr(cv, "commit_info", viejos[1]),
                                 setattr(cv, "compilar", viejos[2]), setattr(cv, "sincronizar", viejos[3]),
                                 setattr(cv, "REPO", viejos[4]), setattr(centro.respaldo, "clave_firma", viejos[5])))
        cv.faltantes = (lambda: []) if instalado else (lambda: ["Gradle", "Android NDK"])
        cv.commit_info = lambda: "abc1234 cambio (hace 2 horas)"
        centro.respaldo.clave_firma = lambda: clave
        self.btn = lambda d: self.b.manejar_cb({"id": "c", "from": {"id": 7}, "data": d,
                                                "message": {"chat": {"id": 1}, "message_id": 5}})
        return tmp.name

    def esperar(self):
        for _ in range(200):
            if not self.b.compilando.locked():
                return
            time.sleep(0.05)
        self.fail("la compilación no terminó")


class Pantallas(Base):
    def test_la_app_ofrece_las_dos_opciones(self):
        self.armar()
        self.btn("app")
        botones = self.tg.datos_botones()
        self.assertIn("acomp", botones)
        self.assertIn("acompv", botones)
        textos = str(self.tg.botones[-1])
        self.assertIn("Compilar en GitHub", textos)
        self.assertIn("Compilar en la VPS", textos)

    def test_sin_instalar_explica_como_instalar(self):
        self.armar(instalado=False)
        self.btn("acompv")
        self.assertIn("Falta: Gradle, Android NDK", self.tg.mensajes[-1])
        self.assertIn("instalar-compilador.sh", self.tg.mensajes[-1])
        self.assertNotIn("acompv_si", self.tg.datos_botones())

    def test_sin_clave_ofrece_traerla_y_no_deja_compilar(self):
        self.armar(clave=None)
        self.btn("acompv")
        self.assertIn("ktraer", self.tg.datos_botones())
        self.assertNotIn("acompv_si", self.tg.datos_botones())
        self.assertIn("clave de firma", self.tg.mensajes[-1])

    def test_listo_muestra_el_estado_y_pide_confirmar(self):
        self.armar()
        self.bot.guardar_app([SERVIDOR])
        self.btn("acompv")
        self.assertIn("acompv_si", self.tg.datos_botones())
        self.assertIn("asinc", self.tg.datos_botones())
        self.assertIn("abc1234", self.tg.mensajes[-1])
        self.assertIn("1 de la lista del bot", self.tg.mensajes[-1])

    def test_sincronizar_ahora_avisa_si_github_no_responde(self):
        self.armar()
        self.bot.compilar_vps.sincronizar = lambda token, rama: (False, "No pude bajar de GitHub (¿está caído?).")
        self.btn("asinc")
        for _ in range(100):
            if any("GitHub (¿está caído?)" in m for m in self.tg.mensajes):
                break
            time.sleep(0.05)
        self.assertTrue(any("⚠️ No pude bajar de GitHub" in m for m in self.tg.mensajes))


class Compilacion(Base):
    def falsa(self, resultado=None, error=None):
        llamadas = []

        def compilar(lista_texto, paquete_marca, clave, url_actualizar="", progreso=None, token="", rama="main"):
            llamadas.append(dict(lista=lista_texto, marca=paquete_marca, clave=clave, url=url_actualizar, token=token, rama=rama))
            progreso("🔨 Compilando en la VPS… 1 min")
            if error:
                raise error
            return resultado or {"apk": b"APK-VPS", "codigo": 921000, "commit": "abc", "sync": (True, "Copia al día con GitHub: abc")}
        self.bot.compilar_vps.compilar = compilar
        return llamadas

    def test_compila_y_manda_el_apk(self):
        self.armar(env="GITHUB_TOKEN=tok\nGITHUB_REPO=o/r\nGITHUB_REF=main\n")
        self.bot.guardar_app([SERVIDOR])
        llamadas = self.falsa()
        self.btn("acompv_si")
        self.esperar()
        self.assertEqual(self.tg.docs[-1][:2], ("zumo-vpn.apk", b"APK-VPS"))
        self.assertIn("versión 921000", self.tg.docs[-1][2])
        c = llamadas[0]
        self.assertIn("[APP 02]", c["lista"])
        self.assertEqual(c["clave"], CLAVE)
        self.assertEqual((c["token"], c["rama"]), ("tok", "main"))
        self.assertEqual(c["url"], "https://raw.githubusercontent.com/o/r/apk/servidores.bin")
        self.assertIsNone(c["marca"], "sin apariencia guardada, usa la del repo")
        self.assertTrue(any("Compilando en la VPS" in m for m in self.tg.ediciones))
        self.assertIn("Copia al día", self.tg.mensajes[-1])

    def test_manda_la_apariencia_que_armo_el_bot(self):
        self.armar()
        os.makedirs(self.bot.MARCA, exist_ok=True)
        with open(os.path.join(self.bot.MARCA, "tema.json"), "w") as f:
            f.write(self.bot.T.a_json(self.bot.cargar_tema()))
        llamadas = self.falsa()
        self.btn("acompv_si")
        self.esperar()
        self.assertIsNotNone(llamadas[0]["marca"])
        self.assertEqual(llamadas[0]["lista"], "", "lista vacía: usa la del repo")

    def test_url_de_actualizacion_se_puede_cambiar(self):
        self.armar(env="ZUMO_URL_ACTUALIZAR=https://bot.zumoserver.com/servidores.bin\n")
        self.assertEqual(self.b.url_actualizar_app(), "https://bot.zumoserver.com/servidores.bin")

    def test_url_por_defecto_sin_config(self):
        self.armar()
        self.assertEqual(self.b.url_actualizar_app(), "https://raw.githubusercontent.com/adri40606941-ui/Zumo/apk/servidores.bin")

    def test_si_falla_lo_cuenta_y_libera_el_candado(self):
        self.armar()
        self.falsa(error=self.bot.compilar_vps.ErrorVps("La compilación en la VPS falló.\ne: x.kt: error"))
        self.btn("acompv_si")
        self.esperar()
        self.assertFalse(self.tg.docs)
        self.assertIn("❌ La compilación en la VPS falló", self.tg.mensajes[-1])
        self.assertIn("e: x.kt: error", self.tg.mensajes[-1])

    def test_un_error_inesperado_no_tumba_el_bot(self):
        self.armar()
        self.falsa(error=RuntimeError("boom"))
        self.btn("acompv_si")
        self.esperar()
        self.assertIn("Error inesperado", self.tg.mensajes[-1])

    def test_no_compila_dos_veces_a_la_vez(self):
        self.armar()
        llamadas = self.falsa()
        self.b.compilando.acquire()
        self.btn("acompv_si")
        self.assertIn("Ya hay una compilación en curso", self.tg.mensajes[-1])
        self.assertEqual(llamadas, [])
        self.b.compilando.release()

    def test_avisa_si_no_pudo_sincronizar_pero_compila(self):
        self.armar()
        self.falsa({"apk": b"X", "codigo": 1, "commit": "a", "sync": (False, "No pude bajar de GitHub (¿está caído?). Sigo con la copia de la VPS")})
        self.btn("acompv_si")
        self.esperar()
        self.assertTrue(self.tg.docs)
        self.assertIn("⚠️ No pude bajar de GitHub", self.tg.mensajes[-1])


class SincronizarDiario(Base):
    def test_no_hace_nada_si_no_esta_instalado(self):
        self.armar()
        self.bot.compilar_vps.REPO = "/no/existe"
        self.bot.compilar_vps.sincronizar = lambda *a: self.fail("no debería sincronizar")
        self.assertIsNone(self.b.sincronizar_una_vez())

    def test_sincroniza_si_esta_instalado(self):
        self.armar(env="GITHUB_TOKEN=tok\n")
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, ".git"))
        self.bot.compilar_vps.REPO = d
        visto = []
        self.bot.compilar_vps.sincronizar = lambda token, rama: visto.append((token, rama)) or (True, "ok")
        self.assertEqual(self.b.sincronizar_una_vez(), (True, "ok"))
        self.assertEqual(visto, [("tok", "main")])
        self.assertFalse(self.b.compilando.locked())

    def test_no_pisa_una_compilacion_en_curso(self):
        self.armar()
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, ".git"))
        self.bot.compilar_vps.REPO = d
        self.bot.compilar_vps.sincronizar = lambda *a: self.fail("no debería sincronizar mientras compila")
        self.b.compilando.acquire()
        self.assertIsNone(self.b.sincronizar_una_vez())
        self.b.compilando.release()


if __name__ == "__main__":
    unittest.main()
