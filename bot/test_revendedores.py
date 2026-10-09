import os
import re
import shlex
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
            linea = next(l for l in shlex.split(c)[2].splitlines() if l.startswith("useradd"))
            p_ = shlex.split(linea.split("||")[0])
            nombre = p_[p_.index("-c") + 1][5:]           # sin el "hwid,"
            self.us[tok] = {"vence": exp, "bloq": False, "nombre": nombre}
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
        if "usermod -c" in c:
            tok = self._tokens(c)[0]
            if tok not in self.us:
                return "NO\n"
            self.us[tok]["nombre"] = shlex.split(shlex.split(c)[2].split("&&")[0])[2][5:]
            return "OK\n"
        if "usermod -U" in c:
            tok = self._tokens(c)[0]
            if tok in self.us:
                self.us[tok]["bloq"] = False
                return "OK\n"
            return "NO\n"
        if "getent passwd" in c:
            tok = c.split("id ")[1].split()[0]
            u = self.us.get(tok)
            if not u:
                return "NO\n"
            pre = "" if u.get("sin_prefijo") else "hwid,"
            return f"G {pre}{u['nombre']}\nV {u['vence'].isoformat()}\n"
        if "for u in" in c:
            lista = c.split("for u in ")[1].split(";")[0].split()
            out = ""
            for t in lista:
                u = self.us.get(t)
                out += (f"{t} SI {u['vence'].isoformat()} {'L' if u['bloq'] else 'P'} {'C' if u.get('conectado') else '-'}\n"
                        if u else f"{t} NO - - -\n")
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
        self.assertEqual(e["ABCD1234"], {"existe": True, "vence": date(2026, 10, 16), "bloqueado": True, "conectado": False})
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
            datos = staticmethod(lambda m, *a, **k: cv.datos(m, *a, correr=self.f, **k))
            renombrar = staticmethod(lambda m, *a, **k: cv.renombrar(m, *a, correr=self.f, **k))
        self.t = [1000.0]
        self.s = Servicio(self.r, lambda _id: self.M, Ops, hoy=lambda: date(2026, 10, 9), reloj=lambda: self.t[0])
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


