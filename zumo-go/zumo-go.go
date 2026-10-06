// zumo-go: proxy "WebSocket" en el puerto 80 que reenvía al SSH local (127.0.0.1:22).
//
// Es la versión en Go de pdirect-c: recibe el pedido HTTP del cliente, contesta el
// banner 101 (o 200) y a partir de ahí relayea los bytes crudos contra el SSH. Usa el
// MISMO archivo de entorno que pdirect (/etc/zumo/pdirect.env), así el banner y el color
// son idénticos a los del pdirect. Anota la IP real en /run/zumo/pmap/<puerto> para que el
// limitador la muestre, igual que pdirect.
//
// Uso:  zumo-go [SSH_PORT=22] [LISTEN_PORT=80] [MODO=101|200]
package main

import (
	"bytes"
	"fmt"
	"io"
	"net"
	"os"
	"strconv"
	"strings"
)

const sshHost = "127.0.0.1"
const pmapDir = "/run/zumo/pmap"
const maxHeader = 16384

var (
	sshPort     = 22
	listenPort  = 80
	gResponse   []byte
	allowedIP   string // 127.0.0.1:<sshPort>
	allowedName string // localhost:<sshPort>
)

// Arma la respuesta HTTP (el banner que ve la app), con la misma lógica y prioridad
// que pdirect-c: PDIRECT_RESPONSE cruda > PDIRECT_BANNER+color > banner ZUMO por defecto.
func buildResponse(code int) {
	if raw := os.Getenv("PDIRECT_RESPONSE"); raw != "" {
		gResponse = []byte(raw)
		return
	}
	banner := os.Getenv("PDIRECT_BANNER")
	if banner == "" {
		if code == 200 {
			gResponse = []byte("HTTP/1.1 200 <font color=\"yellow\"><b>ZUMO</b></font>\r\nContent-Length: 0\r\n\r\n" +
				"HTTP/1.1 200 Conexion Exitosa\r\n\r\n")
		} else {
			gResponse = []byte("HTTP/1.1 101 <font color=\"yellow\"><b>ZUMO</b></font>\r\n\r\n" +
				"HTTP/1.1 101 Conexion Exitosa\r\n\r\n")
		}
		return
	}
	// Sanitizar igual que pdirect: banner sin caracteres de control; color alfanumérico o '#'.
	var b strings.Builder
	for _, r := range banner {
		if r >= 32 && r != 127 && b.Len() < 79 {
			b.WriteRune(r)
		}
	}
	co := strings.Builder{}
	for _, r := range os.Getenv("PDIRECT_COLOR") {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '#' {
			if co.Len() < 31 {
				co.WriteRune(r)
			}
		}
	}
	color := co.String()
	if color == "" {
		color = "yellow"
	}
	if code == 200 {
		gResponse = []byte(fmt.Sprintf(
			"HTTP/1.1 200 <font color=\"%s\"><b>%s</b></font>\r\nContent-Length: 0\r\n\r\n"+
				"HTTP/1.1 200 Conexion Exitosa\r\n\r\n", color, b.String()))
	} else {
		gResponse = []byte(fmt.Sprintf(
			"HTTP/1.1 101 <font color=\"%s\"><b>%s</b></font>\r\n\r\n"+
				"HTTP/1.1 101 Conexion Exitosa\r\n\r\n", color, b.String()))
	}
}

func parsePort(s string, def int) int {
	if s == "" {
		return def
	}
	v, err := strconv.Atoi(s)
	if err != nil || v < 1 || v > 65535 {
		return -1
	}
	return v
}

// ¿El X-Real-Host (si viene) apunta a nuestro SSH local? Si no viene, se acepta.
func validHost(header string) bool {
	for _, line := range strings.Split(header, "\r\n") {
		i := strings.IndexByte(line, ':')
		if i < 0 {
			continue
		}
		if strings.EqualFold(strings.TrimSpace(line[:i]), "X-Real-Host") {
			v := strings.TrimSpace(line[i+1:])
			return strings.EqualFold(v, allowedIP) || strings.EqualFold(v, allowedName)
		}
	}
	return true
}

func headerPresent(header, name string) bool {
	return strings.Contains(strings.ToLower(header), strings.ToLower(name))
}

