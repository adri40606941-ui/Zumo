package main

import (
	"bufio"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

const (
	idA = "aaaaaaaaaaaaaaaa"
	idB = "bbbbbbbbbbbbbbbb"
)

// Los procesos "de sesión SSH" de las pruebas son este mismo binario copiado con otro nombre y
// corriendo como el usuario "nobody": se comporta como el hijo de sshd (usuario normal, nombre propio).
func TestMain(m *testing.M) {
	switch h := os.Getenv("ZID_HELPER"); {
	case strings.HasPrefix(h, "connect:"): // connect:<addr>:<id>
		p := strings.SplitN(h, ":", 4)
		c, err := net.Dial("tcp", p[1]+":"+p[2])
		if err != nil {
			os.Exit(3)
		}
		_, _ = c.Write([]byte("ZID1 " + p[3] + "\n"))
		l, _ := bufio.NewReader(c).ReadString('\n')
		os.Stdout.WriteString(l)
		time.Sleep(60 * time.Second)
		os.Exit(0)
	case h == "sleep":
		time.Sleep(60 * time.Second)
		os.Exit(0)
	}
	os.Exit(m.Run())
}

func newStore(t *testing.T) *store {
	d := t.TempDir()
	return &store{db: filepath.Join(d, "d.db"), lockFile: filepath.Join(d, "d.lock"), logFile: filepath.Join(d, "d.log")}
}

func TestRegisterYVinculo(t *testing.T) {
	s := newStore(t)
	if ok, err := s.register("ana", idA, 100); !ok || err != nil {
		t.Fatal(ok, err)
	}
	r, _ := s.get("ana")
	if r.id != idA || r.lock || r.first != 100 || r.last != 100 {
		t.Fatalf("%+v", r)
	}
	// sin vincular: cambia de celular y se queda con el último
	if ok, _ := s.register("ana", idB, 200); !ok {
		t.Fatal("sin vincular debe dejar pasar")
	}
	if r, _ = s.get("ana"); r.id != idB || r.first != 200 {
		t.Fatalf("%+v", r)
	}
	// vincular
	if err := s.setLock("ana", true); err != nil {
		t.Fatal(err)
	}
	if ok, _ := s.register("ana", idA, 300); ok {
		t.Fatal("otro ID debe rechazarse")
	}
	if r, _ = s.get("ana"); r.id != idB || !r.lock || r.last != 200 {
		t.Fatalf("un rechazo no debe tocar el registro: %+v", r)
	}
	if ok, _ := s.register("ana", idB, 400); !ok {
		t.Fatal("el mismo ID debe pasar")
	}
	if r, _ = s.get("ana"); r.last != 400 {
		t.Fatalf("%+v", r)
	}
	// desvincular conserva el ID; olvidar lo borra
	_ = s.setLock("ana", false)
	if r, _ = s.get("ana"); r.lock || r.id != idB {
		t.Fatalf("%+v", r)
	}
	_ = s.forget("ana")
	if _, ok := s.get("ana"); ok {
		t.Fatal("olvidar debe borrar")
	}
	if err := s.setLock("ana", true); err == nil {
		t.Fatal("no se puede vincular sin ID")
	}
}

func TestRenameYLog(t *testing.T) {
	s := newStore(t)
	_, _ = s.register("ana", idA, 1)
	_, _ = s.register("beto", idB, 1)
	_ = s.rename("ana", "carla")
	if _, ok := s.get("ana"); ok {
		t.Fatal("ana debía desaparecer")
	}
	if r, ok := s.get("carla"); !ok || r.id != idA {
		t.Fatal("carla debía tener el ID de ana")
	}
	_ = s.rename("carla", "beto") // pisa al registro viejo de beto
	if r, _ := s.get("beto"); r.id != idA {
		t.Fatal("beto debía quedar con el ID de carla")
	}
	rs, _ := s.load()
	if len(rs) != 1 {
		t.Fatalf("%d registros", len(rs))
	}
	s.logEvent("beto", "otro-dispositivo", idB, 5)
	s.logEvent("beto", "sin-verificar", "-", 9)
	s.logEvent("otro", "otro-dispositivo", idA, 10)
	if e, ev, id, ok := s.last("beto"); !ok || e != "9" || ev != "sin-verificar" || id != "-" {
		t.Fatal(e, ev, id, ok)
	}
	if _, _, _, ok := s.last("nadie"); ok {
		t.Fatal("nadie no tiene eventos")
	}
}

func TestFormatoDelArchivo(t *testing.T) {
	s := newStore(t)
	_, _ = s.register("ana", idA, 100)
	_ = s.setLock("ana", true)
	b, _ := os.ReadFile(s.db)
	if string(b) != "ana:"+idA+":1:100:100\n" {
		t.Fatalf("formato: %q", b)
	}
}

func TestCLI(t *testing.T) {
	s := newStore(t)
	t.Setenv("ZUMO_DISP_DB", s.db)
	_, _ = s.register("ana", idA, 100)
	out := captureStdout(t, func() {
		if cli(s, []string{"get", "ana"}) != 0 {
			t.Fatal("get")
		}
	})
	if out != idA+"\t0\t100\t100\n" {
		t.Fatalf("%q", out)
	}
	if cli(s, []string{"get", "nadie"}) != 1 {
		t.Fatal("get sin registro debe salir 1")
	}
	if cli(s, []string{"lock", "ana"}) != 0 || cli(s, []string{"lock", "nadie"}) != 1 {
		t.Fatal("lock")
	}
	if cli(s, []string{"unlock", "ana"}) != 0 || cli(s, []string{"forget", "ana"}) != 0 {
		t.Fatal("unlock/forget")
	}
	if cli(s, []string{"rename", "a"}) != 2 || cli(s, []string{"raro"}) != 2 {
		t.Fatal("argumentos")
	}
}

func captureStdout(t *testing.T, fn func()) string {
	r, w, _ := os.Pipe()
	old := os.Stdout
	os.Stdout = w
	fn()
	w.Close()
	os.Stdout = old
	b, _ := io.ReadAll(r)
	return string(b)
}

// ------------------------------------------------------ pruebas con procesos reales (root)

type env struct {
	d    *daemon
	addr string
	port int
	bin  string
}

func setup(t *testing.T) *env {
	if os.Geteuid() != 0 {
		t.Skip("necesita root para correr procesos como otro usuario")
	}
	s := newStore(t)
	dir, err := os.MkdirTemp("", "zidtest")
	if err != nil {
		t.Fatal(err)
	}
	_ = os.Chmod(dir, 0o755) // "nobody" tiene que poder ejecutar el binario
	t.Cleanup(func() { os.RemoveAll(dir) })
	self, _ := os.Executable()
	bin := filepath.Join(dir, "zidsess")
	b, _ := os.ReadFile(self)
	if err := os.WriteFile(bin, b, 0o755); err != nil {
		t.Fatal(err)
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := ln.Addr().(*net.TCPAddr).Port
	d := newDaemon(s, port, 50*time.Millisecond)
	d.comm = "zidsess"
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go d.handle(c)
		}
	}()
	t.Cleanup(func() { ln.Close() })
	return &env{d: d, addr: "127.0.0.1", port: port, bin: bin}
}

