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
| **Panel** (`zumo`) | Crear/editar/borrar usuarios, ver quién está conectado, prender y apagar protocolos. En Herramientas: BBR, test de velocidad, uso de CPU y RAM (con limpieza de RAM con la tecla L), Usuario compartido (quiénes intentaron conectar más sesiones de las permitidas; lo anota el limitador en `/etc/zumo/excesos.log`) y Control de red (velocidad de bajada y subida en vivo, uso del enlace, tráfico total y conexiones SSH). |
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
| `EXPIRE_HOUR` | `21` | Hora (0 a 23, hora de la VPS) del día de vencimiento en que el usuario vence. |
| `EXPIRE_DELETE` | `0` | `0`: al vencer el usuario se bloquea (no puede conectarse) y se desbloquea solo al renovarlo; aparece en Usuarios vencidos. `1`: se borra. |
| `TEMP_CLEANUP` | `1` | Borra los usuarios temporales vencidos. |

Si un cliente cambia de red y reconecta, su sesión vieja tarda hasta ~30 s en
darse por caída y la nueva cuenta como "segunda". Con `KICK=oldest` (por
defecto) no se nota. Con `KICK=newest`, la nueva se corta hasta que la vieja
caiga: subí `GRACE` (por ejemplo 20) si te pasa seguido. Ojo con `oldest`: si
dos personas comparten un usuario, se van sacando una a la otra.

Ver qué cortó: `journalctl -u zumo-limit -f`. Probar sin cortar nada:
`zumo-limit --once --dry-run`.

## App Android (Zumo VPN)

El código está en `android/`. GitHub la compila sola cada vez que cambia algo en esa carpeta, y
también a mano desde **Actions → Compilar app Android → Run workflow**. El APK queda en la rama
`apk` (`zumo-vpn.apk`). Las compilaciones de otras ramas van a `apk-prueba`, para no pisar el
APK publicado.

El cliente carga su cuenta de dos formas:

- **Servidor + usuario y contraseña**: elige un servidor de la lista que trae la app y escribe
  sus datos. El host y el payload no se ven.
- **Archivo `.zs`**: trae servidor, payload, usuario, clave y vencimiento. La app todavía los abre,
  pero el bot ya no los genera.

La lista de servidores está en `android/servidores.txt` (un bloque por servidor, cada uno con su
payload; el formato está explicado en el mismo archivo). Dentro del APK va cifrada. Si el
repositorio es público y no querés que se lean los payloads, pegá la lista en el secreto
`ZUMO_SERVIDORES` (Settings → Secrets and variables → Actions) y dejá el archivo sin servidores:
la compilación usa el secreto. Sin servidores cargados, la app funciona solo con `.zs`.

Para cambiar un payload: editá la lista (o el secreto), compilá y pasales el APK nuevo a los
clientes. Si no le cambiás el nombre al servidor, no tienen que volver a elegirlo.

### Usuarios desde el bot de Telegram

En el bot: **➕ Crear usuario** (también está arriba de la lista de **👥 Usuarios**). Crea lo mismo que el panel:

- **👤 Normal**: usuario, contraseña, días y conexiones.
- **🔑 HWID**: nombre del cliente, su HWID (8 a 32 letras y números), días y conexiones.
- **⏳ Temporal** y **⏳ Temporal HWID**: duran los minutos que elijas (hasta 1440) y se borran solos.

Al crear, renovar o cambiar la clave, el bot manda en un mensaje aparte los datos listos para reenviarle al
cliente (el mismo texto que muestra el panel). En la ficha de cada usuario, **📋 Datos para el cliente** los
vuelve a mandar. El bot no genera archivos `.zs`.

### Compilar la app desde el bot de Telegram

En el bot: **📱 App Android**. Ahí cargás los servidores de la app (nombre, host, puerto, payload), cambiás
el payload de cualquiera y tocás **🔨 Compilar y enviarme el APK**. El bot sube la lista cifrada al secreto
`ZUMO_SERVIDORES` del repo, lanza la compilación en GitHub, espera y te manda el APK por Telegram. Si falla,
te muestra el final del registro. El mensaje con el payload que escribís se borra del chat.

