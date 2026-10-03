#!/bin/bash

export DEBIAN_FRONTEND=noninteractive

clear

echo -e "\e[1;38;5;87m╔══════════════════════════════════╗"
echo -e "║         INSTALANDO PANEL         ║"
echo -e "╚══════════════════════════════════╝\e[0m"
echo

echo -e "\e[1;33m[1/6]\e[0m Instalando dependencias..."
apt-get update -y >/dev/null 2>&1
apt-get install -y --no-install-recommends procps iproute2 curl ca-certificates >/dev/null 2>&1
mkdir -p /etc/zumo
touch /etc/zumo/usuarios.db
grep -qx "/bin/false" /etc/shells || echo "/bin/false" >> /etc/shells
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[2/6]\e[0m Creando activador de protocolos..."

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

echo -e "\e[1;33m[3/6]\e[0m Creando desactivador de protocolos..."

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

echo -e "\e[1;33m[4/6]\e[0m Instalando limitador de conexiones..."

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

echo -e "\e[1;33m[5/6]\e[0m Creando activador de HCR Server..."

cat > /etc/zumo/activar-hcr.sh <<'ZUMOHCRACT'
#!/bin/bash
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
DIR=/opt/hcr-server
PUERTO=8080
RAW="https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/hcr-server"

echo "[1/3] Dependencias..."
command -v curl >/dev/null 2>&1 && command -v systemd-analyze >/dev/null 2>&1 || {
apt-get update && apt-get install -y --no-install-recommends ca-certificates curl systemd util-linux
}

echo "[2/3] Descargando hcr-server..."
install -d -o root -g root -m 0755 "$DIR"
curl -fsSL "$RAW" -o "$DIR/.hcr-server.tmp" || { echo "No se pudo descargar hcr-server"; exit 1; }
chown root:root "$DIR/.hcr-server.tmp"; chmod 0755 "$DIR/.hcr-server.tmp"
"$DIR/.hcr-server.tmp" -version >/dev/null 2>&1 || { rm -f "$DIR/.hcr-server.tmp"; echo "El binario descargado no es válido para esta VPS"; exit 1; }
systemctl stop hcr-server 2>/dev/null || true
mv -f "$DIR/.hcr-server.tmp" "$DIR/hcr-server"

cat > "$DIR/install.sh" <<'ZUMO_HCR_INSTALL'
#!/usr/bin/env bash
set -euo pipefail

PATH="/usr/sbin:/usr/bin:/sbin:/bin"
LC_ALL="C"
LANG="C"
export PATH LC_ALL LANG

SERVICE_NAME="hcr-server"
SYSTEMD_DIR="/etc/systemd/system"
PORT="8080"
PORT_SET="false"
MAX_DOWNLOAD_FRAME="6144"
DOWNLOAD_POLL_TIMEOUT="8s"
TRANSPORT="auto"
TRANSPORT_SET="false"
ACTION="install"

TEMP_UNIT=""

fail() {
	echo "Error: $*" >&2
	exit 1
}

command -v readlink >/dev/null 2>&1 || fail "readlink was not found."
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname -- "${SCRIPT_PATH}")"
BINARY_PATH="${SCRIPT_DIR}/hcr-server"
TLS_CERT_PATH="${SCRIPT_DIR}/fullchain.pem"
TLS_KEY_PATH="${SCRIPT_DIR}/privkey.pem"
UNIT_SOURCE_PATH="${SCRIPT_DIR}/${SERVICE_NAME}.service"
UNIT_LINK_PATH="${SYSTEMD_DIR}/${SERVICE_NAME}.service"

usage() {
	cat <<'EOF'
Install HCR Server as a systemd service from one self-contained directory.

Usage:
  sudo ./install.sh [--port <1-65535>] [--transport <tls|plain|auto>]
  sudo ./install.sh --uninstall
  ./install.sh --help

Options:
  --port <number>     Listener port. Default: 8080
  --transport <mode>  Server transport. Default: auto
                      tls   accepts TLS only
                      plain accepts non-TLS HCR only
                      auto  accepts TLS and non-TLS HCR on the same port
  --uninstall         Stop and unlink the service without deleting this directory
  -h, --help          Show this help

Required next to install.sh:
  hcr-server          Binary for the current Linux architecture
  fullchain.pem       Required by tls and auto
  privkey.pem         Required by tls and auto

The generated hcr-server.service also stays next to this script. Only systemd
symlinks are created outside this directory. The installer does not create
binary backups or rollback files.
EOF
}

