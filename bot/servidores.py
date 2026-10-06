"""Lista de servidores que lleva la app Android (mismo formato que android/servidores.txt).

El bot guarda la lista en /etc/zumo/app-servidores.json y, al compilar, la sube como texto al
secreto ZUMO_SERVIDORES del repositorio. Lo que se escribe acá tiene que entender el parser de la
app (android/.../Servidores.kt), así que las reglas de validación son las mismas.
"""
import re

SI = {"si", "sí", "s", "yes", "y", "true", "1", "on"}


def limpiar_nombre(n):
    """El nombre va entre corchetes en el archivo: sin [ ] = ni saltos de línea."""
    return re.sub(r"[\[\]=\r\n]", "", n or "").strip()[:40]


def limpiar_linea(t):
    return re.sub(r"\s*[\r\n]+\s*", "", t or "").strip()


def nuevo(nombre, host="", puerto=80, payload="", tls=False, sni=""):
    return {"name": limpiar_nombre(nombre), "host": host.strip(), "port": int(puerto),
            "payload": limpiar_linea(payload), "tls": bool(tls), "sni": sni.strip()}


def error_host(host):
    h = (host or "").strip()
    if not h or re.search(r"\s", h) or "/" in h or ":" in h:
        return "Host inválido: solo el dominio o IP, sin http:// ni espacios."
    return None


def valido(s):
    return bool(s["name"]) and error_host(s["host"]) is None and 1 <= int(s["port"]) <= 65535


def a_texto(lista):
    """Texto del secreto ZUMO_SERVIDORES. Los servidores incompletos no se incluyen."""
    bloques = []
    for s in lista:
        if not valido(s):
            continue
        bloques.append(f"[{s['name']}]\nhost = {s['host']}\npuerto = {int(s['port'])}\n"
                       f"tls = {'si' if s.get('tls') else 'no'}\nsni = {s.get('sni', '')}\n"
                       f"payload = {limpiar_linea(s.get('payload', ''))}\n")
    return "\n".join(bloques)


def desde_texto(texto):
    """Lee el formato de servidores.txt (igual que Servidores.parsear de la app)."""
    bloques, actual = [], None
    for cruda in (texto or "").lstrip("\ufeff").splitlines():
        l = cruda.strip()
        if not l or l.startswith("#"):
            continue
        if l.startswith("[") and l.endswith("]") and len(l) > 2:
            dentro = l[1:-1].strip()
            if dentro and not any(c in dentro for c in "[]="):
                actual = {"name": dentro, "host": "", "port": None, "payload": "", "tls": False, "sni": ""}
                bloques.append(actual)
                continue
        if actual is None:
            continue
        corte = next((i for i, c in enumerate(l) if c in "=:"), -1)
        if corte <= 0:
            continue
        k, v = l[:corte].strip().lower(), l[corte + 1:].strip()
        if k in ("host", "servidor", "dominio", "ip"):
            actual["host"] = v
        elif k in ("puerto", "port"):
            actual["port"] = int(v) if v.isdigit() else None
        elif k == "payload":
            actual["payload"] = v
        elif k in ("tls", "ssl"):
            actual["tls"] = v.lower() in SI
        elif k == "sni":
            actual["sni"] = v
    out, vistos = [], set()
    for b in bloques:
        if b["port"] is None:  # como la app: 80, o 443 si hay TLS
            b["port"] = 443 if b["tls"] else 80
        if not valido(b):
            continue
        base, k, unico = b["name"], 2, b["name"]
        while unico in vistos:
            unico = f"{base} ({k})"
            k += 1
        vistos.add(unico)
        out.append({**b, "name": unico})
    return out
