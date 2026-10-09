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
#include <sys/stat.h>
#include <unistd.h>

#define MAX_HEADER 16384
#define SSH_HOST "127.0.0.1"

/* Directorio donde se anota "puerto interno -> IP real del cliente", para que
 * el limitador pueda contar por IP real aunque sshd vea todo como 127.0.0.1. */
#define PMAP_DIR "/run/zumo/pmap"

static void pmap_write(int port, const char *ip)
{
	if (port <= 0) return;
	char p[64];
	snprintf(p, sizeof(p), PMAP_DIR "/%d", port);
	FILE *f = fopen(p, "w");
	if (f) { fputs(ip, f); fputc('\n', f); fclose(f); }
}

static void pmap_del(int port)
{
	if (port <= 0) return;
	char p[64];
	snprintf(p, sizeof(p), PMAP_DIR "/%d", port);
	unlink(p);
}

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
static int g_ws_ok = 1;   /* 0 si hay PDIRECT_RESPONSE o modo 200: no se arma el handshake */

static const char RESP_101[] =
"HTTP/1.1 101 <font color=\"yellow\"><b>ZUMO</b></font>\r\n\r\n"
"HTTP/1.1 101 Conexion Exitosa\r\n\r\n";
static const char RESP_200[] =
"HTTP/1.1 200 <font color=\"yellow\"><b>ZUMO</b></font>\r\nContent-Length: 0\r\n\r\n"
"HTTP/1.1 200 Conexion Exitosa\r\n\r\n";

/* ===== PDirect v2: handshake WebSocket RFC 6455 =====
 * Cloudflare (y cualquier proxy estricto) valida que el origen conteste
 * "101 Switching Protocols" con Upgrade, Connection y Sec-WebSocket-Accept.
 * Accept = base64(SHA1(Sec-WebSocket-Key + GUID)). Solo se usa cuando el pedido
 * trae Sec-WebSocket-Key; sin esa cabecera se contesta igual que PDirect v1. */
