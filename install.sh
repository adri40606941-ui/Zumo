#!/bin/bash

export DEBIAN_FRONTEND=noninteractive

clear

echo -e "\e[1;38;5;87m╔══════════════════════════════════╗"
echo -e "║         INSTALANDO PANEL         ║"
echo -e "╚══════════════════════════════════╝\e[0m"
echo

echo -e "\e[1;33m[1/9]\e[0m Instalando dependencias..."
apt-get update -y >/dev/null 2>&1
apt-get install -y --no-install-recommends procps iproute2 curl ca-certificates gcc libc6-dev >/dev/null 2>&1
mkdir -p /etc/zumo
touch /etc/zumo/usuarios.db
grep -qx "/bin/false" /etc/shells || echo "/bin/false" >> /etc/shells

# Para que sshd note rápido cuando un cliente se cae (red cambiada, app cerrada de golpe)
# y no quede marcado "online" en el panel durante varios minutos, y para
# que acepte algoritmos SSH viejos que todavía usan apps de Android como
# HTTP Custom / HTTP Injector (si no, dan "Cannot negotiate, proposals
# do not match" con Debian 12). Se valida con sshd -t antes de aplicar:
# si algo no es compatible con esta versión de OpenSSH, no se toca nada.
if [ -f /etc/ssh/sshd_config ]; then
_sshd_tmp=$(mktemp)
cp /etc/ssh/sshd_config "$_sshd_tmp"
sed -i '/^ClientAliveInterval/d; /^ClientAliveCountMax/d; /^TCPKeepAlive/d; /^KexAlgorithms/d; /^Ciphers/d; /^MACs/d; /^HostKeyAlgorithms/d; /^PubkeyAcceptedAlgorithms/d' "$_sshd_tmp"
cat >> "$_sshd_tmp" <<'EOF'
ClientAliveInterval 15
ClientAliveCountMax 3
TCPKeepAlive yes

