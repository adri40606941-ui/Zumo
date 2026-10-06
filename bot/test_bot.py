#!/usr/bin/env python3
"""Pruebas del bot sin Telegram ni root: .zs, armado del archivo y comandos con una Telegram falsa."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import zs  # noqa: E402

VECTOR_IV = bytes(range(12))
VECTOR_SECRETO = "secreto-de-prueba"
VECTOR_PERFIL = zs.perfil("vps.ejemplo.com", 80, "GET / HTTP/1.1[crlf]Host: x.net[crlf][crlf]", "Mi VPN",
                          "cliente1", "Clave1", "2026-11-05")


def cargar_bot(tmp):
    os.environ.update({"ZUMO_BOT_ENV": f"{tmp}/bot.env", "ZUMO_BOT_JSON": f"{tmp}/bot.json",
                       "ZUMO_DB": f"{tmp}/usuarios.db", "ZUMO_CLAVES": f"{tmp}/claves.db",
                       "ZUMO_APP_SERVIDORES": f"{tmp}/app-servidores.json",
                       "ZUMO_ZUMOID": f"{tmp}/zumoid", "ZUMO_DISP_DB": f"{tmp}/disp.db",
                       "ZUMO_DISP_LOCK": f"{tmp}/disp.lock", "ZUMO_DISP_LOG": f"{tmp}/disp.log"})
    spec = importlib.util.spec_from_file_location("zumo_bot", os.path.join(AQUI, "zumo-bot.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FalsaTelegram:
    def __init__(self): self.mensajes, self.docs, self.botones, self.ediciones, self.borrados = [], [], [], [], []
    def mensaje(self, chat, texto, botones=None, md=False):
        self.mensajes.append(texto); self.botones.append(botones); return len(self.mensajes)
    def editar(self, chat, mid, texto, botones=None):
        self.ediciones.append(texto); self.mensajes.append(texto); self.botones.append(botones)
    def responder_cb(self, *a, **k): pass
    def borrar(self, chat, mid): self.borrados.append(mid)
    def documento(self, chat, nombre, datos, leyenda=""): self.docs.append((nombre, datos, leyenda))
    archivos = {}
    def descargar(self, file_id): return self.archivos[file_id]

    def datos_botones(self):
        return [d for fila in (self.botones[-1] or []) for _, d in fila]


class Pruebas(unittest.TestCase):
    def test_ida_y_vuelta(self):
        blob = zs.cifrar(VECTOR_PERFIL, "x")
        self.assertEqual(zs.descifrar(blob, "x"), VECTOR_PERFIL)
        self.assertEqual(blob[:3], b"ZS1")
        self.assertNotIn(b"vps.ejemplo.com", blob)
        with self.assertRaises(Exception):
            zs.descifrar(blob, "otro")
        alterado = bytearray(blob); alterado[-1] ^= 1
        with self.assertRaises(Exception):
            zs.descifrar(bytes(alterado), "x")

    def test_vector_para_kotlin(self):
        """El mismo vector lo descifra ZsTest.kt: si cambia el formato, hay que tocar los dos."""
        blob = zs.cifrar(VECTOR_PERFIL, VECTOR_SECRETO, VECTOR_IV)
        ruta = os.path.join(AQUI, "..", "android", "app", "src", "test", "resources", "vector.zs.hex")
        with open(ruta) as f:
            self.assertEqual(f.read().strip(), blob.hex())

    def armar(self, tmp, gh=None):
        bot = cargar_bot(tmp)
        tg = FalsaTelegram()
        b = bot.Bot(tg, {7}, "sec", gh)
        self.n_msg = 100
        def txt(t):
            self.n_msg += 1
            b.manejar({"chat": {"id": 1}, "from": {"id": 7}, "text": t, "message_id": self.n_msg})
        btn = lambda d: b.manejar_cb({"id": "c", "from": {"id": 7}, "data": d, "message": {"chat": {"id": 1}, "message_id": 5}})
        return bot, tg, b, txt, btn

    def test_menu_por_botones_y_exportar(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            open(f"{tmp}/usuarios.db", "w").write("cliente1:2:2026-11-05\n")
            open(f"{tmp}/claves.db", "w").write("cliente1:Clave1\n")
            # un extraño no entra
            b.manejar({"chat": {"id": 1}, "from": {"id": 99}, "text": "hola"})
            self.assertIn("No autorizado", tg.mensajes[-1])
            # cualquier texto abre el menú con botones
            txt("hola")
            self.assertEqual(tg.datos_botones(), ["crear", "lista:0", "srv", "app", "resp", "id"])
            # sin servidor no se puede crear ni exportar
            btn("crear")
            self.assertIn("Primero definí el servidor", tg.mensajes[-1])
            btn("x:cliente1")
            self.assertIn("Falta el servidor", tg.mensajes[-1])
            # servidor por botones + texto
            btn("s:host"); txt("vps.ejemplo.com:8080")
            self.assertIn("vps.ejemplo.com", tg.mensajes[-1]); self.assertIn("8080", tg.mensajes[-1])
            btn("s:name"); txt("Mi VPN")
            btn("s:payload"); txt("GET / HTTP/1.1[crlf]Host: x.net[crlf][crlf]")
            btn("s:tls"); self.assertIn("TLS: sí", tg.mensajes[-1])
            btn("s:tls"); self.assertIn("TLS: no", tg.mensajes[-1])
            # lista y ficha del usuario
            btn("lista:0"); self.assertIn("u:cliente1", tg.datos_botones())
            btn("u:cliente1"); self.assertIn("x:cliente1", tg.datos_botones())
            btn("x:cliente1")
            nombre, datos, _ = tg.docs[-1]
            self.assertEqual(nombre, "Mi_VPN.zs")
            p = zs.descifrar(datos, "sec")
            self.assertEqual((p["user"], p["pass"], p["exp"]), ("cliente1", "Clave1", "2026-11-05"))
            self.assertEqual((p["cfg"]["host"], p["cfg"]["sshPort"]), ("vps.ejemplo.com", 8080))
            self.assertTrue(p["cfg"]["payload"].startswith("GET / HTTP/1.1[crlf]"))
            # límite por botón
            llamadas = []
            bot.bash_lib = lambda *a: llamadas.append(a)
            btn("ln:cliente1:3"); self.assertEqual(llamadas[-1], ("zumo_db_set", "cliente1", "2", "3"))

    def test_crear_usuario_paso_a_paso(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            open(f"{tmp}/usuarios.db", "w").write("")
            json.dump({"host": "h.com", "port": 80, "name": "N"}, open(f"{tmp}/bot.json", "w"))
            creados = []
            def falso_crear(u, clave, dias, lim):
                creados.append((u, clave, dias, lim))
                open(f"{tmp}/usuarios.db", "a").write(f"{u}:{lim}:2030-01-01\n")
                bot.clave_guardar(u, clave)
            bot.crear_usuario = falso_crear
            btn("crear")
            txt("1mal"); self.assertIn("inválido", tg.mensajes[-1])
            txt("pepe")
            txt("con espacio"); self.assertIn("inválida", tg.mensajes[-1])
            txt("Clave9")
            self.assertIn("cd:30", tg.datos_botones())
            btn("cd:otro"); txt("abc"); self.assertIn("Escribí un número", tg.mensajes[-1])
            txt("45")
            self.assertIn("cl:2", tg.datos_botones())
            btn("cl:2")
            self.assertEqual(creados, [("pepe", "Clave9", 45, 2)])
            self.assertEqual(tg.docs[-1][0], "N.zs")
            self.assertEqual(zs.descifrar(tg.docs[-1][1], "sec")["user"], "pepe")
            # cancelar limpia el estado
            btn("crear"); btn("menu"); self.assertNotIn(1, b.estado)

    def test_validaciones(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = cargar_bot(tmp)
            self.assertIn("inválido", bot.crear_usuario("1abc", "x", 5, 1))
            self.assertIn("inválida", bot.crear_usuario("abc", "con espacio", 5, 1))
            self.assertEqual(bot.fecha_cuenta("2026-12-31"), "2027-01-01")

    def test_servidores_formato_ida_y_vuelta(self):
        import servidores as sv
        t = "[APP 02]\nhost = a.com\npuerto = 8080\npayload = GET / HTTP/1.1[crlf]Host: [host][crlf][crlf]\n\n[APP 05]\nhost = b.com\ntls = si\nsni = x.net\n\n[roto]\nhost =\n"
        l = sv.desde_texto(t)
        self.assertEqual([x["name"] for x in l], ["APP 02", "APP 05"])
        self.assertEqual((l[0]["port"], l[1]["port"], l[1]["tls"], l[1]["sni"]), (8080, 443, True, "x.net"))
        self.assertEqual(sv.desde_texto(sv.a_texto(l)), l)
        # un payload con saltos de línea o un nombre con [] no rompen el formato
        s = sv.nuevo("Mi [X]=", "h.com", 80, "GET /\r\nHost: h")
        self.assertEqual(s["name"], "Mi X")
        self.assertNotIn("\n", s["payload"])
        self.assertEqual(len(sv.desde_texto(sv.a_texto([s]))), 1)

    def test_app_agregar_y_cambiar_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            btn("app"); self.assertIn("aadd", tg.datos_botones())
            btn("aadd"); txt("APP 02"); txt("mal host con espacio"); self.assertIn("inválido", tg.mensajes[-1])
            txt("vps.ejemplo.com:8080"); txt("GET / HTTP/1.1[crlf]Host: [host][crlf][crlf]")
            self.assertEqual(bot.cargar_app(), [{"name": "APP 02", "host": "vps.ejemplo.com", "port": 8080,
                                                  "payload": "GET / HTTP/1.1[crlf]Host: [host][crlf][crlf]", "tls": False, "sni": ""}])
            self.assertTrue(tg.borrados)  # el mensaje con el payload se borra del chat
            btn("ap:0"); txt("GET /nuevo HTTP/1.1[crlf][crlf]")
            self.assertEqual(bot.cargar_app()[0]["payload"], "GET /nuevo HTTP/1.1[crlf][crlf]")
            self.assertIn("GET /nuevo", tg.mensajes[-1])
            btn("at:0"); self.assertTrue(bot.cargar_app()[0]["tls"])
            btn("an:0"); txt("APP 03"); self.assertEqual(bot.cargar_app()[0]["name"], "APP 03")
            btn("abs:0"); self.assertEqual(bot.cargar_app(), [])
            # pegar lista completa
            btn("apegar"); txt("sin formato"); self.assertIn("No encontré", tg.mensajes[-1])
            txt("[X]\nhost = x.com\n[Y]\nhost = y.com\n"); self.assertEqual([s["name"] for s in bot.cargar_app()], ["X", "Y"])

    def test_compilar_y_enviar_apk(self):
        import compilar
        class GH:
            repo, rama = "o/r", "main"
            def __init__(self, ok=True): self.llamadas, self.ok = [], ok
            def subir_secreto(self, n, v): self.llamadas.append(("secreto", n, v))
            def ultimo_run(self): return 10
            def lanzar(self): self.llamadas.append("lanzar")
            def run_nuevo(self, antes): return 11
            def run(self, i): return {"status": "completed", "conclusion": "success" if self.ok else "failure", "run_number": 42, "html_url": "http://x"}
            def archivo_de_rama(self, ruta, rama=None):
                if ruta == "zumo-vpn.apk": return b"APK!"
                if self.ok: raise compilar.ErrorGitHub("no")
                return b"linea1\nerror: no compila\n"
        for ok in (True, False):
            with tempfile.TemporaryDirectory() as tmp:
                gh = GH(ok)
                bot, tg, b, txt, btn = self.armar(tmp, gh)
                bot.guardar_app([{"name": "APP 02", "host": "h.com", "port": 80, "payload": "GET /", "tls": False, "sni": ""}])
                btn("acomp"); self.assertIn("acomp_si", tg.datos_botones())
                btn("acomp_si")
                for _ in range(100):
                    if not b.compilando.locked(): break
                    import time; time.sleep(0.05)
                self.assertEqual(gh.llamadas[0][:2], ("secreto", "ZUMO_SERVIDORES"))
                self.assertIn("[APP 02]", gh.llamadas[0][2]); self.assertEqual(gh.llamadas[1], "lanzar")
                if ok:
                    self.assertEqual(tg.docs[-1][:2], ("zumo-vpn.apk", b"APK!"))
                else:
                    self.assertFalse(tg.docs); self.assertTrue(any("error: no compila" in m for m in tg.mensajes))

    def test_compilar_sin_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            btn("acomp"); self.assertIn("GITHUB_TOKEN", tg.mensajes[-1])

    def test_clave_exportada_con_openssl(self):
        """Mismo comando que el workflow (tar | openssl enc -pbkdf2) y lo abre compilar.abrir_clave_exportada."""
        import compilar, subprocess
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(f"{d}/ks")
            open(f"{d}/ks/zumo.jks", "wb").write(os.urandom(2600)); open(f"{d}/ks/pass", "w").write("pw123\n")
            r = subprocess.run(f"tar -C {d}/ks -cf - zumo.jks pass | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -pass env:EXPORT_PASS",
                               shell=True, capture_output=True, env={**os.environ, "EXPORT_PASS": "abc"})
            self.assertEqual(r.returncode, 0)
            jks, pw = compilar.abrir_clave_exportada(r.stdout, "abc")
            self.assertEqual((jks, pw), (open(f"{d}/ks/zumo.jks", "rb").read(), "pw123"))
            with self.assertRaises(compilar.ErrorGitHub):
                compilar.abrir_clave_exportada(r.stdout, "otra")

    def test_asegurar_clave_de_firma(self):
        import compilar, subprocess, base64
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(f"{d}/ks")
            jks = os.urandom(2600)
            open(f"{d}/ks/zumo.jks", "wb").write(jks); open(f"{d}/ks/pass", "w").write("pw123\n")
            class GH:
                repo, rama = "o/r", "main"
                def __init__(self, con_artefacto=True): self.sec, self.borrados, self.con = {}, [], con_artefacto
                def subir_secreto(self, n, v): self.sec[n] = v
                def borrar_secreto(self, n): self.borrados.append(n)
                def ultimo_run(self): return 1
                def lanzar(self): pass
                def run_nuevo(self, a): return 2
                def run(self, i): return {"status": "completed", "conclusion": "success"}
                def artefacto(self, nombre, rid):
                    if not self.con: return None
                    r = subprocess.run(f"tar -C {d}/ks -cf - zumo.jks pass | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -pass env:P",
                                       shell=True, capture_output=True, env={**os.environ, "P": self.sec["ZUMO_EXPORT_PASS"]})
                    return r.stdout
            for con in (True, False):
                with tempfile.TemporaryDirectory() as tmp:
                    gh = GH(con)
                    bot, tg, b, txt, btn = self.armar(tmp, gh)
                    btn("aclave"); self.assertIn("aclave_si", tg.datos_botones())
                    btn("aclave_si")
                    for _ in range(200):
                        if not b.compilando.locked(): break
                        import time; time.sleep(0.05)
                    self.assertEqual(gh.borrados, ["ZUMO_EXPORT_PASS"])  # el rastro se borra siempre
                    if con:
                        self.assertEqual(base64.b64decode(gh.sec["ZUMO_KEYSTORE_B64"]), jks)
                        self.assertEqual(gh.sec["ZUMO_KS_PASS"], "pw123")
                        self.assertIn("guardada como secreto fijo", tg.mensajes[-1])
                    else:
                        self.assertNotIn("ZUMO_KEYSTORE_B64", gh.sec)
                        self.assertIn("ya estaba guardada", tg.mensajes[-1])

    def _con_zumoid(self, tmp):
        """Copia el binario real de zumoid (o lo compila) a tmp/zumoid. Devuelve False si no se pudo."""
        import platform, shutil, subprocess
        repo = os.path.join(AQUI, "..")
        src = os.path.join(repo, "zumoid-amd64")
        if platform.machine() in ("x86_64", "AMD64") and os.path.exists(src):
            shutil.copy(src, f"{tmp}/zumoid")
        else:
            r = subprocess.run(["go", "build", "-o", f"{tmp}/zumoid", "."], cwd=os.path.join(repo, "zumoid"),
                               capture_output=True, env={**os.environ, "CGO_ENABLED": "0"})
            if r.returncode != 0:
                return False
        os.chmod(f"{tmp}/zumoid", 0o755)
        return True

    def test_dispositivo_ver_vincular_olvidar(self):
        with tempfile.TemporaryDirectory() as tmp:
            if not self._con_zumoid(tmp):
                self.skipTest("no hay binario zumoid ni Go para compilarlo")
            bot, tg, b, txt, btn = self.armar(tmp)
            open(f"{tmp}/usuarios.db", "w").write("ana:1:2030-01-01\n")
            # todavía no mandó su ID: se avisa, sin botones de vincular
            btn("u:ana")
            self.assertIn("todavía no conectó", tg.mensajes[-1])
            self.assertNotIn("dv:ana", tg.datos_botones())
            # ya mandó su ID (lo escribe el servicio)
            open(f"{tmp}/disp.db", "w").write("ana:aaaaaaaaaaaaaaaa:0:1700000000:1700003600\n")
            btn("u:ana")
            self.assertIn("Android ID: aaaaaaaaaaaaaaaa", tg.mensajes[-1])
            self.assertIn("Sin vincular", tg.mensajes[-1])
            self.assertIn("dv:ana", tg.datos_botones()); self.assertIn("dx:ana", tg.datos_botones())
            # vincular
            btn("dv:ana")
            self.assertIn("Vinculado", tg.mensajes[-1])
            self.assertIn(":1:", open(f"{tmp}/disp.db").read())
            # intento de otro celular
            open(f"{tmp}/disp.log", "w").write("1700007200\tana\totro-dispositivo\tbbbbbbbbbbbbbbbb\n")
            btn("u:ana")
            self.assertIn("Intento bloqueado: otro celular (bbbbbbbbbbbbbbbb)", tg.mensajes[-1])
            # desvincular
            btn("dv:ana")
            self.assertIn("Sin vincular", tg.mensajes[-1])
            # olvidar pide confirmación
            btn("dx:ana"); self.assertIn("dxs:ana", tg.datos_botones())
            self.assertIn("aaaaaaaaaaaaaaaa", open(f"{tmp}/disp.db").read())
            btn("dxs:ana")
            self.assertNotIn("aaaaaaaaaaaaaaaa", open(f"{tmp}/disp.db").read())
            self.assertIn("todavía no conectó", tg.mensajes[-1])

    def test_dispositivo_sin_servicio_instalado(self):
        with tempfile.TemporaryDirectory() as tmp:  # no hay tmp/zumoid: VPS sin actualizar
            bot, tg, b, txt, btn = self.armar(tmp)
            open(f"{tmp}/usuarios.db", "w").write("ana:1:2030-01-01\n")
            btn("u:ana")
            self.assertNotIn("Android", tg.mensajes[-1])
            self.assertNotIn("dv:ana", tg.datos_botones())
            btn("dv:ana")  # un botón viejo no rompe nada
            self.assertIn("ana", tg.mensajes[-1])

    def test_borrar_usuario_olvida_el_celular(self):
        with tempfile.TemporaryDirectory() as tmp:
            if not self._con_zumoid(tmp):
                self.skipTest("no hay binario zumoid ni Go para compilarlo")
            bot = cargar_bot(tmp)
            open(f"{tmp}/disp.db", "w").write("ana:aaaaaaaaaaaaaaaa:1:1:1\nbeto:bbbbbbbbbbbbbbbb:0:1:1\n")
            bot.run = lambda *a, **k: None
            bot.bash_lib = lambda *a: None
            bot.borrar_usuario("ana")
            self.assertEqual(open(f"{tmp}/disp.db").read(), "beto:bbbbbbbbbbbbbbbb:0:1:1\n")

    def test_respaldo_y_clave_por_botones(self):
        import shutil
        import respaldo
        import centro
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            respaldo.DIR = tmp
            respaldo.FIRMA = f"{tmp}/firma"
            centro.ENV = f"{tmp}/bot.env"
            centro.ULTIMO = f"{tmp}/ultimo"
            centro.reiniciar_bot = lambda: tg.mensajes.append("REINICIO")
            b.leer_env_fn = bot.leer_env
            open(f"{tmp}/bot.env", "w").write("BOT_TOKEN=abc\n")
            open(f"{tmp}/usuarios.db", "w").write("ana:1:2030-01-01\n")
            btn("resp"); self.assertIn("rnow", tg.datos_botones()); self.assertIn("falta", tg.mensajes[-1])
            btn("rnow"); self.assertIn("contraseña del respaldo", tg.mensajes[-1])
            btn("rpass"); txt("corta"); self.assertIn("Muy corta", tg.mensajes[-1])
            txt("clave-del-respaldo"); self.assertIn("guardada", tg.mensajes[-1])
            self.assertIn("RESPALDO_PASS=clave-del-respaldo", open(f"{tmp}/bot.env").read())
            self.assertEqual(oct(os.stat(f"{tmp}/bot.env").st_mode & 0o777), "0o600")
            self.assertTrue(tg.borrados)                                  # la contraseña se borra del chat
            btn("rnow")
            nombre, blob, _ = tg.docs[-1]
            self.assertTrue(nombre.startswith("zumo-respaldo-") and nombre.endswith(".enc"))
            self.assertNotIn(b"BOT_TOKEN", blob)
            # se pierde todo y se restaura mandando el archivo
            os.remove(f"{tmp}/usuarios.db")
            tg.archivos["f1"] = blob
            btn("rrest")
            b.manejar({"chat": {"id": 1}, "from": {"id": 7}, "message_id": 9, "document": {"file_id": "f1", "file_size": len(blob)}})
            txt("mala-clave-xx"); self.assertIn("incorrecta", tg.mensajes[-1])
            btn("rrest")
            b.manejar({"chat": {"id": 1}, "from": {"id": 7}, "message_id": 9, "document": {"file_id": "f1", "file_size": len(blob)}})
            txt("clave-del-respaldo")
            self.assertEqual(open(f"{tmp}/usuarios.db").read(), "ana:1:2030-01-01\n")
            self.assertIn("REINICIO", tg.mensajes)
            # un documento fuera de lugar no se toma
            b.manejar({"chat": {"id": 1}, "from": {"id": 7}, "message_id": 10, "document": {"file_id": "f1"}})
            self.assertNotIn("ki_clave", str(b.estado))
            if shutil.which("keytool"):
                btn("knew_si"); self.assertTrue(respaldo.clave_firma())
                btn("kexp"); txt("pass-export-1")
                enc = tg.docs[-1][1]; self.assertEqual(tg.docs[-1][0], "clave-firma.enc")
                viejo = open(respaldo.clave_firma()[0], "rb").read()
                shutil.rmtree(f"{tmp}/firma")
                tg.archivos["k"] = enc
                btn("kimp")
                b.manejar({"chat": {"id": 1}, "from": {"id": 7}, "message_id": 11, "document": {"file_id": "k", "file_size": len(enc)}})
                txt("pass-export-1")
                self.assertEqual(open(respaldo.clave_firma()[0], "rb").read(), viejo)

    def test_compilar_en_la_vps(self):
        import shutil
        import subprocess
        import local
        import respaldo
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            b.local_activo = True
            respaldo.FIRMA = f"{tmp}/firma"
            local.SRC = f"{tmp}/src"; local.CONTADOR = f"{tmp}/n"
            os.makedirs(f"{local.SRC}/android")
            open(f"{local.SRC}/android/servidores.txt", "w").write("[X]\n")
            subprocess.run(["git", "-C", local.SRC, "init", "-q"], check=True)
            respaldo.guardar_clave_firma(b"J" * 600, "pw")
            open(f"{tmp}/gradle", "w").write("#!/bin/bash\nD=" + local.SRC + "/android/app/build/outputs/apk/release; mkdir -p $D; echo APK > $D/a.apk\n")
            os.chmod(f"{tmp}/gradle", 0o755); local.GRADLE = f"{tmp}/gradle"
            local.actualizar_codigo = lambda: ""
            btn("app"); self.assertIn("resp", tg.datos_botones()); self.assertNotIn("aclave", tg.datos_botones())
            btn("acomp"); self.assertIn("esta VPS", tg.mensajes[-1])
            b.compilando.acquire(); b._compilar(1)
            self.assertEqual(tg.docs[-1][0], "zumo-vpn.apk")
            self.assertEqual(tg.docs[-1][1], b"APK\n")


if __name__ == "__main__":
    unittest.main()