parse_args() {
	while [ "$#" -gt 0 ]; do
		case "$1" in
			--port)
				[ "$#" -ge 2 ] || fail "--port requires a value."
				PORT="$2"
				PORT_SET="true"
				shift 2
				;;
			--transport)
				[ "$#" -ge 2 ] || fail "--transport requires a value."
				TRANSPORT="$2"
				TRANSPORT_SET="true"
				shift 2
				;;
			--uninstall)
				ACTION="uninstall"
				shift
				;;
			-h|--help)
				usage
				exit 0
				;;
			*) fail "Unknown option: $1" ;;
		esac
	done
	case "${TRANSPORT}" in
		tls|plain|auto) ;;
		*) fail "--transport must be tls, plain, or auto." ;;
	esac
	case "${PORT}" in
		""|*[!0-9]*) fail "--port must be a number between 1 and 65535." ;;
	esac
	# Basis sepuluh dipaksa: nilai berawalan nol seperti 0080 akan ditafsirkan sebagai oktal.
	if [ "$((10#${PORT}))" -lt 1 ] || [ "$((10#${PORT}))" -gt 65535 ]; then
		fail "--port must be a number between 1 and 65535."
	fi
	PORT="$((10#${PORT}))"
	if [ "${ACTION}" = "uninstall" ]; then
		[ "${TRANSPORT_SET}" = "false" ] || fail "--transport cannot be combined with --uninstall."
		[ "${PORT_SET}" = "false" ] || fail "--port cannot be combined with --uninstall."
	fi
}

require_command() {
	command -v "$1" >/dev/null 2>&1 || fail "$1 was not found."
}

require_environment() {
	[ "$(id -u)" -eq 0 ] || fail "Run this installer as root."
	[ "$(uname -s)" = "Linux" ] || fail "This installer supports Linux only."
	for command_name in stat systemctl systemd-analyze flock ln mv mktemp sleep; do
		require_command "${command_name}"
	done
	if [ "${ACTION}" = "install" ] && {
		[ "${TRANSPORT}" = "tls" ] || [ "${TRANSPORT}" = "auto" ];
	}; then
		require_command openssl
	fi
	systemctl show --property=Version --value >/dev/null 2>&1 ||
		fail "The systemd system manager is not available."
	if [[ ! "${SCRIPT_DIR}" =~ ^/[-A-Za-z0-9._/@+:]+$ ]]; then
		fail "The installer directory contains unsupported characters: ${SCRIPT_DIR}"
	fi
}

acquire_install_lock() {
	exec 9<"${SYSTEMD_DIR}" || fail "The systemd unit directory could not be opened for locking."
	flock -n 9 || fail "Another HCR Server installer is already running."
}

