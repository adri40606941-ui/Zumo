import os
import tempfile
import unittest
import urllib.parse
from datetime import date

import cuentas_vps as cv
import panel_web
import revendedores as rv
from servicio_rev import Servicio
from test_revendedores import VpsFalsa

M = {"id": "m1", "nombre": "vps", "host": "x", "puerto": 22, "usuario": "root", "clave": "c", "huella": ""}
HTTPS = {"Host": "bot.ejemplo.com"}


class Base(unittest.TestCase):
    def setUp(self):
        self.t = [1000.0]
        self.r = rv.Revendedores(os.path.join(tempfile.mkdtemp(), "r.json"))
        self.a = self.r.crear("juan", "secreto1", "m1")["id"]
        self.b = self.r.crear("luis", "secreto2", "m1")["id"]
        for t in rv.MONEDAS:
            self.r.agregar_monedas(self.a, t, 2)
        self.f = VpsFalsa()

        class Ops:
            crear = staticmethod(lambda m, *a, **k: cv.crear(m, *a, correr=self.f, **k))
            renovar = staticmethod(lambda m, *a, **k: cv.renovar(m, *a, correr=self.f, **k))
            eliminar = staticmethod(lambda m, *a, **k: cv.eliminar(m, *a, correr=self.f, **k))
            bloquear = staticmethod(lambda m, *a, **k: cv.bloquear(m, *a, correr=self.f, **k))
            estado = staticmethod(lambda m, *a, **k: cv.estado(m, *a, correr=self.f, **k))
        hoy = lambda: date(2026, 10, 9)
        self.p = panel_web.PanelWeb(self.r, Servicio(self.r, lambda _i: M, Ops, hoy=hoy), reloj=lambda: self.t[0], hoy=hoy)

    def pedir(self, metodo, ruta, datos=None, cookie="", https=True, ip="1.1.1.1"):
        cab = dict(HTTPS)
        if cookie:
            cab["Cookie"] = "zr=" + cookie
        cuerpo = urllib.parse.urlencode(datos or {}).encode()
        return self.p.manejar(metodo, ruta, cab, cuerpo, ip, https)

    def entrar(self, usuario="juan", clave="secreto1", ip="1.1.1.1"):
        cod, cab, _ = self.pedir("POST", "/r/entrar", {"usuario": usuario, "clave": clave}, ip=ip)
        if cod != 303:
            return None
        return cab["Set-Cookie"].split(";")[0].split("=", 1)[1]

    def csrf(self, cookie):
        return self.p.sesiones[cookie]["csrf"]

    def accion(self, cookie, **datos):
        datos["csrf"] = self.csrf(cookie)
        return self.pedir("POST", "/r/accion", datos, cookie)

    def pagina(self, cookie):
        cod, _, cuerpo = self.pedir("GET", "/r", cookie=cookie)
        return cod, cuerpo.decode()


