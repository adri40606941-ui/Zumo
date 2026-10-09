"""Pantallas del bot para 🧑‍💼 Revendedores (con una Telegram falsa y una lista de máquinas de mentira)."""
import os
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import maquinas as mq  # noqa: E402
from test_bot import FalsaTelegram, cargar_bot  # noqa: E402

MAQ = {"id": "m1", "nombre": "app01", "host": "1.2.3.4", "puerto": 22, "usuario": "root", "clave": "x", "huella": ""}
MAQ2 = {"id": "m2", "nombre": "app02", "host": "5.6.7.8", "puerto": 22, "usuario": "root", "clave": "x", "huella": ""}


class Base(unittest.TestCase):
    def armar(self, maquinas=(MAQ,), env="ZUMO_DOMINIO=bot.ejemplo.com\n"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(f"{tmp.name}/bot.env", "w") as f:
            f.write(env)
        self.bot = cargar_bot(tmp.name)
        self.tg = FalsaTelegram()
        self.b = self.bot.Bot(self.tg, {7}, None)
        viejos = (mq.cargar, mq.buscar)
        self.addCleanup(lambda: (setattr(mq, "cargar", viejos[0]), setattr(mq, "buscar", viejos[1])))
        mq.cargar = lambda: list(maquinas)
        mq.buscar = lambda i: next((m for m in maquinas if m["id"] == i), None)
        self.btn = lambda d: self.b.manejar_cb({"id": "c", "from": {"id": 7}, "data": d,
                                                "message": {"chat": {"id": 1}, "message_id": 5}})
        self.n = 100
        return tmp.name

    def texto(self, t):
        self.n += 1
        self.b.manejar({"chat": {"id": 1}, "from": {"id": 7}, "text": t, "message_id": self.n})

    def alta(self, usuario="juan", clave="secreto1"):
        self.btn("rv_add")
        self.texto(usuario)
        self.texto(clave)
        self.btn("rvm:m1")
        return self.b.revs.listar()[0]["id"]


class Pantallas(Base):
    def test_el_menu_tiene_revendedores(self):
        self.armar()
        self.b.menu(1)
        self.assertIn("rv", self.tg.datos_botones())

    def test_lista_vacia_y_muestra_el_enlace_del_panel(self):
        self.armar()
        self.btn("rv")
        self.assertIn("Todavía no tenés revendedores", self.tg.mensajes[-1])
        self.assertIn("https://bot.ejemplo.com/r", self.tg.mensajes[-1])
        self.assertIn("rv_add", self.tg.datos_botones())

    def test_sin_dominio_avisa(self):
        self.armar(env="")
        self.btn("rv")
        self.assertIn("falta el dominio", self.tg.mensajes[-1])

    def test_alta_con_usuario_y_contrasena_elegidos(self):
        self.armar()
        rid = self.alta("juan", "secreto1")
        r = self.b.revs.buscar(rid)
        self.assertEqual((r["usuario"], r["maquina"], r["activo"]), ("juan", "m1", True))
        self.assertIsNotNone(self.b.revs.verificar("juan", "secreto1"))
        self.assertIn("Usuario: juan", self.tg.mensajes[-1])
        self.assertIn("Contraseña: secreto1", self.tg.mensajes[-1])
        self.assertIn("https://bot.ejemplo.com/r", self.tg.mensajes[-1])
        self.assertIn(self.n, self.tg.borrados, "la contraseña se borra del chat")

    def test_alta_con_contrasena_generada(self):
        self.armar()
        self.btn("rv_add")
        self.texto("pepe")
        self.btn("rvg")
        self.btn("rvm:m1")
        clave = self.tg.mensajes[-1].split("Contraseña: ")[1].split("\n")[0]
        self.assertIsNotNone(self.b.revs.verificar("pepe", clave))

    def test_usuario_invalido_o_repetido(self):
        self.armar()
        self.alta("juan")
        self.btn("rv_add")
        self.texto("Pepe Gómez")
        self.assertIn("inválido", self.tg.mensajes[-1])
        self.texto("juan")
        self.assertIn("ya existe", self.tg.mensajes[-1])

    def test_contrasena_corta_se_pide_de_nuevo(self):
        self.armar()
        self.btn("rv_add")
        self.texto("juan")
        self.texto("123")
        self.assertIn("inválida", self.tg.mensajes[-1])
        self.assertEqual(self.b.revs.listar(), [])

    def test_sin_maquinas_manda_a_agregar_una(self):
        self.armar(maquinas=())
        self.btn("rv_add")
        self.texto("juan")
        self.texto("secreto1")
        self.assertIn("ninguna VPS enlazada", self.tg.mensajes[-1])
        self.assertIn("maq", self.tg.datos_botones())

    def test_ficha_muestra_las_tres_monedas(self):
        self.armar()
        rid = self.alta()
        self.btn(f"rv:{rid}")
        t = self.tg.mensajes[-1]
        for linea in ("Bronce (7 días): 0", "Plata (15 días): 0", "Oro (30 días): 0", "app01 (1.2.3.4)"):
            self.assertIn(linea, t)
        self.assertEqual([d for d in self.tg.datos_botones() if d.startswith("rvc")],
                         [f"rvc:{rid}:bronce", f"rvc:{rid}:plata", f"rvc:{rid}:oro"])


class Monedas(Base):
    def test_botones_rapidos_suman(self):
        self.armar()
        rid = self.alta()
        self.btn(f"rvc:{rid}:plata")
        self.assertIn(f"rvn:{rid}:plata:10", self.tg.datos_botones())
        self.btn(f"rvn:{rid}:plata:10")
        self.assertEqual(self.b.revs.buscar(rid)["monedas"], {"bronce": 0, "plata": 10, "oro": 0})
        self.assertIn("+10 🥈 plata. Ahora tiene 10", self.tg.mensajes[-1])

    def test_escribiendo_el_numero(self):
        self.armar()
        rid = self.alta()
        self.btn(f"rvc:{rid}:oro")
        self.texto("7")
        self.assertEqual(self.b.revs.buscar(rid)["monedas"]["oro"], 7)

    def test_quitar_con_negativo_sin_bajar_de_cero(self):
        self.armar()
        rid = self.alta()
        self.b.revs.agregar_monedas(rid, "bronce", 3)
        self.btn(f"rvc:{rid}:bronce")
        self.texto("-2")
        self.assertEqual(self.b.revs.buscar(rid)["monedas"]["bronce"], 1)
        self.btn(f"rvc:{rid}:bronce")
        self.texto("-5")
        self.assertEqual(self.b.revs.buscar(rid)["monedas"]["bronce"], 1)
        self.assertIn("Solo tiene 1", self.tg.mensajes[-1])

    def test_numero_invalido(self):
        self.armar()
        rid = self.alta()
        for t in ("abc", "0", "5000", "1.5"):
            self.btn(f"rvc:{rid}:oro")
            self.texto(t)
            self.assertIn("número entero", self.tg.mensajes[-1])
        self.assertEqual(self.b.revs.buscar(rid)["monedas"]["oro"], 0)

    def test_movimientos(self):
        self.armar()
        rid = self.alta()
        self.btn(f"rvn:{rid}:oro:5")
        self.btn(f"rvh:{rid}")
        self.assertIn("+5 🥇 oro (admin)", self.tg.mensajes[-1])


class Gestion(Base):
    def test_cambiar_contrasena(self):
        self.armar()
        rid = self.alta()
        self.btn(f"rvk:{rid}")
        self.texto("otra-clave-1")
        self.assertIsNone(self.b.revs.verificar("juan", "secreto1"))
        self.assertIsNotNone(self.b.revs.verificar("juan", "otra-clave-1"))
        self.assertIn("Contraseña: otra-clave-1", self.tg.mensajes[-1])
        self.assertIn(self.n, self.tg.borrados)

    def test_contrasena_generada_al_cambiar(self):
        self.armar()
        rid = self.alta()
        self.btn(f"rvkg:{rid}")
        clave = self.tg.mensajes[-1].split("Contraseña: ")[1].split("\n")[0]
        self.assertIsNotNone(self.b.revs.verificar("juan", clave))

    def test_bloquear_y_activar_acceso(self):
        self.armar()
        rid = self.alta()
        self.btn(f"rvb:{rid}")
        self.assertIsNone(self.b.revs.verificar("juan", "secreto1"))
        self.assertIn("bloqueado", self.tg.mensajes[-1])
        self.btn(f"rvb:{rid}")
        self.assertIsNotNone(self.b.revs.verificar("juan", "secreto1"))

    def test_agregar_y_quitar_vps(self):
        self.armar(maquinas=(MAQ, MAQ2))
        rid = self.alta()
        self.btn(f"rvmq:{rid}")
        self.assertIn(f"rvmq+:{rid}", self.tg.datos_botones())
        self.assertNotIn(f"rvmq-:{rid}:m1", self.tg.datos_botones(), "con una sola VPS no se puede quitar")
        self.btn(f"rvmq+:{rid}")
        self.assertIn(f"rvmq2:{rid}:m2", self.tg.datos_botones())
        self.assertNotIn(f"rvmq2:{rid}:m1", self.tg.datos_botones(), "la que ya tiene no se ofrece")
        self.btn(f"rvmq2:{rid}:m2")
        self.assertEqual(self.b.revs.buscar(rid)["maquinas"], ["m1", "m2"])
        self.assertIn("app01", self.tg.mensajes[-1])
        self.assertIn("app02", self.tg.mensajes[-1])
        self.btn(f"rvmq-:{rid}:m1")
        self.assertEqual(self.b.revs.buscar(rid)["maquinas"], ["m2"])
        self.btn(f"rvmq-:{rid}:m2")
        self.assertEqual(self.b.revs.buscar(rid)["maquinas"], ["m2"], "tiene que quedarle una")
        self.assertIn("al menos una", self.tg.mensajes[-1])

    def test_ficha_muestra_todas_las_vps(self):
        self.armar(maquinas=(MAQ, MAQ2))
        rid = self.alta()
        self.b.revs.agregar_maquina(rid, "m2")
        self.btn(f"rv:{rid}")
        self.assertIn("app01", self.tg.mensajes[-1])
        self.assertIn("app02", self.tg.mensajes[-1])
        self.assertIn(f"rvu:{rid}", self.tg.datos_botones())

    def test_lista_de_usuarios_con_su_vps(self):
        self.armar(maquinas=(MAQ, MAQ2))
        rid = self.alta()
        self.b.revs.agregar_maquina(rid, "m2")
        self.b.revs.registrar_cuenta("ABCD1234", rid, "Ana", 7, "bronce", maq=["m1", "m2"])
        self.b.revs.registrar_cuenta("EFGH5678", rid, "Beto", 7, "bronce", maq=["m1"])
        self.btn("rvu")
        t = self.tg.mensajes[-1]
        self.assertIn("juan", t)
        self.assertIn("Ana · ABCD1234", t)
        self.assertIn("app01, app02", t)
        self.assertIn("Beto · EFGH5678", t)
        self.btn(f"rvu:{rid}")
        self.assertIn("Ana", self.tg.mensajes[-1])

    def test_lista_larga_se_manda_como_archivo(self):
        self.armar()
        rid = self.alta()
        for i in range(80):
            self.b.revs.registrar_cuenta(f"TOKEN{i:05d}", rid, "Cliente número " + str(i), 7, "bronce", maq=["m1"])
        self.btn("rvu")
        self.assertEqual(self.tg.docs[-1][0], "usuarios-revendedores.txt")
        self.assertIn(b"TOKEN00079", self.tg.docs[-1][1])

    def test_lista_sin_usuarios(self):
        self.armar()
        self.btn("rvu")
        self.assertIn("Todavía no hay usuarios", self.tg.mensajes[-1])

    def test_resumen_del_mes(self):
        self.armar()
        rid = self.alta()
        self.b.revs.agregar_monedas(rid, "oro", 5)
        self.b.revs.agregar_monedas(rid, "bronce", 3)
        self.b.revs.anotar(rid, "crear", "ABCD1234", 30, "oro")
        self.b.revs.anotar(rid, "renovar", "ABCD1234", 30, "oro")
        self.btn("rvs")
        t = self.tg.mensajes[-1]
        self.assertIn("juan", t)
        self.assertIn("Cargadas 🥉3 🥈0 🥇5", t)
        self.assertIn("Gastadas 🥉0 🥈0 🥇2", t)
        self.assertIn("TOTAL", t)
        self.assertIn("rvs:prev", self.tg.datos_botones())
        self.btn("rvs:prev")
        self.assertIn("Cargadas 🥉0 🥈0 🥇0", self.tg.mensajes[-1], "el mes pasado no tiene nada")

    def test_pantalla_https_explica_cloudflare(self):
        self.armar()
        self.b.estado_https = lambda: (True, True)
        self.btn("rvt")
        t = self.tg.mensajes[-1]
        self.assertIn("Completo", t)
        self.assertIn("Flexible", t)
        self.assertIn("✅ Puerto 443", t)
        self.b.estado_https = lambda: (False, False)
        self.btn("rvt")
        self.assertIn("⚠️ Puerto 443", self.tg.mensajes[-1])

    def test_avisar_admins_les_llega_a_todos(self):
        self.armar()
        import threading
        self.b.admins = {7, 8}
        enviados = []
        self.tg.mensaje = lambda chat, texto, botones=None, md=False: enviados.append((chat, texto))
        self.b.avisar_admins("hola")
        for h in threading.enumerate():
            if h is not threading.current_thread() and h.daemon:
                h.join(2)
        self.assertEqual(sorted(enviados), [(7, "hola"), (8, "hola")])

    def test_copia_local_diaria_y_se_guardan_14(self):
        tmp = self.armar()
        rid = self.alta()
        import revendedores_bot as rb
        carpeta = os.path.join(tmp, "copias")
        rb.RESPALDOS = carpeta
        ruta = self.b.copia_local_revendedores()
        self.assertTrue(os.path.exists(ruta))
        self.assertEqual(oct(os.stat(ruta).st_mode)[-3:], "600")
        for d in range(1, 20):
            open(os.path.join(carpeta, f"revendedores-2020{d:04d}.json"), "w").close()
        self.b.copia_local_revendedores()
        n = [f for f in os.listdir(carpeta) if f.startswith("revendedores-")]
        self.assertEqual(len(n), 14)
        self.assertIn(os.path.basename(ruta), n, "la de hoy no se borra")

    def test_vigilancia_avisa_cuando_cae_una_vps_y_cuando_vuelve(self):
        self.armar(maquinas=(MAQ, MAQ2))
        rid = self.alta()
        self.b.revs.agregar_maquina(rid, "m2")
        avisos = []
        self.b.avisar_admins = avisos.append
        viva = {"m2": False}

        def correr(m, cmd, timeout=20):
            if m["id"] == "m2" and not viva["m2"]:
                raise OSError("sin red")
            return ""
        viejo = mq.correr
        mq.correr = correr
        self.addCleanup(lambda: setattr(mq, "correr", viejo))
        self.b._vigilar_vps()
        self.assertEqual(avisos, [], "una sola falla no alcanza")
        self.b._vigilar_vps()
        self.assertEqual(len(avisos), 1)
        self.assertIn("app02", avisos[0])
        self.assertIn("juan", avisos[0])
        self.b._vigilar_vps()
        self.assertEqual(len(avisos), 1, "no repite el aviso")
        viva["m2"] = True
        self.b._vigilar_vps()
        self.assertEqual(len(avisos), 2)
        self.assertIn("volvió", avisos[1])

    def test_aviso_sin_contrasena_de_respaldo_una_vez_por_semana(self):
        tmp = self.armar()
        import revendedores_bot as rb
        rb.RESPALDOS = os.path.join(tmp, "copias2")
        os.makedirs(rb.RESPALDOS)
        avisos = []
        self.b.avisar_admins = avisos.append
        self.b.leer_env_fn = lambda: {}
        self.b._aviso_sin_clave()
        self.b._aviso_sin_clave()
        self.assertEqual(len(avisos), 1)
        self.assertIn("contraseña de respaldo", avisos[0])
        self.b.leer_env_fn = lambda: {"RESPALDO_PASS": "algo-largo"}
        os.utime(os.path.join(rb.RESPALDOS, "aviso-sin-clave"), (0, 0))
        self.b._aviso_sin_clave()
        self.assertEqual(len(avisos), 1, "con contraseña no avisa")

    def test_eliminar_pide_confirmacion(self):
        self.armar()
        rid = self.alta()
        self.btn(f"rvx:{rid}")
        self.assertIsNotNone(self.b.revs.buscar(rid))
        self.assertIn(f"rvxx:{rid}", self.tg.datos_botones())
        self.btn(f"rvxx:{rid}")
        self.assertIsNone(self.b.revs.buscar(rid))

    def test_boton_de_revendedor_que_ya_no_existe(self):
        self.armar()
        for acc in ("rv:99", "rvk:99", "rvb:99", "rvx:99", "rvh:99", "rvc:99:oro", "rvmq:99"):
            self.btn(acc)
            self.assertIn("ya no existe", self.tg.mensajes[-1], acc)

    def test_otros_botones_no_se_pisan(self):
        self.armar()
        self.assertFalse(self.b.boton_revendedores(1, 5, "resp", ""))
        self.assertFalse(self.b.boton_revendedores(1, 5, "app", ""))


if __name__ == "__main__":
    unittest.main()