# Muchas apps de Android (HTTP Custom, HTTP Injector, etc.) usan
# librerías SSH viejas que no entienden los algoritmos "modernos" que
# trae Debian por defecto, y la conexión muere con "Cannot negotiate,
# proposals do not match". Esto agrega soporte para los viejos sin
# sacar los nuevos.
KexAlgorithms +diffie-hellman-group1-sha1,diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1
Ciphers +aes128-cbc,3des-cbc,aes256-cbc
MACs +hmac-md5,hmac-sha1
HostKeyAlgorithms +ssh-rsa
PubkeyAcceptedAlgorithms +ssh-rsa
EOF
if sshd -t -f "$_sshd_tmp" 2>/tmp/zumo-sshd-check.log; then
cp "$_sshd_tmp" /etc/ssh/sshd_config
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
else
echo -e " \e[1;31m✘ No se pudo activar la compatibilidad SSH vieja (tu versión de OpenSSH rechazó algún algoritmo), se dejó la config como estaba:\e[0m"
sed 's/^/   /' /tmp/zumo-sshd-check.log
fi
rm -f "$_sshd_tmp"
fi
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[2/9]\e[0m Creando activador de PDirect (WebSocket 80)..."

cat > /etc/zumo/activar-pdirect.sh <<'ZUMOPDIRECTACT'
#!/bin/bash
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
WORK=$(mktemp -d)

# Banner/color/modo opcionales (los pasa el panel). Si se reciben, se guardan
# en /etc/zumo/pdirect.env, que el servicio lee. Si no se pasa nada, se respeta
# el env existente (reactivar no borra el banner elegido antes).
BANNER="${1:-}"
COLOR="${2:-}"
MODO="${3:-}"
if [ -n "$BANNER" ] || [ -n "$COLOR" ] || [ -n "$MODO" ]; then
mkdir -p /etc/zumo
{
[ -n "$BANNER" ] && echo "PDIRECT_BANNER=$BANNER"
[ -n "$COLOR" ] && echo "PDIRECT_COLOR=$COLOR"
[ -n "$MODO" ] && echo "PDIRECT_MODE=$MODO"
} > /etc/zumo/pdirect.env
chmod 644 /etc/zumo/pdirect.env
fi

__oculto() {
local msg="$1"; shift
local log; log=$(mktemp)
"$@" >"$log" 2>&1 &
local pid=$!
local p=0
while kill -0 "$pid" 2>/dev/null; do
p=$(( p + (RANDOM % 4 + 1) ))
[ "$p" -gt 96 ] && p=96
printf "\r \e[1;38;5;141m%s...\e[0m \e[1;33m%3d%%\e[0m" "$msg" "$p"
sleep 0.3
done
wait "$pid"; local rc=$?
if [ "$rc" -eq 0 ]; then
printf "\r \e[1;38;5;141m%s...\e[0m \e[1;32m100%%\e[0m \e[1;32m✔\e[0m          \n" "$msg"
else
printf "\r \e[1;38;5;141m%s...\e[0m \e[1;31mFalló\e[0m          \n" "$msg"
tail -n 15 "$log" | sed 's/^/   /'
fi
rm -f "$log"
return $rc
}

_deps_pdirect() { apt-get update && apt-get install -y --no-install-recommends ca-certificates gcc libc6-dev libevent-dev; }
__oculto "[1/3] Dependencias" _deps_pdirect || exit 1

cat > "$WORK/pdirect.c" <<'ZUMO_PDIRECT_C'
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <ctype.h>
#include <event2/event.h>
#include <event2/buffer.h>
#include <event2/bufferevent.h>
#include <event2/listener.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <unistd.h>

#define MAX_HEADER 16384
#define SSH_HOST "127.0.0.1"

/* Timeouts separados: corto para recibir las cabeceras (anti-slowloris),
 * largo para el túnel ya establecido (el keepalive mantiene vivo el ocioso). */
#define HEADER_TIMEOUT 10
#define RELAY_TIMEOUT 120

/* Control de flujo: si la salida de un lado supera HIGH, se pausa la lectura
 * del otro; se reanuda cuando baja de LOW. Evita que un peer lento infle RAM. */
#define RELAY_HIGH (1 << 20)
#define RELAY_LOW (256 * 1024)

/* Límites anti-abuso para un puerto público. */
#define MAX_CONNS 2000
#define PERIP_MAX 24

static int ssh_port;
static int listen_port = 80;
static char allowed_ip[64];
static char allowed_name[64];
static const char *g_response;
static int g_conns;

static const char RESP_101[] =
"HTTP/1.1 101 <font color=\"yellow\"><b>ZUMO</b></font>\r\n\r\n"
"HTTP/1.1 101 Conexion Exitosa\r\n\r\n";
static const char RESP_200[] =
"HTTP/1.1 200 <font color=\"yellow\"><b>ZUMO</b></font>\r\nContent-Length: 0\r\n\r\n"
"HTTP/1.1 200 Conexion Exitosa\r\n\r\n";

/* ---- tabla de conexiones por IP (chaining; loop de un solo hilo) ---- */
#define IP_BUCKETS 1024
typedef struct ipnode { char ip[46]; int count; struct ipnode *next; } ipnode;
static ipnode *ip_tab[IP_BUCKETS];

static unsigned ip_hash(const char *s)
{
	unsigned h = 5381;
	for (; *s; s++) h = ((h << 5) + h) ^ (unsigned char)*s;
	return h & (IP_BUCKETS - 1);
}
static int ip_count_get(const char *ip)
{
	for (ipnode *n = ip_tab[ip_hash(ip)]; n; n = n->next)
		if (!strcmp(n->ip, ip)) return n->count;
	return 0;
}
static void ip_inc(const char *ip)
{
	unsigned b = ip_hash(ip);
	for (ipnode *n = ip_tab[b]; n; n = n->next)
		if (!strcmp(n->ip, ip)) { n->count++; return; }
	ipnode *n = calloc(1, sizeof(*n));
	if (!n) return;
	strncpy(n->ip, ip, sizeof(n->ip) - 1);
	n->count = 1;
	n->next = ip_tab[b];
	ip_tab[b] = n;
}
static void ip_dec(const char *ip)
{
	unsigned b = ip_hash(ip);
	ipnode **pp = &ip_tab[b];
	while (*pp) {
		ipnode *n = *pp;
		if (!strcmp(n->ip, ip)) {
			if (--n->count <= 0) { *pp = n->next; free(n); }
			return;
		}
		pp = &n->next;
	}
}

typedef struct {
	struct bufferevent *client;
	struct bufferevent *upstream;
	int relaying;
	int closing;
	int closed;
	int await_split;  /* 1 = vimos X-Split y esperamos el segmento partido */
	int counted;      /* 1 = esta conexión suma en los contadores */
	char ip[46];
} Conn;

static void read_cb(struct bufferevent *bev, void *arg);
static void write_cb(struct bufferevent *bev, void *arg);
void upstream_event_cb(struct bufferevent *bev, short events, void *arg);
static void start_upstream(Conn *c);

/* Activa TCP keepalive para detectar peers muertos (redes móviles). */
static void set_keepalive(evutil_socket_t fd)
{
	int on = 1;
	setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &on, sizeof(on));
#ifdef TCP_KEEPIDLE
	int idle = 30, intvl = 10, cnt = 3;
	setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE, &idle, sizeof(idle));
	setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &intvl, sizeof(intvl));
	setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, &cnt, sizeof(cnt));
#endif
}

/* Busca (sin distinguir mayúsculas) si una cabecera está presente. */
static int header_present(const char *h, const char *name)
{
	size_t nl = strlen(name);
	for (const char *p = h; *p; p++)
		if (strncasecmp(p, name, nl) == 0) return 1;
	return 0;
}

