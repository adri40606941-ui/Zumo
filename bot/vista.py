"""Imágenes del bot: vista previa de cómo queda la app y preparación del ícono y del fondo.

Usa Pillow (apt install python3-pil). Si no está instalado, el bot sigue andando: no manda vistas
previas y acepta las imágenes tal cual llegan, siempre que no pesen demasiado.

Las vistas previas se dibujan con las letras que haya en la VPS. Con solo fonts-dejavu-core las ocho
letras de la app (Normal, Marcada, Fina, Angosta...) caerían en la misma, así que las que faltan se
simulan (más gruesa, más fina, más angosta, inclinada). Los emojis a color necesitan
fonts-noto-color-emoji; sin eso el logo se dibuja como un escudo de muestra.
"""
import io

import tema as T

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
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
# Lo que hay que simularle a la letra de la VPS cuando no existe una igual a la de Android:
#   grosor +1 = más gruesa, -1 = más fina; ancho = factor horizontal; sesgo = inclinación.
_AJUSTES = {
    "sans-serif-medium": {"grosor": 1},
    "sans-serif-light": {"grosor": -1},
    "sans-serif-condensed": {"ancho": 0.84},
    "casual": {"ancho": 1.05, "sesgo": 0.10},
    "cursive": {"sesgo": 0.24},
}
# Para estas dos no hay nada parecido en la VPS: la vista previa solo las aproxima (y el bot lo avisa).
APROXIMADAS = ("casual", "cursive")
_cache_fuentes = {}


def _fuente(familia, negrita, px):
    """(letra, ajustes): la letra de la VPS más parecida y lo que todavía hay que simularle."""
    clave = (familia, negrita, px)
    if clave in _cache_fuentes:
        return _cache_fuentes[clave]
    estilo = {"serif": "serif", "monospace": "mono", "sans-serif-condensed": "angosta"}.get(familia, "sans")
    ajustes = dict(_AJUSTES.get(familia, {}))
    if negrita:
        ajustes.pop("grosor", None)           # en negrita, Marcada y Fina son la misma negrita
    candidatos = [(_FUENTES[estilo][1 if negrita else 0], "ancho" if estilo == "angosta" else None)]
    if familia == "sans-serif-light" and not negrita:
        candidatos.insert(0, ("DejaVuSans-ExtraLight.ttf", "grosor"))   # una fina de verdad, si está
    candidatos += [(n, None) for n in (_FUENTES["sans"][1 if negrita else 0], _OTRAS[1 if negrita else 0],
                                       _OTRAS[3 if negrita else 2], _OTRAS[5 if negrita else 4])]
    f = None
    for nombre, resuelve in candidatos:
        try:
            f = ImageFont.truetype(nombre, px)
        except OSError:
            continue
        if resuelve:
            ajustes.pop(resuelve, None)       # la letra ya es así: no hace falta simularlo
        break
    if f is None:
        try:
            f = ImageFont.load_default(size=px)     # Pillow 10.1 o más nuevo
        except TypeError:
            f = ImageFont.load_default()
    _cache_fuentes[clave] = (f, ajustes)
    return _cache_fuentes[clave]


# ---- emojis a color (fonts-noto-color-emoji)
_RUTAS_EMOJI = ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", "NotoColorEmoji.ttf")


def _emoji(texto, alto_px):
    """RGBA con el emoji dibujado a color y de ese alto, o None si la VPS no tiene la letra de emojis."""
    for ruta in _RUTAS_EMOJI:
        try:
            f = ImageFont.truetype(ruta, 109)      # la única medida que trae la fuente
        except OSError:
            continue
        try:
            capa = Image.new("RGBA", (int(f.getlength(texto)) + 60, 200), (0, 0, 0, 0))
            ImageDraw.Draw(capa).text((30, 30), texto, font=f, embedded_color=True)
            caja = capa.getbbox()
            if not caja:
                return None
            capa = capa.crop(caja)
            ancho = max(1, int(round(capa.size[0] * alto_px / capa.size[1])))
            return capa.resize((ancho, alto_px), Image.LANCZOS)
        except Exception:
            return None
    return None


