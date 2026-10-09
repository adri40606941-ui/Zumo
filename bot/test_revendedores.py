import os
import tempfile
import unittest
from datetime import date

import cuentas_vps as cv
import revendedores as rv
from servicio_rev import Servicio


class VpsFalsa:
    """Una VPS de mentira: guarda usuarios en memoria y entiende los comandos de cuentas_vps."""

    def __init__(self):
        self.us = {}              # token -> {"vence": date, "bloq": bool}
        self.scripts = []
        self.caida = False
        self.sin_panel = False

    def __call__(self, m, comando, timeout=20):
        if self.caida:
            raise OSError("sin red")
        self.scripts.append(comando)
        c = comando
        if self.sin_panel and "SINPANEL" in c:
            return "SINPANEL\n"
        if "useradd" in c:
            tok = next(k for k in self._tokens(c))
            if tok in self.us:
                return "EXISTE\n"
            exp = date.fromisoformat(self._fecha_db(c))
            self.us[tok] = {"vence": exp, "bloq": False}
            return "OK\n"
        if "usermod -e" in c:
            tok = self._tokens(c)[0]
            self.us[tok]["vence"] = date.fromisoformat(self._fecha_db(c))
            return "OK\n"
        if "userdel" in c:
            tok = self._tokens(c)[0]
            self.us.pop(tok, None)
            return "OK\n"
        if "usermod -L" in c:
            tok = self._tokens(c)[0]
            if tok in self.us:
                self.us[tok]["bloq"] = True
                return "OK\n"
            return "NO\n"
        if "usermod -U" in c:
            tok = self._tokens(c)[0]
            if tok in self.us:
                self.us[tok]["bloq"] = False
                return "OK\n"
            return "NO\n"
        if "for u in" in c:
            lista = c.split("for u in ")[1].split(";")[0].split()
            out = ""
            for t in lista:
                u = self.us.get(t)
                out += f"{t} SI {u['vence'].isoformat()} {'L' if u['bloq'] else 'P'}\n" if u else f"{t} NO - -\n"
            return out
        if "&& echo EXISTE || echo NO" in c:
            t = c.split("id ")[1].split()[0]
            u = self.us.get(t)
            return "NO\n" if not u else f"EXISTE\n{t}:1:{u['vence'].isoformat()}\n"
        raise AssertionError("comando que no conozco: " + c)

    @staticmethod
    def _tokens(c):
        import re
        return re.findall(r"\b([A-Za-z0-9]{8,32})\b(?=\s|$|\n)", c.split("source")[-1])[:1] or ["?"]

    @staticmethod
    def _fecha_db(c):
        import re
        return re.search(r"(20\d\d-\d\d-\d\d)", c.split("zumo_db_set")[-1] if "zumo_db_set" in c else c.split("zumo_db_add")[-1]).group(1)


