package main

import (
	"io"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

// Un "sshd" falso: manda un banner al conectar y guarda lo que recibe.
type fakeSSH struct {
	ln    net.Listener
	got   chan []byte
	ports chan int // puerto de origen de cada conexión (lo que sshd vería como peer)
}

func newFakeSSH(t *testing.T) *fakeSSH {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	f := &fakeSSH{ln: ln, got: make(chan []byte, 16), ports: make(chan int, 16)}
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			f.ports <- c.RemoteAddr().(*net.TCPAddr).Port
			go func() {
				defer c.Close()
				_, _ = c.Write([]byte("SSH-2.0-fake\r\n"))
				buf := make([]byte, 4096)
				for {
					n, err := c.Read(buf)
					if n > 0 {
						f.got <- append([]byte(nil), buf[:n]...)
					}
					if err != nil {
						return
					}
				}
			}()
		}
	}()
	t.Cleanup(func() { ln.Close() })
	return f
}

func start(t *testing.T, resp string) (addr string, f *fakeSSH, pmap string, s *server) {
	f = newFakeSSH(t)
	sshPort := f.ln.Addr().(*net.TCPAddr).Port
	pmap = t.TempDir()
	s = newServer(sshPort, 0, resp, pmap)
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	go s.serve(ln)
	t.Cleanup(func() { ln.Close() })
	return ln.Addr().String(), f, pmap, s
}

func readN(t *testing.T, c net.Conn, n int) string {
	t.Helper()
	b := make([]byte, n)
	_ = c.SetReadDeadline(time.Now().Add(3 * time.Second))
	if _, err := io.ReadFull(c, b); err != nil {
		t.Fatalf("leyendo %d bytes: %v", n, err)
	}
	return string(b)
}