static void close_conn(Conn *c)
{
	if (!c || c->closed) return;
	c->closed = 1;
	if (c->counted) { g_conns--; ip_dec(c->ip); }
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

/* Abre la conexión al SSH local y empieza el relay con control de flujo. */
static void start_upstream(Conn *c)
{
	bufferevent_disable(c->client, EV_READ);
	struct event_base *base = bufferevent_get_base(c->client);
	c->upstream = bufferevent_socket_new(
		base, -1, BEV_OPT_CLOSE_ON_FREE | BEV_OPT_DEFER_CALLBACKS);
	if (!c->upstream) { close_conn(c); return; }
	bufferevent_setcb(c->upstream, read_cb, write_cb, upstream_event_cb, c);
	struct timeval tv = {RELAY_TIMEOUT, 0};
	bufferevent_set_timeouts(c->upstream, &tv, NULL);
	bufferevent_setwatermark(c->upstream, EV_READ, 0, 262144);
	bufferevent_enable(c->upstream, EV_READ | EV_WRITE);
	if (bufferevent_socket_connect_hostname(
		c->upstream, NULL, AF_INET, SSH_HOST, ssh_port) < 0) {
		close_conn(c);
	}
}

static void read_cb(struct bufferevent *bev, void *arg)
{
	Conn *c = arg;
	if (!c || c->closed) return;
	struct evbuffer *in = bufferevent_get_input(bev);
	if (c->relaying) {
		struct bufferevent *dst =
			(bev == c->client) ? c->upstream : c->client;
		if (!dst) return;
		evbuffer_add_buffer(bufferevent_get_output(dst), in);
		/* backpressure: si el destino se atrasa, pausar esta fuente */
		if (evbuffer_get_length(bufferevent_get_output(dst)) >= RELAY_HIGH)
			bufferevent_disable(bev, EV_READ);
		return;
	}
	/* Esperando el segmento "partido" del payload (modo X-Split): se
	 * descarta y recién ahí se conecta al backend. */
	if (c->await_split) {
		size_t sn = evbuffer_get_length(in);
		if (sn == 0) return;
		evbuffer_drain(in, sn);
		c->await_split = 0;
		start_upstream(c);
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
	unsigned char *eoh = memmem(data, n, "\r\n\r\n", 4);
	if (!eoh) return;
	size_t hlen = (size_t)(eoh - data) + 4;
	char *headers = malloc(hlen + 1);
	if (!headers) { close_conn(c); return; }
	memcpy(headers, data, hlen);
	headers[hlen] = '\0';
	int split = header_present(headers, "X-Split");
	int allowed = valid_host(headers);
	free(headers);
	if (!allowed) {
		evbuffer_drain(in, n);
		reject_conn(c,
			"HTTP/1.1 403 Forbidden\r\n"
			"Connection: close\r\n\r\n");
		return;
	}
	if (split) {
		evbuffer_drain(in, hlen);
		size_t rest = evbuffer_get_length(in);
		if (rest > 0) {
			evbuffer_drain(in, rest);
			start_upstream(c);
		} else {
			c->await_split = 1;
		}
		return;
	}
	evbuffer_drain(in, n);
	start_upstream(c);
}

static void write_cb(struct bufferevent *bev, void *arg)
{
	Conn *c = arg;
	if (!c || c->closed) return;
	if (c->relaying) {
		/* esta salida se vació: reanudar la lectura de quien la alimenta */
		if (evbuffer_get_length(bufferevent_get_output(bev)) <= RELAY_LOW) {
			struct bufferevent *src =
				(bev == c->client) ? c->upstream : c->client;
			if (src) bufferevent_enable(src, EV_READ);
		}
	}
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
		evutil_socket_t ufd = bufferevent_getfd(c->upstream);
		if (ufd >= 0) set_keepalive(ufd);
		bufferevent_setcb(c->client, read_cb, write_cb, NULL, c);
		bufferevent_setcb(c->upstream, read_cb, write_cb, upstream_event_cb, c);
		bufferevent_write(c->client, g_response, strlen(g_response));
		struct timeval tv = {RELAY_TIMEOUT, 0};
		bufferevent_set_timeouts(c->client, &tv, NULL);
		bufferevent_set_timeouts(c->upstream, &tv, NULL);
		bufferevent_setwatermark(c->client, EV_READ, 0, 262144);
		bufferevent_setwatermark(c->upstream, EV_READ, 0, 262144);
		/* el write_cb se dispara al bajar la salida de RELAY_LOW */
		bufferevent_setwatermark(c->client, EV_WRITE, RELAY_LOW, 0);
		bufferevent_setwatermark(c->upstream, EV_WRITE, RELAY_LOW, 0);
		bufferevent_enable(c->client, EV_READ | EV_WRITE);
		bufferevent_enable(c->upstream, EV_READ | EV_WRITE);
		return;
	}
	if (events & (BEV_EVENT_EOF | BEV_EVENT_ERROR | BEV_EVENT_TIMEOUT))
		close_conn(c);
}

static void client_event_cb(struct bufferevent *bev, short events, void *arg)
{
	(void)bev;
	Conn *c = arg;
	if (!c || c->closed) return;
	if (events & (BEV_EVENT_EOF | BEV_EVENT_ERROR | BEV_EVENT_TIMEOUT))
		close_conn(c);
}

static void accept_cb(struct evconnlistener *listener, evutil_socket_t fd,
		      struct sockaddr *addr, int socklen, void *arg)
{
	(void)listener;
	struct event_base *base = arg;
	char ip[46] = "?";
	getnameinfo(addr, socklen, ip, sizeof(ip), NULL, 0, NI_NUMERICHOST);

	/* límites anti-abuso */
	if (g_conns >= MAX_CONNS || ip_count_get(ip) >= PERIP_MAX) {
		close(fd);
		return;
	}

	evutil_make_socket_nonblocking(fd);
	int one = 1;
	setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
	set_keepalive(fd);

	Conn *c = calloc(1, sizeof(*c));
	if (!c) { close(fd); return; }
	c->client = bufferevent_socket_new(
		base, fd, BEV_OPT_CLOSE_ON_FREE | BEV_OPT_DEFER_CALLBACKS);
	if (!c->client) { close(fd); free(c); return; }
	strncpy(c->ip, ip, sizeof(c->ip) - 1);
	c->counted = 1;
	g_conns++;
	ip_inc(ip);

	bufferevent_setcb(c->client, read_cb, write_cb, client_event_cb, c);
	struct timeval tv = {HEADER_TIMEOUT, 0};  /* corto para las cabeceras */
	bufferevent_set_timeouts(c->client, &tv, NULL);
	bufferevent_setwatermark(c->client, EV_READ, 0, MAX_HEADER);
	bufferevent_enable(c->client, EV_READ | EV_WRITE);
}

static void listener_error_cb(struct evconnlistener *listener, void *arg)
{
	(void)listener;
	struct event_base *base = arg;
	perror("PDirect: error del listener");
	event_base_loopexit(base, NULL);
}

/* Crea un socket de escucha dual-stack (IPv6 + IPv4). Si IPv6 no está
 * disponible, cae a IPv4 puro. Devuelve el fd o -1. */
static evutil_socket_t make_listen_fd(void)
{
	evutil_socket_t fd = socket(AF_INET6, SOCK_STREAM, 0);
	if (fd >= 0) {
		int off = 0, on = 1;
		setsockopt(fd, IPPROTO_IPV6, IPV6_V6ONLY, &off, sizeof(off));
		setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));
		struct sockaddr_in6 a6;
		memset(&a6, 0, sizeof(a6));
		a6.sin6_family = AF_INET6;
		a6.sin6_addr = in6addr_any;
		a6.sin6_port = htons((unsigned short)listen_port);
		if (bind(fd, (struct sockaddr *)&a6, sizeof(a6)) == 0) {
			evutil_make_socket_nonblocking(fd);
			return fd;
		}
		close(fd);
	}
	/* Fallback IPv4 */
	fd = socket(AF_INET, SOCK_STREAM, 0);
	if (fd < 0) return -1;
	int on = 1;
	setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));
	struct sockaddr_in a4;
	memset(&a4, 0, sizeof(a4));
	a4.sin_family = AF_INET;
	a4.sin_addr.s_addr = htonl(INADDR_ANY);
	a4.sin_port = htons((unsigned short)listen_port);
	if (bind(fd, (struct sockaddr *)&a4, sizeof(a4)) != 0) {
		close(fd);
		return -1;
	}
	evutil_make_socket_nonblocking(fd);
	return fd;
}

