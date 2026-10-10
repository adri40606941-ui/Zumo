"""Lista de servidores que lleva la app Android (mismo formato que android/servidores.txt).

El bot guarda la lista en /etc/zumo/app-servidores.json y, al compilar, la sube como texto al
secreto ZUMO_SERVIDORES del repositorio. Lo que se escribe acá tiene que entender el parser de la
app (android/.../Servidores.kt), así que las reglas de validación son las mismas.
"""
import re
import threading
import time

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


MAX_RANGO = 65536        # un /16 como máximo (la app prueba hasta 1024 IP al azar de ahí); igual que Rangos.kt


def _ip_num(t):
    return int.from_bytes(bytes(int(x) for x in t.split(".")), "big") if _ipv4(t) else None


def limites_rango(texto):
    """Rango de IP que la app sabe recorrer → (primera, última) como números, o None si no es un rango válido.
    Formas: 104.16.0.0/24 (de /16 a /32) · 104.16.1.10-104.16.1.80 · 104.16.1.10-80. Igual que Rangos.limites."""
    t = (texto or "").strip()
    if "/" in t:
        base, _, bits = t.partition("/")
        n = _ip_num(base)
        if n is None or not bits.isdigit() or not 16 <= int(bits) <= 32:
            return None
        b = int(bits)
        mascara = (0xFFFFFFFF << (32 - b)) & 0xFFFFFFFF
        a, z = n & mascara, (n & mascara) | (~mascara & 0xFFFFFFFF)
        if b <= 30:
            a, z = a + 1, z - 1
    elif "-" in t:
        izq, _, der = t.partition("-")
        a = _ip_num(izq.strip())
        if a is None:
            return None
        der = der.strip()
        z = _ip_num(der)
        if z is None:
            if not (der.isdigit() and 0 <= int(der) <= 255):
                return None
            z = (a & 0xFFFFFF00) | int(der)
    else:
        return None
    if z < a or z - a + 1 > MAX_RANGO:
        return None
    return a, z


def es_rango(texto):
    return limites_rango(texto) is not None


def tamano_rango(texto):
    r = limites_rango(texto)
    return r[1] - r[0] + 1 if r else 0


def error_host(host):
    hs = lista_hosts(host)
    if not hs:
        return "Host inválido: falta el dominio o IP."
    if len(hs) > MAX_HOSTS:
        return f"Demasiados hosts (máximo {MAX_HOSTS})."
    if any(("/" in h or ":" in h) and not es_rango(h) for h in hs):
        return "Host inválido: solo el dominio o IP, sin http:// ni puerto (el puerto va aparte)."
    return None


def normalizar_hosts(texto):
    """Texto escrito por el admin ("a.com, b.com:443", con http:// o no) → (hosts "a.com,b.com", puerto o None).
    El puerto es el del primer host que lo traiga. Si algo no sirve, devuelve (None, None)."""
    hosts, puerto = [], None
    for crudo in lista_hosts(texto):
        if es_rango(crudo):             # rango de IP (104.16.0.0/24, 1.2.3.4-80): la app prueba sus IP
            if crudo not in hosts:
                hosts.append(crudo)
            continue
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


class AvisoConecto:
    """La app entró a un servidor con rango de IP y avisa por cuál IP (ruta /conecto de publico.py).
    Solo se acepta un token que el bot conoce ([cuenta] devuelve (nombre, vence) o None) y se le manda al admin con
    [avisar]. El mismo cliente con la misma IP en el mismo servidor avisa una sola vez cada [ventana] segundos
    (la app reconecta seguido y no tiene sentido llenar el chat)."""

    def __init__(self, cuenta, avisar, reloj=time.time, ventana=6 * 3600):
        self.cuenta, self.avisar, self.reloj, self.ventana = cuenta, avisar, reloj, ventana
        self.vistos = {}
        self._candado = threading.Lock()

    def __call__(self, token, servidor, ip, rango):
        if not es_rango(rango) or not _ipv4(ip):
            return False
        try:
            datos = self.cuenta(token)
        except Exception:
            datos = None
        if not datos:
            return False
        ahora = self.reloj()
        clave = (token, servidor, ip)
        with self._candado:
            if ahora - self.vistos.get(clave, -1e18) < self.ventana:
                return True
            if len(self.vistos) > 5000:
                self.vistos = {k: v for k, v in self.vistos.items() if ahora - v < self.ventana}
            self.vistos[clave] = ahora
        nombre = re.sub(r"[\r\n]+", " ", (datos[0] or "").strip()) or token
        self.avisar(f"🌐 {nombre} se conectó a «{limpiar_nombre(servidor)}» por la IP {ip}\n"
                    f"Rango: {rango} ({tamano_rango(rango)} IP)")
        return True
