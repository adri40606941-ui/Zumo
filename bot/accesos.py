"""Códigos de un solo uso para instalar en una VPS nueva, y las VPS que ya los usaron.

Flujo: el bot crea un código corto (vale pocos minutos y sirve una sola vez). En la VPS nueva, el cargador
(`/i` del dominio) pide el código y lo canjea: el código se gasta y a cambio la VPS recibe un pase propio
(32 hex). Ese pase va en la dirección de descarga (`https://dominio/s/<pase>/install.sh`), así las
actualizaciones del panel siguen andando sin pedir nada, y se puede anular desde el bot VPS por VPS.

Se guarda solo el hash de los códigos y de los pases, en /etc/zumo/accesos.json (0600)."""
import hashlib
import json
import os
import secrets
import threading
import time

ARCHIVO = "/etc/zumo/accesos.json"
ALFABETO = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"     # sin 0/O/1/I para dictarlo o copiarlo sin errores
VIDA_CODIGO = 15 * 60
TIPOS = {"panel": "Panel de usuarios", "bot": "Bot de Telegram"}
MAX_FALLOS, VENTANA, BLOQUEO = 10, 60, 120


def _hash(x):
    return hashlib.sha256(x.encode()).hexdigest()


def normalizar(codigo):
    """'k7m2-x9qp ' -> 'K7M2X9QP'. Cualquier otra cosa no es un código."""
    c = "".join(ch for ch in (codigo or "").upper() if ch not in "- \t\r\n")
    return c if len(c) == 8 and all(ch in ALFABETO for ch in c) else ""


class Accesos:
    def __init__(self, archivo=None, reloj=time.time):
        self.archivo = archivo or os.environ.get("ZUMO_ACCESOS") or ARCHIVO
        self.reloj = reloj
        self.lock = threading.Lock()
        self.fallos = []          # momentos de canjes fallidos (para frenar a quien prueba códigos)
        self.bloqueado_hasta = 0

    # -- disco
    def _leer(self):
        try:
            with open(self.archivo, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        d.setdefault("codigos", {})
        d.setdefault("vps", [])
        d.setdefault("n", 0)
        return d

    def _guardar(self, d):
        os.makedirs(os.path.dirname(self.archivo) or ".", exist_ok=True)
        tmp = self.archivo + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, self.archivo)

    # -- códigos
    def crear_codigo(self, tipo):
        """Devuelve el código en claro ('K7M2-X9QP'); solo se ve ahora, después queda su hash."""
        if tipo not in TIPOS:
            raise ValueError("tipo desconocido")
        c = "".join(secrets.choice(ALFABETO) for _ in range(8))
        with self.lock:
            d = self._leer()
            ahora = self.reloj()
            d["codigos"] = {h: v for h, v in d["codigos"].items() if v["vence"] > ahora}
            d["codigos"][_hash(c)] = {"tipo": tipo, "vence": ahora + VIDA_CODIGO}
            self._guardar(d)
        return c[:4] + "-" + c[4:]

    def canjear(self, codigo, ip=""):
        """Gasta el código y devuelve (pase, tipo); (None, None) si no sirve o hay que esperar."""
        with self.lock:
            ahora = self.reloj()
            if ahora < self.bloqueado_hasta:
                return None, None
            c = normalizar(codigo)
            d = self._leer()
            v = d["codigos"].pop(_hash(c), None) if c else None
            if not v or v["vence"] <= ahora:
                self.fallos = [t for t in self.fallos if ahora - t < VENTANA] + [ahora]
                if len(self.fallos) >= MAX_FALLOS:
                    self.bloqueado_hasta = ahora + BLOQUEO
                    self.fallos = []
                return None, None
            pase = secrets.token_hex(16)
            d["n"] += 1
            d["vps"].append({"id": d["n"], "hash": _hash(pase), "tipo": v["tipo"], "ip": ip[:45],
                             "fecha": int(ahora)})
            self._guardar(d)
            return pase, v["tipo"]

    # -- pases
    def valido(self, pase):
        if not pase or len(pase) != 32:
            return False
        h = _hash(pase)
        with self.lock:
            return any(secrets.compare_digest(h, x["hash"]) for x in self._leer()["vps"])

    def listar(self):
        with self.lock:
            return [{k: x[k] for k in ("id", "tipo", "ip", "fecha")} for x in self._leer()["vps"]]

    def revocar(self, n):
        with self.lock:
            d = self._leer()
            antes = len(d["vps"])
            d["vps"] = [x for x in d["vps"] if x["id"] != n]
            if len(d["vps"]) == antes:
                return False
            self._guardar(d)
            return True

    def codigos_vigentes(self):
        with self.lock:
            ahora = self.reloj()
            return sum(1 for v in self._leer()["codigos"].values() if v["vence"] > ahora)
