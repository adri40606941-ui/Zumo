/* zumo-limit: limitador de sesiones por usuario.
 *
 * Qué hace (cada INTERVAL segundos, 3 por defecto):
 *   1. Cuenta las sesiones SSH abiertas de cada usuario de usuarios.db. Una
 *      sesión = un proceso sshd que pertenece a ese usuario. No depende de la
 *      IP, así que cuenta igual por PDirect, BHTTP, HCR o conexión directa, y
 *      por IPv4 o IPv6.
 *   2. Si un usuario tiene más sesiones que su límite, corta las que sobran.
 *      Por defecto corta la más vieja y deja la nueva (KICK=oldest): si un
 *      cliente pierde la señal y reconecta, entra enseguida y la sesión caída
 *      (que el servidor tarda ~30 s en notar) es la que se corta. Con
 *      KICK=newest se conserva la más vieja y se corta la nueva.
 *   3. Corta todas las sesiones de los usuarios vencidos. Un usuario vence el
 *      día de su fecha a las 21:00 (hora de la VPS; se cambia con EXPIRE_HOUR).
 *   4. Borra los usuarios temporales cuyo tiempo ya pasó (por si el timer de
 *      systemd se perdió con un reinicio).
 *
 * Configuración opcional en /etc/zumo/limit.conf (se relee en cada vuelta):
 *   INTERVAL=3        segundos entre revisiones (1 a 60)
 *   GRACE=0           segundos que una sesión extra puede vivir antes de cortarla
 *   KICK=oldest       oldest = corta la vieja; newest = corta la nueva
 *   EXPIRE_HOUR=21    hora (0 a 23) del día de vencimiento en que se corta
 *   TEMP_CLEANUP=1    1 = borrar temporales vencidos, 0 = no
 *
 * Opciones de línea de comandos:
 *   --once      hace una sola revisión y termina
 *   --dry-run   solo informa lo que cortaría, no corta nada
 *
 * Aviso: pdirect anota la IP real en /run/zumo/pmap/<puerto>; acá solo se usa
 * para mostrarla en el registro y en /run/zumo/online.db. No interviene en la
 * decisión de cortar. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dirent.h>
#include <pwd.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef DB_PATH
#define DB_PATH "/etc/zumo/usuarios.db"
#endif
#ifndef CONF_PATH
#define CONF_PATH "/etc/zumo/limit.conf"
#endif
#ifndef TEMP_DB_PATH
#define TEMP_DB_PATH "/etc/zumo/temporales.db"
#endif
#ifndef TEMP_SCRIPT
#define TEMP_SCRIPT "/etc/zumo/borrar-temporal.sh"
#endif
#ifndef RUN_DIR
#define RUN_DIR "/run/zumo"
#endif
#define PMAP_DIR RUN_DIR "/pmap"

#define MAX_USERS 4096
#define MAX_SESS 16384
#define MAX_SOCK22 16384

typedef struct {
    int interval;      /* segundos entre revisiones */
    int grace;         /* segundos de espera antes de cortar una sesión extra */
    int kick_newest;   /* 1 = corta la más nueva, 0 = corta la más vieja */
    int temp_cleanup;  /* 1 = borrar temporales vencidos */
    int expire_hour;   /* hora (0-23) del día de vencimiento en que se corta */
} Conf;

typedef struct {
    char name[64];
    int limit;
    uid_t uid;
    char exp[11];      /* AAAA-MM-DD o vacío si no se pudo leer */
} UserLim;

/* socket aceptado por sshd (puerto local 22) y su peer */
typedef struct {
    unsigned long inode;
    char rem_ip[46];
    int rem_port;
} Sock22;

/* una sesión SSH de un usuario */
typedef struct {
    int ui;                        /* índice en users[] */
    pid_t pid;
    unsigned long long start;      /* inicio del proceso, en ticks desde el arranque */
    char ip[46];                   /* IP real si se pudo resolver, "" si no */
} Sess;

static int g_dry_run = 0;

static UserLim g_users[MAX_USERS];
static Sock22 g_socks[MAX_SOCK22];
static Sess g_sess[MAX_SESS];

