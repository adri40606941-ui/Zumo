// bhttp-shim: adaptador entre DTunnel y el servidor BHTTP 2.4.x.
// DTunnel descarga con el modo 2 (cabecera con tamano, sin cuerpo); el servidor solo
// entiende el modo 3 (lote). Este adaptador traduce 2 -> 3 (1 respuesta) y re-cifra el
// bloque devuelto. Todo lo demas se reenvia sin tocar.
package main

import (
	"crypto/sha256"
	"encoding/binary"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"time"
)

func keystream(sid []byte, mode byte, seq uint64, resp byte, data []byte) []byte {
	out := make([]byte, len(data))
	var blk [30]byte
	copy(blk[0:16], sid)
	blk[16] = mode
	binary.BigEndian.PutUint64(blk[17:25], seq)
	blk[25] = resp
	for i, c := 0, uint32(0); i < len(data); i, c = i+32, c+1 {
		binary.BigEndian.PutUint32(blk[26:30], c)
		k := sha256.Sum256(blk[:])
		n := len(data) - i
		if n > 32 {
			n = 32
		}
		for j := 0; j < n; j++ {
			out[i+j] = data[i+j] ^ k[j]
		}
	}
	return out
}

func readFrame(r io.Reader) (byte, []byte, error) {
	var h [5]byte
	if _, err := io.ReadFull(r, h[:]); err != nil {
		return 0, nil, err
	}
	n := binary.BigEndian.Uint32(h[1:])
	if n > 1<<20 {
		return 0, nil, fmt.Errorf("respuesta demasiado grande: %d", n)
	}
	body := make([]byte, n)
	if _, err := io.ReadFull(r, body); err != nil {
		return 0, nil, err
	}
	return h[0], body, nil
}

func writeFrame(w io.Writer, st byte, body []byte) error {
	buf := make([]byte, 5+len(body))
	buf[0] = st
	binary.BigEndian.PutUint32(buf[1:5], uint32(len(body)))
	copy(buf[5:], body)
	_, err := w.Write(buf)
	return err
}

func pipe(a, b net.Conn) {
	done := make(chan struct{}, 2)
	cp := func(dst, src net.Conn) { io.Copy(dst, src); done <- struct{}{} }
	go cp(a, b)
	go cp(b, a)
	<-done
}

func dial(backend string) (net.Conn, error) {
	b, err := net.DialTimeout("tcp", backend, 5*time.Second)
	if err != nil {
		log.Printf("backend: %v", err)
		return nil, err
	}
	if tb, ok := b.(*net.TCPConn); ok {
		tb.SetNoDelay(true)
	}
	return b, nil
}

func handle(c net.Conn, backend string, verbose bool) {
	defer c.Close()
	if tc, ok := c.(*net.TCPConn); ok {
		tc.SetNoDelay(true)
		tc.SetKeepAlive(true)
		tc.SetKeepAlivePeriod(30 * time.Second)
	}
	for {
		var h [29]byte
		if _, err := io.ReadFull(c, h[:]); err != nil {
			return
		}
		mode := h[0]
		sid := h[1:17]
		seq := binary.BigEndian.Uint64(h[17:25])
		ln := binary.BigEndian.Uint32(h[25:29])

		switch mode {
		case 2: // descarga simple -> lote de 1
			body := make([]byte, 6)
			binary.BigEndian.PutUint32(body[0:4], ln)
			binary.BigEndian.PutUint16(body[4:6], 1)
			b, err := dial(backend)
			if err != nil {
				return
			}
			var nh [29]byte
			copy(nh[:], h[:])
			nh[0] = 3
			binary.BigEndian.PutUint32(nh[25:29], 6)
			if _, err := b.Write(append(nh[:], keystream(sid, 3, seq, 0, body)...)); err != nil {
				b.Close()
				return
			}
			st, resp, err := readFrame(b)
			b.Close()
			if err != nil {
				return
			}
			if st == 2 && len(resp) >= 4 {
				dl := binary.BigEndian.Uint32(resp[:4])
				if int(dl) <= len(resp)-4 {
					plain := keystream(sid, 3, seq, 1, resp[4:4+dl])
					copy(resp[4:4+dl], keystream(sid, 2, seq, 1, plain))
				}
			}
			if verbose {
				log.Printf("descarga seq=%d tam=%d -> estado %d, %d bytes", seq, ln, st, len(resp))
			}
			if writeFrame(c, st, resp) != nil {
				return
			}
		case 1: // subida / apertura: una sola respuesta
			buf := make([]byte, 29+int(ln))
			copy(buf, h[:])
			if ln > 0 {
				if _, err := io.ReadFull(c, buf[29:]); err != nil {
					return
				}
			}
			b, err := dial(backend)
			if err != nil {
				return
			}
			if _, err := b.Write(buf); err != nil {
				b.Close()
				return
			}
			st, resp, err := readFrame(b)
			b.Close()
			if err != nil || writeFrame(c, st, resp) != nil {
				return
			}
		default: // cualquier otra cosa: tunel transparente
			buf := make([]byte, 29+int(ln))
			copy(buf, h[:])
			if ln > 0 {
				if _, err := io.ReadFull(c, buf[29:]); err != nil {
					return
				}
			}
			b, err := dial(backend)
			if err != nil {
				return
			}
			defer b.Close()
			if _, err := b.Write(buf); err != nil {
				return
			}
			pipe(c, b)
			return
		}
	}
}

func main() {
	listen := flag.String("listen", "0.0.0.0:8001", "direccion publica (la que usa la app)")
	backend := flag.String("backend", "127.0.0.1:18001", "servidor BHTTP real")
	verbose := flag.Bool("v", false, "mostrar cada descarga")
	flag.Parse()
	l, err := net.Listen("tcp", *listen)
	if err != nil {
		log.Fatal(err)
	}
	log.Printf("bhttp-shim %s -> %s", *listen, *backend)
	for {
		c, err := l.Accept()
		if err != nil {
			continue
		}
		go handle(c, *backend, *verbose)
	}
}
