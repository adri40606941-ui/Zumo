import os
import shutil
import stat
import subprocess
import tempfile
import unittest

import local
import respaldo


class Pruebas(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        local.SRC = os.path.join(self.d, "src")
        local.CONTADOR = os.path.join(self.d, "build.n")
        respaldo.FIRMA = os.path.join(self.d, "firma")
        os.makedirs(os.path.join(local.SRC, "android"))
        with open(os.path.join(local.SRC, "android", "servidores.txt"), "w") as f:
            f.write("[ORIGINAL]\n")
        for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
            subprocess.run(["git", "-C", local.SRC, *cmd], check=True)
        respaldo.guardar_clave_firma(b"J" * 600, "pw123")

    def gradle(self, cuerpo):
        ruta = os.path.join(self.d, "gradle")
        with open(ruta, "w") as f:
            f.write("#!/bin/bash\n" + cuerpo)
        os.chmod(ruta, 0o755)
        local.GRADLE = ruta

    def test_compila_y_devuelve_apk(self):
        self.gradle('echo "> Task :app:compileKotlin"\n'
                    'D=' + local.SRC + '/android/app/build/outputs/apk/release; mkdir -p $D; '
                    'cat ' + local.SRC + '/android/servidores.txt > $D/zumo.apk; echo "vc=$ZUMO_VERSION_CODE" >> $D/zumo.apk\n')
        r = local.compilar("[A]\nhost = x\n", actualizar=False)
        self.assertTrue(r["ok"])
        self.assertIn(b"[A]", r["apk"])               # la lista llegó al build
        self.assertIn(b"vc=1", r["apk"])
        self.assertEqual(r["numero"], 1)
        # la lista no queda en el repo
        self.assertEqual(open(os.path.join(local.SRC, "android", "servidores.txt")).read(), "[ORIGINAL]\n")
        self.assertEqual(local.compilar("", actualizar=False)["numero"], 2)

    def test_falla_con_log(self):
        self.gradle('echo "e: Main.kt: Unresolved reference"; echo "BUILD FAILED"; exit 1\n')
        r = local.compilar("", actualizar=False)
        self.assertFalse(r["ok"])
        self.assertIn("Unresolved reference", r["log"])

    def test_sin_clave(self):
        shutil.rmtree(respaldo.FIRMA)
        with self.assertRaises(local.ErrorLocal):
            local.compilar("", actualizar=False)

    def test_progreso(self):
        self.gradle('echo "> Task :app:x"; sleep 2; D=' + local.SRC + '/android/app/build/outputs/apk/release; mkdir -p $D; echo a > $D/z.apk\n')
        vistos = []
        r = local.compilar("", progreso=lambda m, t, h=0, n=0: vistos.append((m, t, h, n)), actualizar=False)
        self.assertTrue(r["ok"])
        self.assertTrue(vistos)

    def test_porcentaje_con_simulacro(self):
        D = local.SRC + '/android/app/build/outputs/apk/release'
        self.gradle('if [[ "$*" == *--dry-run* ]]; then printf ":a SKIPPED\\n:b SKIPPED\\n:c SKIPPED\\n:d SKIPPED\\n"; exit 0; fi\n'
                    'echo "> Task :a"; echo "> Task :b UP-TO-DATE"; sleep 7; echo "> Task :c"; echo "> Task :d"; '
                    'mkdir -p ' + D + '; echo a > ' + D + '/z.apk\n')
        vistos = []
        r = local.compilar("", progreso=lambda m, t, h=0, n=0: vistos.append((h, n)), actualizar=False)
        self.assertTrue(r["ok"])
        self.assertTrue(all(n == 4 for _, n in vistos[1:]), vistos)
        self.assertTrue(any(0 < h <= 4 for h, _ in vistos), vistos)


if __name__ == "__main__":
    unittest.main()
