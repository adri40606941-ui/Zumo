#!/bin/bash

export DEBIAN_FRONTEND=noninteractive

clear

echo -e "\e[1;38;5;87m╔══════════════════════════════════╗"
echo -e "║         INSTALANDO PANEL         ║"
echo -e "╚══════════════════════════════════╝\e[0m"
echo

echo -e "\e[1;33m[1/7]\e[0m Instalando dependencias..."
apt-get update -y >/dev/null 2>&1
apt-get install -y --no-install-recommends procps iproute2 curl ca-certificates >/dev/null 2>&1
mkdir -p /etc/zumo
touch /etc/zumo/usuarios.db
grep -qx "/bin/false" /etc/shells || echo "/bin/false" >> /etc/shells
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[2/7]\e[0m Creando activador de protocolos..."

cat > /etc/zumo/activar-protocolos.sh <<'ZUMOACT'
#!/bin/bash
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
WORK=$(mktemp -d)

echo "[1/4] Dependencias..."
apt-get update
apt-get install -y --no-install-recommends ca-certificates git cmake make gcc libc6-dev libevent-dev

echo "[2/4] Escribiendo y compilando PDirect-C..."
cat > "$WORK/pdirect.c" <<'ZUMO_PDIRECT_C'
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <ctype.h>
#include <event2/event.h>
#include <event2/buffer.h>
#include <event2/bufferevent.h>
#include <event2/listener.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#define MAX_HEADER 16384
#define SSH_HOST "127.0.0.1"

static int ssh_port;
static char allowed_ip[32];
static char allowed_name[32];

static const char RESPONSE[] =
"HTTP/1.1 101 <font color=\"yellow\"><b>ZUMO</b></font>\r\n\r\n"
"HTTP/1.1 101 Conexion Exitosa\r\n\r\n";

typedef struct {
struct bufferevent *client;
struct bufferevent *upstream;
int relaying;
int closing;
int closed;
} Conn;

static void close_conn(Conn *c)
{
if (!c || c->closed) return;
c->closed = 1;
struct bufferevent *a = c->client;
struct bufferevent *b = c->upstream;
c->client = NULL;
c->upstream = NULL;
if (a) bufferevent_free(a);
if (b) bufferevent_free(b);
free(c);
}

static void reject_conn(Conn *c, const char *status)
{
if (!c || c->closed || !c->client) return;
c->closing = 1;
bufferevent_disable(c->client, EV_READ);
if (c->upstream) {
bufferevent_free(c->upstream);
c->upstream = NULL;
}
bufferevent_write(c->client, status, strlen(status));
}

static int valid_host(char *headers)
{
char *save = NULL;
char *line = strtok_r(headers, "\r\n", &save);
while (line) {
char *colon = strchr(line, ':');
if (colon) {
*colon = '\0';
if (strcasecmp(line, "X-Real-Host") == 0) {
char *v = colon + 1;
while (*v && isspace((unsigned char)*v)) v++;
char *end = v + strlen(v);
while (end > v && isspace((unsigned char)end[-1]))
*--end = '\0';
return !strcasecmp(v, allowed_ip) ||
!strcasecmp(v, allowed_name);
}
}
line = strtok_r(NULL, "\r\n", &save);
}
return 1;
}

static void read_cb(struct bufferevent *bev, void *arg)
{
Conn *c = arg;
if (!c || c->closed) return;
struct evbuffer *in = bufferevent_get_input(bev);
if (c->relaying) {
struct bufferevent *dst =
(bev == c->client) ? c->upstream : c->client;
if (dst)
evbuffer_add_buffer(bufferevent_get_output(dst), in);
return;
}
size_t n = evbuffer_get_length(in);
if (n >= MAX_HEADER) {
reject_conn(c,
"HTTP/1.1 431 Request Header Fields Too Large\r\n"
"Connection: close\r\n\r\n");
return;
}
unsigned char *data = evbuffer_pullup(in, -1);
if (!data) return;
if (!memmem(data, n, "\r\n\r\n", 4))
return;
char *headers = malloc(n + 1);
if (!headers) { close_conn(c); return; }
memcpy(headers, data, n);
headers[n] = '\0';
int allowed = valid_host(headers);
free(headers);
evbuffer_drain(in, n);
if (!allowed) {
reject_conn(c,
"HTTP/1.1 403 Forbidden\r\n"
"Connection: close\r\n\r\n");
return;
}
bufferevent_disable(c->client, EV_READ);
struct event_base *base = bufferevent_get_base(c->client);
c->upstream = bufferevent_socket_new(
base, -1, BEV_OPT_CLOSE_ON_FREE | BEV_OPT_DEFER_CALLBACKS);
if (!c->upstream) { close_conn(c); return; }
extern void upstream_event_cb(struct bufferevent *, short, void *);
bufferevent_setcb(c->upstream, read_cb, NULL, upstream_event_cb, c);
struct timeval tv = {60, 0};
bufferevent_set_timeouts(c->upstream, &tv, NULL);
bufferevent_setwatermark(c->upstream, EV_READ, 0, 262144);
bufferevent_enable(c->upstream, EV_READ | EV_WRITE);
if (bufferevent_socket_connect_hostname(
c->upstream, NULL, AF_INET, SSH_HOST, ssh_port) < 0) {
close_conn(c);
}
}

static void write_cb(struct bufferevent *bev, void *arg)
{
Conn *c = arg;
if (!c || c->closed) return;
if (c->closing &&
evbuffer_get_length(bufferevent_get_output(bev)) == 0) {
close_conn(c);
}
}

void upstream_event_cb(struct bufferevent *bev, short events, void *arg)
{
(void)bev;
Conn *c = arg;
if (!c || c->closed) return;
if (events & BEV_EVENT_CONNECTED) {
c->relaying = 1;
bufferevent_setcb(c->client, read_cb, write_cb, NULL, c);
bufferevent_setcb(c->upstream, read_cb, write_cb, upstream_event_cb, c);
bufferevent_write(c->client, RESPONSE, sizeof(RESPONSE) - 1);
struct timeval tv = {60, 0};
bufferevent_set_timeouts(c->client, &tv, NULL);
bufferevent_set_timeouts(c->upstream, &tv, NULL);
bufferevent_setwatermark(c->client, EV_READ, 0, 262144);
bufferevent_setwatermark(c->upstream, EV_READ, 0, 262144);
bufferevent_enable(c->client, EV_READ | EV_WRITE);
bufferevent_enable(c->upstream, EV_READ | EV_WRITE);
return;
}
if (events & (BEV_EVENT_EOF | BEV_EVENT_ERROR | BEV_EVENT_TIMEOUT))
close_conn(c);
}

static void accept_cb(struct evconnlistener *listener, evutil_socket_t fd,
struct sockaddr *addr, int socklen, void *arg)
{
(void)listener; (void)addr; (void)socklen;
struct event_base *base = arg;
evutil_make_socket_nonblocking(fd);
int one = 1;
setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
Conn *c = calloc(1, sizeof(*c));
if (!c) { close(fd); return; }
c->client = bufferevent_socket_new(
base, fd, BEV_OPT_CLOSE_ON_FREE | BEV_OPT_DEFER_CALLBACKS);
if (!c->client) { close(fd); free(c); return; }
extern void client_event_cb(struct bufferevent *, short, void *);
bufferevent_setcb(c->client, read_cb, write_cb, client_event_cb, c);
struct timeval tv = {60, 0};
bufferevent_set_timeouts(c->client, &tv, NULL);
bufferevent_setwatermark(c->client, EV_READ, 0, MAX_HEADER);
bufferevent_enable(c->client, EV_READ | EV_WRITE);
}

void client_event_cb(struct bufferevent *bev, short events, void *arg)
{
(void)bev;
Conn *c = arg;
if (!c || c->closed) return;
if (events & (BEV_EVENT_EOF | BEV_EVENT_ERROR | BEV_EVENT_TIMEOUT))
close_conn(c);
}

static void listener_error_cb(struct evconnlistener *listener, void *arg)
{
(void)listener;
struct event_base *base = arg;
perror("PDirect: error del listener");
event_base_loopexit(base, NULL);
}

int main(int argc, char **argv)
{
char *end = NULL;
long port = argc == 2 ? strtol(argv[1], &end, 10) : 0;
if (argc != 2 || end == argv[1] || *end != '\0' ||
port < 1 || port > 65535) {
fprintf(stderr, "Uso: %s PUERTO_SSH (1-65535)\n", argv[0]);
return 2;
}
ssh_port = (int)port;
snprintf(allowed_ip, sizeof(allowed_ip), "%s:%d", SSH_HOST, ssh_port);
snprintf(allowed_name, sizeof(allowed_name), "localhost:%d", ssh_port);
signal(SIGPIPE, SIG_IGN);
struct event_base *base = event_base_new();
if (!base) { fprintf(stderr, "No se pudo crear event_base\n"); return 1; }
struct sockaddr_in addr;
memset(&addr, 0, sizeof(addr));
addr.sin_family = AF_INET;
addr.sin_addr.s_addr = htonl(INADDR_ANY);
addr.sin_port = htons(80);
struct evconnlistener *listener = evconnlistener_new_bind(
base, accept_cb, base,
LEV_OPT_CLOSE_ON_FREE | LEV_OPT_REUSEABLE,
1024, (struct sockaddr *)&addr, sizeof(addr));
if (!listener) {
perror("No se pudo abrir el puerto 80");
event_base_free(base);
return 1;
}
evconnlistener_set_error_cb(listener, listener_error_cb);
fprintf(stderr, "PDirect-C en 0.0.0.0:80; SSH local %s:%d\n", SSH_HOST, ssh_port);
event_base_dispatch(base);
evconnlistener_free(listener);
event_base_free(base);
return 0;
}
ZUMO_PDIRECT_C

gcc -O2 -o "$WORK/pdirect-c" "$WORK/pdirect.c" -levent_core || exit 1

echo "[3/4] Compilando BadVPN UDPGW..."
git clone --depth 1 https://github.com/ambrop72/badvpn.git "$WORK/badvpn"
cmake -S "$WORK/badvpn" -B "$WORK/badvpn/build" -DCMAKE_BUILD_TYPE=Release -DBUILD_NOTHING_BY_DEFAULT=1 -DBUILD_UDPGW=1
cmake --build "$WORK/badvpn/build" --parallel 2
[ -x "$WORK/badvpn/build/udpgw/badvpn-udpgw" ] || { echo "No se compiló UDPGW"; exit 1; }

echo "[4/4] Instalando y activando..."
systemctl stop pdirect-80 udpgw-7300 2>/dev/null || true
install -d -m 0755 /opt/badvpn
install -m 0755 "$WORK/pdirect-c" /usr/local/bin/pdirect-c
install -m 0755 "$WORK/badvpn/build/udpgw/badvpn-udpgw" /opt/badvpn/badvpn-udpgw

cat > /etc/systemd/system/pdirect-80.service <<'U1'
[Unit]
Description=ZUMO - PDirect-C (TCP 80 -> SSH local)
After=network.target
[Service]
ExecStart=/usr/local/bin/pdirect-c 22
Restart=on-failure
RestartSec=2
DynamicUser=yes
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
LimitNOFILE=65536
[Install]
WantedBy=multi-user.target
U1

cat > /etc/systemd/system/udpgw-7300.service <<'U2'
[Unit]
Description=ZUMO - BadVPN UDPGW (TCP 7300)
After=network.target
[Service]
ExecStart=/opt/badvpn/badvpn-udpgw --listen-addr 0.0.0.0:7300 --max-clients 3 --max-connections-for-client 256
SuccessExitStatus=1
Restart=always
RestartSec=2
DynamicUser=yes
NoNewPrivileges=true
LimitNOFILE=8192
[Install]
WantedBy=multi-user.target
U2

systemctl daemon-reload
systemctl enable --now pdirect-80 udpgw-7300
rm -rf "$WORK"
sleep 2
systemctl is-active pdirect-80 udpgw-7300
ZUMOACT

chmod +x /etc/zumo/activar-protocolos.sh
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[3/7]\e[0m Creando desactivador de protocolos..."

cat > /etc/zumo/desactivar-protocolos.sh <<'DESEOF'
#!/bin/bash
systemctl disable --now pdirect-80 udpgw-7300 2>/dev/null
for port in 80 7300; do
while read -r pid; do
[ -z "$pid" ] && continue
name=$(ps -o comm= -p "$pid" 2>/dev/null)
case "$name" in
pdirect-c|badvpn-udpgw) kill -9 "$pid" 2>/dev/null ;;
esac
done < <(ss -ltnpH "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u)
done
rm -f /etc/systemd/system/pdirect-80.service /etc/systemd/system/udpgw-7300.service
systemctl daemon-reload
systemctl reset-failed pdirect-80 udpgw-7300 2>/dev/null
DESEOF

chmod +x /etc/zumo/desactivar-protocolos.sh
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[4/7]\e[0m Instalando limitador de conexiones..."

cat > /etc/zumo/limitador.sh <<'LIMEOF'
#!/bin/bash
DB=/etc/zumo/usuarios.db
while true; do
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
mapfile -t pids < <(ps -u "$u" -o pid=,comm= --sort=start_time 2>/dev/null | awk '$2=="sshd"{print $1}')
n=${#pids[@]}
if [ "$n" -gt "$lim" ]; then
for ((i=0; i<n-lim; i++)); do kill -9 "${pids[$i]}" 2>/dev/null; done
fi
done < "$DB"
sleep 3
done
LIMEOF

chmod +x /etc/zumo/limitador.sh

cat > /etc/systemd/system/zumo-limit.service <<'SVCEOF'
[Unit]
Description=ZUMO limitador de conexiones
After=network.target
[Service]
ExecStart=/etc/zumo/limitador.sh
Restart=always
[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload >/dev/null 2>&1
systemctl enable --now zumo-limit >/dev/null 2>&1
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[5/7]\e[0m Creando activador de HCR Server..."

cat > /etc/zumo/activar-hcr.sh <<'ZUMOHCRACT'
#!/bin/bash
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
DIR=/opt/hcr-server
PUERTO=8880
BASE="https://raw.githubusercontent.com/adri40606941-ui/Zumo/main"

echo "[1/3] Dependencias..."
if ! command -v curl >/dev/null 2>&1 || ! command -v systemd-analyze >/dev/null 2>&1 || ! command -v flock >/dev/null 2>&1; then
apt-get update && apt-get install -y --no-install-recommends ca-certificates curl systemd util-linux || exit 1
fi

echo "[2/3] Descargando hcr-server..."
install -d -o root -g root -m 0755 "$DIR"
BIN_TMP="$DIR/.hcr-server.tmp"
INS_TMP="$DIR/.install.tmp"
rm -f "$BIN_TMP" "$INS_TMP"
curl -fsSL "$BASE/hcr-server" -o "$BIN_TMP" || { rm -f "$BIN_TMP"; echo "No se pudo descargar hcr-server"; exit 1; }
curl -fsSL "$BASE/hcr-install.sh" -o "$INS_TMP" || { rm -f "$BIN_TMP" "$INS_TMP"; echo "No se pudo descargar hcr-install.sh"; exit 1; }
chown root:root "$BIN_TMP" "$INS_TMP"
chmod 0755 "$BIN_TMP" "$INS_TMP"
"$BIN_TMP" -version >/dev/null 2>&1 || { rm -f "$BIN_TMP" "$INS_TMP"; echo "El binario descargado no es válido para esta VPS"; exit 1; }
bash -n "$INS_TMP" 2>/dev/null || { rm -f "$BIN_TMP" "$INS_TMP"; echo "hcr-install.sh está incompleto o dañado (revisá el archivo en el repo)"; exit 1; }
systemctl stop hcr-server 2>/dev/null || true
mv -f "$BIN_TMP" "$DIR/hcr-server"
mv -f "$INS_TMP" "$DIR/install.sh"

echo "[3/3] Instalando servicio en el puerto $PUERTO..."
"$DIR/install.sh" --port "$PUERTO" --transport plain || { echo "Falló el instalador de HCR"; exit 1; }
systemctl is-active --quiet hcr-server || { echo "hcr-server no quedó activo"; exit 1; }
echo "hcr-server activo en el puerto $PUERTO"
ZUMOHCRACT

chmod +x /etc/zumo/activar-hcr.sh

cat > /etc/zumo/desactivar-hcr.sh <<'DESHCREOF'
#!/bin/bash
DIR=/opt/hcr-server
if [ -x "$DIR/install.sh" ]; then
"$DIR/install.sh" --uninstall >/dev/null 2>&1 || true
fi
systemctl disable --now hcr-server 2>/dev/null
while read -r pid; do
[ -z "$pid" ] && continue
[ "$(ps -o comm= -p "$pid" 2>/dev/null)" = "hcr-server" ] && kill -9 "$pid" 2>/dev/null
done < <(ss -ltnpH "sport = :8880" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u)
systemctl daemon-reload
systemctl reset-failed hcr-server 2>/dev/null
exit 0
DESHCREOF

chmod +x /etc/zumo/desactivar-hcr.sh
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[6/7]\e[0m Creando activador de BHTTP..."

cat > /etc/zumo/activar-bhttp.sh <<'ZUMOBHTTPACT'
#!/bin/bash
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
DIR=/opt/bhttp-server
PUERTO="${1:-8080}"
INTERNO=18022
case "$PUERTO" in ''|*[!0-9]*) echo "Puerto inválido: $PUERTO"; exit 1 ;; esac
PUERTO=$((10#$PUERTO))
if [ "$PUERTO" -lt 1 ] || [ "$PUERTO" -gt 65535 ]; then echo "El puerto debe estar entre 1 y 65535"; exit 1; fi
[ "$PUERTO" -eq "$INTERNO" ] && INTERNO=18023
BASE="https://raw.githubusercontent.com/darnix0/BHTTP/main"
ZUMO="https://raw.githubusercontent.com/adri40606941-ui/Zumo/main"
VERSION="v2.4.1-btun-compat-keepalive"

case "$(uname -m)" in
x86_64|amd64) ARCH=amd64 ;;
aarch64|arm64) ARCH=arm64 ;;
*) echo "Arquitectura no soportada: $(uname -m)"; exit 1 ;;
esac
NAME="superflash-bhttp-server-${VERSION}-linux-${ARCH}"
SHIM="bhttp-shim-${ARCH}"

echo "[1/4] Dependencias..."
if ! command -v curl >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1 || ! command -v ss >/dev/null 2>&1; then
apt-get update && apt-get install -y --no-install-recommends ca-certificates curl coreutils iproute2 || exit 1
fi

echo "[2/4] Descargando y verificando BHTTP ${VERSION} (${ARCH})..."
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$BASE/$NAME" -o "$TMP/bhttp" || { echo "No se pudo descargar $NAME"; exit 1; }
curl -fsSL "$BASE/SHA256SUMS.txt" -o "$TMP/SHA256SUMS.txt" || { echo "No se pudo descargar SHA256SUMS.txt"; exit 1; }
ESPERADO=$(awk -v n="$NAME" '{f=$2; sub(/^\*/,"",f)} f==n{print $1; exit}' "$TMP/SHA256SUMS.txt")
[ -n "$ESPERADO" ] || { echo "SHA256SUMS.txt no lista $NAME"; exit 1; }
REAL=$(sha256sum "$TMP/bhttp" | awk '{print $1}')
[ "$ESPERADO" = "$REAL" ] || { echo "El SHA256 no coincide (esperado $ESPERADO, real $REAL). No se instala."; exit 1; }
chmod 0755 "$TMP/bhttp"
timeout 5 "$TMP/bhttp" -version 2>&1 | grep -qi "bhttp" || { echo "El binario descargado no es válido para esta VPS"; exit 1; }
AYUDA=$(timeout 5 "$TMP/bhttp" -h 2>&1 || true)
for f in -listen -port -backend-host -backend-port; do
echo "$AYUDA" | grep -q -- "$f" || { echo "Este binario no tiene la opción $f; no se instala"; exit 1; }
done

echo "      Adaptador para DTunnel (${SHIM})..."
curl -fsSL "$ZUMO/$SHIM" -o "$TMP/shim" || { echo "No se pudo descargar $SHIM del repositorio (subilo a la raíz del repo)"; exit 1; }
chmod 0755 "$TMP/shim"
timeout 5 "$TMP/shim" -h 2>&1 | grep -q -- "-backend" || { echo "El adaptador descargado no es válido para esta VPS"; exit 1; }

echo "[3/4] Instalando..."
systemctl stop bhttp-shim bhttp-server 2>/dev/null || true
for p in "$PUERTO" "$INTERNO"; do
if ss -ltnpH "sport = :$p" 2>/dev/null | grep -q .; then
echo "El puerto $p está ocupado por otro servicio:"
ss -ltnpH "sport = :$p"
echo "Liberalo (si es el WebSocket: Protocolos -> 2) o elegí otro puerto."
exit 1
fi
done
install -d -m 0755 "$DIR"
install -m 0755 "$TMP/bhttp" "$DIR/bhttp-server"
install -m 0755 "$TMP/shim" "$DIR/bhttp-shim"
cat > /etc/systemd/system/bhttp-server.service <<BHTTPUNIT
[Unit]
Description=ZUMO - BHTTP SuperFlash (127.0.0.1:$INTERNO -> SSH local)
After=network.target ssh.service sshd.service

[Service]
ExecStart=$DIR/bhttp-server -listen 127.0.0.1 -port $INTERNO -backend-host 127.0.0.1 -backend-port 22
Restart=on-failure
RestartSec=2
DynamicUser=yes
NoNewPrivileges=true
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
BHTTPUNIT
cat > /etc/systemd/system/bhttp-shim.service <<SHIMUNIT
[Unit]
Description=ZUMO - BHTTP adaptador DTunnel (TCP $PUERTO -> 127.0.0.1:$INTERNO)
After=bhttp-server.service
Requires=bhttp-server.service

[Service]
ExecStart=$DIR/bhttp-shim -listen 0.0.0.0:$PUERTO -backend 127.0.0.1:$INTERNO
Restart=on-failure
RestartSec=2
DynamicUser=yes
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
SHIMUNIT

echo "[4/4] Activando..."
systemctl daemon-reload
systemctl enable --now bhttp-server bhttp-shim >/dev/null 2>&1
sleep 2
for u in bhttp-server bhttp-shim; do
if ! systemctl is-active --quiet "$u"; then
echo "$u no quedó activo:"
journalctl -u "$u" -n 15 --no-pager 2>/dev/null
exit 1
fi
done
mkdir -p /etc/zumo
echo "$PUERTO" > /etc/zumo/bhttp.port
echo "$INTERNO" > /etc/zumo/bhttp.interno
echo "BHTTP activo en el puerto $PUERTO (adaptador DTunnel -> servidor -> SSH)"
ZUMOBHTTPACT

chmod +x /etc/zumo/activar-bhttp.sh

cat > /etc/zumo/desactivar-bhttp.sh <<'DESBHTTPEOF'
#!/bin/bash
PUERTO=$(cat /etc/zumo/bhttp.port 2>/dev/null)
INTERNO=$(cat /etc/zumo/bhttp.interno 2>/dev/null)
case "$PUERTO" in ''|*[!0-9]*) PUERTO=8080 ;; esac
case "$INTERNO" in ''|*[!0-9]*) INTERNO=18022 ;; esac
systemctl disable --now bhttp-shim bhttp-server 2>/dev/null
for p in "$PUERTO" "$INTERNO"; do
while read -r pid; do
[ -z "$pid" ] && continue
c=$(ps -o comm= -p "$pid" 2>/dev/null)
{ [ "$c" = "bhttp-server" ] || [ "$c" = "bhttp-shim" ]; } && kill -9 "$pid" 2>/dev/null
done < <(ss -ltnpH "sport = :$p" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u)
done
rm -f /etc/systemd/system/bhttp-server.service /etc/systemd/system/bhttp-shim.service /etc/zumo/bhttp.port /etc/zumo/bhttp.interno
systemctl daemon-reload
systemctl reset-failed bhttp-server bhttp-shim 2>/dev/null
exit 0
DESBHTTPEOF

chmod +x /etc/zumo/desactivar-bhttp.sh
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[7/7]\e[0m Instalando panel..."

PANEL_URL="https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/panel.sh"
PANEL_TMP=$(mktemp)
if curl -fsSL "$PANEL_URL" -o "$PANEL_TMP" && bash -n "$PANEL_TMP" 2>/dev/null; then
install -m 0755 "$PANEL_TMP" /usr/local/bin/zumo
rm -f "$PANEL_TMP"
else
rm -f "$PANEL_TMP"
echo -e " \e[1;31m✘ No se pudo descargar un panel.sh válido desde el repo.\e[0m"
exit 1
fi
echo -e " \e[1;32m✔ listo\e[0m"
echo

echo -e "\e[1;38;5;201m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\e[0m"
echo -e "\e[1;32m ✔ instalado correctamente.\e[0m"
echo -e "\e[1;32m Escribí \e[1;38;5;87mzumo\e[1;32m para abrir el panel.\e[0m"
echo -e "\e[1;38;5;201m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\e[0m"
