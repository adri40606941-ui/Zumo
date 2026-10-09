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


def nuevo(nombre, host="", puerto=80, payload="", tls=False, sni="", dns=""):
    return {"name": limpiar_nombre(nombre), "host": ",".join(lista_hosts(host)), "port": int(puerto),
            "payload": limpiar_linea(payload), "tls": bool(tls), "sni": sni.strip(), "dns": normalizar_dns(dns) or ""}


MAX_DNS = 4


def _ipv4(t):
    p = t.split(".")
    return len(p) == 4 and all(x.isdigit() and len(x) <= 3 and int(x) <= 255 for x in p)


def normalizar_dns(texto):
    """DNS que usa la app para ese servidor → "" (el del servidor) | "google" | "cloudflare" | "ip,ip".
    Igual que Dns.normalizar de la app. Devuelve None si el texto no sirve (ninguna IP válida)."""
    t = (texto or "").strip().lower()
    if t in ("", "-", "auto", "automatico", "automático", "servidor"):
        return ""
    if t in ("google", "8.8.8.8"):
        return "google"
    if t in ("cloudflare", "cf", "1.1.1.1"):
        return "cloudflare"
    ips = []
    for x in re.split(r"[,;\s]+", t):
        if _ipv4(x) and x not in ips:
            ips.append(x)
    return ",".join(ips[:MAX_DNS]) or None


def etiqueta_dns(dns):
    d = normalizar_dns(dns) or ""
    return {"": "automático (el del servidor)", "google": "Google (8.8.8.8)",
            "cloudflare": "Cloudflare (1.1.1.1)"}.get(d, d.replace(",", ", "))


MAX_HOSTS = 8


def lista_hosts(host):
    """Los dominios o IP de un servidor: se pueden poner varios, separados por coma, espacio o punto y coma."""
    return [h for h in re.split(r"[,;\s]+", host or "") if h]


def error_host(host):
    hs = lista_hosts(host)
    if not hs:
        return "Host inválido: falta el dominio o IP."
    if len(hs) > MAX_HOSTS:
        return f"Demasiados hosts (máximo {MAX_HOSTS})."
    if any("/" in h or ":" in h for h in hs):
        return "Host inválido: solo el dominio o IP, sin http:// ni puerto (el puerto va aparte)."
    return None


def normalizar_hosts(texto):
    """Texto escrito por el admin ("a.com, b.com:443", con http:// o no) → (hosts "a.com,b.com", puerto o None).
    El puerto es el del primer host que lo traiga. Si algo no sirve, devuelve (None, None)."""
    hosts, puerto = [], None
    for crudo in lista_hosts(texto):
        h = re.sub(r"^https?://", "", crudo).split("/")[0]
        if h.count(":") == 1:
            h, _, p = h.partition(":")
            if not (p.isdigit() and 1 <= int(p) <= 65535):
                return None, None
            if puerto is None:
                puerto = int(p)
        if h and h not in hosts:
            hosts.append(h)
    h = ",".join(hosts)
    return (h, puerto) if h and error_host(h) is None else (None, None)


def valido(s):
    return bool(s["name"]) and error_host(s["host"]) is None and 1 <= int(s["port"]) <= 65535


def a_texto(lista):
    """Texto del secreto ZUMO_SERVIDORES. Los servidores incompletos no se incluyen."""
    bloques = []
    for s in lista:
        if not valido(s):
            continue
        bloques.append(f"[{s['name']}]\nhost = {','.join(lista_hosts(s['host']))}\npuerto = {int(s['port'])}\n"
                       f"tls = {'si' if s.get('tls') else 'no'}\nsni = {s.get('sni', '')}\n"
                       f"dns = {normalizar_dns(s.get('dns', '')) or ''}\n"
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
                actual = {"name": dentro, "host": "", "port": None, "payload": "", "tls": False, "sni": "", "dns": ""}
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
        elif k == "dns":
            actual["dns"] = v
    out, vistos = [], set()
    for b in bloques:
        b["host"] = ",".join(lista_hosts(b["host"]))
        b["dns"] = normalizar_dns(b.get("dns", "")) or ""
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