/* Arma la respuesta HTTP (el "banner" que ve la app). Prioridad:
 *   1) PDIRECT_RESPONSE  -> respuesta cruda completa (uso avanzado)
 *   2) PDIRECT_BANNER    -> se arma el 101/200 con ese texto y color
 *   3) por defecto       -> banner ZUMO amarillo
 * El color va como <font color="..."> (así lo pintan HTTP Injector/Custom). */
static char resp_buf[1024];
static void build_response(int code)
{
	const char *raw = getenv("PDIRECT_RESPONSE");
	if (raw && *raw) { g_response = raw; return; }
	const char *banner = getenv("PDIRECT_BANNER");
	if (!banner || !*banner) {
		g_response = (code == 200) ? RESP_200 : RESP_101;
		return;
	}
	/* Sanitizar: banner sin control chars (evita inyección de cabeceras);
	 * color solo alfanumérico o '#'. */
	char b[80] = {0}, co[32] = {0};
	size_t j = 0;
	for (const char *p = banner; *p && j < sizeof(b) - 1; p++) {
		unsigned char ch = (unsigned char)*p;
		if (ch >= 32 && ch != 127) b[j++] = (char)ch;
	}
	const char *color = getenv("PDIRECT_COLOR");
	if (color) {
		size_t k = 0;
		for (const char *p = color; *p && k < sizeof(co) - 1; p++)
			if (isalnum((unsigned char)*p) || *p == '#') co[k++] = *p;
	}
	if (!co[0]) snprintf(co, sizeof(co), "yellow");
	if (code == 200)
		snprintf(resp_buf, sizeof(resp_buf),
			"HTTP/1.1 200 <font color=\"%s\"><b>%s</b></font>\r\n"
			"Content-Length: 0\r\n\r\n"
			"HTTP/1.1 200 Conexion Exitosa\r\n\r\n", co, b);
	else
		snprintf(resp_buf, sizeof(resp_buf),
			"HTTP/1.1 101 <font color=\"%s\"><b>%s</b></font>\r\n\r\n"
			"HTTP/1.1 101 Conexion Exitosa\r\n\r\n", co, b);
	g_response = resp_buf;
}

static int parse_port(const char *s, int def)
{
	if (!s || !*s) return def;
	char *end = NULL;
	long v = strtol(s, &end, 10);
	if (end == s || *end != '\0' || v < 1 || v > 65535) return -1;
	return (int)v;
}

