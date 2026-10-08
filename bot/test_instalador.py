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


if __name__ == "__main__":
    unittest.main()
