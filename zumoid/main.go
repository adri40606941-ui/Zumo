// zumoid: vincula un usuario a un celular (Android ID) en las VPS de Zumo.
//
// Cómo funciona:
//   - La app, ya logueada por SSH, abre un canal interno hacia 127.0.0.1:7390 y manda "ZID1 <android_id>".
//   - Este servicio saca el usuario del kernel (qué proceso sshd abrió esa conexión), nunca de lo que
//     diga el cliente. Guarda el ID en /etc/zumo/dispositivos.db.
//   - Si el usuario está vinculado ("lock") y el ID no coincide, responde DENY y corta esa sesión.
//   - A los usuarios vinculados también se les cortan las sesiones que no mandan ID en unos segundos
//     (otras apps, versiones viejas) y las que quedaron con otro ID.
//   - Usuarios sin vincular: solo se anota su ID. Si algo falla, responde OK (nunca deja afuera a nadie).
//
// El mismo binario es la herramienta de línea de comandos que usan el panel y el bot:
//
//	zumoid get USUARIO      -> "id<TAB>lock<TAB>primero<TAB>ultimo"   (sale 1 si no hay registro)
//	zumoid lock USUARIO     -> vincula al ID actual                    (sale 1 si no hay ID)
//	zumoid unlock USUARIO   -> desvincula (conserva el ID)
//	zumoid forget USUARIO   -> borra el registro (para cambiar de celular)
//	zumoid rename VIEJO NUEVO
//	zumoid last USUARIO     -> último evento bloqueado: "epoch<TAB>evento<TAB>id"
//	zumoid serve            -> el servicio (por defecto)
//
// Formato de dispositivos.db (una línea por usuario):  usuario:id:lock(0|1):primero:ultimo   (epoch s)
// Solo usa la biblioteca estándar.
package main