class TestAcceso(Base):
    def test_por_http_redirige_a_https(self):
        cod, cab, _ = self.pedir("GET", "/r", https=False)
        self.assertEqual((cod, cab["Location"]), (308, "https://bot.ejemplo.com/r"))

    def test_sin_sesion_muestra_el_login(self):
        cod, cuerpo = self.pagina("")
        self.assertEqual(cod, 200)
        self.assertIn('name="clave"', cuerpo)
        self.assertNotIn("Tus monedas", cuerpo)

    def test_clave_incorrecta(self):
        self.assertIsNone(self.entrar(clave="mala"))
        cod, _, cuerpo = self.pedir("POST", "/r/entrar", {"usuario": "juan", "clave": "mala"})
        self.assertIn("incorrectos", cuerpo.decode())

    def test_cookie_segura(self):
        _, cab, _ = self.pedir("POST", "/r/entrar", {"usuario": "juan", "clave": "secreto1"})
        for parte in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/r"):
            self.assertIn(parte, cab["Set-Cookie"])

    def test_encabezados_en_minuscula_tambien_funcionan(self):
        """Cloudflare o un proxy pueden mandar 'cookie' y 'host' en minúscula."""
        c = self.entrar()
        cod, _, cuerpo = self.p.manejar("GET", "/r", {"host": "bot.ejemplo.com", "cookie": "zr=" + c}, b"", "1.1.1.1", True)
        self.assertIn("Tus monedas", cuerpo.decode())
        cod, cab, _ = self.p.manejar("GET", "/r", {"host": "bot.ejemplo.com"}, b"", "1.1.1.1", False)
        self.assertEqual(cab["Location"], "https://bot.ejemplo.com/r")

    def test_cabeceras_de_seguridad(self):
        c = self.entrar()
        _, cab, _ = self.pedir("GET", "/r", cookie=c)
        self.assertIn("default-src 'none'", cab["Content-Security-Policy"])
        self.assertEqual(cab["X-Frame-Options"], "DENY")
        self.assertEqual(cab["Cache-Control"], "no-store")

    def test_freno_de_intentos_por_usuario(self):
        for _ in range(panel_web.FALLOS_USUARIO):
            self.entrar(clave="mala", ip="9.9.9.%d" % _)
        cod, _, _ = self.pedir("POST", "/r/entrar", {"usuario": "juan", "clave": "secreto1"}, ip="8.8.8.8")
        self.assertEqual(cod, 429)
        self.t[0] += panel_web.VENTANA_USUARIO + 1
        self.assertIsNotNone(self.entrar(ip="8.8.8.8"))

    def test_freno_de_intentos_por_ip(self):
        for i in range(panel_web.FALLOS_IP):
            self.entrar(usuario="x%d" % i, clave="mala", ip="7.7.7.7")
        self.assertEqual(self.pedir("POST", "/r/entrar", {"usuario": "juan", "clave": "secreto1"}, ip="7.7.7.7")[0], 429)

    def test_la_sesion_vence(self):
        c = self.entrar()
        self.assertIn("Tus monedas", self.pagina(c)[1])
        self.t[0] += panel_web.VIDA_SESION + 1
        self.assertNotIn("Tus monedas", self.pagina(c)[1])

    def test_revendedor_bloqueado_pierde_la_sesion(self):
        c = self.entrar()
        self.r.activar(self.a, False)
        self.assertNotIn("Tus monedas", self.pagina(c)[1])
        self.assertIsNone(self.entrar())

    def test_salir(self):
        c = self.entrar()
        self.pedir("POST", "/r/salir", {"csrf": self.csrf(c)}, c)
        self.assertNotIn("Tus monedas", self.pagina(c)[1])

    def test_otras_rutas_no_existen(self):
        self.assertEqual(self.pedir("GET", "/r/otra")[0], 404)
        self.assertEqual(self.pedir("DELETE", "/r")[0], 405)


