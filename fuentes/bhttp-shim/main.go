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
	"sync"
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

// ---- Espera larga en la bajada (modo 3) -------------------------------------------------------
// La app pregunta "¿hay datos?" en lotes. El servidor real contesta vacío al instante, así que
// con la conexión en reposo la app repetía el pedido decenas de veces por segundo (batería y datos).
// Acá, si el lote vuelve vacío, el adaptador sigue preguntando al servidor (en la propia VPS, sin
// costo de red) hasta que aparezcan datos o se cumpla la espera, y recién ahí contesta a la app.
// El servidor real pide números de secuencia consecutivos, por eso cada sesión lleva su propio
// contador hacia el servidor (bseq) y se re-cifran las respuestas con el número que usó la app.
type sesion struct {
	mu     sync.Mutex
	bseq   uint64 // próxima secuencia hacia el servidor real
	ultimo uint64 // última secuencia de la app ya contestada
	hay    bool
	cache  [][]byte // respuestas ya armadas de esa secuencia (para repetirlas si la app reintenta)
	visto  time.Time
}

var (
	sesMu sync.Mutex
	ses   = map[string]*sesion{}
)

func sesionDe(sid []byte, crear bool) *sesion {
	sesMu.Lock()
	defer sesMu.Unlock()
	s := ses[string(sid)]
	if s == nil && crear {
		s = &sesion{}
		ses[string(sid)] = s
	}
	if s != nil {
		s.visto = time.Now()
	}
	return s
}

func olvidar(sid []byte) {
	sesMu.Lock()
	delete(ses, string(sid))
	sesMu.Unlock()
}

// limpia sesiones que la app dejó de usar sin cerrarlas (el servidor real también las vence)
func limpiador() {
	for range time.Tick(time.Minute) {
		sesMu.Lock()
		for k, s := range ses {
			if time.Since(s.visto) > 10*time.Minute {
				delete(ses, k)
			}
		}
		sesMu.Unlock()
	}
}

// pedirLote hace un pedido de bajada (modo 3) al servidor real por la conexión b y lee sus "cant"
// respuestas. Devuelve las respuestas y si alguna trae datos o un estado distinto de 2 (error/fin).
func pedirLote(b net.Conn, sid []byte, bseq uint64, tam uint32, cant uint16) ([][]byte, []byte, bool, error) {
	body := make([]byte, 6)
	binary.BigEndian.PutUint32(body[0:4], tam)
	binary.BigEndian.PutUint16(body[4:6], cant)
	var h [29]byte
	h[0] = 3
	copy(h[1:17], sid)
	binary.BigEndian.PutUint64(h[17:25], bseq)
	binary.BigEndian.PutUint32(h[25:29], 6)
	if _, err := b.Write(append(h[:], keystream(sid, 3, bseq, 0, body)...)); err != nil {
		return nil, nil, false, err
	}
	var estados []byte
	cuerpos := make([][]byte, 0, cant)
	util := false
	for k := 0; k < int(cant); k++ {
		st, resp, err := readFrame(b)
		if err != nil {
			return nil, nil, false, err
		}
		estados = append(estados, st)
		cuerpos = append(cuerpos, resp)
		if st != 2 || len(resp) < 4 || binary.BigEndian.Uint32(resp[:4]) > 0 {
			util = true
		}
	}
	return cuerpos, estados, util, nil
}

