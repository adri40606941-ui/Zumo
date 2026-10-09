# Zumo

Panel para administrar accesos SSH en una VPS y exponerlos a través de varios
transportes pensados para apps de túnel de Android (HTTP Injector, HTTP Custom,
etc.). Se maneja desde un panel de terminal.

## Instalación

En una VPS Debian/Ubuntu, como root:

```bash
curl -fsSL https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/install.sh | bash
```

**Instalar desde tu dominio, sin GitHub, con un código de un solo uso.** El instalador del bot ya deja todo listo en el centro (pide el
dominio —Enter = `bot.zumoserver.com`—, copia el repo a `/opt/zumo-repo` y abre los puertos 80 y 443 si hay ufw), y si GitHub no responde
reinstala el bot desde esa copia. En el bot: **🔑 Instalar en VPS nueva** →
«Código para el panel» (o «para el bot»). Te da un código que sirve una vez y vence en 15 minutos. En la VPS nueva, como root:

```bash
curl -fsSL https://bot.zumoserver.com/i | bash
```

Pide el código, lo cambia por un pase propio de esa VPS e instala todo desde tu dominio. Ese pase queda en la dirección
guardada (`/etc/zumo/base.url`), así las actualizaciones del panel también salen del dominio sin pedir nada.

- Sin código ni pase, el dominio no entrega ningún archivo del instalador (solo el cargador `/i` y la lista de servidores de la app).
- «📋 VPS instaladas» muestra cada VPS (fecha e IP) y deja **anular** el acceso de una: ya no baja más del dominio.
- Quien prueba códigos al azar se frena (10 fallos por minuto bloquean los canjes 2 minutos).
- En el disco del centro solo se guarda el hash de códigos y pases (`/etc/zumo/accesos.json`, 0600).
- BadVPN (v1.999.130) ya no se baja de un repo de terceros: su código está en `fuentes/badvpn.tar.gz` y el instalador lo compara con una suma de control fija antes de compilarlo.

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
| **BHTTP** | Transporte BHTTP → SSH local (servidor + adaptador para DTunnel). Hay dos motores a elegir en el panel: BHTTP (el de siempre) y BHTTP v2. |
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

### Actualizar servidores sin recompilar (botón ↻ de la app)

Para cambiar un host, un puerto o un payload **no hace falta recompilar ni reinstalar** la app.
En el bot, después de editar la lista, tocá **📡 Actualizar servidores en la app (↻)**: sube la lista
al secreto `ZUMO_SERVIDORES` y dispara el workflow *Publicar servidores de la app*, que escribe la
lista cifrada (`servidores.bin`) en la rama `apk`. Tarda menos de un minuto.

En la app hay un botón **↻** arriba a la derecha: al tocarlo, baja esa lista de
`https://raw.githubusercontent.com/<repo>/apk/servidores.bin`, la descifra y la usa en el acto. Si tu
cuenta usaba un servidor cuyo payload cambió, toma el nuevo sin tocar nada. Si la descarga falla, se
queda con la lista que ya tenía.

- El `servidores.bin` va cifrado con el mismo secreto que ya viaja dentro del APK, así que publicarlo
  en una rama pública no agrega exposición.
- La URL se hornea al compilar (variable `ZUMO_ACTUALIZAR_URL`): una app compilada antes de este
  cambio no muestra el botón hasta que la recompiles una vez.
- Cambiar el nombre, el ícono, los colores o el fondo **sí** necesita recompilar (eso no viaja en la lista).
- No hace falta tocar el token de GitHub: el workflow publica con el token propio de Actions.

### Apariencia de la app (nombre, colores, ícono, fondo)

En el bot: **📱 App Android → 🎨 Apariencia de la app**. Desde ahí se cambia, sin tocar código:

- **Plantillas**: diez diseños listos (oscuros y claros). El bot manda una imagen con todos y otra en
  grande del que elijas, ya con tu nombre e ícono, antes de aplicarlo.
