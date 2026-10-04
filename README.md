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
| **Limitador** (`zumo-limit`) | Mata las conexiones SSH que exceden el límite por usuario. |
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

## Archivos del repo

| Archivo | Descripción |
|---|---|
| `install.sh` | Instalador principal. |
| `panel.sh` | Panel de terminal. |
| `panelweb.py` | Panel web (Flask). |
| `zumo-lib.sh` | Operaciones compartidas sobre `usuarios.db`. |
| `hcr-install.sh` / `hcr-server` | Instalador y binario de HCR. |
| `bhttp-server-*` / `bhttp-shim-*` | Binarios de BHTTP por arquitectura. |
| `main.go` | Fuente del adaptador BHTTP (`bhttp-shim`). |
| `diagnostico.sh` | Chequeos de estado. |

## Nota de seguridad

El panel web es **HTTP sin cifrar**. Usalo en una red de confianza o detrás de
una VPN / túnel SSH si lo exponés a internet. El login tiene un límite de
intentos por IP para frenar fuerza bruta, pero eso no reemplaza al cifrado.
