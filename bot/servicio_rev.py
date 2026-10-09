"""Lo que puede hacer un revendedor, con sus reglas: gastar monedas, tocar solo sus usuarios y hablar con su VPS.

Lo usan el panel web (panel_web.py) y las pruebas. Todo error se levanta como ErrorRevendedor, con un texto que se
puede mostrar tal cual en pantalla.
"""
import threading
import time
from datetime import date

import cuentas_vps as cv
import maquinas as mq
from revendedores import ErrorRevendedor, moneda_de


class Servicio:
    def __init__(self, rev, buscar_maquina=None, ops=None, hoy=date.today, reloj=time.time):
        self.reloj = reloj
        self._datos = {}         # token -> (momento, resultado) de datos_cuenta
        self.rev = rev
        self.buscar_maquina = buscar_maquina or mq.buscar
        self.ops = ops or cv
        self.hoy = hoy
        self._candados = {}
        self._lock = threading.Lock()

    def _candado(self, rid):
        with self._lock:
            return self._candados.setdefault(str(rid), threading.Lock())

    def _contexto(self, rid):
        r = self.rev.buscar(rid)
        if not r or not r["activo"]:
            raise ErrorRevendedor("Tu cuenta de revendedor está bloqueada o ya no existe.")
        try:
            m = self.buscar_maquina(r["maquina"])
        except mq.ErrorMaquina as e:
            raise ErrorRevendedor(str(e))
        if not m:
            raise ErrorRevendedor("Tu VPS no está enlazada. Avisale al administrador.")
        return r, m

    def _propio(self, rid, token):
        if self.rev.dueno(token) != str(rid):
            raise ErrorRevendedor("Ese usuario no es tuyo.")

    def _vps(self, f, *a, **k):
        try:
            return f(*a, **k)
        except (cv.ErrorCuenta, mq.ErrorMaquina) as e:
            raise ErrorRevendedor(str(e))

    # -- acciones
    def crear(self, rid, token, nombre, dias):
        with self._candado(rid):
            r, m = self._contexto(rid)
            token = (token or "").strip()
            if self.rev.dueno(token):
                raise ErrorRevendedor("Ese token ya está registrado.")
            tipo = self.rev.gastar(rid, dias)
            try:
                exp = self._vps(self.ops.crear, m, token, nombre, dias, hoy=self.hoy())
            except ErrorRevendedor:
                self.rev.devolver(rid, tipo)
                raise
            self.rev.registrar_cuenta(token, rid, cv.limpiar_nombre(nombre), dias, tipo)
            return {"token": token, "vence": exp, "moneda": tipo}

    def renovar(self, rid, token, dias):
        with self._candado(rid):
            r, m = self._contexto(rid)
            self._propio(rid, token)
            tipo = self.rev.gastar(rid, dias)
            try:
                exp = self._vps(self.ops.renovar, m, token, dias, hoy=self.hoy())
            except ErrorRevendedor:
                self.rev.devolver(rid, tipo)
                raise
            self.rev.anotar(rid, "renovar", token, dias, tipo)
            self._datos.pop(token, None)
            return {"token": token, "vence": exp, "moneda": tipo}

    def bloquear(self, rid, token, si=True):
        with self._candado(rid):
            r, m = self._contexto(rid)
            self._propio(rid, token)
            self._vps(self.ops.bloquear, m, token, si)
            self.rev.anotar(rid, "bloquear" if si else "desbloquear", token)

    def renombrar(self, rid, token, nombre):
        with self._candado(rid):
            r, m = self._contexto(rid)
            self._propio(rid, token)
            n = self._vps(self.ops.renombrar, m, token, nombre)
            self.rev.renombrar_cuenta(token, n)
            self._datos.pop(token, None)
            self.rev.anotar(rid, "nombre", token)
            return n

    def eliminar(self, rid, token):
        with self._candado(rid):
            r, m = self._contexto(rid)
            self._propio(rid, token)
            self._vps(self.ops.eliminar, m, token)
            self.rev.quitar_cuenta(token)
            self._datos.pop(token, None)
            self.rev.anotar(rid, "eliminar", token)

    def listar(self, rid):
        """Sus usuarios, con vencimiento y bloqueo según la VPS. Si la VPS no contesta, sale lo guardado."""
        r, m = self._contexto(rid)
        propias = self.rev.cuentas_de(rid)
        try:
            est = self._vps(self.ops.estado, m, list(propias))
            sin_datos = False
        except ErrorRevendedor:
            est, sin_datos = {}, True
        filas = []
        for t, c in sorted(propias.items(), key=lambda x: -x[1].get("creado", 0)):
            e = est.get(t, {})
            filas.append({"token": t, "nombre": c.get("etq", ""), "vence": e.get("vence"),
                          "bloqueado": e.get("bloqueado", False), "conectado": e.get("conectado", False), "existe": e.get("existe", True), "sin_datos": sin_datos})
        return filas

    def datos_cuenta(self, token):
        """(nombre, vencimiento AAAA-MM-DD o '') para que la app muestre "Nombre [dd/mm]" junto a «Conectado»,
        o None si ese token no es de ningún revendedor (o ya no existe en su VPS). Solo consulta la VPS por
        tokens que el bot conoce, así que nadie puede usar esto para hacer que el bot entre por SSH a cualquier lado.
        El resultado se guarda 60 s (30 s si la VPS no contestó)."""
        c = self.rev.cuentas_de_token(token)
        if not c:
            return None
        ahora = self.reloj()
        g = self._datos.get(token)
        if g and ahora - g[0] < g[2]:
            return g[1]
        try:
            m = self.buscar_maquina(c["maquina"])
            if not m:
                raise ErrorRevendedor("sin VPS")
            existe, nombre, vence = self._vps(self.ops.datos, m, token)
            res = (nombre or c["etq"], vence.isoformat() if vence else "") if existe else None
            vida = 60
        except ErrorRevendedor:
            res, vida = (c["etq"], ""), 30
        if len(self._datos) > 2000:
            self._datos.clear()
        self._datos[token] = (ahora, res, vida)
        return res

    @staticmethod
    def dias_validos(dias):
        return moneda_de(dias) is not None