- **Nombre y lema**: el nombre es el que aparece debajo del ícono, arriba en la pantalla y en la notificación.
- **Colores**: fondo, tarjetas, títulos y botones, letra, botón Conectar y botón Desconectar. Se eligen
  de una paleta o escribiendo el código (`#B388FF`); los bordes y tonos intermedios se acomodan solos.
- **Fondo**: color liso, degradado de dos colores o una imagen (mandás una foto). Se puede oscurecer
  la imagen y hacer las tarjetas translúcidas.
- **Ícono y logo**: importás una imagen y pasa a ser el ícono de la app; también puede ir arriba del
  título en lugar del emoji.
- **Letras**: tipo de letra, tamaño y título en mayúsculas o como lo escribiste.
- **Menús y secciones**: mostrar u ocultar Vencimiento, Velocidad y datos, Ajustes del teléfono e
  Importar `.zs`; forma de las esquinas; y hasta 3 botones de contacto en el menú ☰ (WhatsApp, Telegram, web).
- **Vista previa**: en cualquier momento el bot manda una imagen de cómo queda la pantalla principal, y
  **👁 Menú ☰** (en «Menús y secciones») manda cómo queda el menú con los botones de contacto y «Importar .zs».
  Es una vista aproximada: las ocho letras se simulan con las de la VPS (Informal y Manuscrita solo de forma
  aproximada) y el emoji del logo se ve a color si la VPS tiene `fonts-noto-color-emoji` (lo instala `bot/instalar-bot.sh`).

Los cambios se guardan en la VPS (`/etc/zumo/app-marca/`) y llegan a los clientes cuando tocás
**🔨 Compilar y enviarme el APK** y les pasás el APK nuevo: el bot sube la apariencia al repo como
secretos (`ZUMO_MARCA` y `ZUMO_MARCA_1`…`14`) y la compilación la usa. La firma y el identificador de
la app no cambian, así que se instala encima de la anterior aunque cambie de nombre e ícono.

Sin bot, lo mismo se hace a mano en `android/marca/`: `tema.json` (nombre, colores, etc.) y,
opcionalmente, `icono.png` y `fondo.jpg`.

Para las vistas previas el bot usa `python3-pil` (lo instala `bot/instalar-bot.sh`). En un bot ya
instalado, volvé a correr el instalador para actualizarlo: conserva tu configuración.

Token de GitHub (una sola vez): github.com → Settings → Developer settings → Personal access tokens →
**Fine-grained tokens** → *Generate new token*; en *Repository access* elegí solo `Zumo`; permisos:
**Actions: Read and write**, **Secrets: Read and write**, **Contents: Read-only**. Lo pegás cuando el
instalador del bot lo pide (queda en `/etc/zumo/bot.env`, nunca en el repo). Para cargarlo en un bot ya
instalado, volvé a correr `bot/instalar-bot.sh`.

## Panel de revendedores (🧑‍💼 Revendedores)

Para vender accesos sin darle el bot a nadie. Vos creás al revendedor desde el bot y él trabaja en una página web.

**Monedas:** 🥉 bronce = 7 días · 🥈 plata = 15 días · 🥇 oro = 30 días. Crear o renovar un usuario por 7 / 15 / 30 días
gasta una moneda de bronce / plata / oro. Bloquear, desbloquear y eliminar no gastan nada. Si la VPS falla al crear o
renovar, la moneda se devuelve sola.

**Desde el bot** (Menú → 🧑‍💼 Revendedores):
1. ➕ Agregar revendedor → escribís su usuario y su contraseña (o 🎲 para que el bot invente una) y elegís la VPS donde
   se van a crear sus usuarios (una de las de 🖥 Máquinas: el bot entra por SSH).
2. Tocá al revendedor → 🥉 / 🥈 / 🥇 para agregarle monedas (botones +1 +5 +10 +20, o escribís el número; con un
   número negativo se le quitan). También: cambiar contraseña, **sumarle o quitarle VPS** (🖥 VPS), bloquear o activar su
   acceso, ver sus movimientos, 📋 la lista de usuarios que creó y en qué VPS, y eliminarlo.
