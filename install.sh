#!/bin/bash
export DEBIAN_FRONTEND=noninteractive
clear
echo -e "\e[1;38;5;87m═══ INSTALANDO PANEL ZUMO ═══\e[0m"

echo -e "\e[1;33m[1/6]\e[0m Dependencias..."
apt-get update -y >/dev/null 2>&1
apt-get install -y --no-install-recommends procps iproute2 >/dev/null 2>&1
mkdir -p /etc/zumo
touch /etc/zumo/usuarios.db
echo "0" > /etc/zumo/cpu.stat
grep -qx "/bin/false" /etc/shells || echo "/bin/false" >> /etc/shells
echo -e "      \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[2/6]\e[0m Activador de protocolos..."
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
echo -e "      \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[3/6]\e[0m Desactivador de protocolos..."
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
echo -e "      \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[4/6]\e[0m Limitador..."
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
ExecStart=/bin/bash /etc/zumo/limitador.sh
Restart=always
[Install]
WantedBy=multi-user.target
SVCEOF
systemctl daemon-reload >/dev/null 2>&1
systemctl enable zumo-limit >/dev/null 2>&1
systemctl restart zumo-limit >/dev/null 2>&1
echo -e "      \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[5/6]\e[0m Medidor de CPU (cada 10 seg)..."
cat > /etc/zumo/cpu.sh <<'CPUEOF'
#!/bin/bash
read_cpu() { read a b c d rest < /proc/stat; echo "$((a+b+c+d)) $d"; }
while true; do
  r1=$(read_cpu); t1=${r1% *}; i1=${r1#* }
  sleep 1
  r2=$(read_cpu); t2=${r2% *}; i2=${r2#* }
  dt=$((t2-t1)); di=$((i2-i1))
  cpu=0; [ "$dt" -gt 0 ] && cpu=$(( (dt-di)*100/dt ))
  echo "$cpu" > /etc/zumo/cpu.stat
  sleep 10
done
CPUEOF
chmod +x /etc/zumo/cpu.sh
cat > /etc/systemd/system/zumo-cpu.service <<'SVCEOF'
[Unit]
Description=ZUMO medidor de CPU
After=network.target
[Service]
ExecStart=/bin/bash /etc/zumo/cpu.sh
Restart=always
[Install]
WantedBy=multi-user.target
SVCEOF
systemctl daemon-reload >/dev/null 2>&1
systemctl enable zumo-cpu >/dev/null 2>&1
systemctl restart zumo-cpu >/dev/null 2>&1
echo -e "      \e[1;32m✔ listo\e[0m"

echo -e "\e[1;33m[6/6]\e[0m Panel..."
cat > /usr/local/bin/zumo <<'PANELEOF'
#!/bin/bash
DB=/etc/zumo/usuarios.db
N='\e[0m'
L='\e[38;5;240m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\e[0m'

stats() {
  read -r _ mt mu _ <<< "$(free -m | awk '/^Mem:/{print $1,$2,$3}')"
  local mfreep=$(( (mt-mu)*100/mt ))
  local cpu=$(cat /etc/zumo/cpu.stat 2>/dev/null); [[ "$cpu" =~ ^[0-9]+$ ]] || cpu=0
  local cpufree=$(( 100-cpu ))
  local creadas=$(grep -c ':' "$DB" 2>/dev/null)
  local online=$(ps -eo user:32,comm 2>/dev/null | awk '$2=="sshd" && $1!="root" && $1!="sshd"{print $1}' | sort -u | wc -l)
  echo -e "  \e[1;38;5;117mRAM:\e[0m ${mu}/${mt}MB (\e[1;32m${mfreep}% libre\e[0m)   \e[1;38;5;117mCPU:\e[0m ${cpu}% (\e[1;32m${cpufree}% libre\e[0m)"
  echo -e "  \e[1;38;5;117mCuentas:\e[0m ${creadas}   \e[1;38;5;117mEn línea:\e[0m \e[1;33m${online}\e[0m"
}

banner() {
  clear
  echo
  echo -e "  \e[1;38;5;87m═══  Panel Zumo ▶️  ═══${N}"
  echo -e "  $L"
  stats
  echo -e "  $L"
}

pausa() { echo; read -rp "  Enter para volver..." _; }
op() { echo -e "  \e[1;38;5;201m[$1]\e[0m \e[1;38;5;87m$2${N}"; }
msg_ok() { echo -e "  \e[1;32m✔ $1${N}"; }
msg_err() { echo -e "  \e[1;31m✘ $1${N}"; }

dias() {
  local d=$(( ( $(date -d "$1" +%s) - $(date -d "$(date +%F)" +%s) ) / 86400 ))
  if [ "$d" -lt 0 ]; then echo "vencido"; elif [ "$d" -eq 1 ]; then echo "vence 1 día"; else echo "vence $d días"; fi
}

elegir_usuario() {
  mapfile -t USERS < <(cut -d: -f1 "$DB" | sed '/^$/d')
  if [ ${#USERS[@]} -eq 0 ]; then msg_err "No hay usuarios registrados"; return 1; fi
  for i in "${!USERS[@]}"; do echo -e "  \e[1;38;5;201m[$((i+1))]\e[0m ${USERS[$i]}"; done
  echo; read -rp "  Número de usuario: " n
  if ! [[ "$n" =~ ^[0-9]+$ ]] || [ "$n" -lt 1 ] || [ "$n" -gt ${#USERS[@]} ]; then msg_err "Opción inválida"; return 1; fi
  SEL="${USERS[$((n-1))]}"
}

crear_usuario() {
  banner; echo -e "  \e[1;38;5;87mCREAR USUARIO${N}\n"
  read -rp "  Usuario: " u
  [[ "$u" =~ ^[a-z_][a-z0-9_-]*$ ]] || { msg_err "Nombre inválido"; pausa; return; }
  id "$u" &>/dev/null && { msg_err "El usuario ya existe"; pausa; return; }
  read -rp "  Contraseña: " p
  [ -z "$p" ] && { msg_err "Contraseña vacía"; pausa; return; }
  read -rp "  Días de duración: " d
  [[ "$d" =~ ^[0-9]+$ ]] || { msg_err "Días inválidos"; pausa; return; }
  read -rp "  Límite de conexiones [1]: " lim; lim=${lim:-1}
  [[ "$lim" =~ ^[0-9]+$ ]] || { msg_err "Límite inválido"; pausa; return; }
  exp=$(date -d "+$d days" +%F)
  useradd -M -s /bin/false -e "$exp" "$u" && echo "$u:$p" | chpasswd
  echo "$u:$lim:$exp" >> "$DB"
  echo; echo -e "  $L"
  msg_ok "Usuario creado"
  echo -e "  Usuario:    \e[1;38;5;87m$u${N}"
  echo -e "  Contraseña: \e[1;38;5;87m$p${N}"
  echo -e "  Duración:   \e[1;38;5;87m$(dias "$exp")${N}"
  echo -e "  Límite:     \e[1;38;5;87m$lim conexión(es)${N}"
  echo -e "  $L"; pausa
}

eliminar_usuario() {
  banner; echo -e "  \e[1;38;5;87mELIMINAR USUARIO${N}\n"
  elegir_usuario || { pausa; return; }
  pkill -9 -u "$SEL" 2>/dev/null
  userdel "$SEL" 2>/dev/null
  sed -i "/^$SEL:/d" "$DB"
  msg_ok "Usuario $SEL eliminado"; pausa
}

vencidos() {
  banner; echo -e "  \e[1;38;5;87mUSUARIOS VENCIDOS${N}\n"
  if [ ! -s "$DB" ]; then msg_err "No hay usuarios"; pausa; return; fi
  hoy=$(date -d "$(date +%F)" +%s)
  VENC=()
  while IFS=: read -r u lim exp; do
    [ -z "$u" ] && continue
    e=$(date -d "$exp" +%s 2>/dev/null) || continue
    [ "$e" -lt "$hoy" ] && VENC+=("$u|$exp")
  done < "$DB"
  if [ ${#VENC[@]} -eq 0 ]; then msg_ok "No hay usuarios vencidos"; pausa; return; fi
  printf "  \e[1;38;5;213m%-16s %s${N}\n" "USUARIO" "VENCIÓ"
  for item in "${VENC[@]}"; do
    printf "  \e[1;31m%-16s %s${N}\n" "${item%%|*}" "${item#*|}"
  done
  echo; echo -e "  $L"
  op 1 "Borrar usuarios vencidos"
  op 0 "Volver"
  echo; read -rp "  Opción: " o
  case $o in
    1) read -rp "  ¿Borrar estos ${#VENC[@]} usuario(s)? [s/N]: " c
       [[ "$c" =~ ^[sS]$ ]] || { msg_err "Cancelado"; pausa; return; }
       for item in "${VENC[@]}"; do
         u="${item%%|*}"
         pkill -9 -u "$u" 2>/dev/null
         userdel "$u" 2>/dev/null
         sed -i "/^$u:/d" "$DB"
       done
       msg_ok "${#VENC[@]} usuario(s) vencido(s) eliminado(s)"; pausa ;;
    *) return ;;
  esac
}

cambiar_limite() {
  banner; echo -e "  \e[1;38;5;87mCAMBIAR LÍMITE DE CONEXIONES${N}\n"
  elegir_usuario || { pausa; return; }
  read -rp "  Nuevo límite para $SEL: " lim
  [[ "$lim" =~ ^[0-9]+$ ]] && [ "$lim" -ge 1 ] || { msg_err "Límite inválido"; pausa; return; }
  awk -F: -v u="$SEL" -v l="$lim" 'BEGIN{OFS=":"} $1==u{$2=l} {print}' "$DB" > "$DB.tmp" && mv "$DB.tmp" "$DB"
  msg_ok "Límite de $SEL ahora es $lim"; pausa
}

listar_usuarios() {
  banner; echo -e "  \e[1;38;5;87mUSUARIOS REGISTRADOS${N}\n"
  if [ ! -s "$DB" ]; then msg_err "No hay usuarios"; pausa; return; fi
  printf "  \e[1;38;5;213m%-14s %-8s %-10s %s${N}\n" "USUARIO" "LÍMITE" "ONLINE" "VENCIMIENTO"
  while IFS=: read -r u lim exp; do
    [ -z "$u" ] && continue
    on=$(ps -u "$u" -o comm= 2>/dev/null | grep -c '^sshd$')
    printf "  %-14s %-8s %-10s %s\n" "$u" "$lim" "$on" "$(dias "$exp")"
  done < "$DB"
  pausa
}

menu_usuario() {
  while true; do
    banner; echo -e "  \e[1;38;5;87mUSUARIO${N}\n"
    op 1 "Crear usuario"
    op 2 "Eliminar usuario"
    op 3 "Cambiar límite de conexiones"
    op 4 "Ver usuarios"
    op 5 "Usuarios vencidos"
    op 0 "Volver"
    echo -e "\n  $L"; read -rp "  Opción: " o
    case $o in
      1) crear_usuario ;; 2) eliminar_usuario ;; 3) cambiar_limite ;; 4) listar_usuarios ;; 5) vencidos ;;
      0) return ;; *) msg_err "Opción inválida"; sleep 1 ;;
    esac
  done
}

