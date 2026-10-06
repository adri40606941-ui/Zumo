"""Funciones del bot para la VPS "centro": respaldo cifrado, clave de firma y compilación local.

Se mezcla en la clase Bot de zumo-bot.py (usa self.tg, self.estado, self.mostrar, self.pedir...).
"""
import os
import subprocess
import threading
import time
from datetime import datetime

import compilar
import local
import respaldo

ENV = "/etc/zumo/bot.env"                 # zumo-bot.py lo ajusta al arrancar
ULTIMO = os.environ.get("ZUMO_RESP_ULTIMO", "/etc/zumo/respaldo.ultimo")
MENU_RESP = [[("◂ Menú", "menu")]]


def fijar_env(clave, valor, ruta=None):
    """Pone CLAVE=valor en bot.env (reemplaza la línea si ya estaba). Archivo 0600."""
    ruta = ruta or ENV
    lineas = []
    try:
        with open(ruta, encoding="utf-8") as f:
            lineas = [l.rstrip("\n") for l in f if not l.startswith(clave + "=")]
    except FileNotFoundError:
        pass
    lineas.append(f"{clave}={valor}")
    tmp = ruta + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas) + "\n")
    os.replace(tmp, ruta)


def reiniciar_bot():
    subprocess.Popen(["bash", "-c", "sleep 2; systemctl restart zumo-bot"], start_new_session=True)