3. En la pantalla de Revendedores: 📋 **Todos los usuarios** (quién creó qué y en qué VPS; si es larga te la manda como
   archivo), 📊 **Resumen** del mes (monedas cargadas, gastadas y saldo por revendedor, y el mes anterior) y 🔒 **HTTPS del
   panel** (comprueba el puerto 443 y explica cómo pasar Cloudflare a «Completo»).

**El revendedor** entra a `https://TU-DOMINIO/r` (el mismo dominio de la lista de la app: `ZUMO_DOMINIO` en `bot.env`) con el
usuario y la contraseña que le diste. Ahí ve sus monedas y puede **crear usuarios** (token + nombre + 7/15/30 días),
**renovar** (suma los días al vencimiento; si ya venció, cuenta desde hoy), **bloquear / desbloquear** y **eliminar**.
Solo ve y toca los usuarios que creó él. También tiene: un **buscador** y orden de la lista (más nuevos, los que vencen antes,
por nombre), un aviso y una marca de **«Vence en N d»** para los que vencen en 3 días o menos, el punto 🟢 *en línea* / 🔴 *sin
conexión*, el lápiz **Editar** (cambiar nombre, renovar, bloquear, eliminar), sus **movimientos** y **cambiar su contraseña**.

**Varias VPS por revendedor:** si le asignás más de una, cada usuario nuevo se crea en todas, y renovar (mismo vencimiento en
todas), bloquear, cambiar nombre y eliminar se aplican en todas. Si una VPS no contesta, la operación sigue en las demás, se
le avisa al revendedor cuál falló y el bot completa solo lo que faltó en cuanto vuelve (revisa cada 5 minutos). Una VPS que se
suma después también recibe los usuarios que ya existían.

**Avisos por Telegram a los admins:** usuario creado por un revendedor (con la lista de VPS), revendedor sin monedas de un tipo,
VPS de un revendedor que no contesta (y cuando vuelve), muchos intentos fallidos de contraseña en el panel, y usuarios completados
en una VPS. **Copia:** una por día de `revendedores.json` en `/var/backups/zumo/` (las últimas 14) y, con contraseña de
💾 Respaldo, la copia cifrada diaria que ya manda el bot también lo incluye; sin esa contraseña el bot avisa una vez por semana.

Cosas a tener en cuenta:
- La VPS asignada tiene que tener el panel instalado (`/etc/zumo/zumo-lib.sh`) y estar en 🖥 Máquinas.
- Los usuarios son tokens (HWID), como los de «Crear usuario → Token»: el token es el usuario y la contraseña de Linux.
- El panel solo funciona por **https** (por http redirige). Con Cloudflare en modo *Flexible* o *Full* andan los dos.
- Si el revendedor se equivoca de contraseña 8 veces seguidas queda frenado unos minutos. Para sacarle el acceso:
  ⏸ Bloquear acceso (sus usuarios siguen conectando). Los datos van en `/etc/zumo/revendedores.json` (sin contraseñas
  en claro) y entran en el respaldo del bot.
- La app muestra «Nombre [dd/mm] · N d» (con los días que faltan) junto a «Conectado» también para estos usuarios: el bot lee el nombre y el vencimiento de las
  VPS asignadas (se guarda 60 s, así que un cambio de nombre o de fecha en la VPS aparece en un minuto). No hace falta recompilar la app.

## Archivos del repo