class TestMonedas(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.r = rv.Revendedores(os.path.join(self.d, "r.json"))
        self.a = self.r.crear("juan", "secreto1", "m1")

    def test_cada_duracion_tiene_su_moneda(self):
        self.assertEqual([rv.moneda_de(d) for d in (7, 15, 30, 10)], ["bronce", "plata", "oro", None])

    def test_agregar_y_gastar(self):
        self.r.agregar_monedas(self.a["id"], "plata", 2)
        self.assertEqual(self.r.gastar(self.a["id"], 15), "plata")
        self.assertEqual(self.r.buscar(self.a["id"])["monedas"]["plata"], 1)

    def test_sin_monedas_no_gasta_ni_baja_de_cero(self):
        with self.assertRaises(rv.ErrorRevendedor):
            self.r.gastar(self.a["id"], 7)
        self.assertEqual(self.r.buscar(self.a["id"])["monedas"]["bronce"], 0)
        with self.assertRaises(rv.ErrorRevendedor):
            self.r.agregar_monedas(self.a["id"], "oro", -1)

    def test_duracion_rara_no_gasta(self):
        self.r.agregar_monedas(self.a["id"], "bronce", 1)
        with self.assertRaises(rv.ErrorRevendedor):
            self.r.gastar(self.a["id"], 10)
        self.assertEqual(self.r.buscar(self.a["id"])["monedas"]["bronce"], 1)

    def test_quitar_monedas_con_numero_negativo(self):
        self.r.agregar_monedas(self.a["id"], "oro", 5)
        self.r.agregar_monedas(self.a["id"], "oro", -2)
        self.assertEqual(self.r.buscar(self.a["id"])["monedas"]["oro"], 3)


class TestRevendedores(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.arch = os.path.join(self.d, "r.json")
        self.r = rv.Revendedores(self.arch)

    def test_la_contrasena_no_se_guarda_en_claro(self):
        self.r.crear("pepe", "mi-clave-larga", "m1")
        with open(self.arch) as f:
            self.assertNotIn("mi-clave-larga", f.read())
        self.assertEqual(oct(os.stat(self.arch).st_mode & 0o777), "0o600")

    def test_entrar_con_usuario_y_clave(self):
        self.r.crear("pepe", "mi-clave-larga", "m1")
        self.assertEqual(self.r.verificar("PEPE", "mi-clave-larga")["usuario"], "pepe")
        self.assertIsNone(self.r.verificar("pepe", "otra"))
        self.assertIsNone(self.r.verificar("nadie", "mi-clave-larga"))

    def test_bloqueado_no_entra(self):
        a = self.r.crear("pepe", "mi-clave-larga", "m1")
        self.r.activar(a["id"], False)
        self.assertIsNone(self.r.verificar("pepe", "mi-clave-larga"))
        self.r.activar(a["id"], True)
        self.assertIsNotNone(self.r.verificar("pepe", "mi-clave-larga"))

    def test_usuario_y_clave_validos(self):
        for u, c in (("a", "secreto1"), ("Pepe Gomez", "secreto1"), ("pepe", "123"), ("pepe", " secreto1")):
            with self.assertRaises(rv.ErrorRevendedor):
                self.r.crear(u, c, "m1")

    def test_usuario_repetido(self):
        self.r.crear("pepe", "secreto1", "m1")
        with self.assertRaises(rv.ErrorRevendedor):
            self.r.crear("PEPE", "secreto2", "m1")

    def test_cambiar_clave(self):
        a = self.r.crear("pepe", "secreto1", "m1")
        self.r.cambiar_clave(a["id"], "nueva-clave")
        self.assertIsNone(self.r.verificar("pepe", "secreto1"))
        self.assertIsNotNone(self.r.verificar("pepe", "nueva-clave"))

    def test_eliminar_borra_sus_usuarios_del_registro(self):
        a = self.r.crear("pepe", "secreto1", "m1")
        self.r.registrar_cuenta("TOKEN12345", a["id"], "Ana")
        self.r.eliminar(a["id"])
        self.assertIsNone(self.r.buscar(a["id"]))
        self.assertIsNone(self.r.dueno("TOKEN12345"))

    def test_clave_nueva_es_valida(self):
        self.assertTrue(rv.clave_valida(rv.clave_nueva()))


class TestCuentasVps(unittest.TestCase):
    M = {"id": "m1", "nombre": "vps", "host": "x", "puerto": 22, "usuario": "root", "clave": "c", "huella": ""}

    def test_crear_arma_el_comando_como_el_bot(self):
        f = VpsFalsa()
        exp = cv.crear(self.M, "ABCD1234", "Ana Pérez", 15, hoy=date(2026, 10, 9), correr=f)
        self.assertEqual(exp, date(2026, 10, 24))
        s = f.scripts[0]
        self.assertIn("useradd --badname -M -s /bin/false -e 2026-10-25", s)     # la cuenta vence un día después
        self.assertIn("hwid,Ana Pérez", s)
        self.assertIn("zumo_db_add ABCD1234 1 2026-10-24", s)

    def test_token_invalido_no_llega_a_la_vps(self):
        f = VpsFalsa()
        for t in ("corto", "con espacio1", "a;rm -rf /;x1", "x" * 40, ""):
            with self.assertRaises(cv.ErrorCuenta):
                cv.crear(self.M, t, "n", 7, correr=f)
        self.assertEqual(f.scripts, [])

    def test_nombre_malicioso_queda_entre_comillas(self):
        f = VpsFalsa()
        cv.crear(self.M, "ABCD1234", "x'; rm -rf / #", 7, hoy=date(2026, 10, 9), correr=f)
        self.assertNotIn("; rm -rf / #'", f.scripts[0].replace("'\"'\"'", ""))

    def test_renovar_suma_a_la_fecha_actual_si_no_vencio(self):
        f = VpsFalsa()
        cv.crear(self.M, "ABCD1234", "Ana", 7, hoy=date(2026, 10, 9), correr=f)      # vence 16/10
        exp = cv.renovar(self.M, "ABCD1234", 15, hoy=date(2026, 10, 12), correr=f)
        self.assertEqual(exp, date(2026, 10, 31))

    def test_renovar_vencido_cuenta_desde_hoy(self):
        f = VpsFalsa()
        cv.crear(self.M, "ABCD1234", "Ana", 7, hoy=date(2026, 10, 9), correr=f)
        self.assertEqual(cv.renovar(self.M, "ABCD1234", 30, hoy=date(2026, 11, 1), correr=f), date(2026, 12, 1))

    def test_renovar_inexistente(self):
        with self.assertRaises(cv.ErrorCuenta):
            cv.renovar(self.M, "ABCD1234", 7, correr=VpsFalsa())

    def test_bloquear_desbloquear_y_estado(self):
        f = VpsFalsa()
        cv.crear(self.M, "ABCD1234", "Ana", 7, hoy=date(2026, 10, 9), correr=f)
        cv.bloquear(self.M, "ABCD1234", True, correr=f)
        e = cv.estado(self.M, ["ABCD1234", "ZZZZ9999"], correr=f)
        self.assertEqual(e["ABCD1234"], {"existe": True, "vence": date(2026, 10, 16), "bloqueado": True})
        self.assertFalse(e["ZZZZ9999"]["existe"])
        cv.bloquear(self.M, "ABCD1234", False, correr=f)
        self.assertFalse(cv.estado(self.M, ["ABCD1234"], correr=f)["ABCD1234"]["bloqueado"])

    def test_vps_sin_red(self):
        f = VpsFalsa()
        f.caida = True
        with self.assertRaises(cv.ErrorCuenta):
            cv.crear(self.M, "ABCD1234", "Ana", 7, correr=f)


class TestServicio(unittest.TestCase):
    M = TestCuentasVps.M

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.r = rv.Revendedores(os.path.join(self.d, "r.json"))
        self.a = self.r.crear("juan", "secreto1", "m1")["id"]
        self.b = self.r.crear("luis", "secreto2", "m1")["id"]
        self.f = VpsFalsa()

        class Ops:
            crear = staticmethod(lambda m, *a, **k: cv.crear(m, *a, correr=self.f, **k))
            renovar = staticmethod(lambda m, *a, **k: cv.renovar(m, *a, correr=self.f, **k))
            eliminar = staticmethod(lambda m, *a, **k: cv.eliminar(m, *a, correr=self.f, **k))
            bloquear = staticmethod(lambda m, *a, **k: cv.bloquear(m, *a, correr=self.f, **k))
            estado = staticmethod(lambda m, *a, **k: cv.estado(m, *a, correr=self.f, **k))
        self.s = Servicio(self.r, lambda _id: self.M, Ops, hoy=lambda: date(2026, 10, 9))
        for t in ("bronce", "plata", "oro"):
            self.r.agregar_monedas(self.a, t, 2)

    def monedas(self, rid=None):
        return self.r.buscar(rid or self.a)["monedas"]

    def test_crear_gasta_la_moneda_de_esa_duracion(self):
        res = self.s.crear(self.a, "ABCD1234", "Ana", 15)
        self.assertEqual((res["moneda"], res["vence"]), ("plata", date(2026, 10, 24)))
        self.assertEqual(self.monedas(), {"bronce": 2, "plata": 1, "oro": 2})
        self.assertEqual(self.r.dueno("ABCD1234"), self.a)

    def test_cada_moneda_para_su_duracion(self):
        self.s.crear(self.a, "AAAA1111", "a", 7)
        self.s.crear(self.a, "BBBB2222", "b", 30)
        self.assertEqual(self.monedas(), {"bronce": 1, "plata": 2, "oro": 1})

    def test_sin_moneda_no_crea_nada_en_la_vps(self):
        self.r.agregar_monedas(self.a, "oro", -2)
        with self.assertRaises(rv.ErrorRevendedor):
            self.s.crear(self.a, "ABCD1234", "Ana", 30)
        self.assertEqual(self.f.us, {})

    def test_si_la_vps_falla_se_devuelve_la_moneda(self):
        self.f.caida = True
        with self.assertRaises(rv.ErrorRevendedor):
            self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.assertEqual(self.monedas()["bronce"], 2)
        self.assertIsNone(self.r.dueno("ABCD1234"))

    def test_token_repetido_devuelve_la_moneda(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        with self.assertRaises(rv.ErrorRevendedor):
            self.s.crear(self.b, "ABCD1234", "Otro", 7)
        with self.assertRaises(rv.ErrorRevendedor):
            self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.assertEqual(self.monedas()["bronce"], 1)

    def test_renovar_7_gasta_bronce_y_suma_dias(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)       # vence 16/10
        res = self.s.renovar(self.a, "ABCD1234", 7)
        self.assertEqual(res["vence"], date(2026, 10, 23))
        self.assertEqual(self.monedas()["bronce"], 0)

    def test_renovar_15_gasta_plata_y_30_oro(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.s.renovar(self.a, "ABCD1234", 15)
        self.s.renovar(self.a, "ABCD1234", 30)
        self.assertEqual(self.monedas(), {"bronce": 1, "plata": 1, "oro": 1})

    def test_no_puede_tocar_usuarios_de_otro_revendedor(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        for f in (lambda: self.s.renovar(self.b, "ABCD1234", 7), lambda: self.s.bloquear(self.b, "ABCD1234"),
                  lambda: self.s.eliminar(self.b, "ABCD1234")):
            with self.assertRaises(rv.ErrorRevendedor):
                f()
        self.assertIn("ABCD1234", self.f.us)

    def test_bloquear_y_eliminar_son_gratis(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        antes = self.monedas()
        self.s.bloquear(self.a, "ABCD1234")
        self.assertTrue(self.s.listar(self.a)[0]["bloqueado"])
        self.s.bloquear(self.a, "ABCD1234", False)
        self.s.eliminar(self.a, "ABCD1234")
        self.assertEqual(self.monedas(), antes)
        self.assertEqual(self.s.listar(self.a), [])
        self.assertNotIn("ABCD1234", self.f.us)

    def test_revendedor_bloqueado_no_puede_operar(self):
        self.r.activar(self.a, False)
        with self.assertRaises(rv.ErrorRevendedor):
            self.s.crear(self.a, "ABCD1234", "Ana", 7)

    def test_listar_con_la_vps_caida_muestra_lo_guardado(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.f.caida = True
        fila = self.s.listar(self.a)[0]
        self.assertEqual((fila["token"], fila["nombre"], fila["sin_datos"]), ("ABCD1234", "Ana", True))


if __name__ == "__main__":
    unittest.main()
