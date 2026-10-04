# Zumo

Panel para administrar accesos SSH en una VPS y exponerlos a través de varios
transportes pensados para apps de túnel de Android (HTTP Injector, HTTP Custom,
etc.). Incluye un panel de terminal y un panel web.

## Instalación

En una VPS Debian/Ubuntu, como root:

```bash
curl -fsSL https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/install.sh | bash
```

Después, abrí el panel con:

```bash
zumo
```

## Qué instala

| Componente | Para qué sirve |
|---|---|
| **Panel** (`zumo`) | Crear/editar/borrar usuarios, ver quién está conectado, prender y apagar protocolos. |
| **Panel web** | Lo mismo desde el navegador (opcional, se configura desde el panel). |
| **Limitador** (`zumo-limit`) | Cada 3 s corta las sesiones SSH que pasan el límite de cada usuario, corta a los vencidos y borra los temporales vencidos. |
| **PDirect** | WebSocket en el puerto 80 → SSH local. |
| **BadVPN** | UDPGW en el 7300 (para el tráfico UDP de las apps). |
| **BHTTP** | Transporte BHTTP → SSH local (servidor + adaptador para DTunnel). |
| **HCR Server** | Transporte HCR → SSH local, con TLS, plano o `auto`. |

Los cuatro transportes (PDirect, BHTTP, HCR, BadVPN) son **paralelos**: cada uno
entra al mismo `sshd` del puerto 22. No están apilados uno dentro de otro.

## Usuarios

- **Normal**: usuario y contraseña que vos elegís.
- **HWID**: el ID del dispositivo del cliente se usa como usuario *y* contraseña.
  El nombre del cliente queda como etiqueta para identificarlo en el panel.

La base de usuarios está en `/etc/zumo/usuarios.db`, con el formato
`usuario:limite:vencimiento`. Las operaciones sobre ese archivo pasan todas por
`zumo-lib.sh` (panel de terminal) o por el equivalente en `panelweb.py` (panel
web), que comparten el mismo lock (`/etc/zumo/usuarios.lock`) y escriben de
forma atómica, para que los dos paneles no se pisen.

## Limitador

El límite de cada usuario (`usuarios.db`, segundo campo) es la cantidad de
**sesiones SSH simultáneas**. Con límite 1, si el mismo usuario abre una segunda
conexión, el limitador la corta en la siguiente revisión (como mucho 3 s) y la
primera sigue conectada. Cuenta igual por PDirect, BHTTP, HCR o conexión directa,
y por IPv4 o IPv6.

Se ajusta en `/etc/zumo/limit.conf` (se relee solo, sin reiniciar):

| Clave | Por defecto | Qué hace |
|---|---|---|
| `INTERVAL` | `3` | Segundos entre revisiones. |
| `GRACE` | `0` | Segundos que una sesión de más puede vivir antes de cortarla. |
| `KICK` | `newest` | `newest` corta la sesión nueva; `oldest` corta la vieja. |
| `TEMP_CLEANUP` | `1` | Borra los usuarios temporales vencidos. |

Si un cliente cambia de red y reconecta, su sesión vieja tarda hasta ~30 s en
darse por caída y la nueva cuenta como "segunda". Si te pasa seguido, subí
`GRACE` (por ejemplo 20) o usá `KICK=oldest`.

Ver qué cortó: `journalctl -u zumo-limit -f`. Probar sin cortar nada:
`zumo-limit --once --dry-run`.

## Archivos del repo

| Archivo | Descripción |
|---|---|
| `install.sh` | Instalador principal. |
| `panel.sh` | Panel de terminal. |
| `panelweb.py` | Panel web (Flask). |
| `zumo-lib.sh` | Operaciones compartidas sobre `usuarios.db`. |
| `zumo-limit.c` | Fuente del limitador (el instalador lo baja y lo compila). |
| `limit.conf` | Configuración de ejemplo del limitador. |
| `tests/` | Pruebas del limitador y del panel web (`sudo bash tests/prueba-limitador.sh`, `sudo python3 tests/prueba-panelweb.py`). |
| `hcr-install.sh` / `hcr-server` | Instalador y binario de HCR. |
| `bhttp-server-*` / `bhttp-shim-*` | Binarios de BHTTP por arquitectura. |
| `main.go` | Fuente del adaptador BHTTP (`bhttp-shim`). |
| `diagnostico.sh` | Chequeos de estado. |

## Nota de seguridad

El panel web es **HTTP sin cifrar**. Al configurarlo desde el panel podés elegir
que escuche solo en `127.0.0.1` y entrar por túnel SSH
(`ssh -L 9090:127.0.0.1:9090 root@IP`). Si lo exponés a internet, usalo en una red
de confianza o detrás de una VPN. El login tiene un límite de
intentos por IP para frenar fuerza bruta, pero eso no reemplaza al cifrado.
