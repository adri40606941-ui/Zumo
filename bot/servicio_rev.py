"""Lo que puede hacer un revendedor, con sus reglas: gastar monedas, tocar solo sus usuarios y hablar con sus VPS.

Lo usan el panel web (panel_web.py), el bot y las pruebas. Todo error se levanta como ErrorRevendedor, con un texto
que se puede mostrar tal cual en pantalla.

Un revendedor puede tener varias VPS: cada usuario nuevo se crea en todas, y renovar, bloquear, eliminar o cambiar el
nombre se aplica en todas las que tiene ese usuario. Si una VPS no contesta, la operación sigue en las demás y se avisa
cuáles fallaron; `reparar()` (la corre el bot cada tanto) completa después los usuarios que quedaron sin crear en una
VPS y iguala los vencimientos.
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
    def crear(self, rid, token, nombre, dias):
        with self._candado(rid):
            r = self._activo(rid)
            if not r["maquinas"]:
                raise ErrorRevendedor("Todavía no tenés ninguna VPS asignada. Avisale al administrador.")
            token = (token or "").strip()
            if self.rev.dueno(token):
                raise ErrorRevendedor("Ese token ya está registrado.")
            tipo = self.rev.gastar(rid, dias)
            oks, fallos = self._aplicar(r["maquinas"], lambda m: self._vps(self.ops.crear, m, token, nombre, dias, hoy=self.hoy()))
            if not oks:
                self.rev.devolver(rid, tipo)
                raise ErrorRevendedor(fallos[0][2] if len(fallos) == 1 else "No pude crearlo en ninguna VPS. " + self._motivos(fallos))
            exp = oks[0][1]
            usadas = [i for i, _ in oks]
            etq = cv.limpiar_nombre(nombre)
            self.rev.registrar_cuenta(token, rid, etq, dias, tipo, maq=usadas)
            res = {"token": token, "vence": exp, "moneda": tipo, "vps": self.nombres(usadas),
                   "fallaron": [n for _, n, _ in fallos], "motivos": self._motivos(fallos)}
            self.notificar(f"🆕 {r['usuario']} creó a {etq} ({dias} días, vence {exp:%d/%m/%Y})\n"
                           f"🔑 {token}\n🖥 VPS: {', '.join(res['vps'])}"
                           + (f"\n⚠️ No se pudo en: {', '.join(res['fallaron'])} (se completa solo)" if fallos else ""))
            self._avisar_saldo(rid, tipo)
            return res

    def renovar(self, rid, token, dias):
        with self._candado(rid):
            self._activo(rid)
            c = self._propio(rid, token)
            tipo = self.rev.gastar(rid, dias)
            fijo = []                   # el primer vencimiento calculado: las demás VPS quedan en el mismo día

            def una(m):
                e = self._vps(self.ops.renovar, m, token, dias, hoy=self.hoy(), exp=fijo[0] if fijo else None)
                if not fijo:
                    fijo.append(e)
                return e
            oks, fallos = self._aplicar(c["maquinas"], una)
            if not oks:
                self.rev.devolver(rid, tipo)
                raise ErrorRevendedor(fallos[0][2] if len(fallos) == 1 else "No pude renovarlo en ninguna VPS. " + self._motivos(fallos))
            self.rev.anotar(rid, "renovar", token, dias, tipo)
            self._datos.pop(token, None)
            self._avisar_saldo(rid, tipo)
            return {"token": token, "vence": oks[0][1], "moneda": tipo,
                    "fallaron": [n for _, n, _ in fallos], "motivos": self._motivos(fallos)}

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
        """Lo borra de todas sus VPS. Si alguna no contesta, el usuario queda anotado solo con esas, para reintentar."""
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

    # -- mantenimiento (lo corre el bot cada tanto)
    def reparar(self):
        """Completa los usuarios que faltan en alguna VPS del revendedor (una VPS recién agregada, o una que no
        contestó al crearlos) e iguala los vencimientos. Devuelve la lista de cosas que hizo, en texto."""
        hechos = []
        cuentas = self.rev.todas_las_cuentas()
        revs = {r["id"]: r for r in self.rev.listar()}
        # 1) vencimiento y estado de cada usuario en las VPS donde está
        por_vps = {}
        for t, c in cuentas.items():
            for mid in c["maq"]:
                por_vps.setdefault(mid, []).append(t)
        estado = {}                           # (token, vps) -> estado
        for mid, tokens in por_vps.items():
            m, _ = self._resolver(mid)
            if not m:
                continue
            try:
                for t, e in self._vps(self.ops.estado, m, tokens).items():
                    estado[(t, mid)] = e
            except ErrorRevendedor:
                continue
        for t, c in cuentas.items():
            r = revs.get(c["rev"])
            if not r:
                continue
            with self._candado(c["rev"]):
                hechos += self._reparar_uno(t, c, r, estado)
        return hechos

    def _reparar_uno(self, t, c, r, estado):
        hechos = []
        vivos = {mid: estado[(t, mid)] for mid in c["maq"] if (t, mid) in estado and estado[(t, mid)].get("existe")}
        if not vivos:
            return hechos
        venc = [e["vence"] for e in vivos.values() if e.get("vence")]
        mayor = max(venc) if venc else None
        bloq = any(e.get("bloqueado") for e in vivos.values())
        # igualar vencimientos
        if mayor:
            for mid, e in vivos.items():
                if e.get("vence") != mayor:
                    m, _ = self._resolver(mid)
                    try:
                        self._vps(self.ops.fijar_vence, m, t, mayor)
                        hechos.append(f"📅 {c['etq']}: vencimiento igualado en {self._nombre(mid)}")
                    except (ErrorRevendedor, AttributeError):
                        pass
        # completar en las VPS del revendedor donde no está
        faltan = [mid for mid in r["maquinas"] if mid not in c["maq"]]
        agregados = []
        for mid in faltan:
            m, _ = self._resolver(mid)
            if not m:
                continue
            try:
                self._vps(self.ops.crear, m, t, c["etq"], 0, hoy=mayor or self.hoy())
                if bloq:
                    self._vps(self.ops.bloquear, m, t, True)
                agregados.append(mid)
                hechos.append(f"➕ {c['etq']}: creado en {m['nombre']}")
            except ErrorRevendedor:
                pass
        if agregados:
            self.rev.poner_maq_cuenta(t, list(c["maq"]) + agregados)
            self._datos.pop(t, None)
        return hechos

    @staticmethod
    def dias_validos(dias):
        return moneda_de(dias) is not None


def moneda_de_dias(tipo):
    from revendedores import MONEDAS
    return MONEDAS[tipo]