| Archivo | Descripción |
|---|---|
| `install.sh` | Instalador principal. |
| `panel.sh` | Panel de terminal. |
| `zumo-lib.sh` | Operaciones compartidas sobre `usuarios.db`. |
| `actualizar.sh` / `actualizar-panel.sh` | Actualizan una VPS que ya tenés (todo / solo el panel). |
| `fuentes/zumo-limit.c` | Fuente del limitador (el instalador lo baja y lo compila). |
| `config/limit.conf` | Configuración de ejemplo del limitador. |
| `scripts/quitar-panelweb.sh` | Quita de una VPS el panel web de versiones anteriores. |
| `scripts/zumo-datos.sh` | Contador de datos por usuario (mide lo que mueve cada sesión SSH); alimenta Herramientas → Uso de datos. |
| `tests/` | Pruebas del limitador, de "Eliminar usuario" y de las herramientas (`sudo bash tests/prueba-limitador.sh`, `prueba-borrar.sh`, `prueba-herramientas.sh`, `prueba-datos.sh`, `prueba-respaldo.sh`). |
| `binarios/` | `hcr-install.sh` y `hcr-server` (HCR); `bhttp-server-*`, `bhttp-shim-*` y `bhttp-server.sha256` (BHTTP, por arquitectura). Los baja el instalador. |
| `binarios/bhttp-v2-server` | Servidor BHTTP v2 (BHTTP v1/v2), solo x86_64, de origen externo. Se activa desde el panel: Protocolos → BHTTP → BHTTP v2 (escucha directo en el puerto que elijas, por defecto 8081). Se verifica con `bhttp-v2-server.sha256` antes de instalar. |
| `fuentes/bhttp-shim/main.go` | Fuente del adaptador BHTTP (`bhttp-shim`). |
| `android/` | App Android (Zumo VPN). `android/servidores.txt` es la lista de servidores que trae la app. |
| `bot/respaldo.py` / `bot/centro.py` | Respaldo cifrado, clave de firma y sus botones del bot. |
| `bot/publico.py` / `bot/accesos.py` / `bot/codigos_bot.py` | Servidor web mínimo del bot (lista de la app e instaladores desde el dominio) y los códigos de un solo uso para instalar en VPS nuevas. |
| `bot/compilar_vps.py` / `bot/instalar-compilador.sh` | Compilar la app en la VPS del bot (sin GitHub) y el instalador de lo que hace falta. |
| `bot/maquinas.py` / `bot/maquinas_bot.py` | Sección 🖥 Máquinas: las VPS que el bot maneja por SSH (recursos y protocolos). |
| `bot/revendedores.py` / `bot/servicio_rev.py` / `bot/cuentas_vps.py` / `bot/panel_web.py` / `bot/revendedores_bot.py` | Sección 🧑‍💼 Revendedores: monedas, usuarios por SSH en la VPS asignada y el panel web `/r`. |
| `scripts/diagnostico.sh` | Chequeos de estado. |

## Compilar la app (en GitHub o en la VPS del bot)

Desde **📱 App Android** del bot hay dos opciones: **🔨 Compilar en GitHub** y **🖥 Compilar en la VPS**. Las dos usan
la misma lista de servidores, la misma apariencia y la misma clave de firma, y el bot te manda el APK por Telegram.
Como las dos numeran la versión igual (minutos desde 2025-01-01), un APK de GitHub y otro de la VPS se instalan
siempre uno encima del otro.

**Compilar en la VPS (sirve si GitHub se cae).** Se prepara una sola vez, por SSH en el centro:
`bash /opt/zumo-bot/instalar-compilador.sh`. Instala Java 17, Gradle, el Android SDK/NDK (unos 8 GB), 4 GB de swap si hace
falta y una copia del repo en `/opt/zumo-repo`. Después:

- El bot baja los cambios de GitHub a esa copia una vez por día y antes de cada compilación (**🔄 Sincronizar con GitHub**
  lo hace a mano). Si GitHub no responde, o la copia tiene cambios propios que no se unen solos, compila con la copia de la VPS.
- La copia se puede editar por SSH en `/opt/zumo-repo` (es un repo git común). Los cambios de lista y apariencia que
  arma el bot se aplican solo durante la compilación y no ensucian la copia.
- Hace falta la clave de firma en el centro (💾 Respaldo → ☁️ Traer la clave de GitHub, una sola vez). Sin ella el APK
  saldría con otra firma y los clientes no podrían instalarlo encima del anterior.
- El botón ↻ de la app baja la lista de `https://raw.githubusercontent.com/<repo>/apk/servidores.bin`. Para otra dirección,
  poné `ZUMO_URL_ACTUALIZAR=...` en `/etc/zumo/bot.env`.