/* ---------------------------------------------------------------- config */

static void trim(char *s) {
    size_t n = strlen(s);
    while (n > 0 && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ' || s[n - 1] == '\t'))
        s[--n] = '\0';
    size_t lead = strspn(s, " \t");
    if (lead) memmove(s, s + lead, strlen(s + lead) + 1);
}

static void load_conf(Conf *c) {
    c->interval = 3;
    c->grace = 0;
    c->kick_newest = 0;
    c->temp_cleanup = 1;
    c->expire_hour = 21;
    FILE *f = fopen(CONF_PATH, "r");
    if (!f) return;
    char line[160];
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#') continue;
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        char *k = line, *v = eq + 1;
        trim(k);
        trim(v);
        if (!strcmp(k, "INTERVAL")) {
            int n = atoi(v);
            if (n >= 1 && n <= 60) c->interval = n;
        } else if (!strcmp(k, "GRACE")) {
            int n = atoi(v);
            if (n >= 0 && n <= 3600) c->grace = n;
        } else if (!strcmp(k, "KICK")) {
            if (!strcmp(v, "oldest")) c->kick_newest = 0;
            else if (!strcmp(v, "newest")) c->kick_newest = 1;
        } else if (!strcmp(k, "EXPIRE_HOUR")) {
            int n = atoi(v);
            if (n >= 0 && n <= 23) c->expire_hour = n;
        } else if (!strcmp(k, "TEMP_CLEANUP")) {
            c->temp_cleanup = (atoi(v) != 0);
        }
    }
    fclose(f);
}

/* ----------------------------------------------------------------- users */

/* "AAAA-MM-DD" válido (solo formato, para comparar como texto). */
static int valid_date(const char *s) {
    if (strlen(s) != 10) return 0;
    for (int i = 0; i < 10; i++) {
        if (i == 4 || i == 7) { if (s[i] != '-') return 0; }
        else if (s[i] < '0' || s[i] > '9') return 0;
    }
    return 1;
}

/* Lee usuario:limite:vencimiento. Devuelve cuántos usuarios cargó. */
static int load_users(UserLim *users) {
    FILE *f = fopen(DB_PATH, "r");
    if (!f) return 0;
    char line[256];
    int n = 0;
    while (fgets(line, sizeof(line), f) && n < MAX_USERS) {
        char *c1 = strchr(line, ':');
        if (!c1) continue;
        *c1 = '\0';
        char *c2 = strchr(c1 + 1, ':');
        if (!c2) continue;
        *c2 = '\0';
        char *expstr = c2 + 1;
        trim(expstr);
        if (line[0] == '\0' || strlen(line) >= sizeof(users[0].name)) continue;
        int limit = atoi(c1 + 1);
        if (limit < 1) continue;
        struct passwd *pw = getpwnam(line);
        if (!pw) continue;
        strncpy(users[n].name, line, sizeof(users[n].name) - 1);
        users[n].name[sizeof(users[n].name) - 1] = '\0';
        users[n].limit = limit;
        users[n].uid = pw->pw_uid;
        if (valid_date(expstr)) strcpy(users[n].exp, expstr);
        else users[n].exp[0] = '\0';
        n++;
    }
    fclose(f);
    return n;
}

/* --------------------------------------------- IP real (solo informativo) */

/* "0100007F" (little-endian de /proc/net/tcp) -> "127.0.0.1" */
static void hex4_to_ip(const char *hex, char *out, size_t outsz) {
    unsigned b[4];
    if (sscanf(hex, "%2x%2x%2x%2x", &b[0], &b[1], &b[2], &b[3]) == 4)
        snprintf(out, outsz, "%u.%u.%u.%u", b[3], b[2], b[1], b[0]);
    else
        snprintf(out, outsz, "0.0.0.0");
}

