// zumogo: WebSocket/HTTP-upgrade -> SSH, la versión en Go de PDirect (pdirect-c).
//
// Mismo comportamiento que PDirect, para que la app y el limitador no noten la diferencia:
//   - Lee la cabecera HTTP del cliente (payload) y responde el "101"/"200" con el banner.
//   - X-Real-Host: si viene, tiene que apuntar al SSH local; si no, 403.
//   - X-Split: descarta el segmento partido del payload antes de conectar.
//   - Pasa los bytes al sshd local (127.0.0.1:SSH_PORT) con control de flujo.
//   - Anota "puerto local hacia sshd -> IP real del cliente" en /run/zumo/pmap/<puerto>,
//     que es lo que lee zumo-limit para contar por IP real.
//   - Límites anti-abuso: 2000 conexiones en total y 24 por IP.
//
// Uso:  zumogo [SSH_PORT=22] [LISTEN_PORT=80] [MODO=101|200]
// Variables (las mismas de PDirect, se leen de /etc/zumo/pdirect.env):
//
//	PDIRECT_RESPONSE  respuesta cruda completa (uso avanzado)
//	PDIRECT_BANNER    texto del banner;  PDIRECT_COLOR  color (yellow, #ff0000...)
//	PDIRECT_MODE      101 o 200
//
// Solo usa la biblioteca estándar: se compila sin dependencias (CGO_ENABLED=0).
package main

import (
	"bytes"
	"fmt"
	"net"
	"net/netip"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	version       = "1.0"
	maxHeader     = 16384
	headerTimeout = 10 * time.Second  // anti-slowloris: corto para recibir las cabeceras
	relayTimeout  = 120 * time.Second // túnel establecido: el keepalive de SSH mantiene vivo el ocioso
	maxConns      = 2000
	perIPMax      = 24
	sshHost       = "127.0.0.1"
	relayBuf      = 32 * 1024
)

var (
	resp101 = "HTTP/1.1 101 <font color=\"yellow\"><b>ZUMO</b></font>\r\n\r\n" +
		"HTTP/1.1 101 Conexion Exitosa\r\n\r\n"
	resp200 = "HTTP/1.1 200 <font color=\"yellow\"><b>ZUMO</b></font>\r\nContent-Length: 0\r\n\r\n" +
		"HTTP/1.1 200 Conexion Exitosa\r\n\r\n"
)

type server struct {
	sshPort    int
	listenPort int
	response   []byte
	allowed    [2]string // valores válidos de X-Real-Host
	pmapDir    string

	mu    sync.Mutex
	conns int
	perIP map[string]int
}

func parsePort(s string, def int) (int, error) {
	if s == "" {
		return def, nil
	}
	v, err := strconv.Atoi(s)
	if err != nil || v < 1 || v > 65535 {
		return 0, fmt.Errorf("puerto inválido: %q", s)
	}
	return v, nil
}

// buildResponse arma lo que ve la app: respuesta cruda > banner con color > ZUMO amarillo.
func buildResponse(code int, getenv func(string) string) string {
	if raw := getenv("PDIRECT_RESPONSE"); raw != "" {
		return raw
	}
	banner := getenv("PDIRECT_BANNER")
	if banner == "" {
		if code == 200 {
			return resp200
		}
		return resp101
	}
	// banner sin caracteres de control (evita inyectar cabeceras), máx. 79
	var b strings.Builder
	for i := 0; i < len(banner) && b.Len() < 79; i++ {
		if c := banner[i]; c >= 32 && c != 127 {
			b.WriteByte(c)
		}
	}
	// color: solo alfanumérico o '#', máx. 31
	var co strings.Builder
	color := getenv("PDIRECT_COLOR")
	for i := 0; i < len(color) && co.Len() < 31; i++ {
		c := color[i]
		if c == '#' || (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') {
			co.WriteByte(c)
		}
	}
	if co.Len() == 0 {
		co.WriteString("yellow")
	}
	if code == 200 {
		return fmt.Sprintf("HTTP/1.1 200 <font color=\"%s\"><b>%s</b></font>\r\nContent-Length: 0\r\n\r\n"+
			"HTTP/1.1 200 Conexion Exitosa\r\n\r\n", co.String(), b.String())
	}
	return fmt.Sprintf("HTTP/1.1 101 <font color=\"%s\"><b>%s</b></font>\r\n\r\n"+
		"HTTP/1.1 101 Conexion Exitosa\r\n\r\n", co.String(), b.String())
}

