#!/usr/bin/env python3
"""Pruebas del bot sin Telegram ni root: altas (normal, HWID, temporal), fichas y compilación, con una Telegram falsa.
El bot ya no arma archivos .zs; zs.py queda solo como referencia del formato que todavía lee la app."""
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
    os.environ.update({"ZUMO_BOT_ENV": f"{tmp}/bot.env", "ZUMO_PASSWD": f"{tmp}/passwd",
                       "ZUMO_TEMPDB": f"{tmp}/temporales.db", "ZUMO_BORRADOR": f"{tmp}/borrar-temporal.sh",
                       "ZUMO_PDIRECT_ENV": f"{tmp}/pdirect.env",
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
        b = bot.Bot(tg, {7}, gh)
        self.n_msg = 100
        def txt(t):
            self.n_msg += 1
            b.manejar({"chat": {"id": 1}, "from": {"id": 7}, "text": t, "message_id": self.n_msg})
        btn = lambda d: b.manejar_cb({"id": "c", "from": {"id": 7}, "data": d, "message": {"chat": {"id": 1}, "message_id": 5}})
        return bot, tg, b, txt, btn

    def sistema_falso(self, bot, tmp, timer_ok=True):
        """Reemplaza useradd/chpasswd/systemd-run y la librería del panel por unos de mentira que anotan lo que se pidió."""
        cmds = []
        class R:
            def __init__(self, rc=0): self.returncode, self.stdout, self.stderr = rc, "", ""
        def run(*cmd, entrada=None):
            cmds.append((cmd, entrada))
            if cmd[0] == "id":
                return R(0 if any(l.startswith(cmd[1] + ":") for l in open(f"{tmp}/passwd")) else 1)
            if cmd[0] == "useradd":
                gecos = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
                open(f"{tmp}/passwd", "a").write(f"{cmd[-1]}:x:1001:1001:{gecos}:/home/x:/bin/false\n")
            if cmd[0] == "systemd-run" and not timer_ok:
                return R(1)
            return R(0)
        def lib(*a):
            if a[0] == "zumo_db_add":
                open(f"{tmp}/usuarios.db", "a").write(f"{a[1]}:{a[2]}:{a[3]}\n")
        open(f"{tmp}/passwd", "a").close(); open(f"{tmp}/usuarios.db", "a").close()
        bot.run, bot.bash_lib = run, lib
        return cmds

    def test_menu_ficha_y_datos_del_cliente(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            open(f"{tmp}/usuarios.db", "w").write("cliente1:2:2026-11-05\n")
            open(f"{tmp}/claves.db", "w").write("cliente1:Clave1\n")
            open(f"{tmp}/pdirect.env", "w").write("PDIRECT_PORT=80\nPDIRECT_BANNER=APP 02\n")
            # un extraño no entra
            b.manejar({"chat": {"id": 1}, "from": {"id": 99}, "text": "hola"})
            self.assertIn("No autorizado", tg.mensajes[-1])
            # cualquier texto abre el menú con botones: ya no hay "Servidor y payload"
            txt("hola")
            self.assertEqual(tg.datos_botones(), ["crear", "lista:0", "app", "id"])
            # la lista trae el botón de crear, y la ficha ya no ofrece el .zs
            btn("lista:0"); self.assertEqual(tg.datos_botones()[:2], ["crear", "u:cliente1"])
            btn("u:cliente1")
            self.assertIn("d:cliente1", tg.datos_botones()); self.assertNotIn("x:cliente1", tg.datos_botones())
            self.assertIn("k:cliente1", tg.datos_botones()); self.assertIn("r:cliente1", tg.datos_botones())
            # datos para el cliente: el mismo mensaje del panel, y ningún archivo
            btn("d:cliente1")
            self.assertEqual(tg.mensajes[-1], "👤 cliente1\n🔒 Clave1\n📅 05/11\n🔌 2 dispositivos\n📄 APP 02")
            self.assertEqual(tg.docs, [])
            # los botones de la versión anterior (enviar .zs, servidor) vuelven al menú sin romper nada
            for viejo in ("x:cliente1", "srv", "s:host"):
                btn(viejo); self.assertIn("¿Qué querés hacer?", tg.mensajes[-1])
            # límite por botón
            llamadas = []
            bot.bash_lib = lambda *a: llamadas.append(a)
            btn("ln:cliente1:3"); self.assertEqual(llamadas[-1], ("zumo_db_set", "cliente1", "2", "3"))

    def test_crear_usuario_paso_a_paso(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            cmds = self.sistema_falso(bot, tmp)
            btn("crear"); self.assertEqual(tg.datos_botones(), ["ct:n", "ct:h", "ct:t", "ct:th", "menu"])
            btn("ct:n")
            txt("1mal"); self.assertIn("inválido", tg.mensajes[-1])
            txt("pepe")
            txt("con espacio"); self.assertIn("inválida", tg.mensajes[-1])
            txt("Clave9")
            self.assertIn("cd:30", tg.datos_botones())
            btn("cd:otro"); txt("abc"); self.assertIn("Escribí un número", tg.mensajes[-1])
            txt("45")
            self.assertIn("cl:2", tg.datos_botones())
            btn("cl:2")
            vence = bot.date.today() + bot.timedelta(days=45)
            self.assertEqual(open(f"{tmp}/usuarios.db").read(), f"pepe:2:{vence.isoformat()}\n")
            self.assertEqual(open(f"{tmp}/claves.db").read(), "pepe:Clave9\n")
            self.assertIn((("chpasswd",), "pepe:Clave9\n"), cmds)
            self.assertFalse(any(c[0][0] == "systemd-run" for c in cmds))
            self.assertEqual(tg.docs, [])   # sin archivo .zs: van los datos en un mensaje
            self.assertIn(f"👤 pepe\n🔒 Clave9\n📅 {vence.strftime('%d/%m')}\n🔌 2 dispositivos\n📄 ZUMO", tg.mensajes)
            # repetir el nombre no deja seguir
            btn("crear"); btn("ct:n"); txt("pepe"); self.assertIn("ya existe", tg.mensajes[-1])
            # cancelar limpia el estado, y un botón de un alta que ya no está no rompe nada
            btn("menu"); self.assertNotIn(1, b.estado)
            btn("cl:2"); self.assertIn("quedó a medias", tg.mensajes[-1])

    def test_crear_hwid(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            cmds = self.sistema_falso(bot, tmp)
            btn("ct:h"); self.assertIn("nombre del cliente", tg.mensajes[-1])
            txt("Juan: Perez")
            txt("abc-123"); self.assertIn("HWID inválido", tg.mensajes[-1])       # quedan 6 caracteres
            txt("AB12-cd34 EF56")                                                 # se limpia como en el panel
            self.assertIn("HWID: AB12cd34EF56", tg.mensajes[-1]); self.assertIn("cd:30", tg.datos_botones())
            btn("cm:10"); self.assertIn("¿Qué querés hacer?", tg.mensajes[-1])     # minutos no va en un HWID por días
            btn("ct:h"); txt("Juan: Perez"); txt("AB12cd34EF56"); btn("cd:30")
            self.assertIn("lo habitual es 2", tg.mensajes[-1])
            btn("cl:2")
            vence = bot.date.today() + bot.timedelta(days=30)
            alta = next(c[0] for c in cmds if c[0][0] == "useradd")
            self.assertEqual(alta, ("useradd", "--badname", "-M", "-s", "/bin/false", "-e",
                                    (vence + bot.timedelta(days=1)).isoformat(), "-c", "hwid,Juan Perez", "AB12cd34EF56"))
            self.assertIn((("chpasswd",), "AB12cd34EF56:AB12cd34EF56\n"), cmds)    # usuario y clave son el HWID
            self.assertFalse(os.path.exists(f"{tmp}/claves.db"))
            self.assertEqual(open(f"{tmp}/usuarios.db").read(), f"AB12cd34EF56:2:{vence.isoformat()}\n")
            self.assertIn("🔐 DATOS DE ACCESO\n├ ☁️ Plan: Privado\n├ ⚙️ Máquina: ZUMO\n├ 👤 Usuario: Juan Perez\n"
                          f"├ ⏳ Vence: {vence.strftime('%d/%m/%Y')}", tg.mensajes)
            # el mismo HWID no entra dos veces
            btn("ct:h"); txt("Otro"); txt("AB12cd34EF56"); self.assertIn("ya está registrado", tg.mensajes[-1])
            # en la lista se ve el nombre del cliente; en la ficha, el HWID y no hay "Cambiar clave"
            btn("lista:0"); self.assertIn("🔑 Juan Perez", "".join(t for fila in tg.botones[-1] for t, _ in fila))
            btn("u:AB12cd34EF56")
            self.assertIn("Juan Perez (HWID)", tg.mensajes[-1]); self.assertIn("HWID: AB12cd34EF56", tg.mensajes[-1])
            self.assertNotIn("k:AB12cd34EF56", tg.datos_botones()); self.assertIn("r:AB12cd34EF56", tg.datos_botones())
            btn("k:AB12cd34EF56"); self.assertNotIn(1, b.estado)                   # un botón viejo no pide clave

    def test_crear_temporal(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            cmds = self.sistema_falso(bot, tmp)
            btn("ct:t"); txt("prueba"); txt("abc123")
            self.assertEqual(tg.datos_botones()[:3], ["cm:10", "cm:30", "cm:60"])
            btn("cm:otro"); txt("0"); self.assertIn("1 a 1440", tg.mensajes[-1])
            txt("5000"); self.assertIn("1 a 1440", tg.mensajes[-1])
            txt("30"); self.assertIn("30 minutos", tg.mensajes[-1])
            antes = int(bot.time.time())
            btn("cl:1")
            # vencimiento de papel a 2 días; el borrado lo hace el timer a los 30 minutos
            vence = bot.date.today() + bot.timedelta(days=2)
            self.assertEqual(open(f"{tmp}/usuarios.db").read(), f"prueba:1:{vence.isoformat()}\n")
            u, ep = open(f"{tmp}/temporales.db").read().strip().split(":")
            self.assertEqual(u, "prueba"); self.assertTrue(antes + 1800 <= int(ep) <= antes + 1805)
            timer = next(c[0] for c in cmds if c[0][0] == "systemd-run")
            self.assertEqual(timer, ("systemd-run", "--quiet", "--collect", "--unit=zumo-temp-prueba", "--on-active=30min",
                                     "--timer-property=AccuracySec=5s", f"{tmp}/borrar-temporal.sh", "prueba"))
            self.assertTrue(os.access(f"{tmp}/borrar-temporal.sh", os.X_OK))       # lo repone si faltaba
            self.assertEqual(__import__('subprocess').call(["bash", "-n", f"{tmp}/borrar-temporal.sh"]), 0)
            self.assertIn("👤 prueba\n🔒 abc123\n📅 30 minutos\n🔌 1 dispositivo\n📄 ZUMO", tg.mensajes)
            # lista y ficha: se ve cuánto le queda y no se puede renovar
            btn("lista:0"); self.assertIn("⏳ prueba · 30 min", "".join(t for fila in tg.botones[-1] for t, _ in fila))
            btn("u:prueba"); self.assertIn("se borra en 30 minutos", tg.mensajes[-1])
            self.assertNotIn("r:prueba", tg.datos_botones()); self.assertIn("b:prueba", tg.datos_botones())
            # borrarlo a mano frena el timer y lo saca de los temporales
            btn("bs:prueba")
            self.assertIn((("systemctl", "stop", "zumo-temp-prueba.timer"), None), cmds)
            self.assertEqual(open(f"{tmp}/temporales.db").read(), "")

    def test_crear_temporal_hwid_y_timer_que_falla(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            cmds = self.sistema_falso(bot, tmp, timer_ok=False)
            btn("ct:th"); txt("Demo"); txt("ZZ99yy88xx77")
            self.assertIn("cm:10", tg.datos_botones())
            btn("cd:30"); self.assertIn("¿Qué querés hacer?", tg.mensajes[-1])     # días no va en un temporal
            btn("ct:th"); txt("Demo"); txt("ZZ99yy88xx77"); btn("cm:1")            # no pregunta conexiones: 1, como el panel
            vence = bot.date.today() + bot.timedelta(days=2)
            self.assertEqual(open(f"{tmp}/usuarios.db").read(), f"ZZ99yy88xx77:1:{vence.isoformat()}\n")
            self.assertIn("-c", next(c[0] for c in cmds if c[0][0] == "useradd"))
            self.assertIn("ZZ99yy88xx77:", open(f"{tmp}/temporales.db").read())
            self.assertTrue(any("├ 👤 Usuario: Demo\n├ ⏳ Vence: 1 minuto" in m for m in tg.mensajes))
            # el timer no se pudo agendar: el usuario queda creado y se avisa
            self.assertTrue(any("creado (temporal, 1 minuto" in m for m in tg.mensajes))
            self.assertTrue(any("No se pudo agendar el borrado" in m for m in tg.mensajes))

    def test_renovar_y_cambiar_clave_mandan_los_datos(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            self.sistema_falso(bot, tmp)
            open(f"{tmp}/usuarios.db", "w").write("ana:1:2026-01-01\n")
            open(f"{tmp}/claves.db", "w").write("ana:vieja1\n")
            def lib(*a):
                if a[0] == "zumo_db_set" and a[2] == "3":
                    open(f"{tmp}/usuarios.db", "w").write(f"ana:1:{a[3]}\n")
            bot.bash_lib = lib
            btn("rd:ana:30")
            vence = bot.date.today() + bot.timedelta(days=30)
            self.assertEqual(tg.mensajes[-2], f"👤 ana\n🔒 vieja1\n📅 {vence.strftime('%d/%m')}\n🔌 1 dispositivo\n📄 ZUMO")
            btn("k:ana"); txt("nueva2")
            self.assertIn("🔒 nueva2", tg.mensajes[-2])
            self.assertEqual(tg.docs, [])

    def test_validaciones(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = cargar_bot(tmp)
            self.assertIn("inválido", bot.crear_usuario("1abc", "x", 5, 1))
            self.assertIn("inválida", bot.crear_usuario("abc", "con espacio", 5, 1))
            self.assertIn("HWID inválido", bot.crear_hwid("corto", "Juan", 5, 2))
            self.assertIn("HWID inválido", bot.crear_hwid("con-guion-1234", "Juan", 5, 2))
            self.assertEqual(bot.limpiar_etiqueta("  Ju:an\n "), "Juan")
            self.assertEqual(bot.limpiar_etiqueta(":"), "cliente")
            self.assertEqual((bot.texto_minutos(1), bot.texto_minutos(90)), ("1 minuto", "90 minutos"))
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


if __name__ == "__main__":
    unittest.main()