/* 32 hex de /proc/net/tcp6 (4 palabras de 32 bits en orden del host) -> texto */
static void hex16_to_ip(const char *hex, char *out, size_t outsz) {
    struct in6_addr a;
    for (int i = 0; i < 4; i++) {
        char w[9];
        memcpy(w, hex + i * 8, 8);
        w[8] = '\0';
        unsigned long v = strtoul(w, NULL, 16);
        uint32_t u = (uint32_t)v;
        memcpy(&a.s6_addr[i * 4], &u, 4);
    }
    char tmp[INET6_ADDRSTRLEN];
    if (!inet_ntop(AF_INET6, &a, tmp, sizeof(tmp))) { snprintf(out, outsz, "?"); return; }
    if (strncmp(tmp, "::ffff:", 7) == 0 && strchr(tmp + 7, '.'))
        snprintf(out, outsz, "%s", tmp + 7);
    else
        snprintf(out, outsz, "%s", tmp);
}

/* Agrega a socks[] los ESTABLISHED con puerto local 22 de /proc/net/tcp o tcp6. */
static int scan_file22(const char *path, int v6, Sock22 *socks, int n) {
    FILE *f = fopen(path, "r");
    if (!f) return n;
    char line[512];
    if (!fgets(line, sizeof(line), f)) { fclose(f); return n; }
    while (fgets(line, sizeof(line), f) && n < MAX_SOCK22) {
        char lhex[40], rhex[40], st[8];
        unsigned lport = 0, rport = 0;
        unsigned long inode = 0;
        if (sscanf(line, "%*d: %39[0-9A-Fa-f]:%x %39[0-9A-Fa-f]:%x %7s %*x:%*x %*x:%*x %*x %*d %*d %lu",
                   lhex, &lport, rhex, &rport, st, &inode) != 6)
            continue;
        if (lport != 22) continue;
        if (strcmp(st, "01") != 0) continue;
        socks[n].inode = inode;
        if (v6) hex16_to_ip(rhex, socks[n].rem_ip, sizeof(socks[n].rem_ip));
        else hex4_to_ip(rhex, socks[n].rem_ip, sizeof(socks[n].rem_ip));
        socks[n].rem_port = (int)rport;
        n++;
    }
    fclose(f);
    return n;
}

static int scan_sock22(Sock22 *socks) {
    int n = scan_file22("/proc/net/tcp", 0, socks, 0);
    n = scan_file22("/proc/net/tcp6", 1, socks, n);
    return n;
}

static int pmap_lookup(int port, char *out, size_t outsz) {
    char path[96];
    snprintf(path, sizeof(path), PMAP_DIR "/%d", port);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    if (!fgets(out, (int)outsz, f)) { fclose(f); return 0; }
    fclose(f);
    out[strcspn(out, "\r\n")] = '\0';
    return out[0] != '\0';
}

/* IP real del cliente de una sesión (por el socket que sshd aceptó). */
static int sshd_real_ip(pid_t pid, const Sock22 *socks, int nsocks, char *out, size_t outsz) {
    char dir[64];
    snprintf(dir, sizeof(dir), "/proc/%d/fd", pid);
    DIR *d = opendir(dir);
    if (!d) return 0;
    struct dirent *de;
    int found = 0;
    while (!found && (de = readdir(d)) != NULL) {
        char link[320], target[128];
        snprintf(link, sizeof(link), "%s/%s", dir, de->d_name);
        ssize_t r = readlink(link, target, sizeof(target) - 1);
        if (r <= 0) continue;
        target[r] = '\0';
        if (strncmp(target, "socket:[", 8) != 0) continue;
        unsigned long inode = strtoul(target + 8, NULL, 10);
        for (int i = 0; i < nsocks; i++) {
            if (socks[i].inode != inode) continue;
            if (strcmp(socks[i].rem_ip, "127.0.0.1") == 0) {
                /* túnel local: la IP real, si existe, la anotó pdirect */
                if (!pmap_lookup(socks[i].rem_port, out, outsz)) out[0] = '\0';
            } else {
                snprintf(out, outsz, "%s", socks[i].rem_ip);
            }
            found = 1;
            break;
        }
    }
    closedir(d);
    return found && out[0] != '\0';
}

/* -------------------------------------------------------------- procesos */