Token de GitHub (una sola vez): github.com → Settings → Developer settings → Personal access tokens →
**Fine-grained tokens** → *Generate new token*; en *Repository access* elegí solo `Zumo`; permisos:
**Actions: Read and write**, **Secrets: Read and write**, **Contents: Read-only**. Lo pegás cuando el
instalador del bot lo pide (queda en `/etc/zumo/bot.env`, nunca en el repo). Para cargarlo en un bot ya
instalado, volvé a correr `bot/instalar-bot.sh`.

## Archivos del repo

| Archivo | Descripción |
|---|---|
| `install.sh` | Instalador principal. |
| `panel.sh` | Panel de terminal. |
| `zumo-lib.sh` | Operaciones compartidas sobre `usuarios.db`. |
| `zumo-limit.c` | Fuente del limitador (el instalador lo baja y lo compila). |
| `limit.conf` | Configuración de ejemplo del limitador. |
| `quitar-panelweb.sh` | Quita de una VPS el panel web de versiones anteriores. |
| `zumo-datos.sh` | Contador de datos por usuario (mide lo que mueve cada sesión SSH); alimenta Herramientas → Uso de datos. |
| `tests/` | Pruebas del limitador, de "Eliminar usuario" y de las herramientas (`sudo bash tests/prueba-limitador.sh`, `prueba-borrar.sh`, `prueba-herramientas.sh`, `prueba-datos.sh`, `prueba-respaldo.sh`). |
| `hcr-install.sh` / `hcr-server` | Instalador y binario de HCR. |
| `bhttp-server-*` / `bhttp-shim-*` | Binarios de BHTTP por arquitectura. |
| `main.go` | Fuente del adaptador BHTTP (`bhttp-shim`). |
| `android/` | App Android (Zumo VPN). `android/servidores.txt` es la lista de servidores que trae la app. |
| `zumoid/` / `zumoid-*` / `zumoid.sha256` | Servicio `zumo-id` (control de Android ID): fuente, binarios y hash. |
| `diagnostico.sh` | Chequeos de estado. |

## Vincular usuario a un celular (Android ID)

El cliente conecta normal con su usuario y contraseña. Al conectar, la app
manda el **Android ID** del celular por un canal interno del túnel SSH al
servicio `zumo-id` de la VPS (puerto local 7390). El servicio lo anota y vos lo
ves en el panel (Editar usuario) y en la ficha del usuario en el bot.

- **Vincular** (panel op. 6 / botón del bot): desde ese momento el usuario solo
  puede conectar desde ese Android ID. Desde otro celular se le corta la sesión
  y la app le muestra el código del celular para que te lo pase.
- **Desvincular** o **olvidar celular** (op. 7): el usuario vuelve a ser libre y
  el próximo celular que conecte queda anotado.
- El servidor identifica al usuario por el kernel (`/proc`), no por lo que diga
  el cliente. Si el servicio no está, no contesta o el ID no es usable, la
  conexión sigue normal (falla abierta).

Límites a tener en cuenta:

- El ID sobrevive a desinstalar y reinstalar **si el APK se firma con la misma
  clave** (por eso la clave fija). Cambia con reseteo de fábrica, app clonada
  ("Dual apps") o perfil secundario, y con root se puede falsear.
- Un usuario vinculado que conecta con la app vieja u otra app (HTTP Custom,
  etc.) no manda ID: se le corta unos 12 s después de conectar.
- Solo funciona en las VPS donde actualizaste el panel (`actualizar.sh`) y
  el bot solo ve el `/etc/zumo` de su propia VPS.
- Reconstruir los binarios: `bash zumoid/build.sh`.

## Quitar el panel web de una VPS

Las versiones anteriores traían un panel web opcional. Si lo tenías instalado:

```bash
curl -fsSL https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/quitar-panelweb.sh | bash
```