import (
	"bufio"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"os/user"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const version = "1.0"

var idRe = regexp.MustCompile(`^[0-9a-f]{8,32}$`)

// ------------------------------------------------------------------ almacenamiento

type record struct {
	user, id    string
	lock        bool
	first, last int64
}

type store struct {
	db, lockFile, logFile string
}

func defaultStore() *store {
	env := func(k, d string) string {
		if v := os.Getenv(k); v != "" {
			return v
		}
		return d
	}
	return &store{
		db:       env("ZUMO_DISP_DB", "/etc/zumo/dispositivos.db"),
		lockFile: env("ZUMO_DISP_LOCK", "/etc/zumo/dispositivos.lock"),
		logFile:  env("ZUMO_DISP_LOG", "/etc/zumo/dispositivos.log"),
	}
}

// withLock toma el mismo flock que cualquier otra escritura del DB.
func (s *store) withLock(fn func() error) error {
	f, err := os.OpenFile(s.lockFile, os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX); err != nil {
		return err
	}
	defer syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
	return fn()
}

func (s *store) load() ([]record, error) {
	b, err := os.ReadFile(s.db)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var out []record
	for _, line := range strings.Split(string(b), "\n") {
		p := strings.Split(strings.TrimSpace(line), ":")
		if len(p) < 5 || p[0] == "" {
			continue
		}
		first, _ := strconv.ParseInt(p[3], 10, 64)
		last, _ := strconv.ParseInt(p[4], 10, 64)
		out = append(out, record{user: p[0], id: p[1], lock: p[2] == "1", first: first, last: last})
	}
	return out, nil
}

func (s *store) save(rs []record) error {
	var b strings.Builder
	for _, r := range rs {
		l := "0"
		if r.lock {
			l = "1"
		}
		fmt.Fprintf(&b, "%s:%s:%s:%d:%d\n", r.user, r.id, l, r.first, r.last)
	}
	tmp, err := os.CreateTemp(filepath.Dir(s.db), ".dispositivos.*")
	if err != nil {
		return err
	}
	if _, err := tmp.WriteString(b.String()); err != nil {
		tmp.Close()
		os.Remove(tmp.Name())
		return err
	}
	tmp.Close()
	_ = os.Chmod(tmp.Name(), 0o644)
	return os.Rename(tmp.Name(), s.db)
}

func find(rs []record, u string) int {
	for i, r := range rs {
		if r.user == u {
			return i
		}
	}
	return -1
}

func (s *store) get(u string) (r record, ok bool) {
	rs, err := s.load()
	if err != nil {
		return record{}, false
	}
	if i := find(rs, u); i >= 0 {
		return rs[i], true
	}
	return record{}, false
}

// update aplica fn al registro del usuario (o a uno vacío) bajo lock. fn devuelve false para no guardar.
func (s *store) update(u string, fn func(r *record, existed bool) bool) error {
	return s.withLock(func() error {
		rs, err := s.load()
		if err != nil {
			return err
		}
		i := find(rs, u)
		existed := i >= 0
		var r record
		if existed {
			r = rs[i]
		} else {
			r = record{user: u}
		}
		if !fn(&r, existed) {
			return nil
		}
		if existed {
			rs[i] = r
		} else {
			rs = append(rs, r)
		}
		return s.save(rs)
	})
}

// register anota el ID que mandó el usuario y decide si puede seguir conectado.
func (s *store) register(u, id string, now int64) (allowed bool, err error) {
	allowed = true
	err = s.update(u, func(r *record, existed bool) bool {
		switch {
		case !existed:
			r.id, r.first, r.last = id, now, now
		case r.lock && r.id != id:
			allowed = false
			return false
		case r.lock: // mismo celular
			r.last = now
		default: // sin vincular: se queda con el último que se vio
			if r.id != id {
				r.id, r.first = id, now
			}
			r.last = now
		}
		return true
	})
	if err != nil {
		return true, err // fail-open
	}
	return allowed, nil
}

func (s *store) setLock(u string, lock bool) error {
	found := false
	err := s.update(u, func(r *record, existed bool) bool {
		if !existed || r.id == "" {
			return false
		}
		found = true
		r.lock = lock
		return true
	})
	if err == nil && !found {
		return fmt.Errorf("%s todavía no mandó su Android ID (tiene que conectarse con la app nueva)", u)
	}
	return err
}

func (s *store) forget(u string) error {
	return s.withLock(func() error {
		rs, err := s.load()
		if err != nil {
			return err
		}
		out := rs[:0]
		for _, r := range rs {
			if r.user != u {
				out = append(out, r)
			}
		}
		if len(out) == len(rs) {
			return nil
		}
		return s.save(out)
	})
}

func (s *store) rename(a, b string) error {
	return s.withLock(func() error {
		rs, err := s.load()
		if err != nil {
			return err
		}
		i := find(rs, a)
		if i < 0 {
			return nil
		}
		out := rs[:0]
		for j, r := range rs {
			if r.user == b && j != i {
				continue // el nombre nuevo pisa a cualquier registro viejo
			}
			out = append(out, r)
		}
		if k := find(out, a); k >= 0 {
			out[k].user = b
		}
		return s.save(out)
	})
}

// logEvent anota un intento bloqueado. El archivo se recorta solo cuando pasa de ~200 KB.
func (s *store) logEvent(u, event, id string, now int64) {
	line := fmt.Sprintf("%d\t%s\t%s\t%s\n", now, u, event, id)
	if st, err := os.Stat(s.logFile); err == nil && st.Size() > 200*1024 {
		if b, err := os.ReadFile(s.logFile); err == nil {
			ls := strings.Split(strings.TrimRight(string(b), "\n"), "\n")
			if len(ls) > 500 {
				ls = ls[len(ls)-500:]
			}
			_ = os.WriteFile(s.logFile, []byte(strings.Join(ls, "\n")+"\n"), 0o644)
		}
	}
	f, err := os.OpenFile(s.logFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = f.WriteString(line)
}

func (s *store) last(u string) (epoch, event, id string, ok bool) {
	b, err := os.ReadFile(s.logFile)
	if err != nil {
		return
	}
	ls := strings.Split(strings.TrimRight(string(b), "\n"), "\n")
	for i := len(ls) - 1; i >= 0; i-- {
		p := strings.Split(ls[i], "\t")
		if len(p) >= 4 && p[1] == u {
			return p[0], p[2], p[3], true
		}
	}
	return
}

// -------------------------------------------------------------------- procesos (/proc)

type daemon struct {
	st       *store
	procRoot string
	comm     string // prefijo del nombre de proceso de las sesiones SSH ("sshd")
	port     int
	grace    time.Duration
	kill     func(pid int)

	mu        sync.Mutex
	verified  map[int]string    // pid -> ID con el que se verificó
	firstSeen map[int]time.Time // pid sin verificar -> cuándo se vio por primera vez
	booted    bool
}

func newDaemon(st *store, port int, grace time.Duration) *daemon {
	return &daemon{
		st: st, procRoot: "/proc", comm: "sshd", port: port, grace: grace,
		kill:     func(pid int) { _ = syscall.Kill(pid, syscall.SIGKILL) },
		verified: map[int]string{}, firstSeen: map[int]time.Time{},
	}
}

func (d *daemon) commOf(pid int) string {
	b, err := os.ReadFile(filepath.Join(d.procRoot, strconv.Itoa(pid), "comm"))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

// sessions devuelve pid -> uid de los procesos de sesión SSH (comm que empieza con d.comm), sin los de root.
func (d *daemon) sessions() map[int]int {
	out := map[int]int{}
	ents, err := os.ReadDir(d.procRoot)
	if err != nil {
		return out
	}
	for _, e := range ents {
		pid, err := strconv.Atoi(e.Name())
		if err != nil || !strings.HasPrefix(d.commOf(pid), d.comm) {
			continue
		}
		st, err := os.Stat(filepath.Join(d.procRoot, e.Name()))
		if err != nil {
			continue
		}
		if sys, ok := st.Sys().(*syscall.Stat_t); ok && sys.Uid != 0 {
			out[pid] = int(sys.Uid)
		}
	}
	return out
}

// inodeOf busca el inodo del socket cuyo extremo local es 127.0.0.1:remotePort y el remoto 127.0.0.1:d.port.
func (d *daemon) inodeOf(remotePort int) (string, bool) {
	b, err := os.ReadFile(filepath.Join(d.procRoot, "net", "tcp"))
	if err != nil {
		return "", false
	}
	want := fmt.Sprintf("0100007F:%04X", remotePort)
	rem := fmt.Sprintf("0100007F:%04X", d.port)
	for _, line := range strings.Split(string(b), "\n")[1:] {
		f := strings.Fields(line)
		if len(f) > 9 && strings.EqualFold(f[1], want) && strings.EqualFold(f[2], rem) {
			return f[9], true
		}
	}
	return "", false
}

// owner devuelve el proceso de sesión SSH (y su usuario) que abrió la conexión desde remotePort.
func (d *daemon) owner(remotePort int) (pid int, name string, ok bool) {
	ino, found := d.inodeOf(remotePort)
	if !found || ino == "0" {
		return 0, "", false
	}
	target := "socket:[" + ino + "]"
	for p, uid := range d.sessions() {
		fds, err := os.ReadDir(filepath.Join(d.procRoot, strconv.Itoa(p), "fd"))
		if err != nil {
			continue
		}
		for _, fd := range fds {
			if l, err := os.Readlink(filepath.Join(d.procRoot, strconv.Itoa(p), "fd", fd.Name())); err == nil && l == target {
				u, err := user.LookupId(strconv.Itoa(uid))
				if err != nil {
					return 0, "", false
				}
				return p, u.Username, true
			}
		}
	}
	return 0, "", false
}

// ------------------------------------------------------------------------- servicio

func (d *daemon) handle(c net.Conn) {
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(5 * time.Second))
	line, err := bufio.NewReaderSize(io.LimitReader(c, 128), 128).ReadString('\n')
	if err != nil {
		return
	}
	f := strings.Fields(line)
	if len(f) != 2 || f[0] != "ZID1" {
		_, _ = c.Write([]byte("OK\n"))
		return
	}
	id := strings.ToLower(f[1])
	ra, ok := c.RemoteAddr().(*net.TCPAddr)
	if !ok || !idRe.MatchString(id) {
		_, _ = c.Write([]byte("OK\n"))
		return
	}
	pid, name, found := d.owner(ra.Port)
	if !found {
		_, _ = c.Write([]byte("OK\n")) // no se pudo identificar: no se bloquea a nadie
		return
	}
	now := time.Now().Unix()
	allowed, err := d.st.register(name, id, now)
	if err != nil {
		fmt.Fprintln(os.Stderr, "zumoid:", err)
	}
	if allowed {
		d.mu.Lock()
		d.verified[pid] = id
		delete(d.firstSeen, pid)
		d.mu.Unlock()
		_, _ = c.Write([]byte("OK\n"))
		return
	}
	d.st.logEvent(name, "otro-dispositivo", id, now)
	_, _ = c.Write([]byte("DENY\n"))
	go func() { time.Sleep(300 * time.Millisecond); d.kill(pid) }()
}

// sweep corta las sesiones de usuarios vinculados que no están verificadas con su ID.
func (d *daemon) sweep(now time.Time) {
	rs, err := d.st.load()
	if err != nil {
		return
	}
	locked := map[int]record{} // uid -> registro
	for _, r := range rs {
		if r.lock {
			if u, err := user.Lookup(r.user); err == nil {
				if uid, err := strconv.Atoi(u.Uid); err == nil {
					locked[uid] = r
				}
			}
		}
	}
	sess := d.sessions()
	d.mu.Lock()
	defer d.mu.Unlock()
	first := !d.booted
	d.booted = true
	for pid := range d.verified {
		if _, alive := sess[pid]; !alive {
			delete(d.verified, pid)
		}
	}
	for pid := range d.firstSeen {
		if _, alive := sess[pid]; !alive {
			delete(d.firstSeen, pid)
		}
	}
	for pid, uid := range sess {
		r, isLocked := locked[uid]
		if !isLocked {
			continue
		}
		if first { // recién arrancó el servicio: las sesiones que ya existían se respetan
			if _, ok := d.verified[pid]; !ok {
				d.verified[pid] = r.id
			}
			continue
		}
		if vid, ok := d.verified[pid]; ok {
			if vid != r.id { // se vinculó a otro ID mientras esta sesión estaba abierta
				d.st.logEvent(r.user, "otro-dispositivo", vid, now.Unix())
				d.kill(pid)
				delete(d.verified, pid)
			}
			continue
		}
		t, seen := d.firstSeen[pid]
		if !seen {
			d.firstSeen[pid] = now
			continue
		}
		if now.Sub(t) >= d.grace {
			d.st.logEvent(r.user, "sin-verificar", "-", now.Unix())
			d.kill(pid)
			delete(d.firstSeen, pid)
		}
	}
}

func (d *daemon) serve(ln net.Listener) error {
	go func() {
		for {
			d.sweep(time.Now())
			time.Sleep(2 * time.Second)
		}
	}()
	for {
		c, err := ln.Accept()
		if err != nil {
			return err
		}
		go d.handle(c)
	}
}

// ------------------------------------------------------------------------------ CLI

func cli(st *store, args []string) int {
	need := func(n int) bool {
		if len(args) != n+1 {
			fmt.Fprintln(os.Stderr, "faltan argumentos")
			return false
		}
		return true
	}
	switch args[0] {
	case "get":
		if !need(1) {
			return 2
		}
		r, ok := st.get(args[1])
		if !ok {
			return 1
		}
		l := 0
		if r.lock {
			l = 1
		}
		fmt.Printf("%s\t%d\t%d\t%d\n", r.id, l, r.first, r.last)
	case "lock", "unlock":
		if !need(1) {
			return 2
		}
		if err := st.setLock(args[1], args[0] == "lock"); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
	case "forget":
		if !need(1) {
			return 2
		}
		if err := st.forget(args[1]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
	case "rename":
		if !need(2) {
			return 2
		}
		if err := st.rename(args[1], args[2]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
	case "last":
		if !need(1) {
			return 2
		}
		e, ev, id, ok := st.last(args[1])
		if !ok {
			return 1
		}
		fmt.Printf("%s\t%s\t%s\n", e, ev, id)
	default:
		fmt.Fprintln(os.Stderr, "comando desconocido:", args[0])
		return 2
	}
	return 0
}

func main() {
	args := os.Args[1:]
	if len(args) > 0 && (args[0] == "-version" || args[0] == "--version") {
		fmt.Println("zumoid " + version)
		return
	}
	st := defaultStore()
	if len(args) > 0 && args[0] != "serve" && !strings.HasPrefix(args[0], "-") {
		os.Exit(cli(st, args))
	}
	fs := flag.NewFlagSet("serve", flag.ExitOnError)
	listen := fs.String("listen", "127.0.0.1:7390", "dirección del servicio (solo local)")
	grace := fs.Duration("grace", 12*time.Second, "tiempo que tiene una sesión de un usuario vinculado para mandar su ID")
	comm := fs.String("comm", "sshd", "prefijo del nombre de proceso de las sesiones SSH")
	proc := fs.String("proc", "/proc", "raíz de /proc (para pruebas)")
	if len(args) > 0 && args[0] == "serve" {
		args = args[1:]
	}
	_ = fs.Parse(args)
	ln, err := net.Listen("tcp", *listen)
	if err != nil {
		fmt.Fprintln(os.Stderr, "No se pudo abrir", *listen+":", err)
		os.Exit(1)
	}
	port := ln.Addr().(*net.TCPAddr).Port
	d := newDaemon(st, port, *grace)
	d.comm, d.procRoot = *comm, *proc
	fmt.Fprintf(os.Stderr, "zumoid %s en %s\n", version, *listen)
	if err := d.serve(ln); err != nil {
		fmt.Fprintln(os.Stderr, "zumoid:", err)
		os.Exit(1)
	}
}