mode_is_writable_by_others() {
	(( (8#$1 & 8#022) != 0 ))
}

validate_secure_directory() {
	local current="${SCRIPT_DIR}"
	local mode
	while :; do
		[ -d "${current}" ] && [ ! -L "${current}" ] ||
			fail "Path component must be a real directory: ${current}"
		[ "$(stat -c '%u' -- "${current}")" = "0" ] ||
			fail "Path component must be owned by root: ${current}"
		mode="$(stat -c '%a' -- "${current}")"
		mode_is_writable_by_others "${mode}" &&
			fail "Path component must not be group- or world-writable: ${current}"
		[ "${current}" = "/" ] && break
		current="$(dirname -- "${current}")"
	done
}

validate_root_file() {
	local executable="$1"
	local label="$2"
	local path="$3"
	local mode
	[ -f "${path}" ] && [ ! -L "${path}" ] ||
		fail "${label} must be a regular file: ${path}"
	[ "$(stat -c '%u' -- "${path}")" = "0" ] ||
		fail "${label} must be owned by root: ${path}"
	mode="$(stat -c '%a' -- "${path}")"
	mode_is_writable_by_others "${mode}" &&
		fail "${label} must not be group- or world-writable: ${path}"
	if [ "${executable}" = "true" ] && [ ! -x "${path}" ]; then
		fail "${label} must be executable: ${path}"
	fi
}

validate_unit_link() {
	if [ -L "${UNIT_LINK_PATH}" ]; then
		[ "$(readlink -- "${UNIT_LINK_PATH}")" = "${UNIT_SOURCE_PATH}" ] ||
			fail "A different ${SERVICE_NAME}.service symlink already exists."
	elif [ -e "${UNIT_LINK_PATH}" ]; then
		fail "A non-symlink unit already exists: ${UNIT_LINK_PATH}"
	fi
}

loaded_fragment_path() {
	systemctl show --property=FragmentPath --value "${SERVICE_NAME}.service" 2>/dev/null || true
}

validate_loaded_fragment() {
	case "$1" in
		""|"${UNIT_SOURCE_PATH}"|"${UNIT_LINK_PATH}") ;;
		*) fail "systemd loaded ${SERVICE_NAME}.service from an unexpected unit: $1" ;;
	esac
}

validate_binary_identity() {
	local path="$1"
	local output
	output="$("${path}" -version 2>/dev/null)" ||
		fail "The binary does not support -version."
	[[ "${output}" =~ ^hcr-server\ version\ [0-9]+\.[0-9]+\.[0-9]+(\ -\ Patch\ [1-9][0-9]*)?$ ]] ||
		fail "The binary returned an unexpected version string."
}

validate_tls_pair() {
	local certificate_public_key
	local private_public_key
	openssl x509 -in "${TLS_CERT_PATH}" -noout >/dev/null 2>&1 ||
		fail "The TLS certificate could not be parsed."
	certificate_public_key="$(openssl x509 -in "${TLS_CERT_PATH}" -pubkey -noout 2>/dev/null)" ||
		fail "The TLS certificate public key could not be read."
	private_public_key="$(openssl pkey -in "${TLS_KEY_PATH}" -passin pass: -pubout 2>/dev/null)" ||
		fail "The TLS private key could not be parsed without a passphrase."
	[ "${certificate_public_key}" = "${private_public_key}" ] ||
		fail "The TLS certificate and private key do not match."
}

validate_binary() {
	validate_root_file true "HCR binary" "${BINARY_PATH}"
	validate_binary_identity "${BINARY_PATH}"
}

validate_bundle() {
	local key_mode
	validate_secure_directory
	validate_root_file true "Installer" "${SCRIPT_PATH}"
	validate_binary
	if [ -e "${UNIT_SOURCE_PATH}" ] || [ -L "${UNIT_SOURCE_PATH}" ]; then
		validate_root_file false "Generated systemd unit" "${UNIT_SOURCE_PATH}"
	fi
	if [ "${TRANSPORT}" = "tls" ] || [ "${TRANSPORT}" = "auto" ]; then
		validate_root_file false "TLS certificate" "${TLS_CERT_PATH}"
		validate_root_file false "TLS private key" "${TLS_KEY_PATH}"
		key_mode="$(stat -c '%a' -- "${TLS_KEY_PATH}")"
		(( (8#${key_mode} & 8#077) == 0 )) ||
			fail "TLS private key must not be accessible by group or other users."
		validate_tls_pair
	fi
}

render_unit() {
	local tls_arguments=""
	if [ "${TRANSPORT}" = "tls" ] || [ "${TRANSPORT}" = "auto" ]; then
		tls_arguments=" --tls-cert ${TLS_CERT_PATH} --tls-key ${TLS_KEY_PATH}"
	fi
	TEMP_UNIT="$(mktemp "${SCRIPT_DIR}/.${SERVICE_NAME}.XXXXXX.service")"
	chmod 0600 "${TEMP_UNIT}"
	cat >"${TEMP_UNIT}" <<EOF
[Unit]
Descript
