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
import accesos  # noqa: E402
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


class PanelRevendedores(unittest.TestCase):
    """/r por un servidor de verdad: GET, POST, cookie y límites."""
    def setUp(self):
        import panel_web
        import revendedores
        from servicio_rev import Servicio
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        rev = revendedores.Revendedores(os.path.join(t.name, "r.json"))
        rev.crear("juan", "secreto1", "m1")
        self.web = panel_web.PanelWeb(rev, Servicio(rev, lambda _i: None))
        self.puerto = puerto_libre()
        self.s = publico.servir(self.puerto, t.name, host="127.0.0.1", web=self.web)
        self.addCleanup(lambda: (self.s.shutdown(), self.s.server_close()))

    def pedir(self, metodo, ruta, cuerpo=None, cab=None):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=5)
        c.request(metodo, ruta, body=cuerpo, headers=cab or {})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def test_por_http_directo_redirige_a_https(self):
        st, cab, _ = self.pedir("GET", "/r")
        self.assertEqual((st, cab["Location"]), (308, f"https://127.0.0.1:{self.puerto}/r"))

    def test_tras_cloudflare_muestra_el_login(self):
        st, cab, cuerpo = self.pedir("GET", "/r", cab={"X-Forwarded-Proto": "https"})
        self.assertEqual(st, 200)
        self.assertIn(b'name="clave"', cuerpo)
        self.assertIn("Content-Security-Policy", cab)

    def test_login_por_post(self):
        cab = {"X-Forwarded-Proto": "https", "Content-Type": "application/x-www-form-urlencoded"}
        st, c, _ = self.pedir("POST", "/r/entrar", "usuario=juan&clave=secreto1", cab)
        self.assertEqual(st, 303)
        cookie = c["Set-Cookie"].split(";")[0]
        st, _, cuerpo = self.pedir("GET", "/r", cab={"X-Forwarded-Proto": "https", "Cookie": cookie})
        self.assertIn("Tus monedas".encode(), cuerpo)

    def test_la_sesion_se_mantiene_aunque_el_proxy_mande_la_cookie_en_minuscula(self):
        cab = {"X-Forwarded-Proto": "https", "Content-Type": "application/x-www-form-urlencoded"}
        _, c, _ = self.pedir("POST", "/r/entrar", "usuario=juan&clave=secreto1", cab)
        cookie = c["Set-Cookie"].split(";")[0]
        for nombre in ("Cookie", "cookie", "COOKIE"):
            _, _, cuerpo = self.pedir("GET", "/r", cab={"X-Forwarded-Proto": "https", nombre: cookie})
            self.assertIn(b"Tus monedas", cuerpo, nombre)

    def test_post_enorme_se_rechaza(self):
        st, _, _ = self.pedir("POST", "/r/entrar", "x" * (publico.MAX_POST + 1), {"X-Forwarded-Proto": "https"})
        self.assertEqual(st, 413)

    def test_post_fuera_de_r_sigue_dando_405(self):
        self.assertEqual(self.pedir("POST", "/servidores.bin", "a=1")[0], 405)

    def test_sin_panel_no_existe_la_ruta(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        puerto = puerto_libre()
        s = publico.servir(puerto, t.name, host="127.0.0.1")
        self.addCleanup(lambda: (s.shutdown(), s.server_close()))
        c = http.client.HTTPConnection("127.0.0.1", puerto, timeout=5)
        c.request("GET", "/r")
        self.assertEqual(c.getresponse().status, 404)


class Cuenta(unittest.TestCase):
    """/cuenta?t=<token>: nombre del cliente y vencimiento, solo con un token que existe."""
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.puerto = puerto_libre()
        datos = {"b9da1a72f68f59b8": ("Adrián", "2026-11-09")}
        self.s = publico.servir(self.puerto, t.name, host="127.0.0.1", cuenta=datos.get)
        self.addCleanup(lambda: (self.s.shutdown(), self.s.server_close()))

    def pedir(self, ruta):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=5)
        c.request("GET", ruta)
        r = c.getresponse()
        return r.status, r.read()

    def test_da_nombre_y_vencimiento(self):
        self.assertEqual(self.pedir("/cuenta?t=b9da1a72f68f59b8"), (200, "Adrián\n2026-11-09\n".encode()))

    def test_token_que_no_existe_o_mal_formado_da_404(self):
        self.assertEqual(self.pedir("/cuenta?t=ffffffffffffffff")[0], 404)
        self.assertEqual(self.pedir("/cuenta?t=a")[0], 404)
        self.assertEqual(self.pedir("/cuenta?t=../etc/passwd")[0], 404)
        self.assertEqual(self.pedir("/cuenta")[0], 404)

    def test_frena_a_quien_insiste(self):
        estados = [self.pedir("/cuenta?t=ffffffffffffffff")[0] for _ in range(publico.MAX_CUENTA_POR_MIN + 3)]
        self.assertEqual(estados[-1], 429)

    def test_sin_funcion_de_cuenta_no_existe_la_ruta(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        p = puerto_libre()
        s = publico.servir(p, t.name, host="127.0.0.1")
        self.addCleanup(lambda: (s.shutdown(), s.server_close()))
        c = http.client.HTTPConnection("127.0.0.1", p, timeout=5)
        c.request("GET", "/cuenta?t=b9da1a72f68f59b8")
        self.assertEqual(c.getresponse().status, 404)


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

    def test_el_apk_es_publico_y_se_baja_como_archivo(self):
        self.assertEqual(self.pedir("GET", "/zumo-vpn.apk")[0], 404, "todavía no se publicó")
        publico.publicar(b"PK-apk", self.dir, "zumo-vpn.apk")
        st, cuerpo, r = self.pedir("GET", "/zumo-vpn.apk")
        self.assertEqual((st, cuerpo), (200, b"PK-apk"))
        self.assertEqual(r.getheader("Content-Type"), "application/vnd.android.package-archive")
        self.assertIn("zumo-vpn.apk", r.getheader("Content-Disposition"))
        self.assertEqual(self.pedir("GET", "/servidores.bin")[1], self.datos, "la lista no se pisa")

    def test_el_apk_con_version_en_la_direccion_se_baja_igual(self):
        # El enlace lleva ?v=<hora> para que Cloudflare no entregue un APK viejo de su caché.
        publico.publicar(b"PK-apk", self.dir, "zumo-vpn.apk")
        st, cuerpo, _ = self.pedir("GET", "/zumo-vpn.apk?v=931353")
        self.assertEqual((st, cuerpo), (200, b"PK-apk"))

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


class Instaladores(unittest.TestCase):
    """Instalar desde el dominio: código de un solo uso -> pase -> archivos del repo."""

    ARCHIVOS = {"install.sh": "#!/bin/bash\necho INSTALADO-PANEL \"$ZUMO_BASE\" > \"$SALIDA\"\n", "panel.sh": "p",
                "zumo-lib.sh": "l", "actualizar.sh": "#!/bin/bash\necho ACTUALIZADO \"$ZUMO_BASE\" > \"$SALIDA\"\n", "fuentes/zumo-limit.c": "int x;", "fuentes/pdirect2.c": "int y;", "config/limit.conf": "c",
                "binarios/hcr-server": "BIN", "scripts/zumo-datos.sh": "d",
                "bot/instalar-bot.sh": "#!/bin/bash\necho INSTALADO-BOT > \"$SALIDA\"\n",
                "bot/zumo-bot.py": "bot", "bot/test_bot.py": "t", "README.md": "r", ".git/config": "[remote]",
                "android/app/x.kt": "k"}

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = t.name
        self.repo = os.path.join(t.name, "repo")
        pub = os.path.join(t.name, "pub")
        os.makedirs(pub)
        for rel, txt in self.ARCHIVOS.items():
            os.makedirs(os.path.dirname(os.path.join(self.repo, rel)), exist_ok=True)
            with open(os.path.join(self.repo, rel), "w") as f:
                f.write(txt)
        with open(os.path.join(t.name, "secreto"), "w") as f:
            f.write("NO")
        os.symlink(os.path.join(t.name, "secreto"), os.path.join(self.repo, "scripts", "enlace.sh"))
        self.ahora = [1000.0]
        self.ac = accesos.Accesos(os.path.join(t.name, "accesos.json"), reloj=lambda: self.ahora[0])
        self.puerto = puerto_libre()
        self.s = publico.servir(self.puerto, pub, host="127.0.0.1", repo=self.repo, accesos=self.ac, dominio="bot.test")
        self.addCleanup(lambda: (self.s.shutdown(), self.s.server_close()))

    def pedir(self, ruta, metodo="GET", cabeceras=None):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=5)
        c.request(metodo, ruta, headers=cabeceras or {})
        r = c.getresponse()
        return r.status, r.read()

    def pase(self, tipo="panel"):
        st, cuerpo = self.pedir("/canje?c=" + self.ac.crear_codigo(tipo))
        self.assertEqual(st, 200)
        return cuerpo.decode().split()

    def test_sin_pase_no_sirve_ningun_archivo_del_repo(self):
        for ruta in ("/install.sh", "/panel.sh", "/bot/zumo-bot.py", "/binarios/hcr-server", "/s/install.sh",
                     "/s/" + "0" * 32 + "/install.sh", "/s//install.sh"):
            self.assertEqual(self.pedir(ruta)[0], 404, ruta)

    def test_con_pase_sirve_lo_que_bajan_los_instaladores(self):
        p, tipo = self.pase()
        self.assertEqual((len(p), tipo), (32, "panel"))
        for rel in ("install.sh", "panel.sh", "zumo-lib.sh", "actualizar.sh?nocache=1", "fuentes/zumo-limit.c", "fuentes/pdirect2.c",
                    "config/limit.conf", "binarios/hcr-server", "scripts/zumo-datos.sh", "bot/instalar-bot.sh", "bot/zumo-bot.py"):
            self.assertEqual(self.pedir(f"/s/{p}/{rel}")[0], 200, rel)
        self.assertEqual(self.pedir(f"/s/{p}/binarios/hcr-server"), (200, b"BIN"))

    def test_con_pase_tampoco_sirve_nada_mas(self):
        p, _ = self.pase()
        for rel in ("README.md", ".git/config", "android/app/x.kt", "bot/test_bot.py", "scripts/enlace.sh", "binarios/",
                    "binarios", "scripts/../README.md", "%2e%2e/etc/passwd", "bot/../README.md", "fuentes/otro.c", "etc/passwd"):
            self.assertEqual(self.pedir(f"/s/{p}/{rel}")[0], 404, rel)

    def test_el_codigo_sirve_una_sola_vez(self):
        c = self.ac.crear_codigo("panel")
        self.assertEqual(self.pedir("/canje?c=" + c)[0], 200)
        self.assertEqual(self.pedir("/canje?c=" + c)[0], 403)

    def test_codigo_vencido_o_inventado(self):
        c = self.ac.crear_codigo("panel")
        self.ahora[0] += accesos.VIDA_CODIGO + 1
        self.assertEqual(self.pedir("/canje?c=" + c)[0], 403)
        for q in ("", "?c=", "?c=AAAA-AAAA", "?c=../../x", "?otro=1"):
            self.assertEqual(self.pedir("/canje" + q)[0], 403, q)

    def test_anular_corta_el_acceso(self):
        p, _ = self.pase()
        self.assertEqual(self.pedir(f"/s/{p}/install.sh")[0], 200)
        self.ac.revocar(self.ac.listar()[0]["id"])
        self.assertEqual(self.pedir(f"/s/{p}/install.sh")[0], 404)

    def test_el_cargador_lleva_el_dominio_y_no_lleva_secretos(self):
        st, cuerpo = self.pedir("/i")
        self.assertEqual(st, 200)
        self.assertIn(b'D="bot.test"', cuerpo)
        self.assertNotIn(b"%(", cuerpo)

    def test_el_cargador_usa_el_dominio_configurado_y_no_el_de_la_peticion(self):
        st, cuerpo = self.pedir("/i", cabeceras={"Host": "malo.com"})
        self.assertIn(b'D="bot.test"', cuerpo)
        self.assertNotIn(b"malo.com", cuerpo)

    def test_head_y_escrituras(self):
        p, _ = self.pase()
        self.assertEqual(self.pedir(f"/s/{p}/install.sh", "HEAD"), (200, b""))
        self.assertEqual(self.pedir(f"/s/{p}/install.sh", "POST")[0], 405)

    def test_sin_copia_del_repo_da_404(self):
        import shutil
        p, _ = self.pase()
        shutil.rmtree(self.repo)
        self.assertEqual(self.pedir(f"/s/{p}/install.sh")[0], 404)

    def correr_cargador(self, codigo, tipo_esperado):
        """Ejecuta el cargador real (bash) contra este servidor: curl se redirige al puerto local."""
        cuerpo = self.pedir("/i")[1].decode()
        bin_ = os.path.join(self.tmp, "bin")
        os.makedirs(bin_, exist_ok=True)
        real = subprocess.run(["which", "curl"], capture_output=True, text=True).stdout.strip()
        if not real:
            self.skipTest("sin curl")
        stub = os.path.join(bin_, "curl")
        with open(stub, "w") as f:
            f.write('#!/bin/bash\nargs=()\nfor a in "$@"; do args+=("${a//https:\\/\\/bot.test/http:\\/\\/127.0.0.1:%d}"); done\nexec %s "${args[@]}"\n' % (self.puerto, real))
        os.chmod(stub, 0o755)
        salida = os.path.join(self.tmp, "salida")
        env = dict(os.environ, PATH=bin_ + os.pathsep + os.environ["PATH"], ZUMO_CODIGO=codigo, SALIDA=salida)
        # el cargador exige root; en las pruebas se salta esa línea
        cuerpo = cuerpo.replace('[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }\n', "")
        r = subprocess.run(["bash", "-c", cuerpo], env=env, capture_output=True, text=True, timeout=30)
        return r, salida

    def test_el_cargador_instala_de_punta_a_punta(self):
        r, salida = self.correr_cargador(self.ac.crear_codigo("panel"), "panel")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(salida) as f:
            txt = f.read()
        self.assertIn("INSTALADO-PANEL https://bot.test/s/", txt)
        self.assertEqual(len(txt.strip().rsplit("/", 1)[1]), 32)
        self.assertEqual(len(self.ac.listar()), 1)

    def test_el_cargador_de_bot_baja_el_instalador_del_bot(self):
        r, salida = self.correr_cargador(self.ac.crear_codigo("bot"), "bot")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("INSTALADO-BOT", open(salida).read())

    def test_el_cargador_de_actualizar_pasa_una_vps_vieja_al_dominio(self):
        r, salida = self.correr_cargador(self.ac.crear_codigo("actualizar"), "actualizar")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ACTUALIZADO https://bot.test/s/", open(salida).read())

    def test_el_cargador_con_codigo_malo_no_instala_nada(self):
        r, salida = self.correr_cargador("ZZZZ-ZZZZ", "panel")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Código inválido", r.stdout)
        self.assertFalse(os.path.exists(salida))


class Fuerza(unittest.TestCase):
    def test_frena_a_quien_prueba_codigos(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        ahora = [0.0]
        ac = accesos.Accesos(os.path.join(t.name, "a.json"), reloj=lambda: ahora[0])
        bueno = ac.crear_codigo("panel")
        for _ in range(accesos.MAX_FALLOS):
            self.assertEqual(ac.canjear("AAAA-AAAA"), (None, None))
        self.assertEqual(ac.canjear(bueno), (None, None), "bloqueado: ni el bueno pasa")
        ahora[0] += accesos.BLOQUEO + 1
        self.assertIsNotNone(ac.canjear(bueno)[0])


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