menu_protocolos() {
  while true; do
    banner
    if systemctl is-active --quiet pdirect-80; then
      echo -e "  \e[1;32m● PDirect WebSocket: activo (80 → SSH)${N}"
    else
      echo -e "  \e[1;31m● PDirect WebSocket: inactivo${N}"
    fi
    if systemctl is-active --quiet udpgw-7300; then
      echo -e "  \e[1;32m● BadVPN UDPGW:      activo (7300)${N}\n"
    else
      echo -e "  \e[1;31m● BadVPN UDPGW:      inactivo${N}\n"
    fi
    op 1 "Activar WebSocket (80 + 7300)"
    op 2 "Desactivar WebSocket (libera 80 y 7300)"
    op 0 "Volver"
    echo -e "\n  $L"; read -rp "  Opción: " o
    case $o in
      1) echo -e "  \e[1;38;5;87mCompilando e instalando, aguardá...${N}"
         if bash /etc/zumo/activar-protocolos.sh; then
           msg_ok "WebSocket (80 → SSH) y BadVPN (7300) activos"
         else
           msg_err "Falló; revisá 'journalctl -u pdirect-80 -u udpgw-7300'"
         fi; pausa ;;
      2) echo -e "  \e[1;38;5;87mLiberando puertos 80 y 7300...${N}"
         bash /etc/zumo/desactivar-protocolos.sh
         msg_ok "WebSocket y BadVPN desactivados; puertos 80 y 7300 liberados"; pausa ;;
      0) return ;; *) msg_err "Opción inválida"; sleep 1 ;;
    esac
  done
}

while true; do
  banner
  op 1 "Usuario"
  op 2 "Protocolos"
    op 0 "Salir"
  echo -e "\n  $L"
  read -t 10 -rp "  Opción: " o || continue
  case $o in
    1) menu_usuario ;; 2) menu_protocolos ;;
    0) clear; exit 0 ;;
    "") continue ;;
    *) msg_err "Opción inválida"; sleep 1 ;;
  esac
done
PANELEOF
chmod +x /usr/local/bin/zumo
echo -e "      \e[1;32m✔ listo\e[0m"

echo
echo -e "\e[1;32m✔ Panel instalado correctamente.\e[0m"
echo -e "\e[1;32mEscribí \e[1;38;5;87mzumo\e[1;32m para abrir el panel.\e[0m"
