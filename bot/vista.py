"""Imágenes del bot: vista previa de cómo queda la app y preparación del ícono y del fondo.

Usa Pillow (apt install python3-pil). Si no está instalado, el bot sigue andando: no manda vistas
previas y acepta las imágenes tal cual llegan, siempre que no pesen demasiado.
"""
import io

import tema as T

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    HAY_PIL = True
except ImportError:  # el bot funciona igual, sin vistas previas
    HAY_PIL = False

MAX_ICONO = 110 * 1024     # bytes ya preparados; entre los dos tienen que entrar en marca.MAX_PAQUETE
MAX_FONDO = 340 * 1024
MAX_ENTRADA = 12 * 1024 * 1024


class ErrorImagen(Exception):
    pass


def tipo(datos):
    """'png', 'jpg' o None según los primeros bytes."""
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if datos[:3] == b"\xff\xd8\xff":
        return "jpg"
    return None


# ----------------------------------------------------------------- preparar imágenes
def _abrir(datos):
    if len(datos) > MAX_ENTRADA:
        raise ErrorImagen("La imagen pesa demasiado. Mandala como foto (no como archivo) o achicala.")
    try:
        im = Image.open(io.BytesIO(datos))
        im.load()
        return ImageOps.exif_transpose(im)
    except Exception:
        raise ErrorImagen("No pude leer esa imagen. Mandá un PNG o un JPG.") from None


