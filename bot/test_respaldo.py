import os
import shutil
import tempfile
import unittest

os.environ.setdefault("ZUMO_DIR", "/nonexistent")
import compilar  # noqa: E402
import respaldo  # noqa: E402


class Pruebas(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        respaldo.DIR = self.d
        respaldo.FIRMA = os.path.join(self.d, "firma")
        self.addCleanup(shutil.rmtree, self.d, True)

    def escribir(self, ruta, texto="x", modo=0o600):
        p = os.path.join(self.d, ruta)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(texto)
        os.chmod(p, modo)

    def test_ida_y_vuelta(self):
        self.escribir("bot.env", "BOT_TOKEN=abc\n")
        self.escribir("firma/pass", "secreto\n")
        self.escribir("respaldos/viejo.zbk", "no debe ir")
        self.escribir("usuarios.db", "pedro:1:2030-01-01\n", 0o644)
        blob = respaldo.crear("clave-larga-1")
        self.assertNotIn(b"BOT_TOKEN", blob)
        shutil.rmtree(self.d)
        os.makedirs(self.d)
        hechos = respaldo.restaurar(blob, "clave-larga-1")
        self.assertEqual(hechos, ["bot.env", "firma/pass", "usuarios.db"])
        self.assertEqual(open(os.path.join(self.d, "bot.env")).read(), "BOT_TOKEN=abc\n")
        self.assertEqual(os.stat(os.path.join(self.d, "bot.env")).st_mode & 0o777, 0o600)
        self.assertFalse(os.path.exists(os.path.join(self.d, "respaldos")))

    def test_clave_mala_y_archivo_ajeno(self):
        self.escribir("bot.env")
        blob = respaldo.crear("clave-larga-1")
        with self.assertRaises(respaldo.ErrorRespaldo):
            respaldo.restaurar(blob, "otra-clave-xx")
        with self.assertRaises(respaldo.ErrorRespaldo):
            respaldo.restaurar(b"basura" * 50, "clave-larga-1")

    def test_clave_corta(self):
        with self.assertRaises(respaldo.ErrorRespaldo):
            respaldo.crear("corta")

    def test_no_sale_de_la_carpeta(self):
        import io
        import tarfile
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            for n in (respaldo.MARCA, "../fuera.txt"):
                i = tarfile.TarInfo(n)
                i.size = 1
                t.addfile(i, io.BytesIO(b"x"))
        rc, blob = respaldo._openssl("-e", buf.getvalue(), "clave-larga-1")
        with self.assertRaises(respaldo.ErrorRespaldo):
            respaldo.restaurar(blob, "clave-larga-1")
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.d), "fuera.txt")))

    @unittest.skipUnless(shutil.which("keytool"), "sin keytool")
    def test_clave_firma_exportar_importar(self):
        self.assertIsNone(respaldo.clave_firma())
        self.assertTrue(respaldo.crear_clave_firma())
        self.assertFalse(respaldo.crear_clave_firma())      # no pisa la que hay
        jks, p = respaldo.clave_firma()
        enc = respaldo.exportar_clave("pass-export-1")
        jks2, p2 = compilar.abrir_clave_exportada(enc, "pass-export-1")   # mismo formato que el bot ya lee
        self.assertEqual(open(jks, "rb").read(), jks2)
        self.assertEqual(p, p2)
        self.assertTrue(respaldo.huella_clave())
        self.assertEqual(os.stat(respaldo.FIRMA).st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