HAY_EMOJI = HAY_PIL and _emoji("😀", 16) is not None


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

    def letra(self, sp, negrita=False):
        """(letra, ajustes) para ese tamaño en sp."""
        return _fuente(self.t["fuente"], negrita, max(6, self.px(sp * self.t["escala"] / 100 * 0.94)))

    def largo(self, s, sp, negrita=False):
        """Ancho en dp que ocupa el texto, con la letra y los ajustes del tema."""
        f, aj = self.letra(sp, negrita)
        return ImageDraw.Draw(self.im).textlength(s, font=f) * aj.get("ancho", 1.0) / self.s

    def capa(self):
        return Image.new("RGBA", self.im.size, (0, 0, 0, 0))

    def caja(self, x, y, w, h, color, radio, borde=None, alfa=255):
        capa = self.capa()
        d = ImageDraw.Draw(capa)
        r = self.px(min(radio, h / 2, w / 2))
        d.rounded_rectangle([self.px(x), self.px(y), self.px(x + w), self.px(y + h)], r, fill=_rgba(color, alfa),
                            outline=_rgba(borde) if borde else None, width=self.px(1) if borde else 0)
        self.im = Image.alpha_composite(self.im, capa)

    def _pintar(self, x, y, s, f, aj, color, ancla):
        """Dibuja una línea de texto en (x, y) px. Con ajustes (más gruesa, fina, angosta, inclinada) la
        dibuja en una máscara aparte, la deforma alrededor del punto de anclaje y la pega."""
        if not aj:
            ImageDraw.Draw(self.im).text((x, y), s, font=f, fill=_rgba(color), anchor=ancla)
            return
        tam = getattr(f, "size", 40)
        W, H = 2 * (int(ImageDraw.Draw(self.im).textlength(s, font=f)) + 2 * tam), 4 * tam
        ax, ay = W // 2, H // 2
        mascara = Image.new("L", (W, H), 0)
        g = aj.get("grosor", 0)
        ImageDraw.Draw(mascara).text((ax, ay), s, font=f, fill=255, anchor=ancla,
                                     stroke_width=max(1, round(tam * 0.02)) if g > 0 else 0, stroke_fill=255)
        if g < 0:
            mascara = mascara.filter(ImageFilter.MinFilter(3))
        k, sesgo = aj.get("ancho", 1.0), aj.get("sesgo", 0.0)
        if k != 1.0 or sesgo:
            mascara = mascara.transform((W, H), Image.AFFINE, (1 / k, sesgo, ax * (1 - 1 / k) - sesgo * ay, 0, 1, 0),
                                        resample=Image.BICUBIC)
        self.im.paste(_rgba(color), (x - ax, y - ay, x - ax + W, y - ay + H), mascara)

    def texto(self, x, y, s, sp, color, negrita=False, ancla="la", ancho_max=None):
        f, aj = self.letra(sp, negrita)
        if ancho_max:
            while len(s) > 1 and self.largo(s, sp, negrita) > ancho_max:
                s = s[:-2] + "…"
        self._pintar(self.px(x), self.px(y), s, f, aj, color, ancla)

    def alto_texto(self, sp):
        return sp * self.t["escala"] / 100 * 1.32

    def lineas(self, s, sp, ancho):
        """El texto cortado en líneas que entran en `ancho` dp."""
        lineas, actual = [], ""
        for palabra in s.split():
            prueba = (actual + " " + palabra).strip()
            if self.largo(prueba, sp) <= ancho or not actual:
                actual = prueba
            else:
                lineas.append(actual)
                actual = palabra
        if actual:
            lineas.append(actual)
        return lineas

    def parrafo(self, x, y, s, sp, color, ancho):
        """Texto en varias líneas. Devuelve el alto usado en dp."""
        f, aj = self.letra(sp)
        lineas = self.lineas(s, sp, ancho)
        for i, l in enumerate(lineas):
            self._pintar(self.px(x), self.px(y + i * self.alto_texto(sp)), l, f, aj, color, "la")
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

    def tarjeta(self, alto, x=18, ancho=None):
        """Caja de una tarjeta a la altura actual. Devuelve (x, y) del contenido."""
        self.cortes.append(self.y + 7)
        self.y += 14
        alfa = int(255 * self.t["opacidad"] / 100)
        self.caja(x, self.y, ancho or self.ancho - 36, alto, self.t["tarjeta"], self.radio(), self.t["borde"], alfa)
        y = self.y
        self.y += alto
        return x + 18, y + 16

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

    def boton_borde(self, x, y, w, s, color, alto=46):
        """Botón secundario de la app: solo borde, sin relleno."""
        self.caja(x, y, w, alto, self.t["tarjeta"], self.radio(0.7), color, 0)
        sp = 14.5
        while sp > 11 and self.largo(s, sp) > w - 24:      # la letra de la VPS es más ancha que la de Android: se achica antes de cortar
            sp -= 0.5
        self.texto(x + w / 2, y + alto / 2, s, sp, color, False, "mm", ancho_max=w - 24)

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
        em = _emoji(t["logo"], self.px(40)) if HAY_EMOJI else None      # en la app es texto de 34 sp
        if em:
            self.im.alpha_composite(em, (self.px(cx) - em.size[0] // 2, self.px(y)))
            return 46
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
        self.y = 34
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
        x0 = (self.ancho - self.largo("Desconectado", 20, True) - 20) / 2
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

    # -- el menú ☰ (diálogo "Configuración"), como lo arma abrirMenu() en MainActivity.kt
    def dibujar_menu(self, medir=False):
        t = self.t
        sec = self.alto_texto(15.5) + 12
        dx, dw = 22, self.ancho - 44                 # el diálogo, con margen a los costados
        cx, cw = dx + 18, dw - 36                    # las tarjetas, dentro del diálogo
        w = cw - 36                                  # ancho útil dentro de una tarjeta
        frase = "¿Te mandaron un archivo .zs nuevo (renovación u otra cuenta)? Importalo acá."
        top = 24 + self.alto_texto(20) + 8
        if not medir:
            self.im = Image.alpha_composite(self.im, Image.new("RGBA", self.im.size, (0, 0, 0, 150)))   # lo de atrás, oscurecido
            alto_dialogo = self.dibujar_menu(medir=True) - 40
            self.caja(dx, 20, dw, alto_dialogo, t["tarjeta"], self.radio(0.9))
            self.texto(dx + 24, 20 + 24, "Configuración", 20, t["texto"], True)
        self.y = 20 + top
        if t["enlaces"]:
            alto = 16 + sec + len(t["enlaces"]) * 54 + 16
            x, y = self.tarjeta(alto, cx, cw)
            if not medir:
                y = self.seccion(x, y, "Contacto")
                for e in t["enlaces"]:
                    self.boton_borde(x, y + 8, w, e["texto"], t["acento"])
                    y += 54
        if t["ver_importar"]:
            n = len(self.lineas(frase, 13, w))
            alto = 16 + sec + n * self.alto_texto(13) + 54 + 16
            x, y = self.tarjeta(alto, cx, cw)
            if not medir:
                y = self.seccion(x, y, "Cuenta")
                y += self.parrafo(x, y, frase, 13, t["suave"], w)
                self.boton_borde(x, y + 8, w, "Importar archivo .zs", t["acento"])
        interruptor = self.lineas("Reconectar al encender el teléfono", 14, w - 56)     # el texto baja de línea si no entra
        fila = max(48, len(interruptor) * self.alto_texto(14) + 16)
        alto = 16 + sec + fila + 54 + 16
        x, y = self.tarjeta(alto, cx, cw)
        if not medir:
            y = self.seccion(x, y, "Evitar desconexiones")
            for i, l in enumerate(interruptor):
                self.texto(x, y + fila / 2 + (i - (len(interruptor) - 1) / 2) * self.alto_texto(14), l, 14, t["texto"], False, "lm")
            self.caja(x + w - 40, y + fila / 2 - 8, 40, 16, T.mezclar(t["acento"], t["tarjeta"], 0.45), 8)     # el interruptor, encendido
            self.caja(x + w - 22, y + fila / 2 - 10, 20, 20, t["acento"], 10)
            self.boton_borde(x, y + fila + 8, w, "Guía para evitar cortes de batería", t["aviso"])
        self.y += 14
        if not medir:
            self.texto(dx + dw - 24, self.y + 14, "CERRAR", 13.5, t["acento"], True, "rm")
        self.y += 40
        return self.y + 20

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


def captura_menu(t, icono=None, fondo=None, ancho=720):
    """PNG con el menú ☰ de la app (contacto, importar cuenta y evitar desconexiones) usando el tema t."""
    t = T.normalizar(t)
    alto = int(_Pantalla(t, icono, None, s=1).dibujar_menu(medir=True)) + 1
    pantalla = _Pantalla(t, icono, fondo, alto_dp=alto)
    pantalla.dibujar_menu()
    b = io.BytesIO()
    pantalla.imagen(ancho, alto).save(b, "PNG", optimize=True)
    return b.getvalue()


def muestrario(temas, icono=None, fondo=None, columnas=5):
    """JPG con todas las plantillas juntas, numeradas. temas: lista de (nombre, tema)."""
    celda_w, alto_dp, margen, pie = 300, 530, 18, 44
    celda_h = int(round(celda_w * alto_dp / 360))
    filas = (len(temas) + columnas - 1) // columnas
    hoja = Image.new("RGB", (margen + columnas * (celda_w + margen), margen + filas * (celda_h + pie + margen)), (24, 24, 28))
    d = ImageDraw.Draw(hoja)
    f = _fuente("sans-serif", True, 22)[0]
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
