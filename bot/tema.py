"""Apariencia de la app Android: nombre, colores, fondo, letra, secciones y plantillas.

El bot guarda el tema en /etc/zumo/app-marca/tema.json y, al compilar, lo sube al repo junto con
el ícono y el fondo (ver marca.py). La app lo lee de assets/tema.json (android/.../Tema.kt): las
claves y los valores por defecto de acá tienen que ser los mismos que los de Tema.kt.
"""
import json
import re

# ------------------------------------------------------------------ valores por defecto
POR_DEFECTO = {
    "v": 1,
    "plantilla": "zumo",
    "nombre": "Zumo VPN",
    "lema": "Conexión privada y estable",
    "logo": "🛡",               # emoji o texto corto arriba del título ("" = nada)
    "logo_imagen": False,       # mostrar arriba el ícono importado en vez del emoji
    "titulo_mayus": True,       # el título de la pantalla en MAYÚSCULAS
    "fondo": "#14102B",
    "fondo2": "",               # segundo color: degradado de arriba hacia abajo ("" = color liso)
    "velo": 55,                 # cuánto se oscurece la imagen de fondo (0 a 90 %)
    "tarjeta": "#201A3D",
    "borde": "#36305E",
    "acento": "#B388FF",
    "texto": "#FFFFFF",
    "suave": "#9D96C4",
    "campo": "#2E2854",
    "conectar": "#4CE0A8",
    "desconectar": "#FF6E6E",
    "aviso": "#FFB74D",
    "sobre_boton": "#0F0B21",   # color de la letra encima de los botones de color
    "opacidad": 100,            # tarjetas: 100 = sólidas, menos = se ve el fondo a través (40 a 100)
    "radio": 20,                # esquinas de las tarjetas (0 a 32)
    "fuente": "sans-serif",
    "escala": 100,              # tamaño de la letra en % (85 a 130)
    "ver_vencimiento": True,
    "ver_conexion": True,
    "ver_telefono": True,
    "ver_importar": True,
    "enlaces": [],              # botones de contacto del menú ☰: [{"texto", "url"}], hasta 3
}

COLORES = ("fondo", "tarjeta", "borde", "acento", "texto", "suave", "campo",
           "conectar", "desconectar", "aviso", "sobre_boton")

# lo que el dueño elige a mano en el bot; el resto de los colores se calcula solo
COLORES_EDITABLES = [("fondo", "Fondo"), ("tarjeta", "Tarjetas"), ("acento", "Títulos y botones"),
                     ("texto", "Letra"), ("conectar", "Botón Conectar"), ("desconectar", "Botón Desconectar")]

FUENTES = [("sans-serif", "Normal"), ("sans-serif-medium", "Marcada"), ("sans-serif-light", "Fina"),
           ("sans-serif-condensed", "Angosta"), ("serif", "Clásica (serif)"), ("monospace", "Máquina (mono)"),
           ("casual", "Informal"), ("cursive", "Manuscrita")]

ESCALAS = [(90, "Chica"), (100, "Normal"), (112, "Grande"), (125, "Muy grande")]
RADIOS = [(4, "Rectas"), (12, "Suaves"), (20, "Redondas"), (28, "Muy redondas")]
VELOS = [(25, "Poco"), (45, "Medio"), (55, "Normal"), (70, "Mucho"), (85, "Casi negro")]
OPACIDADES = [(100, "Sólidas"), (85, "Apenas"), (70, "Vidrio"), (55, "Muy transparentes")]

SECCIONES = [("ver_vencimiento", "Vencimiento"), ("ver_conexion", "Velocidad y datos"),
             ("ver_telefono", "Ajustes del teléfono (DNS y batería)"), ("ver_importar", "Importar archivo .zs")]

MAX_ENLACES = 3

