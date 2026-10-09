"""Revendedores: quién es cada uno, cuántas monedas tiene y qué usuarios (tokens) creó.

Monedas (cada una vale una duración):
    bronce = 7 días · plata = 15 días · oro = 30 días
Crear o renovar un usuario por 7 / 15 / 30 días gasta una moneda de bronce / plata / oro. Bloquear, desbloquear y
eliminar son gratis.

Se guarda en /etc/zumo/revendedores.json (0600). Las contraseñas no se guardan: solo su hash (scrypt).
Cada revendedor tiene asignada una máquina (las VPS que el bot maneja por SSH, ver maquinas.py) y solo puede tocar
los usuarios que él mismo creó. La lógica va en la clase Servicio, que usan el panel web y el bot.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

ARCHIVO = "/etc/zumo/revendedores.json"
# tipo de moneda -> días que vale (y emoji para el bot)
MONEDAS = {"bronce": 7, "plata": 15, "oro": 30}
EMOJI = {"bronce": "🥉", "plata": "🥈", "oro": "🥇"}
MAX_MAQUINAS = 6
USUARIO_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,19}$")
CLAVE_MIN, CLAVE_MAX = 6, 64
MAX_MOVIMIENTOS = 5000


class ErrorRevendedor(Exception):
    """Algo no se pudo hacer (el texto se muestra tal cual al revendedor o al administrador)."""


def moneda_de(dias):
    """'bronce' / 'plata' / 'oro' para 7 / 15 / 30 días; None para cualquier otra duración."""
    for tipo, d in MONEDAS.items():
        if d == dias:
            return tipo
    return None


def _hash_clave(clave, sal=None):
    sal = sal or secrets.token_bytes(16)
    h = hashlib.scrypt(clave.encode("utf-8"), salt=sal, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + sal.hex() + "$" + h.hex()


def _comprobar(clave, guardado):
    try:
        _, sal, h = guardado.split("$")
        nuevo = hashlib.scrypt(clave.encode("utf-8"), salt=bytes.fromhex(sal), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(nuevo.hex(), h)
    except (ValueError, TypeError):
        return False


_FALSO = _hash_clave("no-existe")      # para que probar un usuario inexistente tarde lo mismo que uno real


def clave_valida(clave):
    return isinstance(clave, str) and CLAVE_MIN <= len(clave) <= CLAVE_MAX and clave == clave.strip() and "\n" not in clave


def clave_nueva(n=10):
    """Contraseña aleatoria fácil de dictar (sin 0/O/1/l/I)."""
    alfabeto = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alfabeto) for _ in range(n))


class Revendedores:
    def __init__(self, archivo=None, reloj=time.time):
        self.archivo = archivo or os.environ.get("ZUMO_REVENDEDORES") or ARCHIVO
        self.reloj = reloj
        self.lock = threading.RLock()

    # -- disco
    def _leer(self):
        try:
            with open(self.archivo, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        d.setdefault("n", 0)
        d.setdefault("revendedores", {})
        d.setdefault("cuentas", {})
        d.setdefault("mov", [])
        # Migración: antes un revendedor tenía una sola VPS ("maquina") y las cuentas no sabían en cuál estaban.
        for r in d["revendedores"].values():
            if "maquinas" not in r:
                r["maquinas"] = [r["maquina"]] if r.get("maquina") else []
            r["maquina"] = r["maquinas"][0] if r["maquinas"] else ""
        for c in d["cuentas"].values():
            if "maq" not in c:
                r = d["revendedores"].get(c.get("rev"))
                c["maq"] = list(r["maquinas"]) if r else []
        return d

    def _guardar(self, d):
        os.makedirs(os.path.dirname(self.archivo) or ".", exist_ok=True)
        tmp = self.archivo + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, self.archivo)

    def _mov(self, d, rev, que, **datos):
        d["mov"].append({"t": int(self.reloj()), "rev": rev, "que": que, **datos})
        d["mov"] = d["mov"][-MAX_MOVIMIENTOS:]

    # -- revendedores
    def crear(self, usuario, clave, maquina):
        usuario = (usuario or "").strip().lower()
        if not USUARIO_RE.match(usuario):
            raise ErrorRevendedor("Usuario inválido: de 3 a 20 letras minúsculas, números, punto, guion o guion bajo.")
        if not clave_valida(clave):
            raise ErrorRevendedor(f"Contraseña inválida: de {CLAVE_MIN} a {CLAVE_MAX} caracteres, sin espacios al borde.")
        with self.lock:
            d = self._leer()
            if any(r["usuario"] == usuario for r in d["revendedores"].values()):
                raise ErrorRevendedor("Ese usuario de revendedor ya existe.")
            d["n"] += 1
            rid = str(d["n"])
            d["revendedores"][rid] = {"id": rid, "usuario": usuario, "hash": _hash_clave(clave), "maquina": maquina,
                                      "maquinas": [maquina] if maquina else [],
                                      "activo": True, "creado": int(self.reloj()),
                                      "monedas": {t: 0 for t in MONEDAS}}
            self._mov(d, rid, "alta")
            self._guardar(d)
            return self._publico(d["revendedores"][rid])

    @staticmethod
    def _publico(r):
        return {k: v for k, v in r.items() if k != "hash"} | {"monedas": dict(r["monedas"]),
                                                              "maquinas": list(r.get("maquinas", []))}

    def listar(self):
        with self.lock:
            return [self._publico(r) for r in self._leer()["revendedores"].values()]

    def buscar(self, rid):
        with self.lock:
            r = self._leer()["revendedores"].get(str(rid))
            return self._publico(r) if r else None

    def verificar(self, usuario, clave):
        """El revendedor si el usuario y la contraseña son correctos y está activo; si no, None."""
        usuario = (usuario or "").strip().lower()
        with self.lock:
            r = next((x for x in self._leer()["revendedores"].values() if x["usuario"] == usuario), None)
        ok = _comprobar(clave or "", r["hash"] if r else _FALSO)
        return self._publico(r) if (r and ok and r["activo"]) else None

    def _editar(self, rid, f):
        with self.lock:
            d = self._leer()
            r = d["revendedores"].get(str(rid))
            if not r:
                raise ErrorRevendedor("Ese revendedor ya no existe.")
            f(r, d)
            self._guardar(d)
            return self._publico(r)

    def cambiar_clave(self, rid, clave):
        if not clave_valida(clave):
            raise ErrorRevendedor(f"Contraseña inválida: de {CLAVE_MIN} a {CLAVE_MAX} caracteres, sin espacios al borde.")
        return self._editar(rid, lambda r, d: (r.update(hash=_hash_clave(clave)), self._mov(d, r["id"], "clave")))

    def activar(self, rid, activo):
        return self._editar(rid, lambda r, d: (r.update(activo=bool(activo)),
                                              self._mov(d, r["id"], "activo" if activo else "bloqueado")))

    def asignar_maquina(self, rid, maquina):
        """Deja a esa VPS como la única del revendedor (las demás se le sacan)."""
        return self._editar(rid, lambda r, d: r.update(maquina=maquina, maquinas=[maquina]))

    def agregar_maquina(self, rid, maquina):
        """Le suma otra VPS: los usuarios nuevos se crean en todas. Los que ya tenía se completan solos después."""
        def f(r, d):
            if maquina in r["maquinas"]:
                raise ErrorRevendedor("Esa VPS ya está asignada a este revendedor.")
            if len(r["maquinas"]) >= MAX_MAQUINAS:
                raise ErrorRevendedor(f"Como mucho {MAX_MAQUINAS} VPS por revendedor.")
            r["maquinas"].append(maquina)
            r["maquina"] = r["maquinas"][0]
            self._mov(d, r["id"], "vps+", maq=maquina)
        return self._editar(rid, f)

    def quitar_maquina(self, rid, maquina):
        """Le saca una VPS (tiene que quedarle al menos una). Sus usuarios siguen en esa VPS hasta que los elimine."""
        def f(r, d):
            if maquina not in r["maquinas"]:
                raise ErrorRevendedor("Esa VPS no está asignada a este revendedor.")
            if len(r["maquinas"]) < 2:
                raise ErrorRevendedor("Tiene que quedarle al menos una VPS.")
            r["maquinas"].remove(maquina)
            r["maquina"] = r["maquinas"][0]
            self._mov(d, r["id"], "vps-", maq=maquina)
        return self._editar(rid, f)

    def eliminar(self, rid):
        """Borra al revendedor. Los usuarios que creó siguen en la VPS (nadie los puede tocar desde el panel)."""
        with self.lock:
            d = self._leer()
            if str(rid) not in d["revendedores"]:
                raise ErrorRevendedor("Ese revendedor ya no existe.")
            del d["revendedores"][str(rid)]
            d["cuentas"] = {t: c for t, c in d["cuentas"].items() if c["rev"] != str(rid)}
            self._guardar(d)

    # -- monedas
    def agregar_monedas(self, rid, tipo, cantidad):
        """Suma (o resta, con cantidad negativa) monedas. Nunca baja de cero."""
        if tipo not in MONEDAS:
            raise ErrorRevendedor("Esa moneda no existe.")

        def f(r, d):
            nuevo = r["monedas"].get(tipo, 0) + int(cantidad)
            if nuevo < 0:
                raise ErrorRevendedor(f"Solo tiene {r['monedas'].get(tipo, 0)} de {tipo}: no se le pueden quitar {-int(cantidad)}.")
            r["monedas"][tipo] = nuevo
            self._mov(d, r["id"], "monedas", tipo=tipo, n=int(cantidad))
        return self._editar(rid, f)

    def gastar(self, rid, dias):
        """Gasta la moneda que corresponde a esos días. Devuelve el tipo gastado."""
        tipo = moneda_de(dias)
        if not tipo:
            raise ErrorRevendedor("Solo se puede de 7, 15 o 30 días.")

        def f(r, d):
            if r["monedas"].get(tipo, 0) < 1:
                raise ErrorRevendedor(f"No disponés de esa moneda: no tenés monedas de {tipo} ({MONEDAS[tipo]} días).")
            r["monedas"][tipo] -= 1
        self._editar(rid, f)
        return tipo

    def devolver(self, rid, tipo):
        try:
            self.agregar_monedas(rid, tipo, 1)
        except ErrorRevendedor:
            pass

    # -- usuarios (tokens) de cada revendedor
    def registrar_cuenta(self, token, rid, etiqueta, dias=0, tipo="", maq=None):
        """Anota un usuario nuevo y en qué VPS quedó (por omisión, en todas las del revendedor)."""
        with self.lock:
            d = self._leer()
            r = d["revendedores"].get(str(rid))
            maq = list(maq) if maq is not None else (list(r["maquinas"]) if r else [])
            d["cuentas"][token] = {"rev": str(rid), "etq": etiqueta, "creado": int(self.reloj()), "maq": maq}
            self._mov(d, str(rid), "crear", token=token, dias=dias, moneda=tipo, maq=maq)
            self._guardar(d)

    def anotar(self, rid, que, token, dias=0, tipo=""):
        with self.lock:
            d = self._leer()
            self._mov(d, str(rid), que, token=token, dias=dias, moneda=tipo)
            self._guardar(d)

    def cuentas_de(self, rid):
        with self.lock:
            return {t: dict(c) for t, c in self._leer()["cuentas"].items() if c["rev"] == str(rid)}

    def dueno(self, token):
        with self.lock:
            c = self._leer()["cuentas"].get(token)
            return c["rev"] if c else None

    def cuentas_de_token(self, token):
        """{'rev', 'etq', 'maquinas'} del usuario con ese token (las VPS donde está), o None."""
        with self.lock:
            d = self._leer()
            c = d["cuentas"].get(token)
            r = d["revendedores"].get(c["rev"]) if c else None
            return {"rev": c["rev"], "etq": c.get("etq", ""), "maquinas": list(c["maq"])} if r else None

    def poner_maq_cuenta(self, token, maq):
        """Corrige en qué VPS está el usuario (después de completarlo en una VPS nueva o de un fallo)."""
        with self.lock:
            d = self._leer()
            if token in d["cuentas"]:
                d["cuentas"][token]["maq"] = list(maq)
                self._guardar(d)

    def todas_las_cuentas(self):
        """{token: {'rev','etq','creado','maq'}} de todos los revendedores."""
        with self.lock:
            return {t: dict(c, maq=list(c["maq"])) for t, c in self._leer()["cuentas"].items()}

    def resumen(self, desde, hasta=None):
        """Por revendedor, desde el momento `desde` (epoch): monedas que le cargó el admin, monedas que gastó,
        saldo y usuarios. {id: {...}}"""
        with self.lock:
            d = self._leer()
        out = {}
        for rid, r in d["revendedores"].items():
            out[rid] = {"usuario": r["usuario"], "saldo": dict(r["monedas"]), "cargadas": {t: 0 for t in MONEDAS},
                        "gastadas": {t: 0 for t in MONEDAS}, "usuarios": 0, "activo": r["activo"]}
        for c in d["cuentas"].values():
            if c["rev"] in out:
                out[c["rev"]]["usuarios"] += 1
        for m in d["mov"]:
            o = out.get(m["rev"])
            if not o or m["t"] < desde or (hasta is not None and m["t"] >= hasta):
                continue
            if m["que"] == "monedas" and m.get("n", 0) > 0 and m.get("tipo") in MONEDAS:
                o["cargadas"][m["tipo"]] += m["n"]
            elif m["que"] in ("crear", "renovar") and m.get("moneda") in MONEDAS:
                o["gastadas"][m["moneda"]] += 1
        return out

    def renombrar_cuenta(self, token, etiqueta):
        with self.lock:
            d = self._leer()
            if token in d["cuentas"]:
                d["cuentas"][token]["etq"] = etiqueta
                self._guardar(d)

    def quitar_cuenta(self, token):
        with self.lock:
            d = self._leer()
            d["cuentas"].pop(token, None)
            self._guardar(d)

    def movimientos(self, rid, n=20):
        with self.lock:
            return [m for m in self._leer()["mov"] if m["rev"] == str(rid)][-n:]
