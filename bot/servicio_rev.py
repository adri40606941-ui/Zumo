"""Lo que puede hacer un revendedor, con sus reglas: gastar monedas, tocar solo sus usuarios y hablar con su VPS.

Lo usan el panel web (panel_web.py) y las pruebas. Todo error se levanta como ErrorRevendedor, con un texto que se
puede mostrar tal cual en pantalla.
"""
import threading
from datetime import date

import cuentas_vps as cv
import maquinas as mq
from revendedores import ErrorRevendedor, moneda_de


class Servicio:
    def __init__(self, rev, buscar_maquina=None, ops=None, hoy=date.today):
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
            return {"token": token, "vence": exp, "moneda": tipo}

    def bloquear(self, rid, token, si=True):
        with self._candado(rid):
            r, m = self._contexto(rid)
            self._propio(rid, token)
            self._vps(self.ops.bloquear, m, token, si)
            self.rev.anotar(rid, "bloquear" if si else "desbloquear", token)

    def eliminar(self, rid, token):
        with self._candado(rid):
            r, m = self._contexto(rid)
            self._propio(rid, token)
            self._vps(self.ops.eliminar, m, token)
            self.rev.quitar_cuenta(token)
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
                          "bloqueado": e.get("bloqueado", False), "existe": e.get("existe", True), "sin_datos": sin_datos})
        return filas

    @staticmethod
    def dias_validos(dias):
        return moneda_de(dias) is not None