type sess struct {
	cmd   *exec.Cmd
	reply chan string
	done  chan error
}

// lanza una "sesión SSH" como nobody. mode: "sleep" o "connect:<ip>:<port>:<id>"
func (e *env) start(t *testing.T, mode string) *sess {
	cmd := exec.Command(e.bin)
	cmd.Env = []string{"ZID_HELPER=" + mode}
	cmd.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 65534, Gid: 65534}}
	out, _ := cmd.StdoutPipe()
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	s := &sess{cmd: cmd, reply: make(chan string, 1), done: make(chan error, 1)}
	go func() { l, _ := bufio.NewReader(out).ReadString('\n'); s.reply <- strings.TrimSpace(l) }()
	go func() { s.done <- cmd.Wait() }()
	t.Cleanup(func() { _ = cmd.Process.Kill() })
	return s
}

func (e *env) connect(t *testing.T, id string) *sess {
	return e.start(t, "connect:"+e.addr+":"+itoa(e.port)+":"+id)
}

func itoa(n int) string { return strconv.Itoa(n) }

func (s *sess) want(t *testing.T, r string) {
	t.Helper()
	select {
	case got := <-s.reply:
		if got != r {
			t.Fatalf("respuesta %q, esperaba %q", got, r)
		}
	case <-time.After(5 * time.Second):
		t.Fatalf("sin respuesta (esperaba %q)", r)
	}
}

func (s *sess) killed(within time.Duration) bool {
	select {
	case <-s.done:
		return true
	case <-time.After(within):
		return false
	}
}

func TestSesionSinVincularSoloSeAnota(t *testing.T) {
	e := setup(t)
	s := e.connect(t, idA)
	s.want(t, "OK")
	r, ok := e.d.st.get("nobody")
	if !ok || r.id != idA || r.lock {
		t.Fatalf("%+v %v", r, ok)
	}
	if s.killed(500 * time.Millisecond) {
		t.Fatal("no debía cortarse")
	}
}

func TestVinculadoRechazaOtroCelularYCorta(t *testing.T) {
	e := setup(t)
	e.connect(t, idA).want(t, "OK")
	if err := e.d.st.setLock("nobody", true); err != nil {
		t.Fatal(err)
	}
	otro := e.connect(t, idB)
	otro.want(t, "DENY")
	if !otro.killed(3 * time.Second) {
		t.Fatal("la sesión del otro celular debía cortarse")
	}
	if _, ev, id, ok := e.d.st.last("nobody"); !ok || ev != "otro-dispositivo" || id != idB {
		t.Fatal("falta el intento en el registro", ev, id, ok)
	}
	// el mismo celular sigue entrando y no se corta
	igual := e.connect(t, idA)
	igual.want(t, "OK")
	if igual.killed(500 * time.Millisecond) {
		t.Fatal("el celular vinculado no debía cortarse")
	}
}

