"""Interfaz gráfica de gestpad (GTK 4 + libadwaita).

La app lleva el motor dentro (cámara + detectores + salidas) y abre el mismo
socket de control que `gestpad ejecutar`, así que `gestpad alternar` y los
demás comandos funcionan mientras la app está abierta.
"""

import json
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import cara, config, mano  # noqa: E402
from .control import Cliente, en_hilo  # noqa: E402

APP_ID = "io.github.rrc898.Gestpad"
AJUSTES = os.path.join(config.CONFIG_DIR, "app.json")
SALIDAS = [("uinput", "uinput (Wayland y X11)"), ("autopy", "autopy (solo X11/XWayland)"),
           ("pyautogui", "pyautogui (solo X11/XWayland)"), ("nula", "Solo probar (no toca nada)")]
DESCRIPCION = {**mano.DESCRIPCION, **cara.DESCRIPCION}

CSS = """
.punto { font-size: 15px; color: alpha(currentColor, .18); }
.punto.activo { color: var(--accent-color); }
.punto.visto { color: var(--success-color); }
.fila-activa { background: color-mix(in srgb, var(--accent-bg-color) 22%, transparent); }
.vista { border-radius: 12px; background: black; }
.dato { font-variant-numeric: tabular-nums; }
.destino { font-family: monospace; font-size: 0.9em; }
"""


def _cargar_ajustes():
    try:
        with open(AJUSTES) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _guardar_ajustes(d):
    try:
        os.makedirs(config.CONFIG_DIR, exist_ok=True)
        with open(AJUSTES, "w") as f:
            json.dump(d, f, indent=1)
    except OSError:
        pass