// containsFold: ¿aparece name en h sin distinguir mayúsculas? (igual que header_present de PDirect)
func containsFold(h []byte, name string) bool {
	return bytes.Contains(bytes.ToLower(h), []byte(strings.ToLower(name)))
}

// validHost: si hay X-Real-Host tiene que ser el SSH local; sin esa cabecera, pasa.
func (s *server) validHost(h []byte) bool {
	for _, line := range strings.FieldsFunc(string(h), func(r rune) bool { return r == '\r' || r == '\n' }) {
		i := strings.IndexByte(line, ':')
		if i < 0 {
			continue
		}
		if strings.EqualFold(line[:i], "X-Real-Host") {
			v := strings.TrimSpace(line[i+1:])
			return strings.EqualFold(v, s.allowed[0]) || strings.EqualFold(v, s.allowed[1])
		}
	}
	return true
}

func (s *server) acquire(ip string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.conns >= maxConns || s.perIP[ip] >= perIPMax {
		return false
	}
	s.conns++
	s.perIP[ip]++
	return true
}

func (s *server) release(ip string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.conns--
	if s.perIP[ip]--; s.perIP[ip] <= 0 {
		delete(s.perIP, ip)
	}
}

func (s *server) pmapWrite(port int, ip string) {
	if port > 0 {
		_ = os.WriteFile(filepath.Join(s.pmapDir, strconv.Itoa(port)), []byte(ip+"\n"), 0o644)
	}
}

func (s *server) pmapDel(port int) {
	if port > 0 {
		_ = os.Remove(filepath.Join(s.pmapDir, strconv.Itoa(port)))
	}
}

// readRequest lee el primer pedido HTTP del cliente. Devuelve lo que sobró después de las
// cabeceras (para pasarlo al SSH) o ok=false si la conexión se rechazó o se cayó.
func (s *server) readRequest(c net.Conn) (rest []byte, ok bool) {
	buf := make([]byte, 0, 2048)
	tmp := make([]byte, 4096)
	read := func() ([]byte, bool) {
		_ = c.SetReadDeadline(time.Now().Add(headerTimeout))
		n, err := c.Read(tmp)
		if n > 0 {
			return tmp[:n], true
		}
		return nil, err == nil
	}
	for {
		chunk, alive := read()
		buf = append(buf, chunk...)
		if len(buf) >= maxHeader {
			_, _ = c.Write([]byte("HTTP/1.1 431 Request Header Fields Too Large\r\nConnection: close\r\n\r\n"))
			return nil, false
		}
		if i := bytes.Index(buf, []byte("\r\n\r\n")); i >= 0 {
			hlen := i + 4
			headers := buf[:hlen]
			split := containsFold(headers, "X-Split")
			if !s.validHost(headers) {
				_, _ = c.Write([]byte("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n"))
				return nil, false
			}
			rest = buf[hlen:]
			if split {
				// payload partido: se descarta el segmento que sigue y recién ahí se conecta
				if len(rest) == 0 {
					seg, alive2 := read()
					if len(seg) == 0 && !alive2 {
						return nil, false
					}
				}
				return nil, true
			}
			return rest, true
		}
		if len(chunk) == 0 && !alive {
			return nil, false
		}
	}
}

func dialer() *net.Dialer {
	return &net.Dialer{Timeout: 10 * time.Second, KeepAliveConfig: net.KeepAliveConfig{
		Enable: true, Idle: 30 * time.Second, Interval: 10 * time.Second, Count: 3}}
}

// relay copia en un sentido; al terminar (EOF, error o 120 s sin datos) cierra los dos lados.
func relay(dst, src net.Conn) {
	buf := make([]byte, relayBuf)
	defer func() { _ = dst.Close(); _ = src.Close() }()
	for {
		_ = src.SetReadDeadline(time.Now().Add(relayTimeout))
		n, err := src.Read(buf)
		if n > 0 {
			if _, werr := dst.Write(buf[:n]); werr != nil {
				return
			}
		}
		if err != nil {
			return
		}
	}
}