func TestSesionSinIDDeUsuarioVinculadoSeCortaPasadoElPlazo(t *testing.T) {
	e := setup(t)
	_, _ = e.d.st.register("nobody", idA, 1)
	_ = e.d.st.setLock("nobody", true)
	e.d.sweep(time.Now()) // arranque: no hay sesiones todavía
	s := e.start(t, "sleep")
	time.Sleep(200 * time.Millisecond)
	now := time.Now()
	e.d.sweep(now) // la ve por primera vez
	if s.killed(200 * time.Millisecond) {
		t.Fatal("no debía cortarse todavía")
	}
	e.d.sweep(now.Add(time.Second)) // pasó el plazo
	if !s.killed(3 * time.Second) {
		t.Fatal("una sesión sin ID de un usuario vinculado debía cortarse")
	}
	if _, ev, _, ok := e.d.st.last("nobody"); !ok || ev != "sin-verificar" {
		t.Fatal("falta el evento", ev)
	}
}

func TestSesionVerificadaNoSeCorta(t *testing.T) {
	e := setup(t)
	_, _ = e.d.st.register("nobody", idA, 1)
	_ = e.d.st.setLock("nobody", true)
	e.d.sweep(time.Now())
	s := e.connect(t, idA)
	s.want(t, "OK")
	now := time.Now()
	for i := 0; i < 5; i++ {
		e.d.sweep(now.Add(time.Duration(i) * time.Second))
	}
	if s.killed(300 * time.Millisecond) {
		t.Fatal("una sesión verificada no se corta")
	}
}

func TestSesionesQueYaExistianAlArrancarSeRespetan(t *testing.T) {
	e := setup(t)
	_, _ = e.d.st.register("nobody", idA, 1)
	_ = e.d.st.setLock("nobody", true)
	s := e.start(t, "sleep")
	time.Sleep(200 * time.Millisecond)
	now := time.Now()
	e.d.sweep(now) // primer barrido tras (re)iniciar el servicio
	for i := 1; i < 5; i++ {
		e.d.sweep(now.Add(time.Duration(i) * time.Second))
	}
	if s.killed(300 * time.Millisecond) {
		t.Fatal("al reiniciar el servicio no se debe cortar a nadie")
	}
}

func TestVincularAOtroIDCortaLaSesionAbierta(t *testing.T) {
	e := setup(t)
	s := e.connect(t, idA)
	s.want(t, "OK") // sin vincular, verificada con A
	e.d.sweep(time.Now())
	_ = e.d.st.update("nobody", func(r *record, _ bool) bool { r.id, r.lock = idB, true; return true })
	e.d.sweep(time.Now().Add(time.Second))
	if !s.killed(3 * time.Second) {
		t.Fatal("al vincular a otro ID, la sesión del ID viejo debía cortarse")
	}
}

func TestUsuariosSinVincularNuncaSeCortan(t *testing.T) {
	e := setup(t)
	s := e.start(t, "sleep")
	time.Sleep(200 * time.Millisecond)
	now := time.Now()
	for i := 0; i < 5; i++ {
		e.d.sweep(now.Add(time.Duration(i) * time.Minute))
	}
	if s.killed(300 * time.Millisecond) {
		t.Fatal("sin vincular no se corta nunca")
	}
}

func TestEntradaInvalidaONoIdentificadaDejaPasar(t *testing.T) {
	e := setup(t)
	for _, linea := range []string{"hola\n", "ZID1 xyz\n", "ZID1 " + idA + " extra\n"} {
		c, err := net.Dial("tcp", e.addr+":"+itoa(e.port))
		if err != nil {
			t.Fatal(err)
		}
		_, _ = c.Write([]byte(linea))
		l, _ := bufio.NewReader(c).ReadString('\n')
		c.Close()
		if strings.TrimSpace(l) != "OK" {
			t.Fatalf("%q -> %q", linea, l)
		}
	}
	// y una conexión válida que NO viene de un proceso sshd (el propio test) no se puede identificar: OK sin anotar
	c, _ := net.Dial("tcp", e.addr+":"+itoa(e.port))
	_, _ = c.Write([]byte("ZID1 " + idA + "\n"))
	l, _ := bufio.NewReader(c).ReadString('\n')
	c.Close()
	if strings.TrimSpace(l) != "OK" {
		t.Fatalf("no identificada: %q", l)
	}
	if _, ok := e.d.st.get("root"); ok {
		t.Fatal("no debe anotar nada si no se identificó el usuario")
	}
}