// bajadaLarga atiende un pedido de bajada de la app (secuencia cs) esperando hasta "espera" a que haya datos.
// Devuelve false si hay que cortar la conexión con la app.
func bajadaLarga(c net.Conn, bc *net.Conn, backend string, sid []byte, cs uint64, tam uint32, cant uint16, espera time.Duration, verbose bool) bool {
	s := sesionDe(sid, cs == 0)
	if s == nil || cant == 0 || cant > 64 {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	// la app repite el mismo pedido si no le llegó la respuesta: se le da la misma, sin tocar al servidor
	if s.hay && cs == s.ultimo {
		for _, f := range s.cache {
			if _, err := c.Write(f); err != nil {
				return false
			}
		}
		return true
	}
	limite := time.Now().Add(espera)
	var cuerpos [][]byte
	var estados []byte
	for {
		if *bc == nil {
			b, err := dial(backend)
			if err != nil {
				return false
			}
			*bc = b
		}
		var util bool
		var err error
		cuerpos, estados, util, err = pedirLote(*bc, sid, s.bseq, tam, cant)
		if err != nil {
			// el servidor real cierra la conexión cada tantos pedidos: se abre otra y se repite una vez
			(*bc).Close()
			*bc = nil
			b, e2 := dial(backend)
			if e2 != nil {
				return false
			}
			*bc = b
			cuerpos, estados, util, err = pedirLote(*bc, sid, s.bseq, tam, cant)
			if err != nil {
				(*bc).Close()
				*bc = nil
				return false
			}
		}
		s.bseq += uint64(cant)
		if util || time.Now().After(limite) {
			break
		}
		time.Sleep(8 * time.Millisecond)
	}
	// re-cifrar con los números de secuencia de la app
	s.cache = s.cache[:0]
	for k, resp := range cuerpos {
		if estados[k] == 2 && len(resp) >= 4 {
			dl := binary.BigEndian.Uint32(resp[:4])
			if dl > 0 && int(dl) <= len(resp)-4 {
				plain := keystream(sid, 3, s.bseq-uint64(cant)+uint64(k), 1, resp[4:4+dl])
				copy(resp[4:4+dl], keystream(sid, 3, cs+uint64(k), 1, plain))
			}
		}
		buf := make([]byte, 5+len(resp))
		buf[0] = estados[k]
		binary.BigEndian.PutUint32(buf[1:5], uint32(len(resp)))
		copy(buf[5:], resp)
		s.cache = append(s.cache, buf)
	}
	s.ultimo, s.hay = cs, true
	if verbose {
		log.Printf("bajada larga cs=%d bseq=%d", cs, s.bseq-uint64(cant))
	}
	for _, f := range s.cache {
		if _, err := c.Write(f); err != nil {
			return false
		}
	}
	return true
}

// transparente reenvía este pedido y todo lo que siga por la misma conexión, sin tocarlo.
func transparente(c net.Conn, h []byte, ln uint32, backend string) bool {
	buf := make([]byte, 29+int(ln))
	copy(buf, h)
	if ln > 0 {
		if _, err := io.ReadFull(c, buf[29:]); err != nil {
			return false
		}
	}
	b, err := dial(backend)
	if err != nil {
		return false
	}
	defer b.Close()
	if _, err := b.Write(buf); err != nil {
		return false
	}
	pipe(c, b)
	return true
}

func handle(c net.Conn, backend string, verbose bool, espera time.Duration) {
	defer c.Close()
	var bc net.Conn // conexión al servidor real que se reusa entre pedidos de bajada larga
	defer func() {
		if bc != nil {
			bc.Close()
		}
	}()
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
		// el largo viene de afuera: cualquiera que mande texto cualquiera al puerto (un navegador, un escáner)
		// lo leería como un largo enorme y el adaptador reservaría esa memoria. La app sube de a 16 KB.
		if ln > 1<<20 {
			return
		}

		switch mode {
		case 3: // bajada en lote: con espera larga si se puede, si no túnel transparente
			if espera > 0 && ln == 6 {
				var cb [6]byte
				if _, err := io.ReadFull(c, cb[:]); err != nil {
					return
				}
				cl := keystream(sid, 3, seq, 0, cb[:])
				tam := binary.BigEndian.Uint32(cl[0:4])
				cant := binary.BigEndian.Uint16(cl[4:6])
				if sesionDe(sid, false) != nil || seq == 0 {
					if !bajadaLarga(c, &bc, backend, sid, seq, tam, cant, espera, verbose) {
						return
					}
					continue
				}
				// secuencia que no empieza en 0 y sesión desconocida: se deja pasar tal cual
				b, err := dial(backend)
				if err != nil {
					return
				}
				defer b.Close()
				if _, err := b.Write(append(append([]byte{}, h[:]...), cb[:]...)); err != nil {
					return
				}
				pipe(c, b)
				return
			}
			if !transparente(c, h[:], ln, backend) {
				return
			}
			return
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
			if mode == 4 {
				olvidar(sid)
			}
			transparente(c, h[:], ln, backend)
			return
		}
	}
}

func main() {
	listen := flag.String("listen", "0.0.0.0:8001", "direccion publica (la que usa la app)")
	backend := flag.String("backend", "127.0.0.1:18001", "servidor BHTTP real")
	verbose := flag.Bool("v", false, "mostrar cada descarga")
	esperaMs := flag.Int("espera-ms", 1000, "cuánto espera (ms) un pedido de bajada vacío antes de contestar; 0 = contesta al instante como antes")
	flag.Parse()
	go limpiador()
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
		go handle(c, *backend, *verbose, time.Duration(*esperaMs)*time.Millisecond)
	}
}
