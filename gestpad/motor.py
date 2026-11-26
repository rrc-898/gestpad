"""El motor: cámara → detectores → gestos → ratón/teclado.

Corre en un hilo propio. La app (o `gestpad ejecutar`) lo crea, le pide el
estado con instantanea()/imagen() y le da órdenes con orden(); por el socket
de control (control.py) llegan las mismas órdenes desde la terminal.
"""

import math
import queue
import subprocess
import threading
import time

import cv2

from . import cara, config, mano
from .filtros import Histeresis, UnoEuro
from .config import RUEDA
from .salida import ALIAS, Pantalla, crear


class Accionador:
    """Pulsa y suelta destinos («key:SUPER+TAB», «mouse:LEFT», «mod:SCROLL»…)."""

    def __init__(self, motor):
        self.m = motor
        self.cuenta = {}          # tecla/botón -> nº de gestos que lo mantienen
        self.mods = {}            # PRECISION/SCROLL/CONGELAR -> cuenta
        self.repetir = {}         # destino rep: -> próximo disparo

    def _pulsar_fisico(self, tipo, nombre, pulsado):
        if tipo == "key":
            nombre = ALIAS.get(nombre, nombre)       # SUPER y LEFTMETA son la misma tecla
        clave = f"{tipo}:{nombre}"
        n = self.cuenta.get(clave, 0) + (1 if pulsado else -1)
        n = max(n, 0)
        antes = self.cuenta.get(clave, 0)
        self.cuenta[clave] = n
        if (antes == 0) == (n == 0):
            return
        try:
            if tipo == "key":
                self.m.salida.tecla(nombre, pulsado)
            else:
                self.m.salida.boton(nombre, pulsado)
                self.m.cursor.congelar(self.m.op["congelar_clic_s"] * (1 if pulsado else 0.6))
        except (ValueError, OSError) as e:
            self.m.aviso = f"{clave}: {e}"

    def pulsar(self, d, t):
        if isinstance(d, list):
            for x in d:
                self.pulsar(x, t)
            return
        tipo, _, v = d.partition(":")
        if tipo == "key":
            for parte in v.split("+"):
                self._pulsar_fisico("key", parte.upper(), True)
        elif tipo == "mouse":
            v = v.upper()
            if v in RUEDA:
                self.m.salida.rueda(*RUEDA[v])
            else:
                self._pulsar_fisico("mouse", v, True)
        elif tipo == "mod":
            self.mods[v.upper()] = self.mods.get(v.upper(), 0) + 1
        elif tipo == "accion":
            self.m.accion(v)
        elif tipo == "exec":
            try:
                subprocess.Popen(["sh", "-c", v], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as e:
                self.m.aviso = f"exec: {e}"
        elif tipo == "rep":
            self.tocar(v, t)
            self.repetir[d] = t + self.m.op["repetir_retardo_s"]

    def soltar(self, d):
        if isinstance(d, list):
            for x in reversed(d):
                self.soltar(x)
            return
        tipo, _, v = d.partition(":")
        if tipo == "key":
            for parte in reversed(v.split("+")):
                self._pulsar_fisico("key", parte.upper(), False)
        elif tipo == "mouse" and v.upper() not in RUEDA:
            self._pulsar_fisico("mouse", v.upper(), False)
        elif tipo == "mod":
            self.mods[v.upper()] = max(0, self.mods.get(v.upper(), 0) - 1)
        elif tipo == "rep":
            self.repetir.pop(d, None)

    def tocar(self, d, t):
        self.pulsar(d, t)
        self.soltar(d)

    def mod(self, nombre):
        return self.mods.get(nombre, 0) > 0

    def tic(self, t):
        for d, prox in list(self.repetir.items()):
            if t >= prox:
                self.tocar(d[4:], t)
                self.repetir[d] = t + self.m.op["repetir_s"]

    def soltar_todo(self):
        for clave, n in list(self.cuenta.items()):
            if n:
                tipo, nombre = clave.split(":", 1)
                self.cuenta[clave] = 1
                self._pulsar_fisico(tipo, nombre, False)
        self.mods.clear()
        self.repetir.clear()


class Cursor:
    """Mueve el ratón con la mano o la cabeza según modo_raton."""

    def __init__(self, motor):
        self.m = motor
        self.reiniciar()

    def reiniciar(self):
        op = self.m.op
        self.fx = UnoEuro(op["suavizado"], op["suavizado_beta"])
        self.fy = UnoEuro(op["suavizado"], op["suavizado_beta"])
        self.ultimo = None              # último punto filtrado (para el modo relativo)
        self.t = None
        self.congelado_hasta = 0.0
        self.ref_scroll = None
        self.salida_abs = None          # última posición absoluta enviada
        self.desfase = (0.0, 0.0)       # modo absoluto con PRECISION
        self.ref_prec = None

    def congelar(self, s):
        self.congelado_hasta = max(self.congelado_hasta, time.monotonic() + s)

    def soltar(self):
        self.fx.reiniciar()
        self.fy.reiniciar()
        self.ultimo = self.t = self.ref_scroll = self.ref_prec = None

    def mover(self, p, fuente, t, scroll_fijo=False):
        """p: punto en 0..1 (mano) o en «medias pantallas» centrado en 0 (cabeza)."""
        op, acc = self.m.op, self.m.acc
        dt = min(0.1, t - self.t) if self.t else 1 / 30
        self.t = t
        x, y = self.fx(p[0], t), self.fy(p[1], t)
        centro = (0.0, 0.0) if fuente == "cabeza" else self._centro_zona()

        if scroll_fijo or acc.mod("SCROLL"):
            if self.ref_scroll is None:
                self.ref_scroll = centro if scroll_fijo else (x, y)
            dx, dy = x - self.ref_scroll[0], self.ref_scroll[1] - y
            self.m.salida.rueda(self._vel(dy, 0.03, 0.2) * op["scroll_velocidad"] * dt,
                                self._vel(dx, 0.05, 0.2) * op["scroll_velocidad"] * dt)
            self.ultimo = (x, y)
            return
        self.ref_scroll = None
        if acc.mod("CONGELAR") or t < self.congelado_hasta:
            self.ultimo = (x, y)
            return

        prec = op["precision"] if acc.mod("PRECISION") else 1.0
        modo = op["modo_raton"]
        pant = self.m.pantalla.mon
        if modo == "absoluto":
            if fuente == "cabeza":
                fx, fy = 0.5 + x, 0.5 + y
            else:
                x0, y0, x1, y1 = op["zona"]
                fx, fy = (x - x0) / (x1 - x0), (y - y0) / (y1 - y0)
            if acc.mod("PRECISION"):
                if self.ref_prec is None:
                    self.ref_prec = ((fx, fy), self.salida_abs or (fx, fy))
                (ix, iy), (ox, oy) = self.ref_prec
                sx, sy = ox + (fx - ix) * prec, oy + (fy - iy) * prec
                self.desfase = (sx - fx, sy - fy)
            else:
                self.ref_prec = None
                k = math.exp(-dt / 0.35)              # el desfase de PRECISION se va deshaciendo
                self.desfase = (self.desfase[0] * k, self.desfase[1] * k)
                sx, sy = fx + self.desfase[0], fy + self.desfase[1]
            sx, sy = min(max(sx, 0.0), 1.0), min(max(sy, 0.0), 1.0)
            if self.salida_abs is None or abs(sx - self.salida_abs[0]) + abs(sy - self.salida_abs[1]) > 1e-4:
                self.m.salida.mover_abs(sx, sy)
                self.salida_abs = (sx, sy)
        elif modo == "relativo":
            if self.ultimo is not None:
                dx, dy = x - self.ultimo[0], y - self.ultimo[1]
                if fuente == "mano":                   # misma escala en los dos ejes de la cámara
                    dy *= self.m.general["alto"] / self.m.general["ancho"]
                v = math.hypot(dx, dy) / max(dt, 1e-3)
                g = op["ganancia"] * (1 + op["aceleracion"] * min(v, 2.0)) * prec * pant["w"]
                self.m.salida.mover_rel(dx * g, dy * g)
            self.salida_abs = None
        else:  # joystick
            if fuente == "cabeza":
                zm = op["zona_muerta_grados"] / (2 * op["rango_giro"])
                vx, vy = self._vel(x, zm, 0.5), self._vel(y, zm, 0.5)
            else:
                x0, y0, x1, y1 = op["zona"]
                vx = self._vel(x - centro[0], op["zona_muerta"], (x1 - x0) / 2)
                vy = self._vel(y - centro[1], op["zona_muerta"], (y1 - y0) / 2)
            k = op["velocidad"] * prec * dt
            self.m.salida.mover_rel(vx * k, vy * k)
            self.salida_abs = None
        self.ultimo = (x, y)

    @staticmethod
    def _vel(d, muerta, tope):
        """−1..1 con zona muerta y curva suave."""
        a = abs(d) - muerta
        if a <= 0:
            return 0.0
        f = min(1.0, a / max(tope - muerta, 1e-6))
        return math.copysign(f ** 1.5, d)

    def _centro_zona(self):
        x0, y0, x1, y1 = self.m.op["zona"]
        return ((x0 + x1) / 2, (y0 + y1) / 2)


class Motor:
    def __init__(self, cfg, salida=None, prueba=False):
        self.cfg = cfg
        self.general = cfg["general"]
        self.nombre_salida = "nula" if prueba else (salida or self.general["salida"])
        self.perfil = next(p for p in cfg["perfiles"] if p["nombre"] == self.general["perfil_inicial"])
        self.op = self.perfil["opciones"]
        self.pausado = False
        self.aviso = None
        self.vista = False           # pintar la vista previa (solo si alguien la mira)
        self.todo = False            # buscar mano y cara aunque el perfil no las use (página Gestos)
        self._ordenes = queue.Queue()
        self._lock = threading.Lock()
        self._estado = {}
        self._imagen = None
        self._parar = threading.Event()
        self.hilo = None
        self.pantalla = Pantalla(self.general["monitor"])
        self.salida, self.error_salida = crear(self.nombre_salida, self.general["monitor"])
        self.acc = Accionador(self)
        self.cursor = Cursor(self)
        self._preparar_perfil()

    # --- ciclo de vida ---------------------------------------------------------
    def iniciar(self):
        self.hilo = threading.Thread(target=self._bucle, name="motor", daemon=True)
        self.hilo.start()
        return self

    def detener(self):
        self._parar.set()
        if self.hilo:
            self.hilo.join(timeout=3)
        self.acc.soltar_todo()
        self.salida.cerrar()

    def orden(self, o):
        """Órdenes desde la app o el socket. Devuelve la respuesta (espera al motor)."""
        if o.get("orden") == "estado":
            return {"ok": True, **self.instantanea()}
        r = queue.Queue(maxsize=1)
        self._ordenes.put((o, r))
        try:
            return r.get(timeout=2)
        except queue.Empty:
            return {"ok": False, "error": "el motor no responde"}

    def instantanea(self):
        with self._lock:
            return dict(self._estado)

    def imagen(self):
        with self._lock:
            return self._imagen

    # --- perfiles y acciones -----------------------------------------------------
    def _seguidores(self):
        self.seg_mano = mano.SeguidorMano(self.op)
        anterior = getattr(self, "seg_cara", None)
        self.seg_cara = cara.SeguidorCara(self.op)
        if anterior is not None:                   # no hace falta recentrar al cambiar de perfil
            self.seg_cara.neutro = anterior.neutro
            self.seg_cara.base_izq, self.seg_cara.base_der = anterior.base_izq, anterior.base_der
        self.cursor.reiniciar()

    def _preparar_perfil(self):
        self.op = self.perfil["opciones"]
        self._seguidores()
        self.activos_prev = set()
        self.pausa_desde = None
        self.pausa_hecha = False
        self.enganchado = False
        self.mano_vista = -1e9
        self.teclas_eje = {}
        self.hist_teclas = [Histeresis(0.35, 0.25) for _ in range(4)]
        self.recientes = []

    def cambiar_perfil(self, nombre):
        p = next((p for p in self.cfg["perfiles"] if p["nombre"] == nombre), None)
        if p is None:
            return False
        self.acc.soltar_todo()
        self._soltar_teclas_eje()
        self.perfil = p
        self._preparar_perfil()
        return True

    def _vecino(self, paso):
        nombres = [p["nombre"] for p in self.cfg["perfiles"]]
        i = nombres.index(self.perfil["nombre"])
        self.cambiar_perfil(nombres[(i + paso) % len(nombres)])

    def poner_pausa(self, valor):
        self.pausado = valor
        if valor:
            self.acc.soltar_todo()
            self._soltar_teclas_eje()
            self.cursor.soltar()

    def accion(self, v):
        if v == "pausa":
            self.poner_pausa(not self.pausado)
        elif v == "siguiente_perfil":
            self._vecino(1)
        elif v == "perfil_anterior":
            self._vecino(-1)
        elif v == "recentrar":
            self.seg_cara.recentrar()
            self.cursor.soltar()
        elif v.startswith("perfil="):
            self.cambiar_perfil(v[7:])

    def cambiar_salida(self, nombre):
        self.acc.soltar_todo()
        self._soltar_teclas_eje()
        nueva, error = crear(nombre, self.general["monitor"])
        self.salida.cerrar()
        self.salida, self.error_salida, self.nombre_salida = nueva, error, nombre
        self.pantalla = Pantalla(self.general["monitor"])
        self.cursor.reiniciar()

    def recargar(self, cfg):
        self.acc.soltar_todo()
        self._soltar_teclas_eje()
        self.cfg = cfg
        self.general = cfg["general"]
        nombre = self.perfil["nombre"]
        self.perfil = next((p for p in cfg["perfiles"] if p["nombre"] == nombre), cfg["perfiles"][0])
        self._preparar_perfil()

    def _atender(self, o):
        q = o.get("orden")
        if q == "pausa":
            self.poner_pausa(not self.pausado if o.get("valor") is None else bool(o["valor"]))
        elif q == "perfil":
            if not self.cambiar_perfil(o.get("nombre", "")):
                return {"ok": False, "error": f"no existe el perfil «{o.get('nombre')}»"}
        elif q in ("siguiente", "anterior"):
            self._vecino(1 if q == "siguiente" else -1)
        elif q == "recentrar":
            self.accion("recentrar")
        elif q == "salida":
            self.cambiar_salida(o.get("nombre", "uinput"))
            if self.error_salida:
                return {"ok": False, "error": self.error_salida}
        elif q == "opcion":
            # cambio en caliente (desde los deslizadores de la app); no se guarda en el TOML
            for k, v in o.get("valores", {}).items():
                if k in self.op:
                    self.op[k] = v
            self.acc.soltar_todo()
            self.activos_prev = set()
            self._seguidores()
        elif q == "recargar":
            try:
                self.recargar(config.cargar(self.cfg["ruta"]))
            except Exception as e:
                return {"ok": False, "error": str(e)}
        elif q == "vista":
            self.vista = bool(o.get("valor", True))
        else:
            return {"ok": False, "error": f"orden desconocida «{q}»"}
        return {"ok": True}

    # --- bucle ---------------------------------------------------------------------
    def _bucle(self):
        cam = det = None
        from . import vision
        try:
            cam = vision.Camara(self.general["camara"], self.general["ancho"],
                                self.general["alto"], self.general["fps"],
                                self.general["fps_constantes"])
            det = vision.Detectores()
            n, fps, t_fps, cuadros = 0, 0.0, time.monotonic(), 0
            while not self._parar.is_set():
                while not self._ordenes.empty():
                    o, r = self._ordenes.get_nowait()
                    try:
                        r.put(self._atender(o))
                    except Exception as e:
                        r.put({"ok": False, "error": f"{type(e).__name__}: {e}"})
                frame, n = cam.esperar(n)
                t = time.monotonic()
                if frame is None:
                    self._publicar(None, {}, t, fps, cam.error)
                    continue
                if self.general["espejo"]:
                    frame = cv2.flip(frame, 1)
                necesita = set(self.perfil["necesita"])
                if self.todo:
                    necesita |= {"mano", "cara"}
                det.poner_num_manos(1 if self.op["mano"] == "cualquiera" else 2)
                try:
                    res = det.procesar(frame, necesita)
                except FileNotFoundError as e:
                    self._publicar(frame, {}, t, fps, str(e))
                    time.sleep(1)
                    continue
                self._paso(res, frame, t, necesita)
                cuadros += 1
                if t - t_fps >= 1.0:
                    fps, cuadros, t_fps = cuadros / (t - t_fps), 0, t
                self._publicar(frame, res, t, fps, None)
        except Exception as e:  # que un fallo inesperado no deje teclas pulsadas
            import traceback
            traceback.print_exc()
            with self._lock:
                self._estado = {**self._estado, "error": f"{type(e).__name__}: {e}"}
        finally:
            self.acc.soltar_todo()
            self._soltar_teclas_eje()
            if det:
                det.cerrar()
            if cam:
                cam.cerrar()

    def _elegir_mano(self, manos):
        if not manos:
            return None
        quiere = self.op["mano"]
        cand = [i for i, m in enumerate(manos) if quiere == "cualquiera" or m[2] == quiere]
        if not cand:
            return None
        # la más grande en la imagen = la más cercana a la cámara
        return max(cand, key=lambda i: float(((manos[i][0][0, :2] - manos[i][0][9, :2]) ** 2).sum()))

    def _paso(self, res, frame, t, necesita):
        h, w = frame.shape[:2]
        self.i_mano = self._elegir_mano(res["manos"])
        rm = None
        if self.i_mano is not None:
            img, mundo, _lado, _c = res["manos"][self.i_mano]
            rm = mano.rasgos(img, mundo, self.op["umbral_dedo"])
        act_m, ev_m = self.seg_mano.actualizar(rm, t) if "mano" in necesita else (set(), [])
        rm_visto = rm                              # lo que se ve, aunque no esté enganchada
        if self.op["enganche"]:
            # la mano no controla nada hasta enseñar la pose de enganche (una mano en el
            # ratón o en el teclado no debe mover el cursor), y se suelta al perderla de vista
            if rm is not None:
                self.mano_vista = t
            elif t - self.mano_vista > self.op["enganche_soltar_s"]:
                self.enganchado = False
            if not self.enganchado and self.op["enganche"] in act_m:
                self.enganchado = True
            if not self.enganchado:
                act_m, ev_m, rm = set(), [], None
        rc = None
        if res["cara"] is not None:
            rc = cara.rasgos(res["cara"][0], res["cara"][1], w, h)
        act_c, ev_c = self.seg_cara.actualizar(rc, t) if "cara" in necesita else (set(), [])
        activos, eventos = act_m | act_c, ev_m + ev_c
        self.activos, self.rm, self.rc = activos, rm_visto, rc
        for e in eventos:
            self.recientes.append((t, e))
        self.recientes = [x for x in self.recientes if t - x[0] < 2.0]

        # gesto de pausa: mantenerlo pausa_s segundos (funciona también en pausa)
        g_pausa = self.op["pausa"]
        if g_pausa and g_pausa in activos:
            if self.pausa_desde is None:
                self.pausa_desde = t
            elif not self.pausa_hecha and t - self.pausa_desde >= self.op["pausa_s"]:
                self.poner_pausa(not self.pausado)
                self.pausa_hecha = True
        else:
            self.pausa_desde, self.pausa_hecha = None, False

        if self.pausado:
            self.activos_prev = set()
            return

        for g, d in self.perfil["gestos"].items():
            if g in config.EVENTOS:
                if g in eventos:
                    self.acc.tocar(d, t)
            else:
                ahora, antes = g in activos, g in self.activos_prev
                if ahora and not antes:
                    self.acc.pulsar(d, t)
                elif antes and not ahora:
                    self.acc.soltar(d)
        self.activos_prev = activos & set(self.perfil["gestos"])
        self.acc.tic(t)

        # ejes
        for fuente, destino in self.perfil["ejes"].items():
            p = None
            if fuente in ("mano", "indice") and rm is not None:
                if not self.op["raton_con"] or rm["pose"] == self.op["raton_con"] or self.acc.mod("SCROLL"):
                    usar_indice = fuente == "indice" or self.op["punto"] == "indice"
                    p = rm["punta_indice"] if usar_indice else rm["ancla"]
                f = "mano"
            elif fuente == "cabeza" and rc is not None and self.seg_cara.neutro is not None:
                g, c, _l = self.seg_cara.angulos
                p = (g / (2 * self.op["rango_giro"]), -c / (2 * self.op["rango_cabeceo"]))
                f = "cabeza"
            if destino.startswith("teclas:"):
                self._eje_teclas(destino, p, fuente)
                continue
            if p is None:
                self.cursor.soltar()
                continue
            self.cursor.mover(p, f, t, scroll_fijo=destino == "scroll")

    def _eje_teclas(self, destino, p, fuente):
        teclas = destino[7:].split(",")
        if p is None:
            desv = (0.0, 0.0)
        elif fuente == "cabeza":
            desv = (p[0] * 2, p[1] * 2)
        else:
            x0, y0, x1, y1 = self.op["zona"]
            desv = ((p[0] - (x0 + x1) / 2) / ((x1 - x0) / 2), (p[1] - (y0 + y1) / 2) / ((y1 - y0) / 2))
        quiere = [self.hist_teclas[0](-desv[1]), self.hist_teclas[1](desv[1]),
                  self.hist_teclas[2](-desv[0]), self.hist_teclas[3](desv[0])]
        for tecla, si in zip(teclas, quiere):
            if si != self.teclas_eje.get(tecla, False):
                self.acc._pulsar_fisico("key", tecla.upper(), si)
                self.teclas_eje[tecla] = si

    def _soltar_teclas_eje(self):
        for tecla, si in list(getattr(self, "teclas_eje", {}).items()):
            if si:
                self.acc._pulsar_fisico("key", tecla.upper(), False)
        self.teclas_eje = {}

    # --- estado para la app ----------------------------------------------------------
    def _publicar(self, frame, res, t, fps, error):
        rm, rc = getattr(self, "rm", None), getattr(self, "rc", None)
        activos = getattr(self, "activos", set())
        mano_info = None
        if rm is not None and res.get("manos"):
            mano_info = {"pose": rm["pose"], "lado": res["manos"][self.i_mano][2],
                         "estirado": rm["estirado"],
                         "pinzas": {k: round(v, 2) for k, v in rm["pinzas"].items()}}
        cara_info = None
        if rc is not None and res.get("cara") is not None:
            b = rc["blend"]
            cara_info = {
                "angulos": [round(a, 1) for a in self.seg_cara.angulos],
                "calibrando": self.seg_cara.neutro is None,
                "cierre": [round(x, 2) for x in self.seg_cara.cierre],
                "boca": round(b.get("jawOpen", 0), 2),
                "sonrisa": round((b.get("mouthSmileLeft", 0) + b.get("mouthSmileRight", 0)) / 2, 2),
                "cejas": round(b.get("browInnerUp", 0), 2),
            }
        pausa_prog = 0.0
        if self.pausa_desde is not None and not self.pausa_hecha:
            pausa_prog = min(1.0, (t - self.pausa_desde) / max(self.op["pausa_s"], 0.01))
        estado = {
            "perfil": self.perfil["nombre"],
            "descripcion": self.perfil["descripcion"],
            "perfiles": [(p["nombre"], p["descripcion"]) for p in self.cfg["perfiles"]],
            "necesita": sorted(self.perfil["necesita"]),
            "gestos": [(g, d if isinstance(d, str) else " + ".join(d), g in activos)
                       for g, d in self.perfil["gestos"].items()],
            "ejes": self.perfil["ejes"],
            "activos": sorted(activos),
            "recientes": [e for _t, e in self.recientes],
            "pausado": self.pausado,
            "pausa_gesto": self.op["pausa"],
            "pausa_progreso": round(pausa_prog, 2),
            "salida": self.nombre_salida,
            "error_salida": self.error_salida,
            "aviso": self.aviso,
            "mods": sorted(k for k, v in self.acc.mods.items() if v),
            "enganche": self.op["enganche"],
            "enganchado": self.enganchado,
            "mano": mano_info,
            "cara": cara_info,
            "fps": round(fps, 1),
            "error": error,
            "opciones": dict(self.op),
            "ruta": self.cfg["ruta"],
        }
        img = None
        if self.vista and frame is not None:
            vista = frame.copy()
            zona = None
            if self.perfil["ejes"].get("mano") == "raton" and self.op["modo_raton"] == "absoluto":
                zona = self.op["zona"]
            textos = [f"{self.perfil['nombre']}  {fps:.0f} fps"]
            if activos:
                textos.append(" ".join(sorted(activos)))
            vision_dibujar(vista, res, getattr(self, "i_mano", None), zona, textos, self.pausado)
            img = cv2.cvtColor(vista, cv2.COLOR_BGR2RGB)
        with self._lock:
            self._estado = estado
            if self.vista:
                self._imagen = img if img is not None else self._imagen


def vision_dibujar(*a, **k):
    from .vision import dibujar
    return dibujar(*a, **k)