#define WS_GUID "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
/* ZUMO_WS_BEGIN */
typedef struct { unsigned h[5]; unsigned long long len; unsigned char buf[64]; size_t n; } sha1_ctx;
#define ROL(x, k) (((x) << (k)) | ((x) >> (32 - (k))))
static void sha1_block(sha1_ctx *c, const unsigned char *p)
{
	unsigned w[80], a, b, cc, d, e, t;
	for (int i = 0; i < 16; i++)
		w[i] = ((unsigned)p[4*i] << 24) | ((unsigned)p[4*i+1] << 16) |
		       ((unsigned)p[4*i+2] << 8) | p[4*i+3];
	for (int i = 16; i < 80; i++) w[i] = ROL(w[i-3] ^ w[i-8] ^ w[i-14] ^ w[i-16], 1);
	a = c->h[0]; b = c->h[1]; cc = c->h[2]; d = c->h[3]; e = c->h[4];
	for (int i = 0; i < 80; i++) {
		unsigned f, k;
		if (i < 20)      { f = (b & cc) | (~b & d);          k = 0x5A827999; }
		else if (i < 40) { f = b ^ cc ^ d;                   k = 0x6ED9EBA1; }
		else if (i < 60) { f = (b & cc) | (b & d) | (cc & d); k = 0x8F1BBCDC; }
		else             { f = b ^ cc ^ d;                   k = 0xCA62C1D6; }
		t = ROL(a, 5) + f + e + k + w[i];
		e = d; d = cc; cc = ROL(b, 30); b = a; a = t;
	}
	c->h[0] += a; c->h[1] += b; c->h[2] += cc; c->h[3] += d; c->h[4] += e;
}
static void sha1_init(sha1_ctx *c)
{
	c->h[0] = 0x67452301; c->h[1] = 0xEFCDAB89; c->h[2] = 0x98BADCFE;
	c->h[3] = 0x10325476; c->h[4] = 0xC3D2E1F0; c->len = 0; c->n = 0;
}
static void sha1_add(sha1_ctx *c, const void *d, size_t l)
{
	const unsigned char *p = d;
	c->len += l;
	while (l--) { c->buf[c->n++] = *p++; if (c->n == 64) { sha1_block(c, c->buf); c->n = 0; } }
}
static void sha1_end(sha1_ctx *c, unsigned char out[20])
{
	unsigned long long bits = c->len * 8;
	unsigned char pad = 0x80, z = 0;
	sha1_add(c, &pad, 1);
	while (c->n != 56) sha1_add(c, &z, 1);
	for (int i = 7; i >= 0; i--) { unsigned char b = (unsigned char)(bits >> (8 * i)); sha1_add(c, &b, 1); }
	for (int i = 0; i < 5; i++) {
		out[4*i] = (unsigned char)(c->h[i] >> 24); out[4*i+1] = (unsigned char)(c->h[i] >> 16);
		out[4*i+2] = (unsigned char)(c->h[i] >> 8); out[4*i+3] = (unsigned char)c->h[i];
	}
}
/* out debe tener al menos 29 bytes */
static void ws_accept(const char *key, char *out)
{
	static const char T[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
	sha1_ctx c; unsigned char d[20];
	sha1_init(&c);
	sha1_add(&c, key, strlen(key));
	sha1_add(&c, WS_GUID, strlen(WS_GUID));
	sha1_end(&c, d);
	int o = 0;
	for (int i = 0; i < 20; i += 3) {
		unsigned v = (unsigned)d[i] << 16;
		if (i + 1 < 20) v |= (unsigned)d[i+1] << 8;
		if (i + 2 < 20) v |= d[i+2];
		out[o++] = T[(v >> 18) & 63];
		out[o++] = T[(v >> 12) & 63];
		out[o++] = (i + 1 < 20) ? T[(v >> 6) & 63] : '=';
		out[o++] = (i + 2 < 20) ? T[v & 63] : '=';
	}
	out[o] = '\0';
}
/* ZUMO_WS_END */

/* Saca el valor de Sec-WebSocket-Key de las cabeceras (vacío si no hay). */
static void ws_get_key(const char *h, char *out, size_t max)
{
	static const char N[] = "Sec-WebSocket-Key:";
	out[0] = '\0';
	for (const char *p = h; *p; p++) {
		if ((p == h || p[-1] == '\n') && strncasecmp(p, N, sizeof(N) - 1) == 0) {
			p += sizeof(N) - 1;
			while (*p == ' ' || *p == '\t') p++;
			size_t k = 0;
			while (*p && *p != '\r' && *p != '\n' && k < max - 1) {
				if (isalnum((unsigned char)*p) || *p == '+' || *p == '/' || *p == '=')
					out[k++] = *p;
				p++;
			}
			out[k] = '\0';
			return;
		}
	}
}

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
	int uport;        /* puerto local de la conexión al SSH (lo ve sshd como peer) */
	char ip[46];
	char wskey[64];   /* Sec-WebSocket-Key del pedido (vacío si no vino) */
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
	pmap_del(c->uport);
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
	ws_get_key(headers, c->wskey, sizeof(c->wskey));
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
	/* OJO: antes era "evbuffer_drain(in, n)" (todo el buffer). Si el cliente ya
	 * mandó más de un pedido señuelo sin usar X-Split (payloads con varios GET/
	 * COPY seguidos, como el de la app Zumo), y esos bytes llegaron juntos en la
	 * misma lectura que el primer "\r\n\r\n", acá se descartaban en silencio en
	 * vez de pasarlos al relay. Hay que sacar solo el primer pedido (hlen); lo
	 * que venga después (otro señuelo, o ya el protocolo SSH real) se queda en
	 * el buffer y lo relay normal lo manda al SSH en cuanto arranca. */
	evbuffer_drain(in, hlen);
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
		/* Puerto local de esta conexión al SSH = lo que sshd ve como peer.
		 * Lo anotamos con la IP real del cliente para el limitador por IP. */
		if (ufd >= 0) {
			struct sockaddr_in la;
			socklen_t ll = sizeof(la);
			if (getsockname(ufd, (struct sockaddr *)&la, &ll) == 0) {
				c->uport = ntohs(la.sin_port);
				pmap_write(c->uport, c->ip);
			}
		}
		bufferevent_setcb(c->client, read_cb, write_cb, NULL, c);
		bufferevent_setcb(c->upstream, read_cb, write_cb, upstream_event_cb, c);
		if (c->wskey[0] && g_ws_ok) {
			char acc[40], r[256];
			ws_accept(c->wskey, acc);
			int rl = snprintf(r, sizeof(r),
				"HTTP/1.1 101 Switching Protocols\r\n"
				"Upgrade: websocket\r\n"
				"Connection: Upgrade\r\n"
				"Sec-WebSocket-Accept: %s\r\n\r\n", acc);
			bufferevent_write(c->client, r, (size_t)rl);
		} else
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
	/* Normalizar IPv4 mapeado en IPv6 ("::ffff:1.2.3.4" -> "1.2.3.4"). */
	if (strncmp(ip, "::ffff:", 7) == 0 && strchr(ip + 7, '.'))
		memmove(ip, ip + 7, strlen(ip + 7) + 1);

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
	if (raw && *raw) { g_response = raw; g_ws_ok = 0; return; }
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
	if (strcmp(modo, "200") == 0) g_ws_ok = 0;

	snprintf(allowed_ip, sizeof(allowed_ip), "%s:%d", SSH_HOST, ssh_port);
	snprintf(allowed_name, sizeof(allowed_name), "localhost:%d", ssh_port);
	signal(SIGPIPE, SIG_IGN);

	/* Directorio del mapa puerto->IP (lo lee el limitador). */
	mkdir("/run/zumo", 0755);
	mkdir(PMAP_DIR, 0755);

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
	fprintf(stderr, "PDirect-C v2 (WebSocket RFC 6455) en 0.0.0.0/[::]:%d; SSH local %s:%d\n",
		listen_port, SSH_HOST, ssh_port);
	event_base_dispatch(base);
	evconnlistener_free(listener);
	event_base_free(base);
	return 0;
}