def preparar_icono(datos):
    """PNG cuadrado de hasta 432 px (lo que pide Android para el ícono más grande)."""
    if not HAY_PIL:
        if tipo(datos) is None:
            raise ErrorImagen("Mandá el ícono en PNG o JPG.")
        if len(datos) > MAX_ICONO:
            raise ErrorImagen(f"El ícono pesa {len(datos) // 1024} KB y el máximo es {MAX_ICONO // 1024} KB. "
                              "Mandalo como foto (no como archivo) para que Telegram lo achique.")
        return datos
    im = _abrir(datos).convert("RGBA")
    lado = max(im.size)
    if im.size[0] != im.size[1]:       # no es cuadrada: se centra sobre transparente, sin recortar
        lienzo = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
        lienzo.paste(im, ((lado - im.size[0]) // 2, (lado - im.size[1]) // 2))
        im = lienzo
    for px in (432, 320, 256, 192):
        chica = im.resize((px, px), Image.LANCZOS) if lado > px else im
        b = io.BytesIO()
        chica.save(b, "PNG", optimize=True)
        if b.tell() <= MAX_ICONO:
            return b.getvalue()
    b = io.BytesIO()                   # una foto muy cargada: 192 px con paleta de 256 colores
    im.resize((192, 192), Image.LANCZOS).quantize(256, method=Image.FASTOCTREE).save(b, "PNG", optimize=True)
    if b.tell() > MAX_ICONO:
        raise ErrorImagen("No pude dejar el ícono lo bastante liviano. Probá con una imagen más simple.")
    return b.getvalue()


def preparar_fondo(datos):
    """JPG de hasta 1080 px de ancho útil, liviano."""
    if not HAY_PIL:
        if tipo(datos) is None:
            raise ErrorImagen("Mandá el fondo en JPG o PNG.")
        if len(datos) > MAX_FONDO:
            raise ErrorImagen(f"El fondo pesa {len(datos) // 1024} KB y el máximo es {MAX_FONDO // 1024} KB. "
                              "Mandalo como foto (no como archivo) para que Telegram lo achique.")
        return datos
    im = _abrir(datos)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        base = Image.new("RGB", im.size, (0, 0, 0))
        base.paste(im, mask=im.split()[3])
        im = base
    else:
        im = im.convert("RGB")
    for lado, calidad in ((1600, 84), (1600, 74), (1280, 78), (1280, 66), (1080, 66), (960, 58), (800, 50)):
        chica = im.copy()
        chica.thumbnail((lado, lado), Image.LANCZOS)
        b = io.BytesIO()
        chica.save(b, "JPEG", quality=calidad, optimize=True, progressive=True)
        if b.tell() <= MAX_FONDO:
            return b.getvalue()
    raise ErrorImagen("No pude dejar el fondo lo bastante liviano. Probá con otra imagen.")


# ----------------------------------------------------------------------- vista previa
_FUENTES = {
    "sans": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    "angosta": ("DejaVuSansCondensed.ttf", "DejaVuSansCondensed-Bold.ttf"),
    "serif": ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf"),
    "mono": ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"),
}
_OTRAS = ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "FreeSans.ttf", "FreeSansBold.ttf",
          "NotoSans-Regular.ttf", "NotoSans-Bold.ttf")
_cache_fuentes = {}


def _fuente(familia, negrita, px):
    estilo = {"serif": "serif", "monospace": "mono", "sans-serif-condensed": "angosta"}.get(familia, "sans")
    clave = (estilo, negrita, px)
    if clave in _cache_fuentes:
        return _cache_fuentes[clave]
    candidatos = [_FUENTES[estilo][1 if negrita else 0], _FUENTES["sans"][1 if negrita else 0],
                  _OTRAS[1 if negrita else 0], _OTRAS[3 if negrita else 2], _OTRAS[5 if negrita else 4]]
    f = None
    for nombre in candidatos:
        try:
            f = ImageFont.truetype(nombre, px)
            break
        except OSError:
            continue
    if f is None:
        try:
            f = ImageFont.load_default(size=px)     # Pillow 10.1 o más nuevo
        except TypeError:
            f = ImageFont.load_default()
    _cache_fuentes[clave] = f
    return f


def _rgba(h, alfa=255):
    return T.rgb(h) + (alfa,)


class _Pantalla:
    """Dibuja la pantalla principal de la app con un tema. Medidas en dp, como en MainActivity.kt."""

    def __init__(self, t, icono=None, fondo=None, alto_dp=None, s=4):
        self.t, self.s = t, s
        self.ancho = 360
        self.alto = alto_dp or 1200
        self.im = Image.new("RGBA", (self.ancho * s, self.alto * s), _rgba(t["fondo"]))
        self._fondo(fondo)
        self.icono = icono
        self.y = 0
        self.cortes = []        # alturas (dp) entre tarjeta y tarjeta: por ahí se puede partir la imagen

    # -- utilidades
    def px(self, dp):
        return int(round(dp * self.s))

    def fuente(self, sp, negrita=False):
        return _fuente(self.t["fuente"], negrita, max(6, self.px(sp * self.t["escala"] / 100 * 0.94)))

    def capa(self):
        return Image.new("RGBA", self.im.size, (0, 0, 0, 0))

    def caja(self, x, y, w, h, color, radio, borde=None, alfa=255):
        capa = self.capa()
        d = ImageDraw.Draw(capa)
        r = self.px(min(radio, h / 2, w / 2))
        d.rounded_rectangle([self.px(x), self.px(y), self.px(x + w), self.px(y + h)], r, fill=_rgba(color, alfa),
                            outline=_rgba(borde) if borde else None, width=self.px(1) if borde else 0)
        self.im = Image.alpha_composite(self.im, capa)

    def texto(self, x, y, s, sp, color, negrita=False, ancla="la", ancho_max=None):
        d = ImageDraw.Draw(self.im)
        f = self.fuente(sp, negrita)
        if ancho_max:
            while len(s) > 1 and d.textlength(s, font=f) > self.px(ancho_max):
                s = s[:-2] + "…"
        d.text((self.px(x), self.px(y)), s, font=f, fill=_rgba(color), anchor=ancla)

    def alto_texto(self, sp):
        return sp * self.t["escala"] / 100 * 1.32

    def parrafo(self, x, y, s, sp, color, ancho):
        """Texto en varias líneas. Devuelve el alto usado en dp."""
        d = ImageDraw.Draw(self.im)
        f = self.fuente(sp)
        lineas, actual = [], ""
        for palabra in s.split():
            prueba = (actual + " " + palabra).strip()
            if d.textlength(prueba, font=f) <= self.px(ancho) or not actual:
                actual = prueba
            else:
                lineas.append(actual)
                actual = palabra
        if actual:
            lineas.append(actual)
        for i, l in enumerate(lineas):
            d.text((self.px(x), self.px(y + i * self.alto_texto(sp))), l, font=f, fill=_rgba(color))
        return len(lineas) * self.alto_texto(sp)

    # -- piezas
    def _fondo(self, fondo):
        t, W, H = self.t, self.im.size[0], self.im.size[1]
        if fondo:
            try:
                foto = ImageOps.fit(Image.open(io.BytesIO(fondo)).convert("RGB"), (W, H), Image.LANCZOS)
                self.im = foto.convert("RGBA")
                velo = Image.new("RGBA", (W, H), (0, 0, 0, int(255 * t["velo"] / 100)))
                self.im = Image.alpha_composite(self.im, velo)
                return
            except Exception:
                pass
        if t["fondo2"]:
            a, b = T.rgb(t["fondo"]), T.rgb(t["fondo2"])
            franja = Image.new("RGB", (1, 256))
            franja.putdata([tuple(int(a[k] + (b[k] - a[k]) * i / 255) for k in range(3)) for i in range(256)])
            self.im = franja.resize((W, H), Image.BILINEAR).convert("RGBA")

    def radio(self, factor=1.0):
        return self.t["radio"] * factor

    def tarjeta(self, alto):
        """Caja de una tarjeta a la altura actual. Devuelve (x, y) del contenido."""
        self.cortes.append(self.y + 7)
        self.y += 14
        alfa = int(255 * self.t["opacidad"] / 100)
        self.caja(18, self.y, self.ancho - 36, alto, self.t["tarjeta"], self.radio(), self.t["borde"], alfa)
        y = self.y
        self.y += alto
        return 36, y + 16

    def seccion(self, x, y, titulo):
        self.caja(x, y + 3, 15, 15, self.t["acento"], 5)
        self.texto(x + 24, y, titulo, 15.5, self.t["acento"], True)
        return y + self.alto_texto(15.5) + 12

    def campo(self, x, y, w, s, color, negrita=False, alto=45):
        alfa = int(255 * self.t["opacidad"] / 100)
        self.caja(x, y, w, alto, self.t["campo"], self.radio(0.6), self.t["borde"], alfa)
        self.texto(x + 14, y + alto / 2, s, 15.5, color, negrita, "lm", ancho_max=w - 28)

    def boton(self, x, y, w, s, color, alto=52):
        self.caja(x, y, w, alto, color, self.radio(0.8))
        self.texto(x + w / 2, y + alto / 2, s, 16, self.t["sobre_boton"], True, "mm", ancho_max=w - 24)

    def logo(self, cx, y):
        """Logo de arriba: el ícono importado, o un escudo en el lugar del emoji. Devuelve el alto."""
        t = self.t
        if t["logo_imagen"] and self.icono:
            try:
                lado = self.px(64)
                ic = Image.open(io.BytesIO(self.icono)).convert("RGBA").resize((lado, lado), Image.LANCZOS)
                mascara = Image.new("L", (lado, lado), 0)
                ImageDraw.Draw(mascara).rounded_rectangle([0, 0, lado, lado], self.px(14), fill=255)
                ic.putalpha(Image.composite(ic.split()[3], Image.new("L", (lado, lado), 0), mascara))
                self.im.alpha_composite(ic, (self.px(cx) - lado // 2, self.px(y)))
                return 64 + 8
            except Exception:
                pass
        if not t["logo"]:
            return 0
        d = ImageDraw.Draw(self.im)
        p = lambda dx, dy: (self.px(cx + dx), self.px(y + dy))
        d.polygon([p(0, 2), p(15, 8), p(15, 21), p(9, 33), p(0, 39), p(-9, 33), p(-15, 21), p(-15, 8)], fill=_rgba(t["acento"]))
        return 46

    # -- la pantalla entera
    def dibujar(self):
        t = self.t
        ancho_util = self.ancho - 36
        # barra de estado del teléfono
        self.texto(18, 14, "12:30", 12, t["texto"], True, "lm")
        self.caja(self.ancho - 40, 9, 22, 10, t["texto"], 3)
        self.y = 40
        # encabezado
        alto_logo = self.logo(self.ancho / 2, self.y)
        y = self.y + alto_logo
        self.texto(self.ancho / 2, y, T.titulo(t), 25, t["texto"], True, "ma", ancho_max=ancho_util - 100)
        y += self.alto_texto(25) + 2
        if t["lema"]:
            self.texto(self.ancho / 2, y, t["lema"], 13, t["suave"], False, "ma", ancho_max=ancho_util - 60)
            y += self.alto_texto(13)
        medio = (self.y + y) / 2
        alfa = int(255 * t["opacidad"] / 100)
        self.caja(self.ancho - 18 - 44, medio - 22, 44, 44, t["tarjeta"], self.radio(0.7), t["borde"], alfa)
        for k in (-7, 0, 7):
            self.caja(self.ancho - 18 - 33, medio + k - 1, 22, 2.4, t["acento"], 1)
        self.y = y
        # tu cuenta
        e = t["escala"] / 100
        alto = 16 + (self.alto_texto(15.5) + 12) + self.alto_texto(12) + 6 + 45 + 10 + 45 + 10 + 45 + 16
        x, y = self.tarjeta(alto)
        w = ancho_util - 36
        y = self.seccion(x, y, "Tu cuenta")
        self.texto(x, y, "Servidor", 12, t["suave"])
        y += self.alto_texto(12) + 6
        self.campo(x, y, w, "APP 02   ▾", t["texto"], True)
        y += 55
        self.campo(x, y, w, "Usuario", t["suave"])
        y += 55
        self.campo(x, y, w - 56, "Contraseña", t["suave"])
        self.caja(x + w - 48, y, 48, 45, t["campo"], self.radio(0.6), t["borde"], alfa)
        self.caja(x + w - 33, y + 17, 18, 11, t["suave"], 6)
        # estado + conectar
        alto = 16 + self.alto_texto(20) + 8 + 10 + 52 + 16
        x, y = self.tarjeta(alto)
        f = self.fuente(20, True)
        largo = ImageDraw.Draw(self.im).textlength("Desconectado", font=f) / self.s
        x0 = (self.ancho - largo - 20) / 2
        self.caja(x0, y + self.alto_texto(20) / 2 - 5, 10, 10, t["desconectar"], 5)
        self.texto(x0 + 20, y, "Desconectado", 20, t["desconectar"], True)
        y += self.alto_texto(20) + 18
        self.boton(x, y, w, "▶  Conectar", t["conectar"])
        # vencimiento
        if t["ver_vencimiento"]:
            alto = 16 + (self.alto_texto(15.5) + 12) + self.alto_texto(17) + 2 + self.alto_texto(13) + 14
            x, y = self.tarjeta(alto)
            y = self.seccion(x, y, "Vencimiento")
            self.texto(x, y, "Vence el 05/11/2026", 17, t["conectar"], True)
            self.texto(x, y + self.alto_texto(17) + 2, "Faltan 30 días", 13, t["suave"])
        # velocidad, tiempo y datos
        if t["ver_conexion"]:
            alto = 16 + (self.alto_texto(15.5) + 12) + self.alto_texto(11.5) + 4 + self.alto_texto(15.5) + 14
            x, y = self.tarjeta(alto)
            y = self.seccion(x, y, "Conexión")
            for i, (titulo, valor) in enumerate((("Velocidad", "2,4 MB/s"), ("Conectado hace", "12:07"), ("Datos usados", "318 MB"))):
                cx = x + w / 6 + i * w / 3
                self.texto(cx, y, titulo, 11.5, t["suave"], False, "ma")
                self.texto(cx, y + self.alto_texto(11.5) + 4, valor, 15.5, t["texto"], True, "ma")
        # ajustes del teléfono
        if t["ver_telefono"]:
            frase = "Si la VPN no conecta, desactivá el DNS privado y sacale el límite de batería a la app."
            lineas = 2 if e <= 1.0 else 3
            alto = 16 + (self.alto_texto(15.5) + 12) + lineas * self.alto_texto(12.5) + 6 + 10 + 52 + 10 + 52 + 16
            x, y = self.tarjeta(alto)
            y = self.seccion(x, y, "Ajustes del teléfono")
            y += self.parrafo(x, y, frase, 12.5, t["suave"], w) + 16
            self.boton(x, y, w, "DNS privado", t["acento"])
            self.boton(x, y + 62, w, "Uso de batería", t["aviso"])
        self.y += 28
        return self

    def imagen(self, ancho_px, alto_dp=None):
        alto = min(self.alto, alto_dp or self.y)
        recorte = self.im.crop((0, 0, self.im.size[0], self.px(alto)))
        return recorte.resize((ancho_px, int(round(ancho_px * alto / self.ancho))), Image.LANCZOS).convert("RGB")


def captura(t, icono=None, fondo=None, ancho=720):
    """PNG con la pantalla principal de la app usando el tema t.

    La pantalla entera es mucho más alta que ancha y Telegram achica las fotos largas hasta dejarlas
    ilegibles. Por eso, si es larga, va en dos columnas: a la izquierda lo que se ve al abrir la app
    y a la derecha lo que aparece al bajar."""
    t = T.normalizar(t)
    # primero en miniatura, solo para saber el alto: así el fondo (imagen o degradado) ocupa justo la pantalla dibujada
    alto = int(_Pantalla(t, icono, None, s=1).dibujar().y) + 1
    pantalla = _Pantalla(t, icono, fondo, alto_dp=alto).dibujar()
    im = pantalla.imagen(ancho)
    cortes = [c for c in pantalla.cortes if alto * 0.35 <= c <= alto * 0.65]
    if alto > 820 and cortes:
        corte = min(cortes, key=lambda c: abs(c - alto / 2))
        y = int(round(corte * ancho / pantalla.ancho))
        arriba, abajo = im.crop((0, 0, ancho, y)), im.crop((0, y, ancho, im.size[1]))
        margen = ancho // 24
        hoja = Image.new("RGB", (2 * ancho + 3 * margen, max(arriba.size[1], abajo.size[1]) + 2 * margen), (24, 24, 28))
        hoja.paste(arriba, (margen, margen))
        hoja.paste(abajo, (2 * margen + ancho, margen))
        im = hoja
    b = io.BytesIO()
    im.save(b, "PNG", optimize=True)
    return b.getvalue()


def muestrario(temas, icono=None, fondo=None, columnas=5):
    """JPG con todas las plantillas juntas, numeradas. temas: lista de (nombre, tema)."""
    celda_w, alto_dp, margen, pie = 300, 530, 18, 44
    celda_h = int(round(celda_w * alto_dp / 360))
    filas = (len(temas) + columnas - 1) // columnas
    hoja = Image.new("RGB", (margen + columnas * (celda_w + margen), margen + filas * (celda_h + pie + margen)), (24, 24, 28))
    d = ImageDraw.Draw(hoja)
    f = _fuente("sans-serif", True, 22)
    for i, (nombre, t) in enumerate(temas):
        t = T.normalizar(t)
        mini = _Pantalla(t, icono, fondo, alto_dp=alto_dp + 60, s=3).dibujar().imagen(celda_w, alto_dp)
        x = margen + (i % columnas) * (celda_w + margen)
        y = margen + (i // columnas) * (celda_h + pie + margen)
        hoja.paste(mini, (x, y))
        d.rectangle([x - 1, y - 1, x + celda_w, y + celda_h], outline=(70, 70, 78), width=1)
        d.text((x + celda_w / 2, y + celda_h + pie / 2), f"{i + 1}. {nombre}", font=f, fill=(240, 240, 244), anchor="mm")
    b = io.BytesIO()
    hoja.save(b, "JPEG", quality=88, optimize=True)
    return b.getvalue()
