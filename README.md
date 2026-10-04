# Zumo

Panel para administrar accesos SSH en una VPS y exponerlos a través de varios
transportes pensados para apps de túnel de Android (HTTP Injector, HTTP Custom,
etc.). Se maneja desde un panel de terminal.

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
`zumo-lib.sh`, que toma un lock (`/etc/zumo/usuarios.lock`) y escribe de forma
atómica.

## Limitador

El límite de cada usuario (`usuarios.db`, segundo campo) es la cantidad de
**sesiones SSH simultáneas**. Con límite 1, si el mismo usuario abre una segunda
conexión, el limitador corta una de las dos en la siguiente revisión (como mucho
3 s): por defecto la vieja (`KICK=oldest`), así quien reconecta tras perder la
señal entra enseguida. Con `KICK=newest` se queda la primera y se corta la
nueva. Cuenta igual por PDirect, BHTTP, HCR o conexión directa, y por IPv4 o
IPv6.

Se ajusta en `/etc/zumo/limit.conf` (se relee solo, sin reiniciar):

| Clave | Por defecto | Qué hace |
|---|---|---|
| `INTERVAL` | `3` | Segundos entre revisiones. |
| `GRACE` | `0` | Segundos que una sesión de más puede vivir antes de cortarla. |
| `KICK` | `oldest` | `oldest` corta la sesión vieja y deja la nueva; `newest` corta la nueva y deja la vieja. |
| `TEMP_CLEANUP` | `1` | Borra los usuarios temporales vencidos. |

Si un cliente cambia de red y reconecta, su sesión vieja tarda hasta ~30 s en
darse por caída y la nueva cuenta como "segunda". Con `KICK=oldest` (por
defecto) no se nota. Con `KICK=newest`, la nueva se corta hasta que la vieja
caiga: subí `GRACE` (por ejemplo 20) si te pasa seguido. Ojo con `oldest`: si
dos personas comparten un usuario, se van sacando una a la otra.

Ver qué cortó: `journalctl -u zumo-limit -f`. Probar sin cortar nada:
`zumo-limit --once --dry-run`.

## Archivos del repo

| Archivo | Descripción |
|---|---|
| `install.sh` | Instalador principal. |
| `panel.sh` | Panel de terminal. |
| `zumo-lib.sh` | Operaciones compartidas sobre `usuarios.db`. |
| `zumo-limit.c` | Fuente del limitador (el instalador lo baja y lo compila). |
| `limit.conf` | Configuración de ejemplo del limitador. |
| `quitar-panelweb.sh` | Quita de una VPS el panel web de versiones anteriores. |
| `tests/` | Prueba del limitador (`sudo bash tests/prueba-limitador.sh`). |
| `hcr-install.sh` / `hcr-server` | Instalador y binario de HCR. |
| `bhttp-server-*` / `bhttp-shim-*` | Binarios de BHTTP por arquitectura. |
| `main.go` | Fuente del adaptador BHTTP (`bhttp-shim`). |
| `diagnostico.sh` | Chequeos de estado. |

## Quitar el panel web de una VPS

Las versiones anteriores traían un panel web opcional. Si lo tenías instalado:

```bash
curl -fsSL https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/quitar-panelweb.sh | bash
```