class Ventana(Adw.ApplicationWindow):
    def __init__(self, app, cfg, prueba=False, salida=None):
        super().__init__(application=app, title="Gestpad")
        self.set_default_size(1200, 780)
        self.cfg = cfg
        self.ajustes = _cargar_ajustes()
        nombres = [p["nombre"] for p in cfg["perfiles"]]
        if self.ajustes.get("perfil") in nombres:
            cfg["general"]["perfil_inicial"] = self.ajustes["perfil"]
        self.salida_inicial = "nula" if prueba else (salida or self.ajustes.get("salida")
                                                     or cfg["general"]["salida"])
        self.motor = None
        self.parar_socket = None
        self.perfil_pintado = None
        self.filas = {}               # gesto -> (fila, punto) en la página Control
        self.filas_ref = {}           # gesto -> punto en la página Gestos
        self.vistos = set()
        self._img_id = None
        self._bloqueo = False

        self.toasts = Adw.ToastOverlay()
        self.pila = Adw.ViewStack()
        self.toasts.set_child(self.pila)
        cabecera = Adw.HeaderBar()
        cabecera.set_title_widget(Adw.ViewSwitcher(stack=self.pila, policy=Adw.ViewSwitcherPolicy.WIDE))
        self.btn_pausa = Gtk.ToggleButton(icon_name="media-playback-pause-symbolic",
                                          tooltip_text="Pausa (también: mantener el gesto de pausa, "
                                                       "o `gestpad alternar`)")
        self.btn_pausa.connect("toggled", self._al_pausa)
        cabecera.pack_end(self.btn_pausa)
        caja = Gtk.Box(spacing=8)
        caja.append(Gtk.Label(label="Cámara"))
        self.interruptor = Gtk.Switch(valign=Gtk.Align.CENTER,
                                      tooltip_text="Enciende la cámara y empieza a controlar el ordenador")
        self.interruptor.connect("notify::active", self._al_interruptor)
        caja.append(self.interruptor)
        cabecera.pack_start(caja)
        self.banner = Adw.Banner(button_label="Pararlo")
        self.banner.connect("button-clicked", self._parar_otro)
        vista = Adw.ToolbarView()
        vista.add_top_bar(cabecera)
        vista.add_top_bar(self.banner)
        vista.set_content(self.toasts)
        vista.add_bottom_bar(Adw.ViewSwitcherBar(stack=self.pila))
        self.set_content(vista)

        self.pila.add_titled_with_icon(self._pagina_control(), "control", "Control", "camera-web-symbolic")
        self.pila.add_titled_with_icon(self._pagina_gestos(), "gestos", "Gestos", "emblem-ok-symbolic")
        self.pila.add_titled_with_icon(self._pagina_perfiles(), "perfiles", "Perfiles", "view-list-symbolic")
        pagina = os.environ.get("GESTPAD_GUI_PAGINA")      # para capturas
        if pagina:
            self.pila.set_visible_child_name(pagina)

        self.connect("close-request", self._al_cerrar)
        GLib.timeout_add(33, self._tic)
        if os.environ.get("GESTPAD_GUI_NO_ARRANCAR") != "1":
            GLib.idle_add(lambda: self.interruptor.set_active(True) and False)

    def avisar(self, texto):
        self.toasts.add_toast(Adw.Toast(title=texto, timeout=3))

    # ------------------------------------------------------------ motor
    def _arrancar(self):
        from .motor import Motor
        if Cliente().pedir("estado") is not None:
            self.banner.set_title("Ya hay otro gestpad en marcha (¿`gestpad ejecutar`?) usando la cámara.")
            self.banner.set_revealed(True)
            return False
        self.banner.set_revealed(False)
        self.motor = Motor(self.cfg, salida=self.salida_inicial)
        self.motor.vista = True
        if self.motor.error_salida:
            self.avisar(f"Salida «{self.motor.nombre_salida}»: {self.motor.error_salida}")
        self.motor.iniciar()

        def atender(o):
            if o.get("orden") == "salir":
                GLib.idle_add(lambda: self.interruptor.set_active(False) and False)
                return {"ok": True}
            return self.motor.orden(o)

        self.parar_socket = en_hilo(atender)
        return True

    def _detener(self):
        if self.parar_socket:
            self.parar_socket()
            self.parar_socket = None
        if self.motor:
            self.motor.detener()
            self.motor = None
        self.imagen.set_paintable(None)
        self._img_id = None

    def _al_interruptor(self, sw, _):
        if sw.get_active() and self.motor is None:
            if not self._arrancar():
                sw.set_active(False)
        elif not sw.get_active() and self.motor is not None:
            self._detener()
            self.lbl_estado.set_label("Cámara apagada")

    def _parar_otro(self, *_):
        Cliente().pedir("salir")
        self.banner.set_revealed(False)
        GLib.timeout_add(600, lambda: self.interruptor.set_active(True) and False)

    def _al_cerrar(self, *_):
        self._detener()
        return False

    def pedir(self, orden, **datos):
        if self.motor is None:
            return None
        r = self.motor.orden({"orden": orden, **datos})
        if not r.get("ok"):
            self.avisar(r.get("error", "error"))
        return r

    def _al_pausa(self, b):
        if self._bloqueo or self.motor is None:
            return
        self.pedir("pausa", valor=b.get_active())

    # ------------------------------------------------------------ Control
    def _pagina_control(self):
        raiz = Gtk.Box(spacing=18, margin_start=18, margin_end=18, margin_top=12, margin_bottom=12)
        izq = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        self.imagen = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN,
                                  hexpand=True, vexpand=True, css_classes=["vista"])
        marco = Gtk.Overlay(child=self.imagen)
        self.lbl_vacio = Gtk.Label(label="Enciende la cámara arriba a la izquierda",
                                   css_classes=["title-3", "dim-label"])
        marco.add_overlay(self.lbl_vacio)
        izq.append(marco)
        self.barra_pausa = Gtk.ProgressBar(visible=False, show_text=True)
        izq.append(self.barra_pausa)
        self.lbl_estado = Gtk.Label(xalign=0, css_classes=["dim-label", "dato"], wrap=True)
        izq.append(self.lbl_estado)
        raiz.append(izq)

        pag = Adw.PreferencesPage(width_request=400, hexpand=False)
        g = Adw.PreferencesGroup(title="Perfil")
        self.combo_perfil = Adw.ComboRow(title="Perfil activo")
        self.combo_perfil.set_model(Gtk.StringList.new([p["nombre"] for p in self.cfg["perfiles"]]))
        self.combo_perfil.connect("notify::selected", self._al_elegir_perfil)
        g.add(self.combo_perfil)
        self.fila_desc = Adw.ActionRow(subtitle_lines=3, css_classes=["property"])
        g.add(self.fila_desc)
        pag.add(g)

        self.grupo_gestos = Adw.PreferencesGroup(title="Gestos de este perfil")
        pag.add(self.grupo_gestos)

        g = Adw.PreferencesGroup(title="Lo que ve la cámara")
        self.fila_mano = Adw.ActionRow(title="Mano", subtitle="—")
        self.fila_cabeza = Adw.ActionRow(title="Cabeza", subtitle="—",
                                         tooltip_text="Giro / cabeceo / ladeo en grados respecto a la postura neutra")
        g.add(self.fila_mano)
        g.add(self.fila_cabeza)
        self.niveles = {}
        for clave, titulo in (("ojo_izq", "Ojo izquierdo cerrado"), ("ojo_der", "Ojo derecho cerrado"),
                              ("boca", "Boca abierta"), ("sonrisa", "Sonrisa"), ("cejas", "Cejas arriba")):
            fila = Adw.ActionRow(title=titulo)
            nivel = Gtk.LevelBar(min_value=0, max_value=1, valign=Gtk.Align.CENTER, width_request=140)
            fila.add_suffix(nivel)
            g.add(fila)
            self.niveles[clave] = nivel
        b = Gtk.Button(label="Recentrar", valign=Gtk.Align.CENTER,
                       tooltip_text="Toma la postura actual de la cabeza como el centro")
        b.connect("clicked", lambda *_: self.pedir("recentrar"))
        self.fila_cabeza.add_suffix(b)
        pag.add(g)

        g = Adw.PreferencesGroup(title="Ajustes en vivo",
                                 description="Cambian el perfil actual mientras la app esté abierta; "
                                             "para dejarlos fijos, ponlos en perfiles.toml")
        self.spins = {}
        for clave, titulo, sub, mn, mx, paso, dig in (
                ("suavizado", "Suavizado del cursor", "Hz: más bajo = más quieto, más retardo", 0.2, 6, 0.1, 1),
                ("ganancia", "Ganancia (modo relativo)", "Cuánto se mueve el cursor", 0.3, 8, 0.1, 1),
                ("pinza_cerrar", "Distancia de pinza", "Más alto = pellizco más fácil", 0.12, 0.6, 0.01, 2),
                ("umbral_cabeza", "Umbral de la cabeza", "Grados para CABEZA_* y LADEAR_*", 4, 30, 1, 0)):
            fila = Adw.SpinRow.new_with_range(mn, mx, paso)
            fila.set_title(titulo)
            fila.set_subtitle(sub)
            fila.set_digits(dig)
            fila.connect("notify::value", self._al_spin, clave)
            g.add(fila)
            self.spins[clave] = fila
        pag.add(g)

        g = Adw.PreferencesGroup(title="Salida")
        self.combo_salida = Adw.ComboRow(title="Cómo se mueven ratón y teclado")
        self.combo_salida.set_model(Gtk.StringList.new([t for _, t in SALIDAS]))
        self.combo_salida.set_selected([n for n, _ in SALIDAS].index(self.salida_inicial))
        self.combo_salida.connect("notify::selected", self._al_elegir_salida)
        g.add(self.combo_salida)
        pag.add(g)
        raiz.append(pag)
        return raiz

    def _al_elegir_perfil(self, combo, _):
        if self._bloqueo or self.motor is None:
            return
        nombre = self.cfg["perfiles"][combo.get_selected()]["nombre"]
        self.pedir("perfil", nombre=nombre)
        self.ajustes["perfil"] = nombre
        _guardar_ajustes(self.ajustes)

    def _al_elegir_salida(self, combo, _):
        if self._bloqueo:
            return
        nombre = SALIDAS[combo.get_selected()][0]
        self.salida_inicial = nombre
        self.ajustes["salida"] = nombre
        _guardar_ajustes(self.ajustes)
        if self.motor is not None:
            r = self.pedir("salida", nombre=nombre)
            if r and r.get("ok"):
                self.avisar(f"Salida: {nombre}")

    def _al_spin(self, fila, _, clave):
        if self._bloqueo or self.motor is None:
            return
        v = fila.get_value()
        if clave == "pinza_cerrar":
            self.pedir("opcion", valores={"pinza_cerrar": v, "pinza_abrir": v * 1.5})
        else:
            self.pedir("opcion", valores={clave: v})

    def _pintar_perfil(self, e):
        """Rehace la lista de gestos cuando cambia el perfil."""
        self.perfil_pintado = e["perfil"]
        self._bloqueo = True
        nombres = [p["nombre"] for p in self.cfg["perfiles"]]
        if e["perfil"] in nombres:
            self.combo_perfil.set_selected(nombres.index(e["perfil"]))
        for clave, fila in self.spins.items():
            fila.set_value(float(e["opciones"].get(clave, fila.get_value())))
        self._bloqueo = False
        self.fila_desc.set_title(e["descripcion"] or e["perfil"])
        partes = []
        for fuente, destino in e["ejes"].items():
            modo = e["opciones"]["modo_raton"] if destino == "raton" else ""
            partes.append(f"{fuente} → {destino} {modo}".strip())
        if e["pausa_gesto"]:
            partes.append(f"pausa: {e['pausa_gesto']} {e['opciones']['pausa_s']:g} s")
        self.fila_desc.set_subtitle(" · ".join(partes))
        for fila, _p in self.filas.values():
            self.grupo_gestos.remove(fila)
        self.filas = {}
        for g, d, _activo in e["gestos"]:
            fila = Adw.ActionRow(title=g, subtitle=DESCRIPCION.get(g, ""))
            fila.add_suffix(Gtk.Label(label=d, css_classes=["destino", "dim-label"],
                                      ellipsize=3, max_width_chars=22))
            punto = Gtk.Label(label="●", css_classes=["punto"])
            fila.add_suffix(punto)
            self.grupo_gestos.add(fila)
            self.filas[g] = (fila, punto)

    # ------------------------------------------------------------ Gestos
    def _pagina_gestos(self):
        pag = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Probar los gestos",
                                 description="Un punto verde = ya lo ha visto al menos una vez; "
                                             "azul = lo está viendo ahora. Solo se buscan la mano o la "
                                             "cara si el perfil las usa, salvo que actives esto:")
        fila = Adw.SwitchRow(title="Detectar mano y cara a la vez",
                             subtitle="Para probar todos los gestos. Solo los del perfil hacen algo.")
        fila.connect("notify::active", lambda f, _: self.motor and setattr(self.motor, "todo", f.get_active()))
        self.switch_todo = fila
        g.add(fila)
        b = Gtk.Button(label="Empezar de nuevo", valign=Gtk.Align.CENTER, css_classes=["flat"])
        b.connect("clicked", lambda *_: self.vistos.clear())
        fila.add_suffix(b)
        pag.add(g)
        for titulo, lista in (("Mano", mano.GESTOS), ("Cara", cara.GESTOS)):
            g = Adw.PreferencesGroup(title=titulo)
            for gesto in lista:
                fila = Adw.ActionRow(title=gesto, subtitle=DESCRIPCION[gesto])
                punto = Gtk.Label(label="●", css_classes=["punto"])
                fila.add_suffix(punto)
                g.add(fila)
                self.filas_ref[gesto] = punto
            pag.add(g)
        return pag

    # ------------------------------------------------------------ Perfiles
    def _pagina_perfiles(self):
        self.pag_perfiles = Adw.PreferencesPage()
        self.grupo_fichero = Adw.PreferencesGroup(title="Fichero de perfiles")
        self.fila_fichero = Adw.ActionRow(title=self.cfg["ruta"], css_classes=["property"])
        b = Gtk.Button(label="Abrir", valign=Gtk.Align.CENTER)
        b.connect("clicked", self._abrir_fichero)
        self.fila_fichero.add_suffix(b)
        b = Gtk.Button(label="Recargar", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        b.connect("clicked", self._recargar)
        self.fila_fichero.add_suffix(b)
        self.grupo_fichero.add(self.fila_fichero)
        self.pag_perfiles.add(self.grupo_fichero)
        self.grupos_perfiles = []
        self._construir_perfiles()
        return self.pag_perfiles

    def _construir_perfiles(self):
        for g in self.grupos_perfiles:
            self.pag_perfiles.remove(g)
        self.grupos_perfiles = []
        g = Adw.PreferencesGroup(title="Perfiles")
        for p in self.cfg["perfiles"]:
            exp = Adw.ExpanderRow(title=p["nombre"], subtitle=p["descripcion"])
            exp.add_suffix(Gtk.Label(label=" + ".join(sorted(p["necesita"])), css_classes=["dim-label"]))
            for fuente, destino in p["ejes"].items():
                sub = Adw.ActionRow(title=f"{fuente} (eje)")
                sub.add_suffix(Gtk.Label(label=f"{destino} · {p['opciones']['modo_raton']}",
                                         css_classes=["destino", "dim-label"]))
                exp.add_row(sub)
            for gesto, d in p["gestos"].items():
                sub = Adw.ActionRow(title=gesto, subtitle=DESCRIPCION.get(gesto, ""))
                sub.add_suffix(Gtk.Label(label=d if isinstance(d, str) else " + ".join(d),
                                         css_classes=["destino", "dim-label"]))
                exp.add_row(sub)
            if p["opciones"]["pausa"]:
                sub = Adw.ActionRow(title="Pausa", subtitle=f"mantener {p['opciones']['pausa']} "
                                                            f"{p['opciones']['pausa_s']:g} s")
                exp.add_row(sub)
            g.add(exp)
        self.pag_perfiles.add(g)
        self.grupos_perfiles.append(g)

    def _abrir_fichero(self, *_):
        ruta = self.cfg["ruta"]
        Gtk.FileLauncher.new(Gio.File.new_for_path(ruta)).launch(self, None, None, None)

    def _recargar(self, *_):
        try:
            cfg = config.cargar(self.cfg["ruta"])
        except Exception as e:
            self.avisar(f"Error en los perfiles: {e}")
            return
        self.cfg = cfg
        if self.motor is not None:
            self.motor.recargar(cfg)
        self._bloqueo = True
        self.combo_perfil.set_model(Gtk.StringList.new([p["nombre"] for p in cfg["perfiles"]]))
        self._bloqueo = False
        self.perfil_pintado = None
        self._construir_perfiles()
        self.avisar("Perfiles recargados")

    # ------------------------------------------------------------ refresco
    def _tic(self):
        m = self.motor
        if m is None:
            self.lbl_vacio.set_visible(True)
            return True
        visible = self.is_visible() and self.pila.get_visible_child_name() == "control"
        m.vista = visible
        e = m.instantanea()
        if not e:
            return True
        if e["perfil"] != self.perfil_pintado:
            self._pintar_perfil(e)
        img = m.imagen() if visible else None
        if img is not None and id(img) != self._img_id:
            self._img_id = id(img)
            h, w = img.shape[:2]
            tex = Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(img.tobytes()), w * 3)
            self.imagen.set_paintable(tex)
        self.lbl_vacio.set_visible(self._img_id is None)

        self._bloqueo = True
        self.btn_pausa.set_active(e["pausado"])
        self._bloqueo = False
        prog = e["pausa_progreso"]
        self.barra_pausa.set_visible(prog > 0)
        if prog > 0:
            self.barra_pausa.set_fraction(prog)
            self.barra_pausa.set_text(f"{'Reanudar' if e['pausado'] else 'Pausar'}: mantén {e['pausa_gesto']}")

        partes = [f"{e['fps']:.0f} fps", f"salida: {e['salida']}"]
        if e["pausado"]:
            partes.insert(0, "EN PAUSA")
        if e["mods"]:
            partes.append("modo: " + ", ".join(e["mods"]))
        if e["recientes"]:
            partes.append("último: " + e["recientes"][-1])
        for problema in (e.get("error"), e.get("error_salida"), e.get("aviso")):
            if problema:
                partes.append(f"⚠ {problema}")
        self.lbl_estado.set_label("  ·  ".join(partes))

        activos = set(e["activos"]) | set(e["recientes"])
        self.vistos |= activos
        for g, (fila, punto) in self.filas.items():
            si = g in activos
            (punto.add_css_class if si else punto.remove_css_class)("activo")
            (fila.add_css_class if si else fila.remove_css_class)("fila-activa")
        for g, punto in self.filas_ref.items():
            (punto.add_css_class if g in activos else punto.remove_css_class)("activo")
            (punto.add_css_class if g in self.vistos and g not in activos else punto.remove_css_class)("visto")

        mi = e.get("mano")
        if mi:
            dedos = "".join("▮" if x else "▯" for x in mi["estirado"])
            texto = f"{mi['pose'] or 'sin pose'} · mano {mi['lado']} · dedos {dedos}"
            if e["enganche"] and not e["enganchado"] and "mano" in e["necesita"]:
                texto += f"\nsin enganchar: enseña {e['enganche']} para empezar"
            self.fila_mano.set_subtitle(texto)
        else:
            self.fila_mano.set_subtitle("no veo ninguna mano" if "mano" in e["necesita"] or m.todo
                                        else "este perfil no usa la mano")
        ci = e.get("cara")
        if ci:
            if ci["calibrando"]:
                self.fila_cabeza.set_subtitle("mira al frente: tomando la postura neutra…")
            else:
                g_, c_, l_ = ci["angulos"]
                self.fila_cabeza.set_subtitle(f"giro {g_:+.0f}°  cabeceo {c_:+.0f}°  ladeo {l_:+.0f}°")
            vals = {"ojo_izq": ci["cierre"][0], "ojo_der": ci["cierre"][1], "boca": ci["boca"],
                    "sonrisa": ci["sonrisa"], "cejas": ci["cejas"]}
        else:
            self.fila_cabeza.set_subtitle("no veo ninguna cara" if "cara" in e["necesita"] or m.todo
                                          else "este perfil no usa la cara")
            vals = {}
        for k, nivel in self.niveles.items():
            nivel.set_value(min(1.0, max(0.0, vals.get(k, 0.0))))
        return True


class App(Adw.Application):
    def __init__(self, cfg, prueba, salida):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.cfg, self.prueba, self.salida = cfg, prueba, salida

    def do_startup(self):
        Adw.Application.do_startup(self)
        # cerrar bien con Ctrl+C o kill: soltar teclas, cámara y dispositivos virtuales
        for sig in (2, 15):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._al_senal)

    def _al_senal(self):
        for v in self.get_windows():
            v._detener()
        self.quit()
        return GLib.SOURCE_REMOVE

    def do_activate(self):
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        ventana = self.get_active_window() or Ventana(self, self.cfg, self.prueba, self.salida)
        ventana.present()


def main(cfg, prueba=False, salida=None):
    return App(cfg, prueba, salida).run([])
