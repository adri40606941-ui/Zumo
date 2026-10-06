#!/bin/bash
# zumo-lib.sh — operaciones compartidas sobre la base de usuarios.
#
# Formato del DB (una línea por usuario):   usuario:limite:vencimiento
#
# Esta librería es la ÚNICA fuente de verdad para tocar ese archivo desde la
# terminal. panel.sh la hace 'source'. Todas las escrituras toman el lock
# (/etc/zumo/usuarios.lock), así que nunca se pisan entre sí ni dejan el DB corrupto.
#
# Todas las escrituras son atómicas (archivo temporal + mv) y bajo flock.

ZUMO_DB="${ZUMO_DB:-/etc/zumo/usuarios.db}"
ZUMO_LOCK="${ZUMO_LOCK:-/etc/zumo/usuarios.lock}"

# Escribe el DB completo de forma atómica leyendo las líneas de stdin.
# Se llama siempre con el lock ya tomado.
_zumo_atomic_write() {
	local dir tmp
	dir=$(dirname -- "$ZUMO_DB")
	tmp=$(mktemp "$dir/.usuarios.XXXXXX") || return 1
	cat > "$tmp"
	chmod 644 "$tmp"
	mv -f -- "$tmp" "$ZUMO_DB"
}

# zumo_db_add usuario limite vencimiento
zumo_db_add() {
	(
		flock -x 9
		printf '%s:%s:%s\n' "$1" "$2" "$3" >> "$ZUMO_DB"
	) 9>>"$ZUMO_LOCK"
}

# zumo_db_del usuario   — borra SOLO la fila de ese usuario (match exacto por
# campo, no por regex: un nombre con metacaracteres no borra filas ajenas).
zumo_db_del() {
	local u="$1"
	(
		flock -x 9
		[ -f "$ZUMO_DB" ] || exit 0
		awk -F: -v u="$u" '/^$/{next} $1!=u' "$ZUMO_DB" | _zumo_atomic_write
	) 9>>"$ZUMO_LOCK"
}

# zumo_db_set usuario campo valor   (campo: 2=limite, 3=vencimiento)
zumo_db_set() {
	local u="$1" campo="$2" val="$3"
	(
		flock -x 9
		[ -f "$ZUMO_DB" ] || exit 0
		awk -F: -v u="$u" -v c="$campo" -v v="$val" \
			'BEGIN{OFS=":"} /^$/{next} $1==u{$c=v} {print}' "$ZUMO_DB" | _zumo_atomic_write
	) 9>>"$ZUMO_LOCK"
}

# zumo_db_rename viejo nuevo   — renombra el usuario (campo 1) conservando el resto.
zumo_db_rename() {
	local a="$1" b="$2"
	(
		flock -x 9
		[ -f "$ZUMO_DB" ] || exit 0
		awk -F: -v a="$a" -v b="$b" \
			'BEGIN{OFS=":"} /^$/{next} $1==a{$1=b} {print}' "$ZUMO_DB" | _zumo_atomic_write
	) 9>>"$ZUMO_LOCK"
}

# zumo_db_campo usuario campo   — imprime un campo del usuario (2=limite,3=venc).
zumo_db_campo() {
	awk -F: -v u="$1" -v c="$2" '$1==u{print $c; exit}' "$ZUMO_DB" 2>/dev/null
}

# La etiqueta va en el GECOS (/etc/passwd, separado por ':'): no puede llevar
# ':' ni caracteres de control o rompería esa línea del passwd.
zumo_limpiar_etiqueta() {
	local s
	s=$(printf '%s' "${1:-cliente}" | tr -d '\000-\037\177:')
	s=${s:0:48}
	printf '%s' "${s:-cliente}"
}

# Contraseña válida: sin caracteres de control (romperían la línea de chpasswd)
# y con un tope de largo razonable.
zumo_password_valido() {
	case "$1" in
		'' | *[[:cntrl:]]*) return 1 ;;
	esac
	[ "${#1}" -le 128 ]
}

# --- Dispositivo (Android ID) --------------------------------------------------------------
# Lo maneja el servicio zumo-id (binario /usr/local/bin/zumoid, fuente en zumoid/main.go): es la
# única fuente de verdad de /etc/zumo/dispositivos.db, con su propio lock. Estas funciones solo lo llaman.
# Si el binario no está (VPS sin actualizar) no imprimen nada y devuelven error.
ZUMO_ZUMOID="${ZUMO_ZUMOID:-/usr/local/bin/zumoid}"

# zumo_disp_get usuario   — imprime "id<TAB>lock(0|1)<TAB>primero<TAB>ultimo" (epoch), o nada si no hay.
zumo_disp_get()    { [ -x "$ZUMO_ZUMOID" ] && "$ZUMO_ZUMOID" get "$1" 2>/dev/null; }
# zumo_disp_lock usuario  — vincula al ID que mandó la app (error si todavía no mandó ninguno).
zumo_disp_lock()   { [ -x "$ZUMO_ZUMOID" ] && "$ZUMO_ZUMOID" lock "$1"; }
zumo_disp_unlock() { [ -x "$ZUMO_ZUMOID" ] && "$ZUMO_ZUMOID" unlock "$1"; }
# zumo_disp_forget usuario — borra el registro (cambió de celular, o se borró el usuario).
zumo_disp_forget() { [ -x "$ZUMO_ZUMOID" ] && "$ZUMO_ZUMOID" forget "$1"; return 0; }
zumo_disp_rename() { [ -x "$ZUMO_ZUMOID" ] && "$ZUMO_ZUMOID" rename "$1" "$2"; return 0; }
# zumo_disp_last usuario  — último intento bloqueado: "epoch<TAB>evento<TAB>id".
zumo_disp_last()   { [ -x "$ZUMO_ZUMOID" ] && "$ZUMO_ZUMOID" last "$1" 2>/dev/null; }