# ------------------------------------------------------------------------- plantillas
# Cada plantilla cambia colores, degradado, esquinas y letra. No toca nombre, lema, ícono, imagen
# de fondo, secciones ni botones de contacto.
PLANTILLAS = [
    ("zumo", "Zumo violeta", "La de siempre: violeta oscuro.", {}),
    ("oceano", "Océano", "Azul profundo con celeste.", {
        "fondo": "#071A2C", "fondo2": "#0B3B5A", "tarjeta": "#0F2A43", "borde": "#1F4A6B", "acento": "#4FC3F7",
        "suave": "#8FB3CC", "campo": "#153650", "conectar": "#26E0C0", "desconectar": "#FF6B6B",
        "aviso": "#FFC857", "sobre_boton": "#04121F"}),
    ("esmeralda", "Esmeralda", "Verde oscuro, tranquilo.", {
        "fondo": "#0B1F17", "fondo2": "#0F3325", "tarjeta": "#12291F", "borde": "#1F4A37", "acento": "#5BE49B",
        "suave": "#8FBFA6", "campo": "#183527", "conectar": "#7CF2B0", "desconectar": "#FF7A7A",
        "aviso": "#FFD166", "sobre_boton": "#06140E"}),
    ("fuego", "Fuego", "Negro con naranja y rojo.", {
        "fondo": "#160A0A", "fondo2": "#2B0F0A", "tarjeta": "#24100F", "borde": "#4A1F1A", "acento": "#FF7043",
        "suave": "#C9A39A", "campo": "#331715", "conectar": "#FFB300", "desconectar": "#FF5252",
        "aviso": "#FFD180", "sobre_boton": "#1A0B08", "radio": 12}),
    ("oro", "Negro y oro", "Elegante, letra clásica.", {
        "fondo": "#0B0B0D", "tarjeta": "#17171B", "borde": "#3A3320", "acento": "#E6C36A",
        "suave": "#A8A39A", "campo": "#212127", "conectar": "#E6C36A", "desconectar": "#E57373",
        "aviso": "#FFCC80", "sobre_boton": "#14110A", "radio": 8, "fuente": "serif"}),
    ("neon", "Neón", "Oscuro con rosa y verde flúor.", {
        "fondo": "#0A0A14", "fondo2": "#1A0B2E", "tarjeta": "#14142A", "borde": "#3D2A6B", "acento": "#FF4FD8",
        "suave": "#A79BD6", "campo": "#1D1D3A", "conectar": "#39FF88", "desconectar": "#FF4F6D",
        "aviso": "#FFE14F", "sobre_boton": "#0A0A14", "radio": 28, "fuente": "sans-serif-condensed"}),
    ("grafito", "Grafito", "Gris sobrio, sin colores fuertes.", {
        "fondo": "#121417", "tarjeta": "#1C1F24", "borde": "#30353D", "acento": "#8AB4F8",
        "suave": "#9AA0A6", "campo": "#262A31", "conectar": "#81C995", "desconectar": "#F28B82",
        "aviso": "#FDD663", "sobre_boton": "#101215", "radio": 12}),
    ("rosa", "Rosa", "Bordó con rosa.", {
        "fondo": "#240C1A", "fondo2": "#45152F", "tarjeta": "#341327", "borde": "#5E2A47", "acento": "#FF8FC7",
        "suave": "#C9A0B6", "campo": "#451B34", "conectar": "#FFD166", "desconectar": "#FF6B81",
        "aviso": "#FFB870", "sobre_boton": "#200A17", "radio": 28}),
    ("claro", "Claro", "Fondo blanco, azul.", {
        "fondo": "#F3F5FA", "tarjeta": "#FFFFFF", "borde": "#DCE1EC", "acento": "#3D5AFE", "texto": "#1B2030",
        "suave": "#6B7388", "campo": "#EEF1F7", "conectar": "#12B886", "desconectar": "#E5484D",
        "aviso": "#D9822B", "sobre_boton": "#FFFFFF"}),
    ("cielo", "Cielo", "Claro, celeste en degradado.", {
        "fondo": "#CFE9FF", "fondo2": "#F7FBFF", "tarjeta": "#FFFFFF", "borde": "#C9E2F5", "acento": "#0288D1",
        "texto": "#10273A", "suave": "#5F7B90", "campo": "#EEF6FC", "conectar": "#00A884",
        "desconectar": "#E5484D", "aviso": "#C77700", "sobre_boton": "#FFFFFF", "radio": 24}),
]

# lo que una plantilla pisa (lo demás del tema queda como está)
DE_PLANTILLA = COLORES + ("fondo2", "radio", "fuente")

# colores para elegir con un toque (también se puede escribir cualquier #RRGGBB)
PALETA_VIVOS = [("🟣 Violeta", "#B388FF"), ("🔵 Azul", "#3D8BFF"), ("Celeste", "#4FC3F7"), ("Turquesa", "#26E0C0"),
                ("🟢 Verde", "#4CE0A8"), ("Lima", "#A6E22E"), ("🟡 Amarillo", "#FFD54F"), ("Oro", "#E6C36A"),
                ("🟠 Naranja", "#FF8A3D"), ("🔴 Rojo", "#FF5252"), ("Rosa", "#FF6FB5"), ("Fucsia", "#FF4FD8")]