- **Lista desde la VPS (rápido, sin depender de GitHub).** Con `ZUMO_DOMINIO=bot.zumoserver.com` en `/etc/zumo/bot.env`,
  el bot reparte la lista cifrada él mismo (`bot/publico.py`, puertos 80 y 443; solo sirve `/servidores.bin`) y
  «📡 Actualizar servidores» la publica al instante. Las apps que se compilen desde entonces prueban primero la VPS
  y, si no responde, GitHub (la dirección horneada es `https://dominio/servidores.bin|https://raw.githubusercontent.com/...`).
  En Cloudflare: registro A `bot` → IP de la VPS (con la nube naranja) y SSL en **Flexible** o **Full** (no «Full strict»:
  el certificado del 443 es propio). Abrí los puertos 80 y 443 en el firewall. Las apps ya instaladas siguen usando
  la dirección con la que se compilaron hasta que se instale un APK nuevo.
- La primera compilación baja las dependencias de Gradle y tarda más (puede pasar de 20 minutos); las siguientes, menos.

**Todo desde la VPS, GitHub solo de respaldo.**
- **APK para tus clientes**: cada vez que compilás (en la VPS o en GitHub) el bot deja el APK en `https://<dominio>/zumo-vpn.apk` y te
  pasa el enlace. Ese enlace es el que les das a los clientes.
- **Pasar una VPS que ya tiene el panel al dominio**: en el bot, 🔑 Instalar en VPS nueva → «🔄 Pasar una VPS que ya tiene el panel».
  En esa VPS corrés el mismo comando corto con el código; no reinstala nada, solo cambia de dónde baja las actualizaciones.
- **Editar en la VPS**: editá en `/opt/zumo-repo` (por SSH) y compilá desde ahí. «⬆️ Subir cambios a GitHub» (en 🖥 Compilar en la VPS)
  guarda tus cambios en un commit y los sube a GitHub como respaldo. Nunca fuerza: si GitHub tiene cambios que la VPS no tiene,
  primero «🔄 Sincronizar». El token necesita **Contents: Read and write**.

**Pasar el repo de GitHub a privado** (Settings → General → Danger Zone → Change visibility). Antes:
1. En `/etc/zumo/bot.env` poné `ZUMO_REPO_PRIVADO=1` (y reiniciá: `systemctl restart zumo-bot`). Así las apps nuevas bajan la lista
   solo de tu dominio y no intentan GitHub.
2. Compilá una app nueva y dales a los clientes el enlace `https://<dominio>/zumo-vpn.apk`. Las apps viejas siguen andando; para
   actualizar la lista tienen que instalar ese APK.
3. Pasá cada VPS con panel al dominio (código «Pasar una VPS…»). Después de hacerse privado, `raw.githubusercontent.com` ya no
   responde sin token, así que las VPS que no pasaste dejarían de poder actualizar.
4. El token del bot ya tiene que ser de ese repo (Contents, Actions y Secrets). Con repo privado, GitHub Actions consume los minutos
   gratis de la cuenta (unos 10 por compilación).
5. Una VPS del bot nueva, estando privado: con otro bot ya andando, usá «Código para el bot»; si no, bajá el instalador con el token:
   `curl -fsSL -H "Authorization: token TU_TOKEN" https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/bot/instalar-bot.sh | bash`.

**Compilar en GitHub.** Desde el botón "📱 App Android → 🔨 Compilar en GitHub" del bot.
Hace falta el token de GitHub en `/etc/zumo/bot.env` (el instalador del bot lo pide). La clave de firma queda
fija en el repo con "🔑 Asegurar clave de firma", así la app siempre se actualiza encima de la anterior.
"💾 Respaldo" manda a Telegram un archivo cifrado con `/etc/zumo` (se envía solo una vez por día si pusiste contraseña).

## Quitar el panel web de una VPS

Las versiones anteriores traían un panel web opcional. Si lo tenías instalado:

```bash
curl -fsSL https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/scripts/quitar-panelweb.sh | bash
```

## Baneo automático de IP que fallan el puerto 22