class TestNombreYFecha(TestServicio):
    """/cuenta para la app: "Nombre [dd/mm]" de los usuarios de la VPS asignada al revendedor."""

    def test_nombre_y_vencimiento_desde_la_vps(self):
        self.s.crear(self.a, "ABCD1234", "Ana Pérez", 15)
        self.assertEqual(self.s.datos_cuenta("ABCD1234"), ("Ana Pérez", "2026-10-24"))

    def test_si_cambias_el_nombre_o_la_fecha_en_la_vps_se_actualiza(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.s.datos_cuenta("ABCD1234")
        self.f.us["ABCD1234"]["nombre"] = "Ana Gómez"
        self.f.us["ABCD1234"]["vence"] = date(2026, 12, 25)
        self.assertEqual(self.s.datos_cuenta("ABCD1234")[0], "Ana", "dentro de los 60 s sale lo guardado")
        self.t[0] += 61
        self.assertEqual(self.s.datos_cuenta("ABCD1234"), ("Ana Gómez", "2026-12-25"))

    def test_renombrar_cambia_la_vps_y_lo_guardado(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.assertEqual(self.s.renombrar(self.a, "ABCD1234", "Ana: Gómez")["nombre"], "Ana Gómez")
        self.assertEqual(self.f.us["ABCD1234"]["nombre"], "Ana Gómez")
        self.assertEqual(self.s.listar(self.a)[0]["nombre"], "Ana Gómez")
        self.assertEqual(self.s.datos_cuenta("ABCD1234")[0], "Ana Gómez")

    def test_no_renombra_usuarios_ajenos(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        with self.assertRaises(rv.ErrorRevendedor):
            self.s.renombrar(self.b, "ABCD1234", "Otro")

    def test_listar_marca_quien_esta_conectado(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.s.crear(self.a, "EFGH5678", "Beto", 7)
        self.f.us["EFGH5678"]["conectado"] = True
        con = {x["token"]: x["conectado"] for x in self.s.listar(self.a)}
        self.assertEqual(con, {"ABCD1234": False, "EFGH5678": True})

    def test_nombre_editado_en_la_vps_sin_el_prefijo_hwid(self):
        self.s.crear(self.a, "ABCD1234", "Ssssss", 7)
        self.f.us["ABCD1234"]["nombre"] = "adri"
        self.f.us["ABCD1234"]["sin_prefijo"] = True
        self.assertEqual(self.s.datos_cuenta("ABCD1234")[0], "adri")

    def test_guarda_el_resultado_para_no_entrar_por_ssh_cada_vez(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.s.datos_cuenta("ABCD1234")
        n = len(self.f.scripts)
        for _ in range(5):
            self.s.datos_cuenta("ABCD1234")
        self.assertEqual(len(self.f.scripts), n)

    def test_token_desconocido_no_toca_ninguna_vps(self):
        self.assertIsNone(self.s.datos_cuenta("ZZZZ9999"))
        self.assertEqual(self.f.scripts, [])

    def test_renovar_actualiza_la_fecha_en_el_acto(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.s.datos_cuenta("ABCD1234")
        self.s.renovar(self.a, "ABCD1234", 30)
        self.assertEqual(self.s.datos_cuenta("ABCD1234")[1], "2026-11-15")

    def test_usuario_borrado_en_la_vps_ya_no_figura(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        del self.f.us["ABCD1234"]
        self.assertIsNone(self.s.datos_cuenta("ABCD1234"))

    def test_si_la_vps_no_contesta_sale_el_nombre_guardado_sin_fecha(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.f.caida = True
        self.assertEqual(self.s.datos_cuenta("ABCD1234"), ("Ana", ""))
        self.f.caida = False
        self.t[0] += 31
        self.assertEqual(self.s.datos_cuenta("ABCD1234")[1], "2026-10-16")

    def test_revendedor_bloqueado_sus_clientes_siguen_viendo_su_nombre(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.r.activar(self.a, False)
        self.assertEqual(self.s.datos_cuenta("ABCD1234")[0], "Ana")


if __name__ == "__main__":
    unittest.main()


class TestVariasVps(unittest.TestCase):
    """Un revendedor con dos VPS: todo se hace en las dos, y si una cae se sigue con la otra."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.r = rv.Revendedores(os.path.join(self.d, "r.json"))
        self.a = self.r.crear("juan", "secreto1", "m1")["id"]
        self.r.agregar_maquina(self.a, "m2")
        self.m = {"m1": {"id": "m1", "nombre": "app01", "host": "1.1.1.1"}, "m2": {"id": "m2", "nombre": "app02", "host": "2.2.2.2"}}
        self.f = {"m1": VpsFalsa(), "m2": VpsFalsa()}
        corre = lambda m: self.f[m["id"]]

        class Ops:
            crear = staticmethod(lambda m, *a, **k: cv.crear(m, *a, correr=corre(m), **k))
            renovar = staticmethod(lambda m, *a, **k: cv.renovar(m, *a, correr=corre(m), **k))
            eliminar = staticmethod(lambda m, *a, **k: cv.eliminar(m, *a, correr=corre(m), **k))
            bloquear = staticmethod(lambda m, *a, **k: cv.bloquear(m, *a, correr=corre(m), **k))
            estado = staticmethod(lambda m, *a, **k: cv.estado(m, *a, correr=corre(m), **k))
            datos = staticmethod(lambda m, *a, **k: cv.datos(m, *a, correr=corre(m), **k))
            renombrar = staticmethod(lambda m, *a, **k: cv.renombrar(m, *a, correr=corre(m), **k))
            fijar_vence = staticmethod(lambda m, *a, **k: cv.fijar_vence(m, *a, correr=corre(m), **k))
        self.avisos = []
        self.t = [1000.0]
        self.s = Servicio(self.r, lambda i: self.m.get(i), Ops, hoy=lambda: date(2026, 10, 9), reloj=lambda: self.t[0],
                          notificar=self.avisos.append)
        for t in ("bronce", "plata", "oro"):
            self.r.agregar_monedas(self.a, t, 3)

    def test_crear_en_las_dos_y_avisar_al_admin(self):
        res = self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.assertEqual(res["vps"], ["app01", "app02"])
        self.assertIn("ABCD1234", self.f["m1"].us)
        self.assertIn("ABCD1234", self.f["m2"].us)
        self.assertEqual(self.r.cuentas_de(self.a)["ABCD1234"]["maq"], ["m1", "m2"])
        self.assertEqual(self.r.buscar(self.a)["monedas"]["bronce"], 2, "gasta una sola moneda, no una por VPS")
        self.assertEqual(len(self.avisos), 1)
        self.assertIn("juan creó a Ana", self.avisos[0])
        self.assertIn("app01, app02", self.avisos[0])
        self.assertIn("ABCD1234", self.avisos[0])

    def test_si_una_vps_cae_se_crea_en_la_otra_y_se_completa_despues(self):
        self.f["m2"].caida = True
        res = self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.assertEqual(res["vps"], ["app01"])
        self.assertEqual(res["fallaron"], ["app02"])
        self.assertIn("app02", self.avisos[0])
        self.assertEqual(self.r.cuentas_de(self.a)["ABCD1234"]["maq"], ["m1"])
        self.f["m2"].caida = False
        hechos = self.s.reparar()
        self.assertTrue(any("creado en app02" in h for h in hechos), hechos)
        self.assertIn("ABCD1234", self.f["m2"].us)
        self.assertEqual(self.f["m2"].us["ABCD1234"]["vence"], date(2026, 10, 16), "con el mismo vencimiento")
        self.assertEqual(self.r.cuentas_de(self.a)["ABCD1234"]["maq"], ["m1", "m2"])
        self.assertEqual(self.s.reparar(), [], "la segunda vez no hay nada que hacer")

    def test_si_caen_las_dos_no_gasta_moneda(self):
        self.f["m1"].caida = self.f["m2"].caida = True
        with self.assertRaises(rv.ErrorRevendedor):
            self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.assertEqual(self.r.buscar(self.a)["monedas"]["bronce"], 3)
        self.assertIsNone(self.r.dueno("ABCD1234"))

    def test_renovar_deja_las_dos_en_la_misma_fecha(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.f["m2"].us["ABCD1234"]["vence"] = date(2026, 10, 10)        # una quedó atrasada
        res = self.s.renovar(self.a, "ABCD1234", 15)
        self.assertEqual(res["vence"], date(2026, 10, 31))
        self.assertEqual(self.f["m1"].us["ABCD1234"]["vence"], date(2026, 10, 31))
        self.assertEqual(self.f["m2"].us["ABCD1234"]["vence"], date(2026, 10, 31))
        self.assertEqual(self.r.buscar(self.a)["monedas"]["plata"], 2)

    def test_renovar_con_una_caida_avisa_y_la_repara_despues(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.f["m2"].caida = True
        res = self.s.renovar(self.a, "ABCD1234", 15)
        self.assertEqual(res["fallaron"], ["app02"])
        self.f["m2"].caida = False
        hechos = self.s.reparar()
        self.assertTrue(any("vencimiento igualado en app02" in h for h in hechos), hechos)
        self.assertEqual(self.f["m2"].us["ABCD1234"]["vence"], res["vence"])

    def test_bloquear_nombre_y_eliminar_en_todas(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.s.bloquear(self.a, "ABCD1234", True)
        self.assertTrue(self.f["m1"].us["ABCD1234"]["bloq"] and self.f["m2"].us["ABCD1234"]["bloq"])
        self.s.renombrar(self.a, "ABCD1234", "Ana Gómez")
        self.assertEqual(self.f["m2"].us["ABCD1234"]["nombre"], "Ana Gómez")
        self.s.eliminar(self.a, "ABCD1234")
        self.assertEqual((self.f["m1"].us, self.f["m2"].us), ({}, {}))
        self.assertIsNone(self.r.dueno("ABCD1234"))

    def test_eliminar_con_una_caida_deja_pendiente_solo_esa(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.f["m2"].caida = True
        with self.assertRaises(rv.ErrorRevendedor) as c:
            self.s.eliminar(self.a, "ABCD1234")
        self.assertIn("app02", str(c.exception))
        self.assertNotIn("ABCD1234", self.f["m1"].us)
        self.assertEqual(self.r.cuentas_de(self.a)["ABCD1234"]["maq"], ["m2"], "queda anotado solo donde falta borrar")
        self.f["m2"].caida = False
        self.s.eliminar(self.a, "ABCD1234")
        self.assertIsNone(self.r.dueno("ABCD1234"))

    def test_listar_junta_las_vps_conectado_en_cualquiera(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.f["m2"].us["ABCD1234"]["conectado"] = True
        fila = self.s.listar(self.a)[0]
        self.assertTrue(fila["conectado"])
        self.assertEqual(fila["maquinas"], ["app01", "app02"])
        self.assertFalse(fila["sin_datos"])

    def test_listar_con_una_vps_caida_marca_sin_datos_pero_muestra_lo_de_la_otra(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.f["m2"].caida = True
        fila = self.s.listar(self.a)[0]
        self.assertTrue(fila["sin_datos"])
        self.assertEqual(fila["vence"], date(2026, 10, 16))

    def test_la_app_pregunta_en_la_otra_si_una_cae(self):
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.f["m1"].caida = True
        self.assertEqual(self.s.datos_cuenta("ABCD1234"), ("Ana", "2026-10-16"))

    def test_vps_nueva_completa_los_usuarios_que_ya_tenia(self):
        r2 = self.r.crear("luis", "secreto2", "m1")["id"]
        for t in ("bronce",):
            self.r.agregar_monedas(r2, t, 2)
        self.s.crear(r2, "ZZZZ9999", "Zeta", 7)
        self.assertNotIn("ZZZZ9999", self.f["m2"].us)
        self.r.agregar_maquina(r2, "m2")
        hechos = self.s.reparar()
        self.assertTrue(any("Zeta: creado en app02" in h for h in hechos), hechos)
        self.assertIn("ZZZZ9999", self.f["m2"].us)

    def test_reparar_copia_el_bloqueo(self):
        self.f["m2"].caida = True
        self.s.crear(self.a, "ABCD1234", "Ana", 7)
        self.s.bloquear(self.a, "ABCD1234", True)
        self.f["m2"].caida = False
        self.s.reparar()
        self.assertTrue(self.f["m2"].us["ABCD1234"]["bloq"])

    def test_aviso_cuando_se_queda_sin_monedas(self):
        r = self.r.buscar(self.a)
        self.r.agregar_monedas(self.a, "oro", -2)
        self.s.crear(self.a, "ABCD1234", "Ana", 30)
        self.assertTrue(any("se quedó sin monedas de oro" in a for a in self.avisos), self.avisos)

    def test_migracion_de_un_revendedor_viejo_de_una_sola_vps(self):
        import json
        viejo = {"n": 1, "revendedores": {"1": {"id": "1", "usuario": "ana", "hash": "x", "maquina": "m1", "activo": True,
                                                "creado": 1, "monedas": {"bronce": 1, "plata": 0, "oro": 0}}},
                 "cuentas": {"TOK12345": {"rev": "1", "etq": "Cli", "creado": 5}}, "mov": []}
        ruta = os.path.join(self.d, "viejo.json")
        json.dump(viejo, open(ruta, "w"))
        r = rv.Revendedores(ruta)
        self.assertEqual(r.buscar("1")["maquinas"], ["m1"])
        self.assertEqual(r.cuentas_de("1")["TOK12345"]["maq"], ["m1"])
        self.assertEqual(r.cuentas_de_token("TOK12345")["maquinas"], ["m1"])

    def test_agregar_y_quitar_maquina_reglas(self):
        with self.assertRaises(rv.ErrorRevendedor):
            self.r.agregar_maquina(self.a, "m2")            # ya la tiene
        self.r.quitar_maquina(self.a, "m1")
        with self.assertRaises(rv.ErrorRevendedor):
            self.r.quitar_maquina(self.a, "m2")             # es la última
        self.assertEqual(self.r.buscar(self.a)["maquina"], "m2", "'maquina' sigue apuntando a la primera")
