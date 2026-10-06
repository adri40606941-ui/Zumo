#!/usr/bin/env python3
"""Comprueba que un APK sirva en celulares con páginas de memoria de 16 KB (Android 15 en adelante).

Para las librerías de 64 bits (arm64-v8a, x86_64) pide dos cosas:
  1. cada segmento PT_LOAD del .so está alineado a 16 KB o más;
  2. si el .so va sin comprimir dentro del APK, empieza en un múltiplo de 16 KB (si va comprimido,
     el sistema lo extrae al instalar, como pide useLegacyPackaging, y no hace falta).
Las de 32 bits no se tocan: en 32 bits el sistema sigue usando páginas de 4 KB.

Uso: comprobar-16kb.py app.apk     (sale con 0 si está todo bien)
"""
import struct
import sys
import zipfile

PAGINA = 16384
LIBS_64 = ("lib/arm64-v8a/", "lib/x86_64/")


def alineaciones_load(datos):
    if datos[:4] != b"\x7fELF" or datos[4] != 2 or datos[5] != 1:     # solo ELF64 little endian
        return None
    e_phoff, = struct.unpack_from("<Q", datos, 0x20)
    e_phentsize, e_phnum = struct.unpack_from("<HH", datos, 0x36)
    res = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, = struct.unpack_from("<I", datos, off)
        if p_type == 1:                                                  # PT_LOAD
            p_align, = struct.unpack_from("<Q", datos, off + 0x30)
            res.append(p_align)
    return res


def revisar(ruta):
    problemas, vistas = [], 0
    with zipfile.ZipFile(ruta) as z:
        for info in z.infolist():
            if not info.filename.endswith(".so") or not info.filename.startswith(LIBS_64):
                continue
            vistas += 1
            al = alineaciones_load(z.read(info))
            if al is None:
                problemas.append(f"{info.filename}: no es un ELF de 64 bits legible")
            elif not al or min(al) < PAGINA:
                problemas.append(f"{info.filename}: segmentos alineados a {min(al) if al else '?'} (hacen falta {PAGINA})")
            if info.compress_type == zipfile.ZIP_STORED:
                with open(ruta, "rb") as f:
                    f.seek(info.header_offset + 26)
                    n, m = struct.unpack("<HH", f.read(4))
                inicio = info.header_offset + 30 + n + m
                if inicio % PAGINA:
                    problemas.append(f"{info.filename}: empieza en {inicio}, no es múltiplo de {PAGINA}")
    if vistas == 0:
        problemas.append("el APK no trae librerías de 64 bits")
    return vistas, problemas


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    n, malos = revisar(sys.argv[1])
    for m in malos:
        print("✘", m)
    if malos:
        sys.exit(1)
    print(f"✔ {n} librería(s) de 64 bits listas para páginas de 16 KB")
