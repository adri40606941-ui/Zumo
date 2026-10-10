"""Lo que puede hacer un revendedor, con sus reglas: gastar monedas, tocar solo sus usuarios y hablar con sus VPS.

Lo usan el panel web (panel_web.py), el bot y las pruebas. Todo error se levanta como ErrorRevendedor, con un texto
que se puede mostrar tal cual en pantalla.

Un revendedor puede tener varias VPS, pero cada usuario vive en UNA sola: la que elige el revendedor al crearlo o, si no
elige, la que tenga menos usuarios (y si esa no contesta, la siguiente). Renovar, bloquear, eliminar y cambiar el nombre
se hacen en la VPS de ese usuario.
"""
import threading
import time
from datetime import date

import cuentas_vps as cv
import maquinas as mq
from revendedores import ErrorRevendedor, moneda_de


class Servicio:
    def __init__(self, rev, buscar_maquina=None, ops=None, hoy=date.today, reloj=time.time, notificar=None):
        self.reloj = reloj
        self._datos = {}         # token -> (momento, resultado, vida) de datos_cuenta
        self.rev = rev
        self.buscar_maquina = buscar_maquina or mq.buscar
        self.ops = ops or cv
        self.hoy = hoy
        self.notificar = notificar or (lambda texto: None)      # el bot lo cambia por "avisar a los admins"
        self._candados = {}
        self._lock = threading.Lock()

    def _candado(self, rid):
        with self._lock:
            return self._candados.setdefault(str(rid), threading.Lock())

    # -- ayudas
    def _resolver(self, mid):
        """(máquina o None, texto de error)."""
        try:
            m = self.buscar_maquina(mid)
        except mq.ErrorMaquina as e:
            return None, str(e)
        return (m, "") if m else (None, "esa VPS ya no está enlazada")

    def _nombre(self, mid):
        m, _ = self._resolver(mid)
        return m["nombre"] if m else f"VPS {mid}"

    def nombres(self, ids):
        return [self._nombre(i) for i in ids]

    def _activo(self, rid):
        r = self.rev.buscar(rid)
        if not r or not r["activo"]:
            raise ErrorRevendedor("Tu cuenta de revendedor está bloqueada o ya no existe.")
        return r

    def _propio(self, rid, token):
        c = self.rev.cuentas_de_token(token)
        if not c or c["rev"] != str(rid):
            raise ErrorRevendedor("Ese usuario no es tuyo.")
        return c

    def _vps(self, f, *a, **k):
        try:
            return f(*a, **k)
        except (cv.ErrorCuenta, mq.ErrorMaquina) as e:
            raise ErrorRevendedor(str(e))

    def _aplicar(self, ids, f):
        """Corre f(máquina) en cada VPS. Devuelve ([(id, resultado)], [(id, nombre, motivo)])."""
        oks, fallos = [], []
        for mid in ids:
            m, err = self._resolver(mid)
            if not m:
                fallos.append((mid, f"VPS {mid}", err))
                continue
            try:
                oks.append((mid, f(m)))
            except ErrorRevendedor as ex:
                fallos.append((mid, m["nombre"], str(ex)))
        return oks, fallos

    @staticmethod
    def _motivos(fallos):
        return "; ".join(f"{n}: {t}" for _, n, t in fallos)

    def _avisar_saldo(self, rid, tipo):
        r = self.rev.buscar(rid)
        if r and r["monedas"].get(tipo, 0) == 0:
            self.notificar(f"🪙 {r['usuario']} se quedó sin monedas de {tipo} ({moneda_de_dias(tipo)} días).")

    # -- acciones
    def elegir_vps(self, r, vps=""):
        """Las VPS donde probar crear un usuario, en orden: la elegida, o todas empezando por la de menos usuarios."""
        if vps:
            if vps not in r["maquinas"]:
                raise ErrorRevendedor("Esa VPS no es una de las tuyas.")
            return [vps]
        carga = self.cargas(r)
        return sorted(r["maquinas"], key=lambda i: (carga[i], r["maquinas"].index(i)))

    def cargas(self, r):
        """{id de VPS: cantidad de usuarios de este revendedor} para mostrar en el formulario."""
        carga = {i: 0 for i in r["maquinas"]}
        for c in self.rev.cuentas_de(r["id"]).values():
            for i in c["maq"]:
                if i in carga:
                    carga[i] += 1
        return carga

    def crear(self, rid, token, nombre, dias, vps=""):
        with self._candado(rid):
            r = self._activo(rid)
            if not r["maquinas"]:
                raise ErrorRevendedor("Todavía no tenés ninguna VPS asignada. Avisale al administrador.")
            token = (token or "").strip()
            if self.rev.dueno(token):
                raise ErrorRevendedor("Ese token ya está registrado.")
            candidatas = self.elegir_vps(r, vps)
            tipo = self.rev.gastar(rid, dias)
            usada, exp, fallos = None, None, []
            for mid in candidatas:           # la primera que conteste
                oks, f = self._aplicar([mid], lambda m: self._vps(self.ops.crear, m, token, nombre, dias, hoy=self.hoy()))
                if oks:
                    usada, exp = mid, oks[0][1]
                    break
                fallos += f
            if usada is None:
                self.rev.devolver(rid, tipo)
                raise ErrorRevendedor(fallos[0][2] if len(fallos) == 1 else "No pude crearlo en ninguna VPS. " + self._motivos(fallos))
            etq = cv.limpiar_nombre(nombre)
            self.rev.registrar_cuenta(token, rid, etq, dias, tipo, maq=[usada])
            res = {"token": token, "vence": exp, "moneda": tipo, "vps": self.nombres([usada]),
                   "fallaron": [n for _, n, _ in fallos], "motivos": self._motivos(fallos)}
            self.notificar(f"🆕 {r['usuario']} creó a {etq} ({dias} días, vence {exp:%d/%m/%Y})\n"
                           f"🔑 {token}\n🖥 VPS: {res['vps'][0]}"
                           + (f"\n⚠️ {', '.join(res['fallaron'])} no contestó; se usó otra." if fallos else ""))
            self._avisar_saldo(rid, tipo)
            return res

    def renovar(self, rid, token, dias):
        with self._candado(rid):
            self._activo(rid)
            c = self._propio(rid, token)
            tipo = self.rev.gastar(rid, dias)
            oks, fallos = self._aplicar(c["maquinas"], lambda m: self._vps(self.ops.renovar, m, token, dias, hoy=self.hoy()))
            if not oks:
                self.rev.devolver(rid, tipo)
                raise ErrorRevendedor(fallos[0][2] if len(fallos) == 1 else "No pude renovarlo. " + self._motivos(fallos))
            self.rev.anotar(rid, "renovar", token, dias, tipo)
            self._datos.pop(token, None)
            self._avisar_saldo(rid, tipo)
            return {"token": token, "vence": oks[0][1], "moneda": tipo, "fallaron": [], "motivos": ""}

    def bloquear(self, rid, token, si=True):
        with self._candado(rid):
            self._activo(rid)
            c = self._propio(rid, token)
            oks, fallos = self._aplicar(c["maquinas"], lambda m: self._vps(self.ops.bloquear, m, token, si))
            if not oks:
                raise ErrorRevendedor(fallos[0][2] if len(fallos) == 1 else self._motivos(fallos))
            self.rev.anotar(rid, "bloquear" if si else "desbloquear", token)
            return {"fallaron": [n for _, n, _ in fallos], "motivos": self._motivos(fallos)}

    def renombrar(self, rid, token, nombre):
        with self._candado(rid):
            self._activo(rid)
            c = self._propio(rid, token)
            oks, fallos = self._aplicar(c["maquinas"], lambda m: self._vps(self.ops.renombrar, m, token, nombre))
            if not oks:
                raise ErrorRevendedor(fallos[0][2] if len(fallos) == 1 else self._motivos(fallos))
            n = oks[0][1]
            self.rev.renombrar_cuenta(token, n)
            self._datos.pop(token, None)
            self.rev.anotar(rid, "nombre", token)
            return {"nombre": n, "fallaron": [x for _, x, _ in fallos], "motivos": self._motivos(fallos)}

    def eliminar(self, rid, token):
        """Lo borra de su VPS. Si esa no contesta, el usuario queda anotado para reintentar."""
        with self._candado(rid):
            self._activo(rid)
            c = self._propio(rid, token)
            oks, fallos = [], []
            for mid in c["maquinas"]:
                m, err = self._resolver(mid)
                if not m:
                    oks.append(mid)         # VPS desenlazada: no hay nada más que hacer ahí
                    continue
                try:
                    self._vps(self.ops.eliminar, m, token)
                    oks.append(mid)
                except ErrorRevendedor as ex:
                    fallos.append((mid, m["nombre"], str(ex)))
            if fallos:
                self.rev.poner_maq_cuenta(token, [i for i, _, _ in fallos])
                raise ErrorRevendedor(f"Se eliminó en algunas VPS pero no en: {self._motivos(fallos)}. Probá de nuevo en un momento.")
            self.rev.quitar_cuenta(token)
            self._datos.pop(token, None)
            self.rev.anotar(rid, "eliminar", token)

    # -- lectura
    def listar(self, rid):
        """Sus usuarios, con vencimiento, bloqueo y conexión según las VPS. Si una VPS no contesta, sale lo que se pudo."""
        self._activo(rid)
        propias = self.rev.cuentas_de(rid)
        por_vps = {}
        for t, c in propias.items():
            for mid in c["maq"]:
                por_vps.setdefault(mid, []).append(t)
        info = {t: [] for t in propias}      # token -> [estado en cada VPS que contestó]
        mudas = set()
        for mid, tokens in por_vps.items():
            m, _ = self._resolver(mid)
            try:
                if not m:
                    raise ErrorRevendedor("sin VPS")
                est = self._vps(self.ops.estado, m, tokens)
            except ErrorRevendedor:
                mudas.add(mid)
                continue
            for t in tokens:
                if t in est:
                    info[t].append(est[t])
        filas = []
        for t, c in sorted(propias.items(), key=lambda x: -x[1].get("creado", 0)):
            ests = info[t]
            sin_datos = not ests
            venc = [e["vence"] for e in ests if e.get("vence")]
            filas.append({
                "token": t, "nombre": c.get("etq", ""), "creado": c.get("creado", 0),
                "vence": max(venc) if venc else None,
                "bloqueado": any(e.get("bloqueado") for e in ests),
                "conectado": any(e.get("conectado") for e in ests),
                "existe": True if sin_datos else any(e.get("existe") for e in ests),
                "sin_datos": sin_datos or any(i in mudas for i in c["maq"]),
                "maquinas": self.nombres(c["maq"]),
            })
        return filas

    def datos_cuenta(self, token):
        """(nombre, vencimiento AAAA-MM-DD o '') para que la app muestre "Nombre [dd/mm]" junto a «Conectado»,
        o None si ese token no es de ningún revendedor (o ya no existe en sus VPS). Solo consulta las VPS por
        tokens que el bot conoce, así que nadie puede usar esto para hacer que el bot entre por SSH a cualquier lado.
        El resultado se guarda 60 s (30 s si ninguna VPS contestó)."""
        c = self.rev.cuentas_de_token(token)
        if not c:
            return None
        ahora = self.reloj()
        g = self._datos.get(token)
        if g and ahora - g[0] < g[2]:
            return g[1]
        contesto, res = False, None
        for mid in c["maquinas"]:
            m, _ = self._resolver(mid)
            if not m:
                continue
            try:
                existe, nombre, vence = self._vps(self.ops.datos, m, token)
            except ErrorRevendedor:
                continue
            contesto = True
            if existe:
                res = (nombre or c["etq"], vence.isoformat() if vence else "")
                break
        vida = 60
        if not contesto:
            res, vida = (c["etq"], ""), 30
        if len(self._datos) > 2000:
            self._datos.clear()
        self._datos[token] = (ahora, res, vida)
        return res

    @staticmethod
    def dias_validos(dias):
        return moneda_de(dias) is not None


def moneda_de_dias(tipo):
    from revendedores import MONEDAS
    return MONEDAS[tipo]
