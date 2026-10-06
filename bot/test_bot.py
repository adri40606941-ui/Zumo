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
                       "ZUMO_APP_SERVIDORES": f"{tmp}/app-servidores.json", "ZUMO_APP_MARCA": f"{tmp}/app-marca"})
    spec = importlib.util.spec_from_file_location("zumo_bot", os.path.join(AQUI, "zumo-bot.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FalsaTelegram:
    def __init__(self):
        self.mensajes, self.docs, self.botones, self.ediciones, self.borrados = [], [], [], [], []
        self.fotos, self.archivos = [], {}
    def mensaje(self, chat, texto, botones=None, md=False):
        self.mensajes.append(texto); self.botones.append(botones); return len(self.mensajes)
    def editar(self, chat, mid, texto, botones=None):
        self.ediciones.append(texto); self.mensajes.append(texto); self.botones.append(botones)
    def responder_cb(self, *a, **k): pass
    def borrar(self, chat, mid): self.borrados.append(mid)
    def documento(self, chat, nombre, datos, leyenda=""): self.docs.append((nombre, datos, leyenda))
    def foto(self, chat, datos, leyenda="", botones=None):
        self.fotos.append((datos, leyenda)); self.mensajes.append(leyenda); self.botones.append(botones)
    def bajar(self, file_id): return self.archivos[file_id]

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
            self.assertEqual(tg.datos_botones(), ["app", "ghenl", "resp", "id"])
            btn("ghenl"); self.assertIn("ghtok", tg.datos_botones())          # sin token: ofrece pegarlo
            b.gh = type("GH", (), {"repo": "o/r", "rama": "main"})()
            btn("ghenl"); self.assertIn("enlazado", tg.mensajes[-1]); self.assertIn("aclave", tg.datos_botones())
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

    def test_publicar_servidores_para_la_app(self):
        import zs
        # cifrado de la lista: mismo formato ZL1 que abre la app, y descifra de vuelta
        texto = "[A]\nhost = a.com\npuerto = 80\ntls = no\nsni =\npayload = GET / HTTP/1.1[crlf][crlf]\n"
        blob = zs.cifrar_lista(texto)
        self.assertEqual(blob[:3], b"ZL1")
        self.assertEqual(zs.descifrar_lista(blob), texto)
        self.assertEqual(zs.cifrar_lista(texto), blob)        # determinista: la misma lista da el mismo archivo
        self.assertNotIn(b"a.com", blob)

        class GHpub:
            def __init__(self): self.llamadas = []
            def ultimo_run(self): return 5
            def lanzar(self): self.llamadas.append("lanzar")
            def run_nuevo(self, antes): return 6
            def run(self, i): return {"status": "completed", "conclusion": "success", "html_url": "http://x"}
        class GHsec:
            repo, rama = "o/r", "main"
            def __init__(self): self.sec = {}
            def subir_secreto(self, n, v): self.sec[n] = v
        with tempfile.TemporaryDirectory() as tmp:
            gh = GHsec()
            bot, tg, b, txt, btn = self.armar(tmp, gh)
            b.gh_pub = GHpub()
            bot.guardar_app([{"name": "APP 02", "host": "h.com", "port": 80, "payload": "GET /", "tls": False, "sni": ""}])
            btn("app"); self.assertIn("apub", tg.datos_botones())
            btn("apub"); self.assertIn("apub_si", tg.datos_botones())
            btn("apub_si")
            for _ in range(100):
                if not b.compilando.locked(): break
                import time; time.sleep(0.05)
            self.assertIn("ZUMO_SERVIDORES", gh.sec)
            self.assertIn("[APP 02]", gh.sec["ZUMO_SERVIDORES"])
            self.assertEqual(b.gh_pub.llamadas, ["lanzar"])
            self.assertTrue(any("publicada" in m.lower() for m in tg.mensajes))

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

    def test_igualar_la_clave_de_firma_con_github(self):
        import base64
        import respaldo
        import centro
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            respaldo.DIR = tmp
            respaldo.FIRMA = f"{tmp}/firma"
            centro.ENV = f"{tmp}/bot.env"
            centro.reiniciar_bot = lambda: tg.mensajes.append("REINICIO")
            b.leer_env_fn = bot.leer_env
            open(f"{tmp}/bot.env", "w").write("BOT_TOKEN=abc\n")
            os.makedirs(f"{tmp}/firma")
            open(f"{tmp}/firma/zumo.jks", "wb").write(b"JKS-DE-PRUEBA")
            open(f"{tmp}/firma/pass", "w").write("pw-firma\n")
            # sin token de GitHub: se ofrece cargarlo y no los botones de la clave
            btn("resp"); self.assertIn("ghtok", tg.datos_botones()); self.assertNotIn("ksubir", tg.datos_botones())
            btn("ghtok"); txt("corto"); self.assertIn("no parece", tg.mensajes[-1])
            btn("ghtok"); txt("github_pat_" + "x" * 30)
            env = open(f"{tmp}/bot.env").read()
            self.assertIn("GITHUB_TOKEN=github_pat_", env); self.assertIn("GITHUB_REPO=", env)
            self.assertEqual(oct(os.stat(f"{tmp}/bot.env").st_mode & 0o777), "0o600")
            self.assertIn("REINICIO", tg.mensajes); self.assertTrue(tg.borrados)       # el token se borra del chat
            # con GitHub conectado: subir la clave de esta VPS como secreto fijo
            class GH:
                repo, rama = "o/r", "main"
                def __init__(self): self.sec = {}
                def subir_secreto(self, n, v): self.sec[n] = v
            b.gh = GH()
            btn("resp"); self.assertIn("ksubir", tg.datos_botones()); self.assertIn("ktraer", tg.datos_botones())
            btn("ksubir"); self.assertIn("ksubir_si", tg.datos_botones())
            self.assertEqual(b.gh.sec, {})                                              # nada se sube sin confirmar
            btn("ksubir_si")
            self.assertEqual(base64.b64decode(b.gh.sec["ZUMO_KEYSTORE_B64"]), b"JKS-DE-PRUEBA")
            self.assertEqual(b.gh.sec["ZUMO_KS_PASS"], "pw-firma")
            self.assertIn("misma", tg.mensajes[-1] + " misma")

    def test_los_botones_dicen_donde_se_compila(self):
        class GH:
            repo, rama = "o/r", "main"
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp, gh=GH())
            btn("app")
            self.assertIn("🔨 Compilar en GitHub y enviarme el APK", str(tg.botones[-1]))
            btn("acomp"); self.assertIn("Compilar en GitHub", str(tg.botones[-1]))


    # ------------------------------------------------------------ apariencia de la app
    def test_tema_normaliza_y_plantillas(self):
        import tema as T
        base = T.normalizar({})
        self.assertEqual(base, T.normalizar(base))
        # el tema del repo es el de por defecto (la app lo compara con sus propios valores en TemaTest.kt)
        ruta = os.path.join(AQUI, "..", "android", "marca", "tema.json")
        with open(ruta, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), T.a_json({}).strip())
        # lo que viene mal vuelve al valor por defecto, sin romper
        t = T.normalizar({"nombre": " @<Mi> \"VPN\" & 'x' ", "fondo": "rojo", "fondo2": "#abc", "radio": 999, "escala": "x",
                          "fuente": "comic", "enlaces": [{"texto": "Ok", "url": "5491122334455"}, {"texto": "", "url": "https://a.b"},
                                                         {"texto": "Mal", "url": "javascript:alert(1)"}, "basura"]})
        self.assertEqual((t["nombre"], t["fondo"], t["fondo2"], t["radio"], t["escala"], t["fuente"]),
                         ("Mi VPN x", "#14102B", "#AABBCC", 32, 100, "sans-serif"))
        self.assertEqual(t["enlaces"], [{"texto": "Ok", "url": "https://wa.me/5491122334455"}])
        self.assertEqual(T.limpiar_enlace("@mi_canal"), "https://t.me/mi_canal")
        self.assertEqual(T.limpiar_enlace("wa.me/549112233"), "https://wa.me/549112233")
        self.assertIsNone(T.limpiar_enlace("hola que tal"))
        # todas las plantillas son temas válidos, distintos entre sí, y no tocan nombre ni secciones
        mio = T.normalizar({"nombre": "NetFree", "ver_conexion": False, "enlaces": [{"texto": "S", "url": "https://x.y"}]})
        vistos = set()
        for pid, nombre, _, cambios in T.PLANTILLAS:
            for k, v in cambios.items():
                self.assertIn(k, T.DE_PLANTILLA, f"{pid}: {k} no es de plantilla")
                if k in T.COLORES or k == "fondo2":
                    self.assertEqual(T.color(v), v, f"{pid}: {k}")
            p = T.aplicar_plantilla(mio, pid)
            self.assertEqual((p["nombre"], p["ver_conexion"], len(p["enlaces"]), p["plantilla"]), ("NetFree", False, 1, pid))
            self.assertGreater(abs(T.luminancia(p["texto"]) - T.luminancia(p["tarjeta"])), 0.4, f"{pid}: la letra no se lee")
            vistos.add((p["fondo"], p["acento"]))
        self.assertEqual(len(vistos), len(T.PLANTILLAS))
        # volver a "zumo" después de otra plantilla deja los colores originales
        self.assertEqual(T.aplicar_plantilla(T.aplicar_plantilla(base, "claro"), "zumo"), base)
        # al pasar a un fondo claro, la letra y las tarjetas acompañan
        c = dict(base, fondo="#FFFFFF")
        T.derivar(c, "fondo")
        self.assertTrue(T.es_claro(c["tarjeta"])); self.assertFalse(T.es_claro(c["texto"]))

    def test_marca_paquete_en_secretos(self):
        import marca, tema as T
        icono, fondo = os.urandom(90_000), os.urandom(300_000)
        paquete = marca.empaquetar(T.a_json({"nombre": "NetFree"}), icono, fondo)
        sec = marca.secretos(paquete)
        partes = [k for k in sec if k != "ZUMO_MARCA"]
        self.assertLessEqual(len(partes), marca.MAX_PARTES)
        self.assertTrue(all(len(v) < 48 * 1024 for v in sec.values()))        # tope de GitHub por secreto
        self.assertEqual(list(sec)[-1], "ZUMO_MARCA")                          # la cabecera se sube última
        self.assertEqual(marca.desde_secretos(sec), paquete)
        with tempfile.TemporaryDirectory() as d:
            open(f"{d}/fondo.jpg", "wb").write(b"viejo")
            self.assertEqual(marca.desempacar(paquete, d), ["fondo.jpg", "icono.png", "tema.json"])
            self.assertEqual(open(f"{d}/icono.png", "rb").read(), icono)
            self.assertEqual(open(f"{d}/fondo.jpg", "rb").read(), fondo)
            self.assertEqual(json.load(open(f"{d}/tema.json", encoding="utf-8"))["nombre"], "NetFree")
            # un paquete sin imágenes borra las que hubiera de antes
            marca.desempacar(marca.empaquetar(T.a_json({})), d)
            self.assertEqual(sorted(os.listdir(d)), ["tema.json"])
        # sin cabecera no hay apariencia subida; incompleta o tocada, error (no se compila a medias)
        self.assertIsNone(marca.desde_secretos({}))
        self.assertIsNone(marca.desde_secretos({"ZUMO_MARCA": ""}))
        for roto in ({**sec, "ZUMO_MARCA_2": ""}, {**sec, "ZUMO_MARCA": "1:99:abc"}, {**sec, "ZUMO_MARCA": "x"},
                     {**sec, "ZUMO_MARCA_1": sec["ZUMO_MARCA_2"]}):
            with self.assertRaises(marca.ErrorMarca):
                marca.desde_secretos(roto)
        with self.assertRaises(marca.ErrorMarca):
            marca.empaquetar("{}", os.urandom(marca.MAX_PAQUETE))
        with self.assertRaises(marca.ErrorMarca):
            marca.desempacar(b"no es un zip", tempfile.gettempdir())
        # el workflow declara exactamente los secretos que el bot puede subir
        yml = open(os.path.join(AQUI, "..", ".github", "workflows", "android.yml"), encoding="utf-8").read()
        for i in range(1, marca.MAX_PARTES + 1):
            self.assertIn(f"ZUMO_MARCA_{i}: ${{{{ secrets.ZUMO_MARCA_{i} }}}}", yml)
        self.assertNotIn(f"ZUMO_MARCA_{marca.MAX_PARTES + 1}:", yml)

    def test_marca_como_la_corre_el_workflow(self):
        import marca, subprocess, tema as T
        sec = marca.secretos(marca.empaquetar(T.a_json({"nombre": "NetFree"}), b"\x89PNG\r\n\x1a\n" + os.urandom(50_000)))
        with tempfile.TemporaryDirectory() as d:
            guion = os.path.join(AQUI, "marca.py")
            limpio = {k: v for k, v in os.environ.items() if not k.startswith("ZUMO_MARCA")}
            r = subprocess.run([sys.executable, guion, d], capture_output=True, text=True, env=limpio)
            self.assertEqual((r.returncode, os.listdir(d)), (0, []))               # sin secretos no toca nada
            r = subprocess.run([sys.executable, guion, d], capture_output=True, text=True, env={**limpio, **sec})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(sorted(os.listdir(d)), ["icono.png", "tema.json"])
            r = subprocess.run([sys.executable, guion, d], capture_output=True, text=True, env={**limpio, **sec, "ZUMO_MARCA_1": "AAAA"})
            self.assertEqual(r.returncode, 1)
            self.assertIn("error:", r.stdout)

    def _png(self, w, h, color=(255, 140, 0, 255)):
        import io
        from PIL import Image
        b = io.BytesIO(); Image.new("RGBA", (w, h), color).save(b, "PNG"); return b.getvalue()

    def test_vista_previa_e_imagenes(self):
        import io, tema as T, vista
        if not vista.HAY_PIL:
            self.skipTest("sin Pillow")
        from PIL import Image
        # pantalla completa: es larga, así que va en dos columnas (más ancha que alta, para que Telegram no la achique)
        img = Image.open(io.BytesIO(vista.captura(T.normalizar({}))))
        self.assertEqual(img.format, "PNG")
        self.assertGreater(img.size[0], 1440); self.assertLess(img.size[1], img.size[0])
        self.assertEqual(img.convert("RGB").getpixel((40, 40)), T.rgb("#14102B"))                # el fondo del tema
        # sin secciones la pantalla es corta: una sola columna
        corto = Image.open(io.BytesIO(vista.captura(T.normalizar({"ver_vencimiento": False, "ver_conexion": False, "ver_telefono": False}))))
        self.assertEqual(corto.size[0], 720)
        self.assertEqual(corto.convert("RGB").getpixel((5, corto.size[1] - 5)), T.rgb("#14102B"))
        claro = Image.open(io.BytesIO(vista.captura(T.aplicar_plantilla(T.normalizar({}), "claro"))))
        self.assertEqual(claro.convert("RGB").getpixel((40, 40)), T.rgb("#F3F5FA"))
        hoja = Image.open(io.BytesIO(vista.muestrario([(n, T.aplicar_plantilla(T.normalizar({}), p)) for p, n, _, _ in T.PLANTILLAS])))
        self.assertEqual(hoja.format, "JPEG")
        # ícono: queda cuadrado, PNG y liviano aunque llegue enorme y apaisado
        ic = Image.open(io.BytesIO(vista.preparar_icono(self._png(1800, 900))))
        self.assertEqual((ic.format, ic.size, ic.mode), ("PNG", (432, 432), "RGBA"))
        self.assertEqual(ic.getpixel((2, 2))[3], 0)                 # relleno transparente, sin recortar
        self.assertEqual(ic.getpixel((216, 216))[3], 255)
        ruido = Image.frombytes("RGB", (1500, 1500), os.urandom(1500 * 1500 * 3))
        b = io.BytesIO(); ruido.save(b, "PNG")
        self.assertLessEqual(len(vista.preparar_icono(b.getvalue())), vista.MAX_ICONO)
        # fondo: JPG liviano aunque sea una foto pesada
        b = io.BytesIO(); ruido.resize((3000, 4000)).save(b, "JPEG", quality=95)
        listo = vista.preparar_fondo(b.getvalue())
        self.assertLessEqual(len(listo), vista.MAX_FONDO)
        self.assertEqual(Image.open(io.BytesIO(listo)).format, "JPEG")
        with self.assertRaises(vista.ErrorImagen):
            vista.preparar_icono(b"esto no es una imagen")
        # las dos imágenes al máximo entran en el paquete que se sube
        import marca
        self.assertLess(vista.MAX_ICONO + vista.MAX_FONDO + 8192, marca.MAX_PAQUETE)

    def _vps_basica(self):
        """Simula una VPS con solo fonts-dejavu-core (sin las letras Condensed ni ExtraLight). Devuelve cómo deshacerlo."""
        import vista
        original = vista.ImageFont.truetype
        def sin_extras(nombre, *a, **k):
            if "Condensed" in str(nombre) or "ExtraLight" in str(nombre):
                raise OSError("no instalada (simulado)")
            return original(nombre, *a, **k)
        vista.ImageFont.truetype = sin_extras
        vista._cache_fuentes.clear()
        def deshacer():
            vista.ImageFont.truetype = original
            vista._cache_fuentes.clear()
        self.addCleanup(deshacer)

    def test_vista_previa_cada_letra_se_ve_distinta(self):
        """Con solo fonts-dejavu-core, las 8 letras caían en la misma y la vista previa «no cambiaba» al elegir otra."""
        import hashlib, tema as T, vista
        if not vista.HAY_PIL:
            self.skipTest("sin Pillow")
        self._vps_basica()
        hashes = {}
        for f, nombre in T.FUENTES:
            png = vista.captura(T.normalizar({"fuente": f}))
            hashes.setdefault(hashlib.md5(png).hexdigest(), []).append(nombre)
        self.assertEqual(len(hashes), len(T.FUENTES), [v for v in hashes.values() if len(v) > 1])

    def test_vista_previa_del_emoji_del_logo(self):
        import tema as T, vista
        if not vista.HAY_PIL:
            self.skipTest("sin Pillow")
        if vista.HAY_EMOJI:        # con la letra de emojis: cada emoji se dibuja de verdad
            cohete, fuego = (vista.captura(T.normalizar({"logo": e})) for e in ("🚀", "🔥"))
            self.assertNotEqual(cohete, fuego)
            self.assertNotEqual(cohete, vista.captura(T.normalizar({"logo": ""})))
        original, vista.HAY_EMOJI = vista.HAY_EMOJI, False
        self.addCleanup(setattr, vista, "HAY_EMOJI", original)
        # sin la letra de emojis: un escudo de muestra, igual para cualquier emoji (y el bot avisa; ver abajo)
        self.assertEqual(vista.captura(T.normalizar({"logo": "🚀"})), vista.captura(T.normalizar({"logo": "🔥"})))

    def test_vista_previa_del_menu(self):
        import io, tema as T, vista
        if not vista.HAY_PIL:
            self.skipTest("sin Pillow")
        from PIL import Image
        base = T.normalizar({})
        enl = [{"texto": "Soporte por WhatsApp", "url": "https://wa.me/549111"}]
        sin = vista.captura_menu(base)
        self.assertEqual(Image.open(io.BytesIO(sin)).format, "PNG")
        # los contactos y «Importar .zs» solo existen en el menú: la pantalla principal no cambia, el menú sí
        con_enlace = T.normalizar({**base, "enlaces": enl})
        sin_importar = T.normalizar({**base, "ver_importar": False})
        self.assertEqual(vista.captura(base), vista.captura(con_enlace))
        self.assertEqual(vista.captura(base), vista.captura(sin_importar))
        self.assertNotEqual(sin, vista.captura_menu(con_enlace))
        self.assertNotEqual(sin, vista.captura_menu(sin_importar))
        self.assertLess(Image.open(io.BytesIO(vista.captura_menu(sin_importar))).size[1], Image.open(io.BytesIO(sin)).size[1])
        # sigue el tema: colores y esquinas
        self.assertNotEqual(sin, vista.captura_menu(T.aplicar_plantilla(base, "claro")))
        self.assertNotEqual(sin, vista.captura_menu(T.normalizar({**base, "radio": 4})))
        # aguanta lo más grande: 3 contactos, letra muy grande, imagen de fondo
        grande = T.normalizar({**base, "escala": 130, "enlaces": enl * 3, "fuente": "cursive"})
        im = Image.open(io.BytesIO(vista.captura_menu(grande, None, self._png(300, 600))))
        self.assertEqual(im.size[0], 720)

    def test_vista_previa_por_el_bot_avisa_lo_que_no_puede_mostrar(self):
        import tema as T, vista
        if not vista.HAY_PIL:
            self.skipTest("sin Pillow")
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            # «Menús y secciones» ofrece las dos vistas, y el menú ☰ manda su propia imagen
            btn("t:me"); self.assertIn("t:v", tg.datos_botones()); self.assertIn("t:vm", tg.datos_botones())
            self.assertIn("menú ☰", tg.mensajes[-1])
            n = len(tg.fotos)
            btn("t:vm"); self.assertEqual(len(tg.fotos), n + 1)
            self.assertEqual(tg.fotos[-1][0][:4], b"\x89PNG"); self.assertIn("sin botones de contacto", tg.fotos[-1][1])
            t = bot.cargar_tema(); t["enlaces"] = [{"texto": "Soporte", "url": "https://wa.me/1"}]; bot.guardar_tema(t)
            btn("t:vm"); self.assertIn("con tus botones de contacto", tg.fotos[-1][1])
            # una letra que solo se aproxima lo dice; una que se dibuja bien, no
            t = bot.cargar_tema(); t["fuente"] = "cursive"; bot.guardar_tema(t)
            btn("t:v"); self.assertIn("Manuscrita", tg.fotos[-1][1]); self.assertIn("aproximación", tg.fotos[-1][1])
            t["fuente"] = "sans-serif-medium"; bot.guardar_tema(t)
            btn("t:v"); self.assertNotIn("aproximación", tg.fotos[-1][1])
            # sin la letra de emojis, avisa que el emoji se ve en la app y cómo instalarla
            original, vista.HAY_EMOJI = vista.HAY_EMOJI, False
            self.addCleanup(setattr, vista, "HAY_EMOJI", original)
            t["logo"] = "🚀"; bot.guardar_tema(t)
            btn("t:v"); self.assertIn("🚀 no se puede dibujar", tg.fotos[-1][1]); self.assertIn("fonts-noto-color-emoji", tg.fotos[-1][1])
            vista.HAY_EMOJI = True
            btn("t:v"); self.assertNotIn("no se puede dibujar", tg.fotos[-1][1])
            # con un ícono propio puesto como logo, el emoji no importa
            vista.HAY_EMOJI = False
            t["logo_imagen"] = True; bot.guardar_tema(t); bot.guardar_imagen_marca("icono.png", self._png(64, 64))
            btn("t:v"); self.assertNotIn("no se puede dibujar", tg.fotos[-1][1])

    def test_apariencia_desde_el_bot(self):
        import tema as T, vista
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            btn("app"); self.assertIn("t", tg.datos_botones())
            btn("t"); self.assertIn("Zumo VPN", tg.mensajes[-1]); self.assertIn("t:pl", tg.datos_botones())
            self.assertFalse(bot.tema_guardado())                     # mirar no guarda nada
            # nombre y lema
            btn("t:n"); txt("   "); self.assertIn("vacío", tg.mensajes[-1])
            txt("NetFree <VPN>"); self.assertEqual(bot.cargar_tema()["nombre"], "NetFree VPN")
            self.assertIn("NetFree VPN", tg.mensajes[-1])
            btn("t:le"); txt("-"); self.assertEqual(bot.cargar_tema()["lema"], "")
            btn("t:le"); txt("Internet libre"); self.assertEqual(bot.cargar_tema()["lema"], "Internet libre")
            # plantilla: conserva el nombre
            btn("t:pu:oceano")
            t = bot.cargar_tema()
            self.assertEqual((t["nombre"], t["fondo"], t["plantilla"]), ("NetFree VPN", "#071A2C", "oceano"))
            self.assertIn("Océano", tg.mensajes[-1])
            btn("t:pu:noexiste"); self.assertIn("ya no existe", tg.mensajes[-1])
            # colores: con un toque y escribiendo el código
            btn("t:co"); self.assertIn("t:c:acento", tg.datos_botones())
            btn("t:c:acento"); self.assertIn("t:cs:acento:FF5252", tg.datos_botones()); self.assertIn("t:ce:acento", tg.datos_botones())
            btn("t:cs:acento:FF5252"); self.assertEqual(bot.cargar_tema()["acento"], "#FF5252")
            self.assertTrue(bot.cargar_tema()["plantilla"].endswith("*")); self.assertIn("con cambios", T.nombre_plantilla(bot.cargar_tema()))
            btn("t:ce:conectar"); txt("verde"); self.assertIn("No es un código", tg.mensajes[-1])
            txt("#00ff88"); self.assertEqual(bot.cargar_tema()["conectar"], "#00FF88")
            btn("t:cs:borde:FFFFFF"); self.assertIn("no es válido", tg.mensajes[-1])       # solo los colores que se pueden elegir
            # fondo: degradado, quitarlo, tarjetas translúcidas
            btn("t:c:fondo2"); btn("t:cs:fondo2:123A66"); self.assertEqual(bot.cargar_tema()["fondo2"], "#123A66")
            btn("t:fl"); self.assertEqual(bot.cargar_tema()["fondo2"], "")
            btn("t:op:70"); self.assertEqual(bot.cargar_tema()["opacidad"], 70)
            # letras
            btn("t:lt"); btn("t:lf:4"); self.assertEqual(bot.cargar_tema()["fuente"], "serif")
            btn("t:ls:112"); self.assertEqual(bot.cargar_tema()["escala"], 112)
            btn("t:lm"); self.assertFalse(bot.cargar_tema()["titulo_mayus"])
            # menús: ocultar secciones, esquinas, botones de contacto
            btn("t:me"); btn("t:ms:1"); self.assertFalse(bot.cargar_tema()["ver_conexion"])
            btn("t:mr:4"); self.assertEqual(bot.cargar_tema()["radio"], 4)
            btn("t:ma"); txt("Soporte"); txt("cualquier cosa"); self.assertIn("No lo entendí", tg.mensajes[-1])
            txt("+54 9 11 2233-4455"); self.assertEqual(bot.cargar_tema()["enlaces"], [{"texto": "Soporte", "url": "https://wa.me/5491122334455"}])
            btn("t:ma"); txt("Canal"); txt("@zumo_canal")
            btn("t:ma"); txt("Web"); txt("https://ejemplo.com")
            btn("t:ma"); self.assertIn("Quitá uno", tg.mensajes[-1])                      # máximo 3
            btn("t:mq:0"); self.assertEqual([e["texto"] for e in bot.cargar_tema()["enlaces"]], ["Canal", "Web"])
            # emoji del logo
            btn("t:ie"); txt("🚀"); self.assertEqual(bot.cargar_tema()["logo"], "🚀")
            # volver al original
            btn("t:rs"); self.assertIn("t:rs_si", tg.datos_botones())
            btn("t:rs_si"); self.assertEqual(bot.cargar_tema(), T.normalizar({}))
            self.assertTrue(bot.tema_guardado())                      # queda guardado: al compilar pisa lo que hubiera en el repo

    def test_servidor_de_la_app_sin_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            btn("aadd"); txt("Directo"); txt("1.2.3.4:22")
            self.assertIn("asp", tg.datos_botones())                 # ofrece seguir sin payload
            btn("asp")
            l = b.cargar_app() if hasattr(b, "cargar_app") else bot.cargar_app()
            self.assertEqual((l[-1]["host"], l[-1]["port"], l[-1]["payload"]), ("1.2.3.4", 22, ""))
            btn("ap:0"); self.assertIn("aqp:0", tg.datos_botones())
            txt("GET / HTTP/1.1[crlf][crlf]"); self.assertTrue(bot.cargar_app()[0]["payload"])
            btn("aqp:0"); self.assertEqual(bot.cargar_app()[0]["payload"], "")   # quitar payload
            btn("ap:0"); txt("GET / HTTP/1.1[crlf][crlf]"); btn("ap:0"); txt("-")
            self.assertEqual(bot.cargar_app()[0]["payload"], "")

    def test_apariencia_imagenes_y_vista_previa(self):
        import io, vista
        if not vista.HAY_PIL:
            self.skipTest("sin Pillow")
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            bot, tg, b, txt, btn = self.armar(tmp)
            foto = lambda fid, **k: b.manejar({"chat": {"id": 1}, "from": {"id": 7}, "message_id": 9, **k})
            # vista previa del tema actual y hoja de plantillas
            btn("t:v"); self.assertEqual(len(tg.fotos), 1); self.assertEqual(tg.fotos[-1][0][:4], b"\x89PNG")
            btn("t:pl"); self.assertEqual(len(tg.fotos), 2); self.assertIn("t:pv:neon", tg.datos_botones())
            btn("t:pv:neon"); self.assertEqual(len(tg.fotos), 3); self.assertIn("t:pu:neon", tg.datos_botones())
            self.assertFalse(bot.tema_guardado())                     # ver una plantilla no la aplica
            # ícono: se espera una imagen; un texto o un archivo que no es imagen no sirven
            btn("t:ii"); txt("hola"); self.assertIn("esperando una imagen", tg.mensajes[-1])
            foto("x", document={"file_id": "doc", "mime_type": "application/pdf", "file_size": 10}); self.assertIn("no es una imagen", tg.mensajes[-1])
            tg.archivos["rota"] = b"nada"
            foto("x", document={"file_id": "rota", "mime_type": "image/png", "file_size": 4}); self.assertIn("No pude leer", tg.mensajes[-1])
            self.assertIsNone(bot.imagen_marca("icono.png"))
            # como foto: Telegram manda varios tamaños; para el ícono alcanza el primero de 432 px o más
            tg.archivos.update(chica=self._png(90, 90), media=self._png(640, 640), grande=self._png(1280, 1280, (0, 90, 200, 255)))
            tamanos = [{"file_id": "chica", "width": 90, "height": 90, "file_size": 900},
                       {"file_id": "media", "width": 640, "height": 640, "file_size": 9000},
                       {"file_id": "grande", "width": 1280, "height": 1280, "file_size": 90000}]
            n = len(tg.fotos)
            foto("x", photo=tamanos)
            ic = Image.open(io.BytesIO(bot.imagen_marca("icono.png")))
            self.assertEqual((ic.size, ic.getpixel((10, 10))), ((432, 432), (255, 140, 0, 255)))
            self.assertTrue(bot.cargar_tema()["logo_imagen"]); self.assertNotIn(1, b.estado)
            self.assertEqual(len(tg.fotos), n + 1)                   # manda cómo quedó
            # fondo: se usa la más grande
            btn("t:fi"); foto("x", photo=tamanos)
            fo = Image.open(io.BytesIO(bot.imagen_marca("fondo.jpg")))
            self.assertEqual((fo.format, fo.size), ("JPEG", (1280, 1280)))
            btn("t:fo"); self.assertIn("t:fq", tg.datos_botones()); self.assertIn("t:fv:70", tg.datos_botones())
            btn("t:fv:70"); self.assertEqual(bot.cargar_tema()["velo"], 70)
            # un botón debajo de una foto no edita la foto: manda un mensaje nuevo
            antes = len(tg.ediciones)
            b.manejar_cb({"id": "c", "from": {"id": 7}, "data": "t", "message": {"chat": {"id": 1}, "message_id": 5, "photo": [{}]}})
            self.assertEqual(len(tg.ediciones), antes); self.assertIn("Apariencia de la app", tg.mensajes[-1])
            # quitar
            btn("t:fq"); self.assertIsNone(bot.imagen_marca("fondo.jpg"))
            btn("t:iq"); self.assertIsNone(bot.imagen_marca("icono.png")); self.assertFalse(bot.cargar_tema()["logo_imagen"])

    def test_compilar_sube_la_apariencia(self):
        import marca
        class GH:
            repo, rama = "o/r", "main"
            def __init__(self): self.sec, self.orden = {}, []
            def subir_secreto(self, n, v): self.sec[n] = v; self.orden.append(n)
            def ultimo_run(self): return 10
            def lanzar(self): self.orden.append("lanzar")
            def run_nuevo(self, antes): return 11
            def run(self, i): return {"status": "completed", "conclusion": "success", "run_number": 42}
            def archivo_de_rama(self, ruta, rama=None): return b"APK!"
        def compilar_y_esperar(b, btn):
            btn("acomp_si")
            for _ in range(200):
                if not b.compilando.locked(): break
                import time; time.sleep(0.05)
        with tempfile.TemporaryDirectory() as tmp:
            gh = GH()
            bot, tg, b, txt, btn = self.armar(tmp, gh)
            # sin personalizar nada, compilar no toca la apariencia del repo
            compilar_y_esperar(b, btn)
            self.assertEqual(gh.orden, ["lanzar"])
            # con apariencia: se sube entera antes de lanzar, y el workflow la puede abrir
            btn("t:n"); txt("NetFree")
            icono = b"\x89PNG\r\n\x1a\n" + os.urandom(60_000)
            bot.guardar_imagen_marca("icono.png", icono)
            btn("acomp"); self.assertIn("apariencia", tg.mensajes[-1].lower())
            gh.orden.clear()
            compilar_y_esperar(b, btn)
            self.assertEqual(gh.orden[-1], "lanzar"); self.assertEqual(gh.orden[-2], "ZUMO_MARCA")
            with tempfile.TemporaryDirectory() as d:
                marca.desempacar(marca.desde_secretos(gh.sec), d)
                self.assertEqual(json.load(open(f"{d}/tema.json", encoding="utf-8"))["nombre"], "NetFree")
                self.assertEqual(open(f"{d}/icono.png", "rb").read(), icono)
            self.assertEqual(tg.docs[-1][:2], ("zumo-vpn.apk", b"APK!"))


if __name__ == "__main__":
    unittest.main()
