import os
import shutil
import stat
import subprocess
import tempfile
import unittest

import compilar_vps as cv
import marca


def git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class Base(unittest.TestCase):
    """Un origen (bare) y una copia de la VPS, más un SDK y un Gradle falsos."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.viejo = (cv.REPO, cv.SDK, cv.GRADLE, cv.SALIDA, cv._hay_java)
        self.addCleanup(self._restaurar)
        self.origen = os.path.join(self.d, "origen.git")
        trabajo = os.path.join(self.d, "trabajo")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.origen], check=True)
        subprocess.run(["git", "clone", "-q", self.origen, trabajo], check=True, capture_output=True)
        os.makedirs(os.path.join(trabajo, "android", "app", "hev-socks5-tunnel"))
        os.makedirs(os.path.join(trabajo, "android", "marca"))
        for ruta, texto in (("android/servidores.txt", "[repo]\nhost = repo.com\n"),
                            ("android/marca/tema.json", '{"nombre": "Del repo"}'),
                            ("android/app/hev-socks5-tunnel/Android.mk", "# falso")):
            with open(os.path.join(trabajo, ruta), "w") as f:
                f.write(texto)
        git(trabajo, "checkout", "-q", "-b", "main")
        git(trabajo, "add", "-A")
        git(trabajo, "commit", "-q", "-m", "inicial")
        git(trabajo, "push", "-q", "origin", "main")
        self.trabajo = trabajo
        cv.REPO = os.path.join(self.d, "vps")
        subprocess.run(["git", "clone", "-q", self.origen, cv.REPO], check=True, capture_output=True)
        cv.SDK = os.path.join(self.d, "sdk")
        for sub in ("platforms/android-34", "build-tools/34.0.0", f"ndk/{cv.NDK}"):
            os.makedirs(os.path.join(cv.SDK, sub))
        cv.SALIDA = os.path.join(self.d, "apk")
        cv.GRADLE = os.path.join(self.d, "gradle")
        cv._hay_java = lambda: True
        self.jks = os.path.join(self.d, "zumo.jks")
        with open(self.jks, "wb") as f:
            f.write(b"clave")

    def _restaurar(self):
        cv.REPO, cv.SDK, cv.GRADLE, cv.SALIDA, cv._hay_java = self.viejo

    def subir_cambio(self, archivo="nuevo.txt", texto="hola"):
        with open(os.path.join(self.trabajo, archivo), "w") as f:
            f.write(texto)
        git(self.trabajo, "add", "-A")
        git(self.trabajo, "commit", "-q", "-m", f"cambio {archivo}")
        git(self.trabajo, "push", "-q", "origin", "main")

    def gradle_falso(self, cuerpo):
        with open(cv.GRADLE, "w") as f:
            f.write("#!/bin/bash\n" + cuerpo)
        os.chmod(cv.GRADLE, os.stat(cv.GRADLE).st_mode | stat.S_IEXEC)


class Version(unittest.TestCase):
    def test_sube_con_el_tiempo_y_cabe_en_un_entero(self):
        a, b = cv.version_code(1_790_000_000), cv.version_code(1_790_000_060)
        self.assertEqual(b, a + 1)
        self.assertLess(cv.version_code(1_790_000_000), 2**31 - 1)

    def test_siempre_supera_a_las_versiones_viejas_de_github(self):
        self.assertGreater(cv.version_code(1_791_000_000), 104)


class Instalacion(Base):
    def test_todo_listo(self):
        self.gradle_falso("exit 0\n")
        self.assertEqual(cv.faltantes(), [])

    def test_avisa_que_falta(self):
        self.assertIn("Gradle", cv.faltantes())
        shutil.rmtree(os.path.join(cv.SDK, "ndk"))
        self.assertIn("Android NDK", cv.faltantes())
        cv._hay_java = lambda: False
        self.assertIn("Java 17", cv.faltantes())
        cv.REPO = os.path.join(self.d, "no-existe")
        self.assertIn("la copia del repo", cv.faltantes())


class Sincronizar(Base):
    def test_baja_los_cambios(self):
        self.subir_cambio()
        ok, msg = cv.sincronizar()
        self.assertTrue(ok, msg)
        self.assertTrue(os.path.exists(os.path.join(cv.REPO, "nuevo.txt")))

    def test_si_github_no_responde_sigue_con_la_copia(self):
        os.rename(self.origen, self.origen + ".caido")
        ok, msg = cv.sincronizar()
        self.assertFalse(ok)
        self.assertIn("GitHub", msg)
        self.assertIn("inicial", msg)

    def test_cambios_propios_que_chocan_no_rompen_nada(self):
        with open(os.path.join(cv.REPO, "propio.txt"), "w") as f:
            f.write("mío")
        git(cv.REPO, "add", "-A")
        git(cv.REPO, "commit", "-q", "-m", "mío")
        self.subir_cambio()
        ok, msg = cv.sincronizar()
        self.assertFalse(ok)
        self.assertTrue(os.path.exists(os.path.join(cv.REPO, "propio.txt")))
        self.assertFalse(os.path.exists(os.path.join(cv.REPO, "nuevo.txt")))

    def test_otra_rama_no_se_toca(self):
        git(cv.REPO, "checkout", "-q", "-b", "mi-prueba")
        self.subir_cambio()
        ok, msg = cv.sincronizar()
        self.assertFalse(ok)
        self.assertIn("mi-prueba", msg)

    def test_sin_copia_no_falla(self):
        cv.REPO = os.path.join(self.d, "no-existe")
        self.assertFalse(cv.sincronizar()[0])


class Preservar(Base):
    def test_restaura_incluso_los_cambios_sin_guardar(self):
        sv = os.path.join(cv.REPO, "android", "servidores.txt")
        with open(sv, "w") as f:
            f.write("[edicion mia]\n")
        marca_dir = os.path.join(cv.REPO, "android", "marca")
        with cv._preservar([sv, marca_dir]):
            with open(sv, "w") as f:
                f.write("otra cosa")
            with open(os.path.join(marca_dir, "icono.png"), "wb") as f:
                f.write(b"x")
            os.remove(os.path.join(marca_dir, "tema.json"))
        with open(sv) as f:
            self.assertEqual(f.read(), "[edicion mia]\n")
        self.assertEqual(os.listdir(marca_dir), ["tema.json"])

    def test_restaura_si_algo_falla(self):
        sv = os.path.join(cv.REPO, "android", "servidores.txt")
        with self.assertRaises(RuntimeError):
            with cv._preservar([sv]):
                os.remove(sv)
                raise RuntimeError("x")
        self.assertTrue(os.path.exists(sv))


GRADLE_OK = r'''
cd "$(dirname "$0")"
ANDROID="$2"
echo "> Task :app:preBuild"
{ echo "KS=$ZUMO_KEYSTORE"; echo "PASS=$ZUMO_KS_PASS"; echo "VER=$ZUMO_VERSION_CODE"; echo "URL=$ZUMO_ACTUALIZAR_URL"; echo "HOME=$ANDROID_HOME"; } > env.txt
cp "$ANDROID/servidores.txt" servidores-vista.txt
cp "$ANDROID/marca/tema.json" tema-vista.txt
mkdir -p "$ANDROID/app/build/outputs/apk/release"
printf 'APK-FIRMADO' > "$ANDROID/app/build/outputs/apk/release/app-release.apk"
'''


class Compilar(Base):
    def compilar(self, **kw):
        args = dict(lista_texto="", paquete_marca=None, clave=(self.jks, "secreta"), url_actualizar="http://x/s.bin")
        args.update(kw)
        return cv.compilar(**args)

    def test_compila_y_devuelve_el_apk(self):
        self.gradle_falso(GRADLE_OK)
        mensajes = []
        r = self.compilar(lista_texto="[Mi servidor]\nhost = a.com", progreso=mensajes.append)
        self.assertEqual(r["apk"], b"APK-FIRMADO")
        self.assertEqual(r["codigo"], cv.version_code(), "mismo minuto salvo que justo cambie")
        env = open(os.path.join(self.d, "env.txt")).read()
        self.assertIn(f"KS={self.jks}", env)
        self.assertIn("PASS=secreta", env)
        self.assertIn("URL=http://x/s.bin", env)
        self.assertIn(f"HOME={cv.SDK}", env)
        self.assertIn(f"VER={r['codigo']}", env)
        self.assertIn("[Mi servidor]", open(os.path.join(self.d, "servidores-vista.txt")).read())
        self.assertTrue(any("Compilando" in m for m in mensajes))
        self.assertTrue(os.path.exists(os.path.join(cv.SALIDA, f"zumo-vpn-{r['codigo']}.apk")))

    def test_deja_la_copia_como_estaba(self):
        self.gradle_falso(GRADLE_OK)
        paquete = marca.empaquetar('{"v": 1, "nombre": "Otro"}', None, None)
        self.compilar(lista_texto="[X]\nhost = x.com", paquete_marca=paquete)
        self.assertIn("Del repo", open(os.path.join(cv.REPO, "android", "marca", "tema.json")).read())
        self.assertIn("repo.com", open(os.path.join(cv.REPO, "android", "servidores.txt")).read())
        self.assertIn("Otro", open(os.path.join(self.d, "tema-vista.txt")).read(), "durante la compilación sí se usó la apariencia del bot")
        self.assertEqual(git(cv.REPO, "status", "--porcelain", "--untracked-files=no"), "",
                         "los archivos del repo quedan como estaban: el próximo git pull anda")

    def test_sin_lista_ni_apariencia_usa_la_del_repo(self):
        self.gradle_falso(GRADLE_OK)
        self.compilar()
        self.assertIn("repo.com", open(os.path.join(self.d, "servidores-vista.txt")).read())

    def test_si_gradle_falla_muestra_el_error(self):
        self.gradle_falso('echo "> Task :app:compileKotlin"\necho "e: MainActivity.kt: Unresolved reference: x"\necho "BUILD FAILED"\nexit 1\n')
        with self.assertRaises(cv.ErrorVps) as e:
            self.compilar()
        self.assertIn("Unresolved reference", str(e.exception))
        self.assertIn("falló", str(e.exception))

    def test_no_acepta_un_apk_sin_firmar(self):
        self.gradle_falso(GRADLE_OK.replace("app-release.apk", "app-release-unsigned.apk"))
        with self.assertRaises(cv.ErrorVps) as e:
            self.compilar()
        self.assertIn("firmado", str(e.exception))

    def test_sin_clave_no_compila(self):
        self.gradle_falso(GRADLE_OK)
        with self.assertRaises(cv.ErrorVps) as e:
            self.compilar(clave=None)
        self.assertIn("clave de firma", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(self.d, "env.txt")), "no llegó a correr Gradle")

    def test_sin_instalar_dice_que_falta_y_como_instalarlo(self):
        with self.assertRaises(cv.ErrorVps) as e:
            self.compilar()
        self.assertIn("Gradle", str(e.exception))
        self.assertIn("instalar-compilador.sh", str(e.exception))

    def test_apariencia_danada_se_explica(self):
        self.gradle_falso(GRADLE_OK)
        with self.assertRaises(cv.ErrorVps) as e:
            self.compilar(paquete_marca=b"no es un zip")
        self.assertIn("apariencia", str(e.exception))
        self.assertIn("Del repo", open(os.path.join(cv.REPO, "android", "marca", "tema.json")).read())

    def test_guarda_solo_los_ultimos_apk(self):
        self.gradle_falso(GRADLE_OK)
        os.makedirs(cv.SALIDA)
        for i in range(5):
            ruta = os.path.join(cv.SALIDA, f"zumo-vpn-{i}.apk")
            open(ruta, "wb").write(b"viejo")
            os.utime(ruta, (1000 + i, 1000 + i))
        self.compilar()
        self.assertEqual(len(os.listdir(cv.SALIDA)), cv.MANTENER_APK)

    def test_compila_con_los_cambios_nuevos_de_github(self):
        self.gradle_falso(GRADLE_OK + 'cp "$ANDROID/../nuevo.txt" visto-nuevo.txt\n')
        self.subir_cambio()
        r = self.compilar()
        self.assertTrue(r["sync"][0])
        self.assertEqual(open(os.path.join(self.d, "visto-nuevo.txt")).read(), "hola")


if __name__ == "__main__":
    unittest.main()