class CentroMixin:
    local_activo = False          # True si el bot compila en esta VPS
    leer_env_fn = None            # lo pone zumo-bot.py

    # ------------------------------------------------------------------ pantallas
    def pantalla_resp(self, chat, mid, aviso=""):
        env = self.leer_env_fn() if self.leer_env_fn else {}
        c = respaldo.clave_firma()
        txt = (aviso + "\n\n" if aviso else "") + "💾 Respaldo y clave de firma\n\n"
        txt += "Contraseña del respaldo: " + ("✅ puesta" if env.get("RESPALDO_PASS") else "⚠️ falta (ponela para respaldar)") + "\n"
        txt += "Respaldo automático diario: " + ("sí" if env.get("RESPALDO_PASS") and env.get("RESPALDO_AUTO") != "0" else "no") + "\n"
        if c:
            h = respaldo.huella_clave()
            txt += "Clave de firma de la app: ✅ en esta VPS" + (f"\nHuella: {h}" if h else "") + "\n"
        else:
            txt += "Clave de firma de la app: ⚠️ no hay en esta VPS\n"
        txt += "\nEl respaldo se manda cifrado a este chat. Sin la contraseña no se puede abrir: guardala aparte."
        filas = [[("💾 Respaldar ahora", "rnow")],
                 [("🔒 Contraseña del respaldo", "rpass"), ("♻️ Restaurar", "rrest")],
                 [("📥 Importar clave de firma", "kimp"), ("📤 Exportar clave", "kexp")]]
        if not c and getattr(self, "gh", None) and not self.local_activo:
            filas.append([("☁️ Traer la clave de GitHub", "ktraer")])
        if not c:
            filas.append([("🆕 Crear clave nueva", "knew")])
        filas.append([("◂ Menú", "menu")])
        self.mostrar(chat, mid, txt, filas)

    # ------------------------------------------------------------------- botones
    def boton_resp(self, chat, mid, acc):
        """Devuelve True si manejó el botón."""
        if acc == "resp":
            self.estado.pop(chat, None)
            self.pantalla_resp(chat, mid)
        elif acc == "rnow":
            self.respaldar(chat, manual=True)
        elif acc == "rpass":
            self.pedir(chat, "🔒 Escribí la contraseña del respaldo (mínimo 8 caracteres). La borro del chat apenas la leo.\n"
                             "Guardala en otro lado: sin ella no se puede restaurar.", "r_pass")
        elif acc == "rrest":
            self.pedir(chat, "♻️ Mandame el archivo de respaldo (zumo-respaldo-….enc) como documento.\n"
                             "⚠️ Reemplaza los archivos de /etc/zumo con los del respaldo.", "r_archivo")
        elif acc == "kimp":
            self.pedir(chat, "📥 Mandame el archivo clave-firma.enc como documento.", "k_archivo")
        elif acc == "kexp":
            if not respaldo.clave_firma():
                return self.pantalla_resp(chat, mid, "No hay clave de firma en esta VPS.") or True
            self.pedir(chat, "📤 Elegí una contraseña para proteger el archivo (mínimo 8 caracteres). Mandame la contraseña:", "kexp_clave")
        elif acc == "ktraer":
            if not getattr(self, "gh", None):
                return self.pantalla_resp(chat, mid, "Falta GITHUB_TOKEN en bot.env.") or True
            self.mostrar(chat, mid, "☁️ Traer la clave de firma de GitHub\n\nHace una compilación en GitHub, saca la clave actual cifrada y la guarda en esta VPS. "
                                    "La app no cambia. Sirve para llevar la clave a una VPS nueva.",
                         [[("✅ Hacerlo ahora", "ktraer_si"), ("✖ No", "resp")]])
        elif acc == "ktraer_si":
            self.compilar_app(chat, asegurar="traer")
        elif acc == "knew":
            self.mostrar(chat, mid, "🆕 Crear clave de firma nueva\n\n⚠️ Solo sirve si la app todavía no está en manos de clientes: "
                                    "los que ya la tienen instalada tendrían que desinstalarla para instalar la nueva (y se pierde el vínculo por Android ID). "
                                    "Si ya publicaste la app, importá la clave de siempre.",
                         [[("✅ Crear igual", "knew_si"), ("✖ No", "resp")]])
        elif acc == "knew_si":
            try:
                respaldo.crear_clave_firma()
                self.pantalla_resp(chat, mid, "✅ Clave nueva creada. Hacé un respaldo ahora.")
            except respaldo.ErrorRespaldo as e:
                self.pantalla_resp(chat, mid, "⚠️ " + str(e))
        else:
            return False
        return True

    # ------------------------------------------------------------ texto y documentos
    def texto_resp(self, chat, e, paso, t):
        """Devuelve True si manejó el paso."""
        if paso == "r_pass":
            self.borrar_entrada(chat, e)
            if len(t) < 8:
                self.tg.mensaje(chat, "⚠️ Muy corta: mínimo 8 caracteres. Probá de nuevo:", [[("✖ Cancelar", "resp")]])
                return True
            fijar_env("RESPALDO_PASS", t)
            self.estado.pop(chat, None)
            self.tg.mensaje(chat, "✅ Contraseña guardada. Ahora podés respaldar.", [[("💾 Respaldar ahora", "rnow")], [("◂ Respaldo", "resp")]])
            return True
        if paso == "r_clave":
            self.borrar_entrada(chat, e)
            datos = e.get("datos")
            self.estado.pop(chat, None)
            try:
                hechos = respaldo.restaurar(datos, t)
            except respaldo.ErrorRespaldo as ex:
                self.tg.mensaje(chat, "⚠️ " + str(ex), [[("♻️ Reintentar", "rrest")], [("◂ Respaldo", "resp")]])
                return True
            self.tg.mensaje(chat, f"✅ Restaurado: {len(hechos)} archivo(s) en /etc/zumo. Reinicio el bot para que tome la configuración…")
            reiniciar_bot()
            return True
        if paso == "ki_clave":
            self.borrar_entrada(chat, e)
            datos = e.get("datos")
            self.estado.pop(chat, None)
            try:
                jks, pw = compilar.abrir_clave_exportada(datos, t)
            except compilar.ErrorGitHub as ex:
                self.tg.mensaje(chat, "⚠️ " + str(ex), [[("📥 Reintentar", "kimp")], [("◂ Respaldo", "resp")]])
                return True
            if respaldo.clave_firma():
                anterior = respaldo.FIRMA + ".ant-" + time.strftime("%Y%m%d%H%M%S")
                os.rename(respaldo.FIRMA, anterior)
            respaldo.guardar_clave_firma(jks, pw)
            self.tg.mensaje(chat, "✅ Clave de firma importada. Las próximas compilaciones salen firmadas con ella.\n"
                                  f"Huella: {respaldo.huella_clave() or '(no se pudo calcular)'}",
                            [[("💾 Respaldar ahora", "rnow")], [("◂ Respaldo", "resp")]])
            return True
        if paso == "kexp_clave":
            self.borrar_entrada(chat, e)
            self.estado.pop(chat, None)
            try:
                enc = respaldo.exportar_clave(t)
            except respaldo.ErrorRespaldo as ex:
                self.tg.mensaje(chat, "⚠️ " + str(ex), [[("◂ Respaldo", "resp")]])
                return True
            self.tg.documento(chat, "clave-firma.enc", enc, "Clave de firma cifrada. Para otra VPS: 💾 Respaldo → Importar clave de firma.")
            self.tg.mensaje(chat, "✅ Listo. Guardá la contraseña aparte.", [[("◂ Respaldo", "resp")]])
            return True
        return False

    def documento_resp(self, chat, msg):
        """Archivo recibido: lo pide el paso r_archivo o k_archivo. Devuelve True si lo usó."""
        e = self.estado.get(chat)
        d = msg.get("document")
        if not e or not d or e.get("paso") not in ("r_archivo", "k_archivo"):
            return False
        if d.get("file_size", 0) > 20 * 1024 * 1024:
            self.tg.mensaje(chat, "⚠️ El archivo es demasiado grande (máx. 20 MB).", [[("◂ Respaldo", "resp")]])
            return True
        datos = self.tg.bajar(d["file_id"])
        sig = "r_clave" if e["paso"] == "r_archivo" else "ki_clave"
        self.estado[chat] = {"paso": sig, "datos": datos}
        self.tg.mensaje(chat, "🔑 Ahora escribí la contraseña de ese archivo (la borro del chat):", [[("✖ Cancelar", "resp")]])
        return True

    # ------------------------------------------------------------------- respaldo
    def respaldar(self, chat, manual=False):
        env = self.leer_env_fn() if self.leer_env_fn else {}
        pw = env.get("RESPALDO_PASS", "")
        if not pw:
            return self.tg.mensaje(chat, "⚠️ Primero poné la contraseña del respaldo.", [[("🔒 Contraseña del respaldo", "rpass")]])
        try:
            blob = respaldo.crear(pw)
        except respaldo.ErrorRespaldo as ex:
            return self.tg.mensaje(chat, "⚠️ " + str(ex), [[("◂ Respaldo", "resp")]])
        nombre = "zumo-respaldo-" + datetime.now().strftime("%Y%m%d-%H%M") + ".enc"
        self.tg.documento(chat, nombre, blob, "💾 Respaldo cifrado de Zumo. Guardalo y conservá la contraseña aparte.")
        if manual:
            self.tg.mensaje(chat, "✅ Respaldo enviado.", [[("◂ Respaldo", "resp")]])

    def respaldo_diario(self):
        """Hilo: una vez por día manda el respaldo a cada admin (si hay contraseña puesta)."""
        while True:
            try:
                env = self.leer_env_fn() if self.leer_env_fn else {}
                if env.get("RESPALDO_PASS") and env.get("RESPALDO_AUTO") != "0":
                    try:
                        ult = os.path.getmtime(ULTIMO)
                    except OSError:
                        ult = 0
                    if time.time() - ult > 23.5 * 3600:
                        for a in sorted(self.admins):
                            self.respaldar(a)
                        with open(ULTIMO, "w") as f:
                            f.write(str(int(time.time())))
            except Exception as ex:
                print("zumo-bot: respaldo diario:", ex, flush=True)
            time.sleep(3600)

    # ----------------------------------------------------------- compilación local
    def _compilar_local(self, chat, lista):
        import servidores as srv
        mid = self.tg.mensaje(chat, "🔨 Compilando en esta VPS… 0 min")
        ult = {"txt": ""}

        def progreso(minutos, tarea):
            txt = f"🔨 Compilando en esta VPS… {minutos} min" + (f"\n⚙️ {tarea}" if tarea else "")
            if txt != ult["txt"]:
                ult["txt"] = txt
                self.tg.editar(chat, mid, txt)
        try:
            r = local.compilar(srv.a_texto(lista) if lista else "", progreso)
        except local.ErrorLocal as ex:
            return self.tg.mensaje(chat, f"⚠️ {ex}", [[("📱 App Android", "app")]])
        if r["aviso"]:
            self.tg.mensaje(chat, "ℹ️ " + r["aviso"])
        if r["ok"]:
            self.tg.documento(chat, "zumo-vpn.apk", r["apk"], f"✅ Compilación {r['numero']} · {len(lista)} servidor(es)")
            self.tg.mensaje(chat, "✅ Listo. Instalá el APK encima de la versión anterior: se actualiza sin perder nada.",
                            [[("📱 App Android", "app")], [("◂ Menú", "menu")]])
        else:
            self.tg.mensaje(chat, f"❌ La compilación falló.\n{r['log']}", [[("📱 App Android", "app")]])