func TestFlujoNormalYPmap(t *testing.T) {
	addr, f, pmap, _ := start(t, resp101)
	c, _ := net.Dial("tcp", addr)
	defer c.Close()
	_, _ = c.Write([]byte("GET / HTTP/1.1\r\nHost: x.net\r\n\r\n"))
	if got := readN(t, c, len(resp101)); got != resp101 {
		t.Fatalf("respuesta distinta: %q", got)
	}
	if got := readN(t, c, 14); got != "SSH-2.0-fake\r\n" {
		t.Fatalf("banner del SSH: %q", got)
	}
	_, _ = c.Write([]byte("SSH-2.0-cliente\r\n"))
	if got := string(<-f.got); got != "SSH-2.0-cliente\r\n" {
		t.Fatalf("el SSH recibió %q", got)
	}
	// pmap: puerto con el que se conectó al SSH -> IP real del cliente
	port := <-f.ports
	b, err := os.ReadFile(filepath.Join(pmap, strconv.Itoa(port)))
	if err != nil || strings.TrimSpace(string(b)) != "127.0.0.1" {
		t.Fatalf("pmap: %q %v", b, err)
	}
	c.Close()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if _, err := os.Stat(filepath.Join(pmap, strconv.Itoa(port))); os.IsNotExist(err) {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("el pmap no se borró al cerrar")
}

func TestLoQueSobraDespuesDeLaCabeceraVaAlSSH(t *testing.T) {
	addr, f, _, _ := start(t, resp101)
	c, _ := net.Dial("tcp", addr)
	defer c.Close()
	_, _ = c.Write([]byte("GET / HTTP/1.1\r\nHost: x\r\n\r\nSSH-2.0-pegado\r\n"))
	readN(t, c, len(resp101))
	if got := string(<-f.got); got != "SSH-2.0-pegado\r\n" {
		t.Fatalf("el SSH recibió %q", got)
	}
}

func TestXSplitDescartaElSegmentoSiguiente(t *testing.T) {
	addr, f, _, _ := start(t, resp101)
	c, _ := net.Dial("tcp", addr)
	defer c.Close()
	_, _ = c.Write([]byte("GET / HTTP/1.1\r\nX-Split: 1\r\n\r\n"))
	time.Sleep(100 * time.Millisecond)
	select {
	case <-f.ports:
		t.Fatal("conectó al SSH antes del segmento partido")
	default:
	}
	_, _ = c.Write([]byte("GET /basura HTTP/1.1\r\n\r\n"))
	readN(t, c, len(resp101))
	time.Sleep(100 * time.Millisecond)
	_, _ = c.Write([]byte("SSH-2.0-real\r\n"))
	if got := string(<-f.got); got != "SSH-2.0-real\r\n" {
		t.Fatalf("el SSH recibió %q (el segmento partido debía descartarse)", got)
	}
}

func TestXSplitConSegmentoEnElMismoPaquete(t *testing.T) {
	addr, f, _, _ := start(t, resp101)
	c, _ := net.Dial("tcp", addr)
	defer c.Close()
	_, _ = c.Write([]byte("GET / HTTP/1.1\r\nX-Split: 1\r\n\r\nGET /basura HTTP/1.1\r\n\r\n"))
	readN(t, c, len(resp101))
	_, _ = c.Write([]byte("SSH-2.0-real\r\n"))
	if got := string(<-f.got); got != "SSH-2.0-real\r\n" {
		t.Fatalf("el SSH recibió %q", got)
	}
}

func TestXRealHost(t *testing.T) {
	addr, f, _, s := start(t, resp101)
	mal, _ := net.Dial("tcp", addr)
	defer mal.Close()
	_, _ = mal.Write([]byte("GET / HTTP/1.1\r\nX-Real-Host: otro.com:22\r\n\r\n"))
	if got := readN(t, mal, 12); got != "HTTP/1.1 403" {
		t.Fatalf("esperaba 403, vino %q", got)
	}
	bien, _ := net.Dial("tcp", addr)
	defer bien.Close()
	_, _ = bien.Write([]byte("GET / HTTP/1.1\r\nx-real-host: 127.0.0.1:" + strconv.Itoa(s.sshPort) + "\r\n\r\n"))
	readN(t, bien, len(resp101))
	<-f.ports
	loc, _ := net.Dial("tcp", addr)
	defer loc.Close()
	_, _ = loc.Write([]byte("GET / HTTP/1.1\r\nX-Real-Host: localhost:" + strconv.Itoa(s.sshPort) + "\r\n\r\n"))
	readN(t, loc, len(resp101))
}

func TestCabeceraDemasiadoGrande(t *testing.T) {
	addr, _, _, _ := start(t, resp101)
	c, _ := net.Dial("tcp", addr)
	defer c.Close()
	_, _ = c.Write([]byte("GET / HTTP/1.1\r\nX: " + strings.Repeat("a", maxHeader)))
	if got := readN(t, c, 12); got != "HTTP/1.1 431" {
		t.Fatalf("esperaba 431, vino %q", got)
	}
}

func TestLimitePorIP(t *testing.T) {
	addr, _, _, s := start(t, resp101)
	var abiertas []net.Conn
	defer func() {
		for _, c := range abiertas {
			c.Close()
		}
	}()
	for i := 0; i < perIPMax; i++ {
		c, err := net.Dial("tcp", addr)
		if err != nil {
			t.Fatal(err)
		}
		abiertas = append(abiertas, c)
	}
	time.Sleep(200 * time.Millisecond)
	extra, _ := net.Dial("tcp", addr)
	defer extra.Close()
	_ = extra.SetReadDeadline(time.Now().Add(2 * time.Second))
	if _, err := extra.Read(make([]byte, 1)); err != io.EOF {
		t.Fatalf("la conexión %d debía cerrarse (EOF), vino %v", perIPMax+1, err)
	}
	abiertas[0].Close()
	time.Sleep(300 * time.Millisecond)
	s.mu.Lock()
	n := s.perIP["127.0.0.1"]
	s.mu.Unlock()
	if n != perIPMax-1 {
		t.Fatalf("contador por IP: %d, esperaba %d", n, perIPMax-1)
	}
}

func TestBanner(t *testing.T) {
	env := func(m map[string]string) func(string) string { return func(k string) string { return m[k] } }
	if got := buildResponse(101, env(nil)); got != resp101 {
		t.Fatal("por defecto debe ser ZUMO amarillo")
	}
	if got := buildResponse(200, env(nil)); got != resp200 {
		t.Fatal("modo 200 por defecto")
	}
	got := buildResponse(101, env(map[string]string{"PDIRECT_BANNER": "Mi\r\nVPN", "PDIRECT_COLOR": "re<d>#"}))
	if !strings.Contains(got, `<font color="red#"><b>MiVPN</b></font>`) || strings.Count(got, "\r\n") != 4 {
		t.Fatalf("banner sin sanear: %q", got)
	}
	if got := buildResponse(200, env(map[string]string{"PDIRECT_BANNER": "X"})); !strings.HasPrefix(got, "HTTP/1.1 200 <font color=\"yellow\"><b>X</b>") || !strings.Contains(got, "Content-Length: 0") {
		t.Fatalf("modo 200 con banner: %q", got)
	}
	if got := buildResponse(101, env(map[string]string{"PDIRECT_RESPONSE": "RAW", "PDIRECT_BANNER": "X"})); got != "RAW" {
		t.Fatal("PDIRECT_RESPONSE tiene prioridad")
	}
	if got := buildResponse(101, env(map[string]string{"PDIRECT_BANNER": strings.Repeat("Z", 200)})); strings.Count(got, "Z") != 79 {
		t.Fatal("el banner se corta en 79")
	}
}

func TestParsePort(t *testing.T) {
	for in, want := range map[string]int{"": 22, "80": 80, "65535": 65535} {
		if v, err := parsePort(in, 22); err != nil || v != want {
			t.Fatalf("%q -> %d %v", in, v, err)
		}
	}
	for _, in := range []string{"0", "65536", "ab", "-1"} {
		if _, err := parsePort(in, 22); err == nil {
			t.Fatalf("%q debía fallar", in)
		}
	}
}