`install.sh` y `actualizar.sh` activan fail2ban solos (`scripts/zumo-baneo.sh`): una IP que falla la contraseña 5 veces en 10 min queda baneada del puerto SSH 22 (1 h por defecto; se cambia en el panel → Herramientas → Fail2ban). Los fallos que llegan por PDirect (puerto 80), WebSocket o BHTTP entran al sshd como 127.0.0.1, que está en `ignoreip`, así que **nunca banean a nadie**; el baneo no afecta a los puertos 80/443 ni al limitador. Si ya tenías un `jail.local`, no se pisa.

## Instalar sin GitHub ni dominio (paquete de instalación)

En el bot: **💾 Respaldo → 📦 Paquete de instalación** manda el repo completo cifrado con la contraseña del respaldo
(`zumo-paquete-AAAAMMDD.enc`, en partes `.parte1`, `.parte2` si pesa más de 40 MB). Guardalo junto al respaldo. Con una VPS limpia, desde una PC:

```bash
scp zumo-paquete-*.enc* root@IP_NUEVA:/root/
# en la VPS nueva, como root:
cat /root/zumo-paquete-*.enc* | openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 | tar xz -C /opt   # deja /opt/zumo-repo
bash /opt/zumo-repo/bot/instalar-bot.sh
```

El instalador usa esa copia cuando GitHub y el dominio no responden. Después restaurá el respaldo desde el bot.

## Varios dominios o IP por servidor

En el bot, al poner o cambiar el host de un servidor de la app se pueden escribir varios separados por coma, espacio o punto y coma
(`a.com, b.com, 1.2.3.4:443`; hasta 8; el puerto es el del primero que lo traiga). La app los prueba todos a la vez con un TCP
(sin iniciar sesión, así no cuenta como intento fallido), usa el que responde primero y, si ese falla, sigue con los otros hosts del mismo
servidor. Si el servidor rechaza el token no insiste con sus otros hosts. Hay que compilar y repartir una app nueva: las apps viejas no entienden la lista.

## Que la VPS tenga siempre lo mismo que GitHub

Los instaladores y actualizadores del dominio salen de la copia del repo de la central (`/opt/zumo-repo`). El bot la sincroniza con GitHub
**cada hora** y **cada vez que le pides un código** de instalación o actualización; también a mano en 📱 App Android → Compilar en la VPS → 🔄 Sincronizar con GitHub.
Para actualizar una VPS con panel: `zumo-actualizar` (o el código «actualizar» si todavía no está pasada al dominio).

## PDirect v2 (pruebas, para Cloudflare)

PDirect (puerto 80) queda **igual**. PDirect v2 es un servicio aparte (`pdirect2`, programa `fuentes/pdirect2.c`) que, cuando el pedido trae `Sec-WebSocket-Key`, contesta el handshake del estándar (RFC 6455): una sola respuesta `101 Switching Protocols` con `Upgrade`, `Connection` y `Sec-WebSocket-Accept` correcto. Sin esa cabecera contesta igual que PDirect. Sigue anotando la IP real para el limitador.

- Activar: panel → Protocolos → **PDirect v2 (pruebas, Cloudflare)**. Puerto por defecto 2052 (Cloudflare acepta 80, 8080, 8880, 2052, 2082, 2086, 2095). El 80 solo si PDirect está apagado.
- `zumo-actualizar` lo recompila solo si cambió el código y no lo reinicia si no hay cambios.
- Para Cloudflare: el dominio en tu zona con la nube naranja, el payload con `Upgrade: websocket` y el puerto de v2.
- Probarlo: `bash tests/prueba-pdirect2.sh` (necesita `libevent-dev`).

## Puertos extra (camuflaje)

Panel → Protocolos → **Puertos extra (camuflaje)**. Elegís el puerto de fachada (el que pone el cliente: 8080, 2052, 443…) y el puerto real al que va (80 = PDirect, o el de PDirect v2). La VPS lo redirige por dentro (iptables, regla que se reaplica al reiniciar); la IP del cliente no cambia, así que el limitador sigue contando bien. El panel muestra "Camuflado 8080→80" en los puertos activos. Para usarlo con Cloudflare el destino tiene que ser un PDirect que conteste el WebSocket estándar (v2). Prueba: `bash tests/prueba-puertos-extra.sh`.