func (s *server) handle(c net.Conn, ip string) {
	defer c.Close()
	defer s.release(ip)
	rest, ok := s.readRequest(c)
	if !ok {
		return
	}
	up, err := dialer().Dial("tcp", net.JoinHostPort(sshHost, strconv.Itoa(s.sshPort)))
	if err != nil {
		return
	}
	defer up.Close()
	if la, isTCP := up.LocalAddr().(*net.TCPAddr); isTCP {
		s.pmapWrite(la.Port, ip)
		defer s.pmapDel(la.Port)
	}
	if _, err := c.Write(s.response); err != nil {
		return
	}
	if len(rest) > 0 {
		if _, err := up.Write(rest); err != nil {
			return
		}
	}
	done := make(chan struct{}, 2)
	go func() { relay(up, c); done <- struct{}{} }()
	go func() { relay(c, up); done <- struct{}{} }()
	<-done
	_ = c.Close()
	_ = up.Close()
	<-done
}

func (s *server) serve(ln net.Listener) error {
	for {
		c, err := ln.Accept()
		if err != nil {
			if ne, ok := err.(net.Error); ok && ne.Timeout() {
				continue
			}
			return err
		}
		ip := "?"
		if ap, err := netip.ParseAddrPort(c.RemoteAddr().String()); err == nil {
			ip = ap.Addr().Unmap().String() // "::ffff:1.2.3.4" -> "1.2.3.4"
		}
		if !s.acquire(ip) {
			_ = c.Close()
			continue
		}
		go s.handle(c, ip)
	}
}

func newServer(sshPort, listenPort int, response string, pmapDir string) *server {
	return &server{
		sshPort: sshPort, listenPort: listenPort, response: []byte(response), pmapDir: pmapDir,
		allowed: [2]string{fmt.Sprintf("%s:%d", sshHost, sshPort), fmt.Sprintf("localhost:%d", sshPort)},
		perIP:   map[string]int{},
	}
}

func main() {
	if len(os.Args) > 1 {
		switch os.Args[1] {
		case "-version", "--version":
			fmt.Println("zumogo " + version)
			return
		case "-h", "--help", "-help":
			fmt.Fprintf(os.Stderr, "zumogo %s: WebSocket -> SSH (como PDirect, en Go)\nUso: %s [SSH_PORT=22] [LISTEN_PORT=80] [101|200]\n", version, os.Args[0])
			return
		}
	}
	arg := func(i int) string {
		if len(os.Args) > i {
			return os.Args[i]
		}
		return ""
	}
	sshPort, err1 := parsePort(arg(1), 22)
	listenPort, err2 := parsePort(arg(2), 80)
	if err1 != nil || err2 != nil {
		fmt.Fprintf(os.Stderr, "Uso: %s [SSH_PORT] [LISTEN_PORT] [101|200]\n", os.Args[0])
		os.Exit(2)
	}
	modo := arg(3)
	if modo == "" {
		modo = os.Getenv("PDIRECT_MODE")
	}
	code := 101
	if modo == "200" {
		code = 200
	}
	pmap := os.Getenv("ZUMOGO_PMAP")
	if pmap == "" {
		pmap = "/run/zumo/pmap"
	}
	_ = os.MkdirAll(filepath.Dir(pmap), 0o755)
	_ = os.MkdirAll(pmap, 0o755)

	s := newServer(sshPort, listenPort, buildResponse(code, os.Getenv), pmap)
	ln, err := net.Listen("tcp", ":"+strconv.Itoa(listenPort)) // dual-stack IPv6 + IPv4
	if err != nil {
		fmt.Fprintln(os.Stderr, "No se pudo abrir el puerto de escucha:", err)
		os.Exit(1)
	}
	fmt.Fprintf(os.Stderr, "Zumo Go %s en :%d; SSH local %s:%d\n", version, listenPort, sshHost, sshPort)
	if err := s.serve(ln); err != nil {
		fmt.Fprintln(os.Stderr, "Zumo Go: error del listener:", err)
		os.Exit(1)
	}
}