/* Inicio del proceso en ticks (campo 22 de /proc/PID/stat). */
static int proc_start(pid_t pid, unsigned long long *start) {
    char path[64], buf[1024];
    snprintf(path, sizeof(path), "/proc/%d/stat", pid);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = '\0';
    char *p = strrchr(buf, ')');   /* el nombre puede traer espacios y paréntesis */
    if (!p) return 0;
    p++;
    int field = 3;                 /* el primer campo después de ')' es el 3 (estado) */
    char *save = NULL;
    for (char *tok = strtok_r(p, " ", &save); tok; tok = strtok_r(NULL, " ", &save), field++) {
        if (field == 22) {
            *start = strtoull(tok, NULL, 10);
            return 1;
        }
    }
    return 0;
}

static double uptime_secs(void) {
    double up = 0;
    FILE *f = fopen("/proc/uptime", "r");
    if (f) {
        if (fscanf(f, "%lf", &up) != 1) up = 0;
        fclose(f);
    }
    return up;
}

/* Junta las sesiones sshd de los usuarios del db. */
static int scan_sessions(Sess *sess, const UserLim *users, int nusers) {
    int n = 0;
    DIR *d = opendir("/proc");
    if (!d) return 0;
    struct dirent *de;
    while ((de = readdir(d)) != NULL && n < MAX_SESS) {
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
            if (!strncmp(l, "Name:", 5)) { if (strstr(l, "sshd")) is_sshd = 1; }
            else if (!strncmp(l, "Uid:", 4)) { sscanf(l + 4, "%ld", &uid); break; }
        }
        fclose(sf);
        if (!is_sshd || uid <= 0) continue;
        int ui = -1;
        for (int i = 0; i < nusers; i++)
            if (users[i].uid == (uid_t)uid) { ui = i; break; }
        if (ui < 0) continue;
        unsigned long long st = 0;
        if (!proc_start(pid, &st)) continue;
        sess[n].ui = ui;
        sess[n].pid = pid;
        sess[n].start = st;
        sess[n].ip[0] = '\0';
        n++;
    }
    closedir(d);
    return n;
}

/* Agrupa por usuario; dentro de cada grupo, de la más vieja a la más nueva. */
static int cmp_sess(const void *a, const void *b) {
    const Sess *x = a, *y = b;
    if (x->ui != y->ui) return x->ui < y->ui ? -1 : 1;
    if (x->start != y->start) return x->start < y->start ? -1 : 1;
    if (x->pid != y->pid) return x->pid < y->pid ? -1 : 1;
    return 0;
}

static void cut(const Sess *s, const UserLim *u, const char *motivo, int total) {
    fprintf(stderr, "zumo-limit: %susuario=%s pid=%d ip=%s motivo=%s sesiones=%d limite=%d\n",
            g_dry_run ? "[dry-run] " : "", u->name, (int)s->pid,
            s->ip[0] ? s->ip : "?", motivo, total, u->limit);
    if (!g_dry_run) kill(s->pid, SIGKILL);
}

/* ------------------------------------------------------------ temporales */

static int valid_login(const char *s) {
    size_t n = strlen(s);
    if (n < 1 || n > 32) return 0;
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'))) return 0;
    }
    return 1;
}

/* Si pasó el tiempo de un temporal (usuario:epoch), llama al borrador del panel. */
static void cleanup_temps(time_t now) {
    if (access(TEMP_SCRIPT, X_OK) != 0) return;
    FILE *f = fopen(TEMP_DB_PATH, "r");
    if (!f) return;
    char line[128];
    char victims[16][40];
    int nv = 0;
    while (fgets(line, sizeof(line), f) && nv < 16) {
        char *c = strchr(line, ':');
        if (!c) continue;
        *c = '\0';
        long long ep = atoll(c + 1);
        if (ep <= 0 || (time_t)ep > now) continue;
        if (!valid_login(line)) continue;
        strcpy(victims[nv++], line);
    }
    fclose(f);
    for (int i = 0; i < nv; i++) {
        fprintf(stderr, "zumo-limit: %stemporal vencido, se borra: %s\n",
                g_dry_run ? "[dry-run] " : "", victims[i]);
        if (g_dry_run) continue;
        pid_t p = fork();
        if (p == 0) {
            execl(TEMP_SCRIPT, TEMP_SCRIPT, victims[i], (char *)NULL);
            _exit(127);
        }
        if (p > 0) {
            int st;
            waitpid(p, &st, 0);
        }
    }
}