PALETA_FONDOS = [("Violeta noche", "#14102B"), ("Azul noche", "#071A2C"), ("Verde noche", "#0B1F17"),
                 ("Bordó", "#240C1A"), ("🟤 Marrón", "#1F140C"), ("⚫ Negro", "#0B0B0D"), ("Gris oscuro", "#1C1F24"),
                 ("🔵 Azul", "#123A66"), ("⚪ Blanco", "#FFFFFF"), ("Gris claro", "#F3F5FA"), ("Celeste claro", "#CFE9FF"),
                 ("Crema", "#FBF4E6")]
PALETA_LETRA = [("⚪ Blanco", "#FFFFFF"), ("Gris claro", "#E6E8EE"), ("Crema", "#FFF3D6"),
                ("⚫ Negro", "#101216"), ("Gris oscuro", "#1B2030"), ("Azul oscuro", "#10273A")]


def paleta(clave):
    if clave in ("fondo", "fondo2", "tarjeta"):
        return PALETA_FONDOS
    if clave == "texto":
        return PALETA_LETRA
    return PALETA_VIVOS


# ----------------------------------------------------------------------------- colores
HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")


def color(t):
    """'#b388ff', 'B388FF' o '#fff' → '#B388FF'. None si no es un color."""
    t = (t or "").strip()
    m3 = re.match(r"^#?([0-9A-Fa-f]{3})$", t)
    if m3:
        t = "".join(c * 2 for c in m3.group(1))
    m = HEX_RE.match(t)
    return "#" + m.group(1).upper() if m else None


def rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def hexa(r, g, b):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(x)))) for x in (r, g, b))


def mezclar(a, b, t):
    """Color entre a y b: t = 0 da a, t = 1 da b."""
    ra, ga, ba = rgb(a)
    rb, gb, bb = rgb(b)
    return hexa(ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t)


def luminancia(h):
    r, g, b = rgb(h)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def es_claro(h):
    return luminancia(h) > 0.6


def derivar(t, cambio):
    """Después de cambiar a mano el color 'cambio', acomoda los que dependen de él."""
    if cambio == "fondo" and es_claro(t["fondo"]) != es_claro(t["tarjeta"]):
        # se pasó de un fondo oscuro a uno claro (o al revés): las tarjetas y la letra acompañan
        if es_claro(t["fondo"]):
            t["tarjeta"], t["texto"] = "#FFFFFF", "#1B2030"
        else:
            t["tarjeta"], t["texto"] = mezclar(t["fondo"], "#FFFFFF", 0.07), "#FFFFFF"
        cambio = "tarjeta"
    if cambio == "tarjeta" and es_claro(t["tarjeta"]) == es_claro(t["texto"]):
        t["texto"] = "#1B2030" if es_claro(t["tarjeta"]) else "#FFFFFF"   # que la letra se lea
    if cambio in ("tarjeta", "texto"):
        t["borde"] = mezclar(t["tarjeta"], t["texto"], 0.14)
        t["campo"] = mezclar(t["tarjeta"], t["texto"], 0.07)
        t["suave"] = mezclar(t["texto"], t["tarjeta"], 0.42)
    if cambio in ("acento", "conectar", "desconectar", "tarjeta", "fondo"):
        medio = (luminancia(t["acento"]) + luminancia(t["conectar"])) / 2
        t["sobre_boton"] = mezclar("#000000", t["fondo"] if not es_claro(t["fondo"]) else "#101216", 0.6) if medio > 0.5 else "#FFFFFF"
    return t


# ------------------------------------------------------------------------------ textos
def _sin_control(t):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", t or "")


def limpiar_nombre(t):
    """Nombre de la app (va en el ícono del teléfono): sin símbolos que rompan el manifiesto."""
    t = re.sub(r"[<>&\"'\\]", "", _sin_control(t))
    t = re.sub(r"\s+", " ", t).strip().lstrip("@?").strip()
    return t[:30].strip()


def limpiar_texto(t, largo):
    return re.sub(r"\s+", " ", _sin_control(t)).strip()[:largo].strip()


