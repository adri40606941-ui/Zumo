#!/usr/bin/env python3
"""Prueba del comprobador de páginas de 16 KB con APK de juguete. Uso: python3 tests/prueba-16kb.py"""
import importlib.util
import os
import struct
import tempfile
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("c", os.path.join(AQUI, "..", "android", "comprobar-16kb.py"))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def elf(align):
    h = bytearray(64 + 56)
    h[0:4] = b"\x7fELF"; h[4] = 2; h[5] = 1
    struct.pack_into("<Q", h, 0x20, 64); struct.pack_into("<HH", h, 0x36, 56, 1)
    struct.pack_into("<I", h, 64, 1); struct.pack_into("<Q", h, 64 + 0x30, align)
    return bytes(h)


def apk(d, nombre, align, comprimido, relleno=0, abi="arm64-v8a"):
    ruta = os.path.join(d, nombre)
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("classes.dex", b"x" * relleno)
        zi = zipfile.ZipInfo(f"lib/{abi}/libx.so")
        zi.compress_type = zipfile.ZIP_DEFLATED if comprimido else zipfile.ZIP_STORED
        z.writestr(zi, elf(align))
    return ruta


fallos = 0
def chequear(nombre, ok):
    global fallos
    print(("  ok   " if ok else "  FALLA ") + nombre)
    fallos += 0 if ok else 1

with tempfile.TemporaryDirectory() as d:
    # 16 KB justos: el .so empieza en 30 + len(nombre) de "classes.dex"(11) + relleno => se calcula el relleno
    # para que el encabezado local del .so caiga en un múltiplo de 16384.
    inicio_so = 30 + len("classes.dex") + 0            # tras el primer encabezado local, antes de los datos
    relleno = 16384 - (inicio_so + 30 + len("lib/arm64-v8a/libx.so"))
    _, p = c.revisar(apk(d, "bueno.apk", 16384, False, relleno))
    chequear("alineado bien", p == [])
    _, p = c.revisar(apk(d, "viejo.apk", 4096, False, relleno))
    chequear("detecta alineación de 4 KB", any("alineados a 4096" in x for x in p))
    _, p = c.revisar(apk(d, "comp.apk", 16384, True))
    chequear(".so comprimido (se extrae al instalar) está bien", p == [])
    _, p = c.revisar(apk(d, "comp4.apk", 4096, True))
    chequear("comprimido pero con alineación de 4 KB falla", any("alineados a 4096" in x for x in p))
    _, p = c.revisar(apk(d, "desal.apk", 16384, False, relleno + 7))
    chequear("detecta .so mal ubicado en el zip", any("múltiplo" in x for x in p))
    n, p = c.revisar(apk(d, "32.apk", 4096, False, 0, abi="armeabi-v7a"))
    chequear("sin 64 bits avisa", any("64 bits" in x for x in p))
print("TODO OK" if fallos == 0 else f"{fallos} fallos")
raise SystemExit(1 if fallos else 0)
