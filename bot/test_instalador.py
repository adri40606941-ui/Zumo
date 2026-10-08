"""El instalador baja los archivos del bot de una lista: si falta un módulo que el bot importa, no arranca."""
import os
import re
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))


def _leer(nombre):
    with open(os.path.join(AQUI, nombre), encoding="utf-8") as f:
        return f.read()


class Instalador(unittest.TestCase):
    def test_todos_los_modulos_del_bot_estan_en_la_lista(self):
        lista = re.search(r'^ARCHIVOS="([^"]+)"', _leer("instalar-bot.sh"), re.M).group(1).split()
        locales = {f[:-3] for f in os.listdir(AQUI) if f.endswith(".py") and not f.startswith("test_")}
        pendientes, vistos = ["zumo-bot"], set()
        while pendientes:                      # sigue los import de módulo en módulo
            m = pendientes.pop()
            if m in vistos:
                continue
            vistos.add(m)
            ruta = m + ".py"
            self.assertIn(ruta, lista, f"{ruta} lo importa el bot pero instalar-bot.sh no lo baja")
            for linea in _leer(ruta).splitlines():
                g = re.match(r"\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", linea)
                if g and g.group(1) in locales:
                    pendientes.append(g.group(1))

    def test_la_lista_no_tiene_archivos_que_no_existen(self):
        lista = re.search(r'^ARCHIVOS="([^"]+)"', _leer("instalar-bot.sh"), re.M).group(1).split()
        for f in lista:
            self.assertTrue(os.path.exists(os.path.join(AQUI, f)), f"{f} está en la lista pero no existe")

    def test_badvpn_sale_del_repo_y_la_suma_coincide(self):
        import hashlib
        raiz = os.path.dirname(AQUI)
        instalador = open(os.path.join(raiz, "install.sh"), encoding="utf-8").read()
        suma = re.search(r'^BADVPN_SHA256="([0-9a-f]{64})"', instalador, re.M).group(1)
        with open(os.path.join(raiz, "fuentes", "badvpn.tar.gz"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), suma, "cambió fuentes/badvpn.tar.gz: actualizá BADVPN_SHA256")
        self.assertNotIn("git clone", instalador.split("ZUMOBADVPNACT")[1], "BadVPN no se baja de repos de terceros")
        import publico
        self.assertIn("fuentes/badvpn.tar.gz", publico.REPO_EXACTOS, "el dominio tiene que poder entregarlo")

    def test_el_instalador_del_bot_deja_dominio_y_copia_del_repo(self):
        t = _leer("instalar-bot.sh")
        for pista in ("ZUMO_DOMINIO=", "/opt/zumo-repo", "ufw allow 80,443/tcp", "/opt/zumo-repo/bot/$f"):
            self.assertIn(pista, t)


if __name__ == "__main__":
    unittest.main()
