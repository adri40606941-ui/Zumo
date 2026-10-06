#!/usr/bin/env python3
"""Paquete con la apariencia de la app (tema + ícono + fondo) que viaja del bot a GitHub.

Un secreto de GitHub Actions guarda como mucho 48 KB, así que el paquete (un .zip) se parte en
varios secretos:

    ZUMO_MARCA      "1:<cantidad de partes>:<sha256 del zip>"
    ZUMO_MARCA_1    primera parte, en base64
    ZUMO_MARCA_2    ...

El bot arma los secretos con secretos(); el workflow (.github/workflows/android.yml) corre este
archivo, que vuelve a juntar las partes y deja tema.json, icono.png y fondo.jpg en android/marca.
Solo usa la biblioteca estándar: en GitHub no hay nada más instalado.
"""
import base64
import hashlib
import io
import os
import sys
import zipfile

PARTE = 34000          # bytes por parte: en base64 son ~45 KB, por debajo del tope de 48 KB
MAX_PARTES = 14        # las mismas que declara el workflow
MAX_PAQUETE = PARTE * MAX_PARTES
ARCHIVOS = ("tema.json", "icono.png", "fondo.jpg")   # lo único que se saca del paquete


class ErrorMarca(Exception):
    pass


def empaquetar(tema_json, icono=None, fondo=None):
    """Zip con tema.json y, si hay, icono.png y fondo.jpg (las imágenes ya vienen comprimidas)."""
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(zipfile.ZipInfo("tema.json", (2024, 1, 1, 0, 0, 0)), tema_json.encode("utf-8"), zipfile.ZIP_DEFLATED)
        if icono:
            z.writestr(zipfile.ZipInfo("icono.png", (2024, 1, 1, 0, 0, 0)), icono, zipfile.ZIP_STORED)
        if fondo:
            z.writestr(zipfile.ZipInfo("fondo.jpg", (2024, 1, 1, 0, 0, 0)), fondo, zipfile.ZIP_STORED)
    datos = b.getvalue()
    if len(datos) > MAX_PAQUETE:
        raise ErrorMarca(f"Las imágenes pesan demasiado ({len(datos) // 1024} KB; el máximo es {MAX_PAQUETE // 1024} KB). "
                         "Usá un ícono o un fondo más liviano.")
    return datos


def secretos(paquete):
    """{nombre_del_secreto: valor} para subir al repo. La cabecera va última: si la subida se corta
    a la mitad, queda la cabecera vieja y la compilación avisa en vez de usar un paquete a medias."""
    partes = [paquete[i:i + PARTE] for i in range(0, len(paquete), PARTE)] or [b""]
    d = {f"ZUMO_MARCA_{i + 1}": base64.b64encode(p).decode() for i, p in enumerate(partes)}
    d["ZUMO_MARCA"] = f"1:{len(partes)}:{hashlib.sha256(paquete).hexdigest()}"
    return d


def desde_secretos(env):
    """Junta las partes. None si no hay apariencia subida; ErrorMarca si está incompleta o dañada."""
    cab = (env.get("ZUMO_MARCA") or "").strip()
    if not cab:
        return None
    try:
        version, n, suma = cab.split(":")
        n = int(n)
    except ValueError:
        raise ErrorMarca("la cabecera ZUMO_MARCA no tiene el formato esperado") from None
    if version != "1" or not 1 <= n <= MAX_PARTES:
        raise ErrorMarca(f"ZUMO_MARCA dice versión {version} y {n} parte(s): este workflow no lo entiende")
    try:
        paquete = b"".join(base64.b64decode((env.get(f"ZUMO_MARCA_{i}") or "").strip(), validate=True) for i in range(1, n + 1))
    except ValueError:
        raise ErrorMarca("una parte de la apariencia no es base64 válido") from None
    if hashlib.sha256(paquete).hexdigest() != suma:
        raise ErrorMarca("las partes de la apariencia no coinciden con la cabecera (subida incompleta)")
    return paquete


def desempacar(paquete, destino):
    """Deja en 'destino' los archivos conocidos del paquete y borra los que el paquete no trae.
    Devuelve los nombres escritos."""
    try:
        z = zipfile.ZipFile(io.BytesIO(paquete))
        contenido = {n: z.read(n) for n in z.namelist() if n in ARCHIVOS}
    except (zipfile.BadZipFile, KeyError, RuntimeError):
        raise ErrorMarca("el paquete de apariencia no es un zip válido") from None
    if "tema.json" not in contenido:
        raise ErrorMarca("el paquete de apariencia no trae tema.json")
    os.makedirs(destino, exist_ok=True)
    for n in ARCHIVOS:
        ruta = os.path.join(destino, n)
        if n in contenido:
            with open(ruta, "wb") as f:
                f.write(contenido[n])
        elif os.path.exists(ruta):
            os.remove(ruta)
    return sorted(contenido)


def main(argv):
    destino = argv[1] if len(argv) > 1 else "android/marca"
    try:
        paquete = desde_secretos(os.environ)
        if paquete is None:
            print(f"Apariencia: la del repo ({destino})")
            return 0
        escritos = desempacar(paquete, destino)
    except ErrorMarca as e:
        print(f"error: apariencia de la app: {e}. Volvé a compilar desde el bot.")
        return 1
    print(f"Apariencia: la que subió el bot ({', '.join(escritos)}; {len(paquete) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