class TestPanel(Base):
    def setUp(self):
        super().setUp()
        self.c = self.entrar()

    def test_muestra_las_monedas_con_su_numero(self):
        _, p = self.pagina(self.c)
        self.assertIn("× 2", p)
        for clase in ("bronce", "plata", "oro"):
            self.assertIn(f'class="m {clase}"', p)
        self.assertIn("<i>7</i>", p)
        self.assertIn("<i>15</i>", p)
        self.assertIn("<i>30</i>", p)

    def test_crear_usuario_gasta_moneda_y_aparece(self):
        cod, cab, _ = self.accion(self.c, a="crear", token="ABCD1234", nombre="Ana", dias="15")
        self.assertEqual((cod, cab["Location"]), (303, "/r"))
        _, p = self.pagina(self.c)
        self.assertIn("Usuario creado", p)
        self.assertIn("ABCD1234", p)
        self.assertIn("24/10/2026", p)
        self.assertEqual(self.r.buscar(self.a)["monedas"]["plata"], 1)
        self.assertNotIn("Usuario creado", self.pagina(self.c)[1], "el aviso se muestra una sola vez")

    def test_acciones_sin_csrf_se_ignoran(self):
        self.pedir("POST", "/r/accion", {"a": "crear", "token": "ABCD1234", "nombre": "Ana", "dias": "7"}, self.c)
        self.pedir("POST", "/r/accion", {"csrf": "falso", "a": "crear", "token": "ABCD1234", "nombre": "Ana", "dias": "7"}, self.c)
        self.pedir("POST", "/r/accion", {"csrf": "ñandú", "a": "crear", "token": "ABCD1234", "nombre": "Ana", "dias": "7"}, self.c)
        self.assertEqual(self.f.us, {})
        self.assertEqual(self.r.buscar(self.a)["monedas"]["bronce"], 2)

    def test_acciones_sin_sesion_se_ignoran(self):
        self.pedir("POST", "/r/accion", {"csrf": "x", "a": "crear", "token": "ABCD1234", "nombre": "Ana", "dias": "7"})
        self.assertEqual(self.f.us, {})

    def test_renovar_7_15_30(self):
        self.accion(self.c, a="crear", token="ABCD1234", nombre="Ana", dias="7")      # vence 16/10
        for a, esperada in (("r7", "23/10/2026"), ("r15", "07/11/2026"), ("r30", "07/12/2026")):
            self.accion(self.c, a=a, token="ABCD1234")
            self.assertIn(esperada, self.pagina(self.c)[1])
        self.assertEqual(self.r.buscar(self.a)["monedas"], {"bronce": 0, "plata": 1, "oro": 1})

    def test_sin_monedas_avisa_y_no_hace_nada(self):
        self.r.agregar_monedas(self.a, "bronce", -2)
        self.accion(self.c, a="crear", token="ABCD1234", nombre="Ana", dias="7")
        self.assertIn("No te quedan monedas de bronce", self.pagina(self.c)[1])
        self.assertEqual(self.f.us, {})

    def test_bloquear_desbloquear(self):
        self.accion(self.c, a="crear", token="ABCD1234", nombre="Ana", dias="7")
        self.accion(self.c, a="bloquear", token="ABCD1234")
        p = self.pagina(self.c)[1]
        self.assertIn("Bloqueado", p)
        self.assertIn("Desbloquear", p)
        self.accion(self.c, a="desbloquear", token="ABCD1234")
        self.assertNotIn("Bloqueado</span>", self.pagina(self.c)[1])

    def test_eliminar_pide_confirmacion(self):
        self.accion(self.c, a="crear", token="ABCD1234", nombre="Ana", dias="7")
        cod, cab, cuerpo = self.accion(self.c, a="eliminar", token="ABCD1234")
        self.assertEqual(cod, 200)
        self.assertIn("¿Eliminar a Ana?", cuerpo.decode())
        self.assertIn("ABCD1234", self.f.us, "todavía no se borró")
        self.accion(self.c, a="eliminar_ok", token="ABCD1234")
        self.assertNotIn("ABCD1234", self.f.us)
        self.assertIn("Todavía no creaste ninguno", self.pagina(self.c)[1])

    def test_no_ve_ni_toca_los_usuarios_de_otro(self):
        self.accion(self.c, a="crear", token="ABCD1234", nombre="Ana", dias="7")
        otro = self.entrar("luis", "secreto2")
        self.assertNotIn("ABCD1234", self.pagina(otro)[1])
        self.accion(otro, a="eliminar_ok", token="ABCD1234")
        self.accion(otro, a="bloquear", token="ABCD1234")
        self.assertIn("Ese usuario no es tuyo", self.pagina(otro)[1])
        self.assertIn("ABCD1234", self.f.us)
        self.assertFalse(self.f.us["ABCD1234"]["bloq"])

    def test_nombre_con_html_sale_escapado(self):
        self.accion(self.c, a="crear", token="ABCD1234", nombre="<script>alert(1)</script>", dias="7")
        p = self.pagina(self.c)[1]
        self.assertNotIn("<script>alert", p)
        self.assertIn("&lt;script&gt;", p)

    def test_dato_invalido(self):
        self.accion(self.c, a="crear", token="ABCD1234", nombre="Ana", dias="x")
        self.assertIn("Dato inválido", self.pagina(self.c)[1])

    def test_vps_caida_se_avisa(self):
        self.accion(self.c, a="crear", token="ABCD1234", nombre="Ana", dias="7")
        self.f.caida = True
        self.t[0] += 60            # que no use la lista en caché
        self.assertIn("No pude consultar la VPS", self.pagina(self.c)[1])


if __name__ == "__main__":
    unittest.main()