// Escribe "puerto local del upstream -> IP real" para el limitador. Devuelve el path (o "").
func pmapWrite(localPort int, ip string) string {
	if localPort <= 0 {
		return ""
	}
	p := fmt.Sprintf("%s/%d", pmapDir, localPort)
	if f, err := os.Create(p); err == nil {
		f.WriteString(ip + "\n")
		f.Close()
		return p
	}
	return ""
}

func handle(client net.Conn) {
	defer client.Close()

	// Leer las cabeceras hasta el primer \r\n\r\n (con tope anti-abuso).
	buf := make([]byte, 0, 2048)
	tmp := make([]byte, 2048)
	idx := -1
	for {
		n, err := client.Read(tmp)
		if n > 0 {
			buf = append(buf, tmp[:n]...)
			if idx = bytes.Index(buf, []byte("\r\n\r\n")); idx >= 0 {
				break
			}
			if len(buf) >= maxHeader {
				client.Write([]byte("HTTP/1.1 431 Request Header Fields Too Large\r\nConnection: close\r\n\r\n"))
				return
			}
		}
		if err != nil {
			return
		}
	}
	hlen := idx + 4
	header := string(buf[:hlen])
	leftover := buf[hlen:]

	if !validHost(header) {
		client.Write([]byte("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n"))
		return
	}
	split := headerPresent(header, "X-Split")

	// Conectar al SSH local.
	upstream, err := net.Dial("tcp", fmt.Sprintf("%s:%d", sshHost, sshPort))
	if err != nil {
		return
	}
	defer upstream.Close()

	// Anotar la IP real para el limitador (clave = puerto local de esta conexión al SSH).
	var pmapPath string
	if la, ok := upstream.LocalAddr().(*net.TCPAddr); ok {
		ip := client.RemoteAddr().String()
		if h, _, e := net.SplitHostPort(ip); e == nil {
			ip = h
		}
		pmapPath = pmapWrite(la.Port, ip)
	}
	if pmapPath != "" {
		defer os.Remove(pmapPath)
	}

	// Modo X-Split: se descarta el segmento partido y NO se reenvía al SSH.
	if split {
		if len(leftover) == 0 {
			// esperar el segundo segmento y descartarlo
			client.Read(tmp)
		}
		leftover = nil
	}

	// Contestar el banner (101/200) y arrancar el relay.
	if _, err := client.Write(gResponse); err != nil {
		return
	}
	if len(leftover) > 0 {
		if _, err := upstream.Write(leftover); err != nil {
			return
		}
	}

	done := make(chan struct{}, 2)
	go func() { io.Copy(upstream, client); done <- struct{}{} }()
	go func() { io.Copy(client, upstream); done <- struct{}{} }()
	<-done
}

func main() {
	args := os.Args[1:]
	if len(args) > 0 {
		sshPort = parsePort(args[0], 22)
	}
	if len(args) > 1 {
		listenPort = parsePort(args[1], 80)
	}
	if sshPort < 0 || listenPort < 0 {
		fmt.Fprintf(os.Stderr, "Uso: %s [SSH_PORT] [LISTEN_PORT] [101|200]\n", os.Args[0])
		os.Exit(2)
	}
	mode := os.Getenv("PDIRECT_MODE")
	if len(args) > 2 && args[2] != "" {
		mode = args[2]
	}
	if mode == "200" {
		buildResponse(200)
	} else {
		buildResponse(101)
	}

	allowedIP = fmt.Sprintf("%s:%d", sshHost, sshPort)
	allowedName = fmt.Sprintf("localhost:%d", sshPort)
	os.MkdirAll(pmapDir, 0755)

	ln, err := net.Listen("tcp", fmt.Sprintf("0.0.0.0:%d", listenPort))
	if err != nil {
		fmt.Fprintf(os.Stderr, "No se pudo abrir el puerto %d: %v\n", listenPort, err)
		os.Exit(1)
	}
	fmt.Printf("zumo-go: escuchando en :%d -> %s:%d\n", listenPort, sshHost, sshPort)
	for {
		c, err := ln.Accept()
		if err != nil {
			continue
		}
		if tc, ok := c.(*net.TCPConn); ok {
			tc.SetNoDelay(true)
			tc.SetKeepAlive(true)
		}
		go handle(c)
	}
}