int main(int argc, char **argv)
{
	/* Uso (todo opcional, compatible con la llamada previa 'pdirect-c 22'):
	 *   pdirect-c [SSH_PORT=22] [LISTEN_PORT=80] [MODO=101|200]
	 * El banner también se puede sobreescribir con PDIRECT_RESPONSE. */
	ssh_port = parse_port(argc > 1 ? argv[1] : NULL, 22);
	listen_port = parse_port(argc > 2 ? argv[2] : NULL, 80);
	if (ssh_port < 0 || listen_port < 0) {
		fprintf(stderr, "Uso: %s [SSH_PORT] [LISTEN_PORT] [101|200]\n", argv[0]);
		return 2;
	}
	const char *modo = argc > 3 ? argv[3] : getenv("PDIRECT_MODE");
	if (!modo || !*modo) modo = "101";
	build_response(strcmp(modo, "200") == 0 ? 200 : 101);

	snprintf(allowed_ip, sizeof(allowed_ip), "%s:%d", SSH_HOST, ssh_port);
	snprintf(allowed_name, sizeof(allowed_name), "localhost:%d", ssh_port);
	signal(SIGPIPE, SIG_IGN);

	struct event_base *base = event_base_new();
	if (!base) { fprintf(stderr, "No se pudo crear event_base\n"); return 1; }

	evutil_socket_t lfd = make_listen_fd();
	if (lfd < 0) {
		perror("No se pudo abrir el puerto de escucha");
		event_base_free(base);
		return 1;
	}
	struct evconnlistener *listener = evconnlistener_new(
		base, accept_cb, base,
		LEV_OPT_CLOSE_ON_FREE | LEV_OPT_REUSEABLE, 1024, lfd);
	if (!listener) {
		perror("No se pudo crear el listener");
		close(lfd);
		event_base_free(base);
		return 1;
	}
	evconnlistener_set_error_cb(listener, listener_error_cb);
	fprintf(stderr, "PDirect-C en 0.0.0.0/[::]:%d; SSH local %s:%d\n",
		listen_port, SSH_HOST, ssh_port);
	event_base_dispatch(base);
	evconnlistener_free(listener);
	event_base_free(base);
	return 0;
}
ZUMO_PDIRECT_C

_pdirect_build() { gcc -O2 -o "$WORK/pdirect-c" "$WORK/pdirect.c" -levent_core; }
__oculto "[2/3] Compilando PDirect-C" _pdirect_build || exit 1

echo "[3/3] Instalando y activando..."
systemctl stop pdirect-80 2>/dev/null || true
install -m 0755 "$WORK/pdirect-c" /usr/local/bin/pdirect-c

cat > /etc/systemd/system/pdirect-80.service <<'U1'
[Unit]
Description=ZUMO - PDirect-C (TCP 80 -> SSH local)
After=network.target
[Service]
EnvironmentFile=-/etc/zumo/pdirect.env
ExecStart=/usr/local/bin/pdirect-c 22
Restart=on-failure
RestartSec=2
DynamicUser=yes
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectControlGroups=true
ProtectKernelTunables=true
ProtectKernelModules=true
RestrictAddressFamilies=AF_INET AF_INET6
RestrictNamespaces=true
MemoryDenyWriteExecute=true
LockPersonality=true
SystemCallArchitectures=native
LimitCORE=0
LimitNOFILE=65536
TasksMax=1024
MemoryMax=256M
[Install]
WantedBy=multi-user.target
U1

systemctl daemon-reload
systemctl enable --now pdirect-80
rm -rf "$WORK"
sleep 1
systemctl is-active --quiet pdirect-80
ZUMOPDIRECTACT