/* ---------------------------------------------------------------- export */

/* "usuario ip1 ip2 ..." con las IPs reales conectadas, para que lo lea el panel. */
static void export_online(const UserLim *users, int nusers, const Sess *sess, int ns) {
    char tmp[] = RUN_DIR "/online.db.tmp";
    FILE *of = fopen(tmp, "w");
    if (!of) return;
    for (int i = 0; i < nusers; i++) {
        char seen[64][46];
        int nseen = 0;
        for (int j = 0; j < ns && nseen < 64; j++) {
            if (sess[j].ui != i || !sess[j].ip[0]) continue;
            int dup = 0;
            for (int k = 0; k < nseen; k++)
                if (!strcmp(seen[k], sess[j].ip)) { dup = 1; break; }
            if (dup) continue;
            snprintf(seen[nseen], sizeof(seen[0]), "%s", sess[j].ip);
            nseen++;
        }
        if (nseen == 0) continue;
        fprintf(of, "%s", users[i].name);
        for (int k = 0; k < nseen; k++) fprintf(of, " %s", seen[k]);
        fputc('\n', of);
    }
    fclose(of);
    rename(tmp, RUN_DIR "/online.db");
}

/* ------------------------------------------------------------------ vuelta */

static void run_cycle(const Conf *cf) {
    int nusers = load_users(g_users);
    if (nusers > 0) {
        int ns = scan_sessions(g_sess, g_users, nusers);
        qsort(g_sess, (size_t)ns, sizeof(Sess), cmp_sess);

        int nsocks = scan_sock22(g_socks);
        for (int i = 0; i < ns; i++)
            sshd_real_ip(g_sess[i].pid, g_socks, nsocks, g_sess[i].ip, sizeof(g_sess[i].ip));

        char hoy[11];
        time_t now = time(NULL);
        struct tm tmv;
        localtime_r(&now, &tmv);
        strftime(hoy, sizeof(hoy), "%Y-%m-%d", &tmv);
        double up = uptime_secs();
        long hz = sysconf(_SC_CLK_TCK);
        if (hz <= 0) hz = 100;

        int i = 0;
        while (i < ns) {
            int j = i;
            while (j < ns && g_sess[j].ui == g_sess[i].ui) j++;
            const UserLim *u = &g_users[g_sess[i].ui];
            int n = j - i;

            int cmp_exp = u->exp[0] ? strcmp(u->exp, hoy) : 1;
            /* vence el día indicado a la hora EXPIRE_HOUR (21:00 por defecto) */
            if (u->exp[0] && (cmp_exp < 0 || (cmp_exp == 0 && tmv.tm_hour >= cf->expire_hour))) {
                for (int k = i; k < j; k++) cut(&g_sess[k], u, "vencido", n);
            } else if (n > u->limit) {
                /* la sesión que disparó el exceso es la más nueva del grupo */
                double age_newest = up - (double)g_sess[j - 1].start / (double)hz;
                if (cf->grace <= 0 || age_newest >= cf->grace) {
                    int from, to;
                    if (cf->kick_newest) { from = i + u->limit; to = j; }
                    else { from = i; to = j - u->limit; }
                    for (int k = from; k < to; k++) cut(&g_sess[k], u, "excede-limite", n);
                }
            }
            i = j;
        }
        export_online(g_users, nusers, g_sess, ns);
    }
    if (cf->temp_cleanup) cleanup_temps(time(NULL));
}

int main(int argc, char **argv) {
    int once = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--once")) once = 1;
        else if (!strcmp(argv[i], "--dry-run")) g_dry_run = 1;
        else {
            fprintf(stderr, "Uso: %s [--once] [--dry-run]\n", argv[0]);
            return 2;
        }
    }
    setvbuf(stderr, NULL, _IOLBF, 0);
    mkdir(RUN_DIR, 0755);
    for (;;) {
        Conf cf;
        load_conf(&cf);
        run_cycle(&cf);
        if (once) break;
        sleep((unsigned)cf.interval);
    }
    return 0;
}