def limpiar_enlace(t):
    """Acepta un link, un número de WhatsApp o un @usuario de Telegram. None si no sirve."""
    t = (t or "").strip()
    if re.match(r"^\+?[\d\s().-]{8,20}$", t):
        return "https://wa.me/" + re.sub(r"\D", "", t)
    if re.match(r"^@[A-Za-z0-9_]{4,32}$", t):
        return "https://t.me/" + t[1:]
    if re.match(r"^(wa\.me|t\.me|chat\.whatsapp\.com|api\.whatsapp\.com|instagram\.com|facebook\.com|www\.)", t, re.I):
        t = "https://" + t
    if re.match(r"^(https?://|tg://|mailto:|tel:)\S{3,300}$", t, re.I) and not re.search(r"[\s\"<>]", t):
        return t
    return None


# ------------------------------------------------------------------------- normalizar
def _entero(v, defecto, minimo, maximo):
    try:
        return max(minimo, min(maximo, int(v)))
    except (TypeError, ValueError):
        return defecto


def normalizar(d):
    """Tema completo y válido a partir de cualquier diccionario (lo que falte o esté mal → por defecto)."""
    d = d if isinstance(d, dict) else {}
    t = dict(POR_DEFECTO)
    t["plantilla"] = str(d.get("plantilla", t["plantilla"]))[:40]
    t["nombre"] = limpiar_nombre(str(d.get("nombre", ""))) or POR_DEFECTO["nombre"]
    t["lema"] = limpiar_texto(str(d.get("lema", POR_DEFECTO["lema"])), 60)
    t["logo"] = limpiar_texto(str(d.get("logo", POR_DEFECTO["logo"])), 8)
    for k in COLORES:
        t[k] = color(str(d.get(k, ""))) or POR_DEFECTO[k]
    t["fondo2"] = color(str(d.get("fondo2", ""))) or ""
    t["velo"] = _entero(d.get("velo"), POR_DEFECTO["velo"], 0, 90)
    t["opacidad"] = _entero(d.get("opacidad"), 100, 40, 100)
    t["radio"] = _entero(d.get("radio"), POR_DEFECTO["radio"], 0, 32)
    t["escala"] = _entero(d.get("escala"), 100, 85, 130)
    fuente = str(d.get("fuente", ""))
    t["fuente"] = fuente if fuente in dict(FUENTES) else POR_DEFECTO["fuente"]
    for k in ("logo_imagen", "titulo_mayus", "ver_vencimiento", "ver_conexion", "ver_telefono", "ver_importar"):
        t[k] = bool(d.get(k, POR_DEFECTO[k]))
    enlaces = []
    for e in d.get("enlaces") if isinstance(d.get("enlaces"), list) else []:
        if not isinstance(e, dict):
            continue
        texto, url = limpiar_texto(str(e.get("texto", "")), 30), limpiar_enlace(str(e.get("url", "")))
        if texto and url:
            enlaces.append({"texto": texto, "url": url})
    t["enlaces"] = enlaces[:MAX_ENLACES]
    return t


def plantilla(pid):
    """(id, nombre, descripción, cambios) de una plantilla, o None."""
    return next((p for p in PLANTILLAS if p[0] == pid), None)


def aplicar_plantilla(t, pid):
    """Pone los colores, esquinas y letra de la plantilla; conserva nombre, imágenes, secciones y contactos."""
    p = plantilla(pid)
    if p is None:
        return t
    t = dict(t)
    for k in DE_PLANTILLA:
        t[k] = POR_DEFECTO[k]
    t.update(p[3])
    t["plantilla"] = pid
    return normalizar(t)


def nombre_plantilla(t):
    pid = t.get("plantilla", "")
    p = plantilla(pid.removesuffix("*"))
    if p is None:
        return "Propia"
    return p[1] + (" (con cambios tuyos)" if pid.endswith("*") else "")


def marcar_cambio(t):
    """El dueño tocó algo de lo que define la plantilla: queda como 'con cambios'."""
    if not t.get("plantilla", "").endswith("*"):
        t["plantilla"] = t.get("plantilla", "") + "*"
    return t


def a_json(t):
    """Texto de assets/tema.json de la app."""
    return json.dumps(normalizar(t), ensure_ascii=False, indent=1)


def titulo(t):
    return t["nombre"].upper() if t.get("titulo_mayus", True) else t["nombre"]