chmod +x /etc/zumo/activar-pdirect.sh
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[3/9]\e[0m Creando desactivador de PDirect..."

cat > /etc/zumo/desactivar-pdirect.sh <<'DESPDEOF'
#!/bin/bash
systemctl disable --now pdirect-80 2>/dev/null
while read -r pid; do
[ -z "$pid" ] && continue
[ "$(ps -o comm= -p "$pid" 2>/dev/null)" = "pdirect-c" ] && kill -9 "$pid" 2>/dev/null
done < <(ss -ltnpH "sport = :80" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u)
rm -f /etc/systemd/system/pdirect-80.service
systemctl daemon-reload
systemctl reset-failed pdirect-80 2>/dev/null
DESPDEOF

chmod +x /etc/zumo/desactivar-pdirect.sh
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[4/9]\e[0m Creando activador de BadVPN..."

cat > /etc/zumo/activar-badvpn.sh <<'ZUMOBADVPNACT'
#!/bin/bash
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
WORK=$(mktemp -d)

__oculto() {
local msg="$1"; shift
local log; log=$(mktemp)
"$@" >"$log" 2>&1 &
local pid=$!
local p=0
while kill -0 "$pid" 2>/dev/null; do
p=$(( p + (RANDOM % 4 + 1) ))
[ "$p" -gt 96 ] && p=96
printf "\r \e[1;38;5;141m%s...\e[0m \e[1;33m%3d%%\e[0m" "$msg" "$p"
sleep 0.3
done
wait "$pid"; local rc=$?
if [ "$rc" -eq 0 ]; then
printf "\r \e[1;38;5;141m%s...\e[0m \e[1;32m100%%\e[0m \e[1;32m✔\e[0m          \n" "$msg"
else
printf "\r \e[1;38;5;141m%s...\e[0m \e[1;31mFalló\e[0m          \n" "$msg"
tail -n 15 "$log" | sed 's/^/   /'
fi
rm -f "$log"
return $rc
}

_deps_badvpn() { apt-get update && apt-get install -y --no-install-recommends ca-certificates git cmake make gcc libc6-dev; }
__oculto "[1/3] Dependencias" _deps_badvpn || exit 1

_badvpn_build() {
git clone --depth 1 https://github.com/ambrop72/badvpn.git "$WORK/badvpn" &&
cmake -S "$WORK/badvpn" -B "$WORK/badvpn/build" -DCMAKE_BUILD_TYPE=Release -DBUILD_NOTHING_BY_DEFAULT=1 -DBUILD_UDPGW=1 &&
cmake --build "$WORK/badvpn/build" --parallel 2
}
__oculto "[2/3] Compilando BadVPN UDPGW" _badvpn_build || exit 1
[ -x "$WORK/badvpn/build/udpgw/badvpn-udpgw" ] || { echo " No se compiló UDPGW"; exit 1; }

echo "[3/3] Instalando y activando..."
systemctl stop udpgw-7300 2>/dev/null || true
install -d -m 0755 /opt/badvpn
install -m 0755 "$WORK/badvpn/build/udpgw/badvpn-udpgw" /opt/badvpn/badvpn-udpgw

cat > /etc/systemd/system/udpgw-7300.service <<'U2'
[Unit]
Description=ZUMO - BadVPN UDPGW (TCP 7300)
After=network.target
[Service]
ExecStart=/opt/badvpn/badvpn-udpgw --listen-addr 0.0.0.0:7300 --max-clients 200 --max-connections-for-client 256
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
systemctl enable --now udpgw-7300
rm -rf "$WORK"
sleep 1
systemctl is-active --quiet udpgw-7300
ZUMOBADVPNACT

chmod +x /etc/zumo/activar-badvpn.sh
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[5/9]\e[0m Creando desactivador de BadVPN..."

cat > /etc/zumo/desactivar-badvpn.sh <<'DESBVEOF'
#!/bin/bash
systemctl disable --now udpgw-7300 2>/dev/null
while read -r pid; do
[ -z "$pid" ] && continue
[ "$(ps -o comm= -p "$pid" 2>/dev/null)" = "badvpn-udpgw" ] && kill -9 "$pid" 2>/dev/null
done < <(ss -ltnpH "sport = :7300" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u)
rm -f /etc/systemd/system/udpgw-7300.service
systemctl daemon-reload
systemctl reset-failed udpgw-7300 2>/dev/null
DESBVEOF

chmod +x /etc/zumo/desactivar-badvpn.sh
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[6/9]\e[0m Compilando limitador de conexiones..."

LIMWORK=$(mktemp -d)
cat > "$LIMWORK/zumo-limit.c" <<'ZUMO_LIMIT_C'
/* zumo-limit: limitador de conexiones SSH por usuario.
 * Lee /etc/zumo/usuarios.db (usuario:limite:vencimiento) y, cada 3s,
 * escanea /proc directamente (sin invocar "ps") para contar procesos
 * sshd por usuario y matar los mas nuevos que excedan el limite. */
#define _GNU_SOURCE
#include <dirent.h>
#include <pwd.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define DB_PATH "/etc/zumo/usuarios.db"
#define MAX_USERS 4096
#define MAX_PROCS 16384

typedef struct { char name[64]; int limit; uid_t uid; } UserLim;
typedef struct { pid_t pid; uid_t uid; unsigned long long start; } ProcInfo;

static int load_users(UserLim *users) {
    FILE *f = fopen(DB_PATH, "r");
    if (!f) return 0;
    char line[256];
    int n = 0;
    while (fgets(line, sizeof(line), f) && n < MAX_USERS) {
        char *c1 = strchr(line, ':');
        if (!c1) continue;
        *c1 = 0;
        char *c2 = strchr(c1 + 1, ':');
        if (!c2) continue;
        *c2 = 0;
        if (line[0] == '\0' || strlen(line) >= sizeof(users[0].name)) continue;
        int limit = atoi(c1 + 1);
        if (limit < 1) continue;
        struct passwd *pw = getpwnam(line);
        if (!pw) continue;
        strncpy(users[n].name, line, sizeof(users[n].name) - 1);
        users[n].name[sizeof(users[n].name) - 1] = '\0';
        users[n].limit = limit;
        users[n].uid = pw->pw_uid;
        n++;
    }
    fclose(f);
    return n;
}

static unsigned long long starttime_of(pid_t pid) {
    char path[64], buf[1024];
    snprintf(path, sizeof(path), "/proc/%d/stat", pid);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    if (!fgets(buf, sizeof(buf), f)) { fclose(f); return 0; }
    fclose(f);
    char *p = strrchr(buf, ')');
    if (!p) return 0;
    p += 2; /* salta ") " -> campo 3 (state) */
    int skip = 19; /* de campo 3 a campo 22 (starttime) */
    while (skip-- > 0 && p) { p = strchr(p, ' '); if (p) p++; }
    return p ? strtoull(p, NULL, 10) : 0;
}

static int cmp_start(const void *a, const void *b) {
    unsigned long long sa = ((const ProcInfo *)a)->start;
    unsigned long long sb = ((const ProcInfo *)b)->start;
    return (sa > sb) - (sa < sb);
}

static int scan_sshd(ProcInfo *procs) {
    int n = 0;
    DIR *d = opendir("/proc");
    if (!d) return 0;
    struct dirent *de;
    while ((de = readdir(d)) != NULL && n < MAX_PROCS) {
        if (de->d_type != DT_DIR) continue;
        pid_t pid = atoi(de->d_name);
        if (pid <= 0) continue;
        char path[64], l[256];
        snprintf(path, sizeof(path), "/proc/%d/status", pid);
        FILE *sf = fopen(path, "r");
        if (!sf) continue;
        int is_sshd = 0;
        long uid = -1;
        while (fgets(l, sizeof(l), sf)) {
            if (!strncmp(l, "Name:", 5)) {
                if (strstr(l, "sshd")) is_sshd = 1;
            } else if (!strncmp(l, "Uid:", 4)) {
                sscanf(l + 4, "%ld", &uid);
                break;
            }
        }
        fclose(sf);
        if (!is_sshd || uid < 0) continue;
        procs[n].pid = pid;
        procs[n].uid = (uid_t)uid;
        procs[n].start = starttime_of(pid);
        n++;
    }
    closedir(d);
    return n;
}

int main(void) {
    for (;;) {
        UserLim users[MAX_USERS];
        int nusers = load_users(users);
        if (nusers > 0) {
            static ProcInfo procs[MAX_PROCS];
            int nprocs = scan_sshd(procs);
            for (int i = 0; i < nusers; i++) {
                ProcInfo mine[MAX_PROCS];
                int nm = 0;
                for (int j = 0; j < nprocs; j++)
                    if (procs[j].uid == users[i].uid && nm < MAX_PROCS) mine[nm++] = procs[j];
                if (nm > users[i].limit) {
                    qsort(mine, nm, sizeof(ProcInfo), cmp_start);
                    for (int k = 0; k < nm - users[i].limit; k++) kill(mine[k].pid, SIGKILL);
                }
            }
        }
        sleep(3);
    }
    return 0;
}
ZUMO_LIMIT_C

if gcc -O2 -o "$LIMWORK/zumo-limit" "$LIMWORK/zumo-limit.c" 2>"$LIMWORK/err.log"; then
install -m 0755 "$LIMWORK/zumo-limit" /usr/local/bin/zumo-limit
rm -f /etc/zumo/limitador.sh
else
echo -e " \e[1;31m✘ No se pudo compilar el limitador:\e[0m"
sed 's/^/   /' "$LIMWORK/err.log"
rm -rf "$LIMWORK"
exit 1
fi
rm -rf "$LIMWORK"

cat > /etc/systemd/system/zumo-limit.service <<'SVCEOF'
[Unit]
Description=ZUMO limitador de conexiones
After=network.target
[Service]
ExecStart=/usr/local/bin/zumo-limit
Restart=always
[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload >/dev/null 2>&1
systemctl enable --now zumo-limit >/dev/null 2>&1
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[7/9]\e[0m Creando activador de HCR Server..."

cat > /etc/zumo/activar-hcr.sh <<'ZUMOHCRACT'
#!/bin/bash
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
DIR=/opt/hcr-server
PUERTO="${1:-8880}"
case "$PUERTO" in ''|*[!0-9]*) echo "Puerto inválido: $PUERTO"; exit 1 ;; esac
PUERTO=$((10#$PUERTO))
if [ "$PUERTO" -lt 1 ] || [ "$PUERTO" -gt 65535 ]; then echo "El puerto debe estar entre 1 y 65535"; exit 1; fi
BASE="https://raw.githubusercontent.com/adri40606941-ui/Zumo/main"

__oculto() {
local msg="$1"; shift
local log; log=$(mktemp)
"$@" >"$log" 2>&1 &
local pid=$!
local p=0
while kill -0 "$pid" 2>/dev/null; do
p=$(( p + (RANDOM % 4 + 1) ))
[ "$p" -gt 96 ] && p=96
printf "\r \e[1;38;5;141m%s...\e[0m \e[1;33m%3d%%\e[0m" "$msg" "$p"
sleep 0.3
done
wait "$pid"; local rc=$?
if [ "$rc" -eq 0 ]; then
printf "\r \e[1;38;5;141m%s...\e[0m \e[1;32m100%%\e[0m \e[1;32m✔\e[0m          \n" "$msg"
else
printf "\r \e[1;38;5;141m%s...\e[0m \e[1;31mFalló\e[0m          \n" "$msg"
tail -n 15 "$log" | sed 's/^/   /'
fi
rm -f "$log"
return $rc
}

if ! command -v curl >/dev/null 2>&1 || ! command -v systemd-analyze >/dev/null 2>&1 || ! command -v flock >/dev/null 2>&1; then
_deps_hcr() { apt-get update && apt-get install -y --no-install-recommends ca-certificates curl systemd util-linux; }
__oculto "[1/3] Dependencias" _deps_hcr || exit 1
else
echo -e " \e[1;38;5;141m[1/3] Dependencias...\e[0m \e[1;32m✔\e[0m"
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

_hcr_install() { "$DIR/install.sh" --port "$PUERTO" --transport plain; }
__oculto "[3/3] Instalando servicio en el puerto $PUERTO" _hcr_install || { echo "Falló el instalador de HCR"; exit 1; }
systemctl is-active --quiet hcr-server || { echo "hcr-server no quedó activo"; exit 1; }
echo "$PUERTO" > /etc/zumo/hcr.port
echo "hcr-server activo en el puerto $PUERTO"
ZUMOHCRACT

chmod +x /etc/zumo/activar-hcr.sh

cat > /etc/zumo/desactivar-hcr.sh <<'DESHCREOF'
#!/bin/bash
DIR=/opt/hcr-server
PUERTO=$(cat /etc/zumo/hcr.port 2>/dev/null)
[[ "$PUERTO" =~ ^[0-9]+$ ]] || PUERTO=8880
if [ -x "$DIR/install.sh" ]; then
"$DIR/install.sh" --uninstall >/dev/null 2>&1 || true
fi
systemctl disable --now hcr-server 2>/dev/null
while read -r pid; do
[ -z "$pid" ] && continue
[ "$(ps -o comm= -p "$pid" 2>/dev/null)" = "hcr-server" ] && kill -9 "$pid" 2>/dev/null
done < <(ss -ltnpH "sport = :$PUERTO" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u)
rm -f /etc/zumo/hcr.port
systemctl daemon-reload
systemctl reset-failed hcr-server 2>/dev/null
exit 0
DESHCREOF

chmod +x /etc/zumo/desactivar-hcr.sh
echo -e " \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[8/9]\e[0m Creando activador de BHTTP..."

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
ZUMO="https://raw.githubusercontent.com/adri40606941-ui/Zumo/main"

case "$(uname -m)" in
x86_64|amd64) ARCH=amd64 ;;
aarch64|arm64) ARCH=arm64 ;;
*) echo "Arquitectura no soportada: $(uname -m)"; exit 1 ;;
esac
NAME="bhttp-server-${ARCH}"
SHIM="bhttp-shim-${ARCH}"

__oculto() {
local msg="$1"; shift
local log; log=$(mktemp)
"$@" >"$log" 2>&1 &
local pid=$!
local p=0
while kill -0 "$pid" 2>/dev/null; do
p=$(( p + (RANDOM % 4 + 1) ))
[ "$p" -gt 96 ] && p=96
printf "\r \e[1;38;5;141m%s...\e[0m \e[1;33m%3d%%\e[0m" "$msg" "$p"
sleep 0.3
done
wait "$pid"; local rc=$?
if [ "$rc" -eq 0 ]; then
printf "\r \e[1;38;5;141m%s...\e[0m \e[1;32m100%%\e[0m \e[1;32m✔\e[0m          \n" "$msg"
else
printf "\r \e[1;38;5;141m%s...\e[0m \e[1;31mFalló\e[0m          \n" "$msg"
tail -n 15 "$log" | sed 's/^/   /'
fi
rm -f "$log"
return $rc
}

if ! command -v curl >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1 || ! command -v ss >/dev/null 2>&1; then
_deps_bhttp() { apt-get update && apt-get install -y --no-install-recommends ca-certificates curl coreutils iproute2; }
__oculto "[1/4] Dependencias" _deps_bhttp || exit 1
else
echo -e " \e[1;38;5;141m[1/4] Dependencias...\e[0m \e[1;32m✔\e[0m"
fi

echo "[2/4] Descargando y verificando BHTTP (${ARCH})..."
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$ZUMO/$NAME" -o "$TMP/bhttp" || { echo "No se pudo descargar $NAME (subilo a la raíz del repo)"; exit 1; }
curl -fsSL "$ZUMO/bhttp-server.sha256" -o "$TMP/SHA256SUMS.txt" || { echo "No se pudo descargar bhttp-server.sha256"; exit 1; }
ESPERADO=$(awk -v n="$NAME" '{f=$2; sub(/^\*/,"",f)} f==n{print $1; exit}' "$TMP/SHA256SUMS.txt")
[ -n "$ESPERADO" ] || { echo "bhttp-server.sha256 no lista $NAME"; exit 1; }
REAL=$(sha256sum "$TMP/bhttp" | awk '{print $1}')
[ "$ESPERADO" = "$REAL" ] || { echo "El SHA256 no coincide (esperado $ESPERADO, real $REAL). No se instala."; exit 1; }
chmod 0755 "$TMP/bhttp"
timeout 5 "$TMP/bhttp" -version 2>&1 | grep -qi "bhttp" || { echo "El binario descargado no es válido para esta VPS"; exit 1; }
AYUDA=$(timeout 5 "$TMP/bhttp" -h 2>&1 || true)
for f in -listen -port -backend-host -backend-port; do
echo "$AYUDA" | grep -q -- "$f" || { echo "Este binario no tiene la opción $f; no se instala"; exit 1; }
done

echo "      Adaptador BHTTP (${SHIM})..."
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
Description=ZUMO B - BHTTP (127.0.0.1:$INTERNO -> SSH local)
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
Description=ZUMO B - BHTTP adaptador (TCP $PUERTO -> 127.0.0.1:$INTERNO)
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
echo "BHTTP activo en el puerto $PUERTO (ZUMO B -> servidor -> SSH)"
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

echo -e "\e[1;33m[9/9]\e[0m Instalando panel..."

# Librería compartida de operaciones sobre el DB (lock + escritura atómica),
# usada tanto por el panel de terminal como por el panel web.
LIB_URL="https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/zumo-lib.sh"
LIB_TMP=$(mktemp)
if curl -fsSL "$LIB_URL" -o "$LIB_TMP" && bash -n "$LIB_TMP" 2>/dev/null; then
install -m 0644 "$LIB_TMP" /etc/zumo/zumo-lib.sh
rm -f "$LIB_TMP"
else
rm -f "$LIB_TMP"
echo -e " \e[1;31m✘ No se pudo descargar zumo-lib.sh desde el repo.\e[0m"
exit 1
fi

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
echo
