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

    def test_cambiar_de_vps(self):
        self.armar(maquinas=(MAQ, MAQ2))
        rid = self.alta()
        self.btn(f"rvmq:{rid}")
        self.assertIn(f"rvmq2:{rid}:m2", self.tg.datos_botones())
        self.btn(f"rvmq2:{rid}:m2")
        self.assertEqual(self.b.revs.buscar(rid)["maquina"], "m2")

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
