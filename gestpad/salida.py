"""Salidas: lo que los gestos hacen en el ordenador.

  uinput    (por defecto) dispositivos virtuales del kernel: ratón relativo,
            puntero absoluto y teclado. Funciona en Wayland (Hyprland incluido),
            en X11 y en la consola, en cualquier ventana.
  autopy    librería autopy (XTest). En Wayland solo llega a ventanas de XWayland.
  pyautogui librería pyautogui (XTest en Linux; también vale en Windows y macOS).
  nula      no hace nada (modo prueba: ver los gestos sin tocar el escritorio).

Todas tienen la misma interfaz: mover_abs(fx, fy) con fx, fy en 0..1 de la
pantalla elegida, mover_rel(dx, dy) en píxeles, boton(nombre, pulsado),
tecla(nombre, pulsado) con nombres KEY_ de Linux sin el prefijo y
rueda(vertical, horizontal) en muescas (admite fracciones).
"""

import json
import os
import shutil
import subprocess

from . import codigos as C

BOTONES = {"LEFT": C.BTN_LEFT, "RIGHT": C.BTN_RIGHT, "MIDDLE": C.BTN_MIDDLE,
           "SIDE": C.BTN_SIDE, "EXTRA": C.BTN_EXTRA}
MODIFICADORES = {"LEFTCTRL", "RIGHTCTRL", "LEFTSHIFT", "RIGHTSHIFT", "LEFTALT", "RIGHTALT",
                 "LEFTMETA", "RIGHTMETA"}
ALIAS = {"CTRL": "LEFTCTRL", "SHIFT": "LEFTSHIFT", "ALT": "LEFTALT", "SUPER": "LEFTMETA",
         "META": "LEFTMETA", "WIN": "LEFTMETA", "ALTGR": "RIGHTALT"}
ABS_MAX = 32767


def codigo_tecla(nombre):
    nombre = ALIAS.get(nombre.upper(), nombre.upper())
    c = getattr(C, f"KEY_{nombre}", None)
    if c is None:
        raise ValueError(f"tecla desconocida: {nombre}")
    return c


class Pantalla:
    """Geometría de los monitores (Hyprland o, si no, la que dé la librería)."""

    def __init__(self, monitor=""):
        self.monitores = []
        if shutil.which("hyprctl"):
            try:
                out = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True,
                                     text=True, timeout=2).stdout
                for m in json.loads(out):
                    w, h = m["width"] / m["scale"], m["height"] / m["scale"]
                    if m.get("transform", 0) % 2:
                        w, h = h, w
                    self.monitores.append({"nombre": m["name"], "x": m["x"], "y": m["y"],
                                           "w": w, "h": h, "foco": m.get("focused", False)})
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.monitores = []
        if not self.monitores:
            self.monitores = [{"nombre": "pantalla", "x": 0, "y": 0, "w": 1920, "h": 1080, "foco": True}]
        elegido = [m for m in self.monitores if m["nombre"] == monitor] or \
                  [m for m in self.monitores if m["foco"]] or self.monitores
        self.mon = elegido[0]
        x0 = min(m["x"] for m in self.monitores)
        y0 = min(m["y"] for m in self.monitores)
        x1 = max(m["x"] + m["w"] for m in self.monitores)
        y1 = max(m["y"] + m["h"] for m in self.monitores)
        self.caja = (x0, y0, x1 - x0, y1 - y0)

    def a_disposicion(self, fx, fy):
        """(fx, fy) del monitor elegido -> fracción de toda la disposición (lo que espera uinput ABS)."""
        m, (bx, by, bw, bh) = self.mon, self.caja
        x = m["x"] + min(max(fx, 0.0), 1.0) * (m["w"] - 1)
        y = m["y"] + min(max(fy, 0.0), 1.0) * (m["h"] - 1)
        return (x - bx) / bw, (y - by) / bh


class SalidaNula:
    nombre = "nula"

    def __init__(self, *_a, **_k):
        self.pulsados = set()

    def mover_abs(self, fx, fy): pass
    def mover_rel(self, dx, dy): pass
    def rueda(self, v, h=0.0): pass

    def boton(self, nombre, pulsado):
        (self.pulsados.add if pulsado else self.pulsados.discard)(f"mouse:{nombre}")

    def tecla(self, nombre, pulsado):
        (self.pulsados.add if pulsado else self.pulsados.discard)(f"key:{nombre}")

    def soltar_todo(self):
        for p in list(self.pulsados):
            tipo, n = p.split(":", 1)
            (self.boton if tipo == "mouse" else self.tecla)(n, False)

    def cerrar(self):
        self.soltar_todo()


class SalidaUinput(SalidaNula):
    nombre = "uinput"

    def __init__(self, pantalla, sufijo=""):
        super().__init__()
        from .linux_input import DispositivoVirtual
        self.pantalla = pantalla
        teclas = [v for k, v in vars(C).items() if k.startswith("KEY_") and 0 < v < 0x2ff
                  and k not in ("KEY_MAX", "KEY_CNT")]
        self.teclado = DispositivoVirtual(f"gestpad teclado{sufijo}", teclas=sorted(set(teclas)),
                                          product=0x6701)
        self.raton = DispositivoVirtual(f"gestpad raton{sufijo}", teclas=list(BOTONES.values()),
                                        rel=[C.REL_X, C.REL_Y, C.REL_WHEEL, C.REL_HWHEEL,
                                             C.REL_WHEEL_HI_RES, C.REL_HWHEEL_HI_RES],
                                        product=0x6702)
        self.puntero = DispositivoVirtual(f"gestpad puntero{sufijo}", teclas=[C.BTN_LEFT],
                                          ejes={C.ABS_X: (0, ABS_MAX, 0, 0), C.ABS_Y: (0, ABS_MAX, 0, 0)},
                                          product=0x6703)
        self._resto = [0.0, 0.0]           # fracciones de píxel acumuladas
        self._rueda = [0.0, 0.0]           # en unidades de alta resolución (120 = una muesca)
        self._muesca = [0, 0]

    def mover_abs(self, fx, fy):
        ax, ay = self.pantalla.a_disposicion(fx, fy)
        self.puntero.emitir(C.EV_ABS, C.ABS_X, round(ax * ABS_MAX))
        self.puntero.emitir(C.EV_ABS, C.ABS_Y, round(ay * ABS_MAX))
        self.puntero.sincronizar()

    def mover_rel(self, dx, dy):
        self._resto[0] += dx
        self._resto[1] += dy
        ix, iy = int(self._resto[0]), int(self._resto[1])
        if ix or iy:
            self._resto[0] -= ix
            self._resto[1] -= iy
            if ix:
                self.raton.emitir(C.EV_REL, C.REL_X, ix)
            if iy:
                self.raton.emitir(C.EV_REL, C.REL_Y, iy)
            self.raton.sincronizar()

    def rueda(self, v, h=0.0):
        hubo = False
        for i, (cant, hi, baja) in enumerate(((v, C.REL_WHEEL_HI_RES, C.REL_WHEEL),
                                              (h, C.REL_HWHEEL_HI_RES, C.REL_HWHEEL))):
            self._rueda[i] += cant * 120
            paso = int(self._rueda[i])
            if not paso:
                continue
            self._rueda[i] -= paso
            self.raton.emitir(C.EV_REL, hi, paso)
            self._muesca[i] += paso
            n = int(self._muesca[i] / 120)
            if n:
                self._muesca[i] -= n * 120
                self.raton.emitir(C.EV_REL, baja, n)
            hubo = True
        if hubo:
            self.raton.sincronizar()

    def boton(self, nombre, pulsado):
        super().boton(nombre, pulsado)
        self.raton.emitir(C.EV_KEY, BOTONES[nombre], int(pulsado))
        self.raton.sincronizar()

    def tecla(self, nombre, pulsado):
        super().tecla(nombre, pulsado)
        self.teclado.emitir(C.EV_KEY, codigo_tecla(nombre), int(pulsado))
        self.teclado.sincronizar()

    def cerrar(self):
        super().cerrar()
        for d in (self.teclado, self.raton, self.puntero):
            d.cerrar()


# --- autopy / pyautogui ------------------------------------------------------

# nombre KEY_ de Linux -> nombre de pyautogui (autopy usa los suyos, abajo)
_PYAG = {"LEFTCTRL": "ctrlleft", "RIGHTCTRL": "ctrlright", "LEFTSHIFT": "shiftleft",
         "RIGHTSHIFT": "shiftright", "LEFTALT": "altleft", "RIGHTALT": "altright",
         "LEFTMETA": "winleft", "RIGHTMETA": "winright", "ENTER": "enter", "ESC": "esc",
         "SPACE": "space", "TAB": "tab", "BACKSPACE": "backspace", "DELETE": "delete",
         "UP": "up", "DOWN": "down", "LEFT": "left", "RIGHT": "right", "PAGEUP": "pageup",
         "PAGEDOWN": "pagedown", "HOME": "home", "END": "end", "PLAYPAUSE": "playpause",
         "NEXTSONG": "nexttrack", "PREVIOUSSONG": "prevtrack", "VOLUMEUP": "volumeup",
         "VOLUMEDOWN": "volumedown", "MUTE": "volumemute", "INSERT": "insert"}


def _nombre_pyag(n):
    n = ALIAS.get(n.upper(), n.upper())
    if n in _PYAG:
        return _PYAG[n]
    if len(n) == 1 or (n.startswith("F") and n[1:].isdigit()):
        return n.lower()
    raise ValueError(f"pyautogui no tiene la tecla {n}")


class SalidaPyautogui(SalidaNula):
    nombre = "pyautogui"

    def __init__(self, _pantalla=None, sufijo=""):
        super().__init__()
        import pyautogui
        pyautogui.FAILSAFE = False         # el «failsafe» de la esquina se dispara al apuntar ahí con la mano
        pyautogui.PAUSE = 0
        self.p = pyautogui
        self.w, self.h = pyautogui.size()
        self._resto = [0.0, 0.0]

    def mover_abs(self, fx, fy):
        self.p.moveTo(int(fx * (self.w - 1)), int(fy * (self.h - 1)), _pause=False)

    def mover_rel(self, dx, dy):
        self._resto[0] += dx
        self._resto[1] += dy
        ix, iy = int(self._resto[0]), int(self._resto[1])
        if ix or iy:
            self._resto[0] -= ix
            self._resto[1] -= iy
            self.p.moveRel(ix, iy, _pause=False)

    def rueda(self, v, h=0.0):
        if v:
            self.p.scroll(round(v * 3) or (1 if v > 0 else -1), _pause=False)
        if h:
            self.p.hscroll(round(h * 3) or (1 if h > 0 else -1), _pause=False)

    def boton(self, nombre, pulsado):
        super().boton(nombre, pulsado)
        (self.p.mouseDown if pulsado else self.p.mouseUp)(button=nombre.lower(), _pause=False)

    def tecla(self, nombre, pulsado):
        k = _nombre_pyag(nombre)
        super().tecla(nombre, pulsado)
        (self.p.keyDown if pulsado else self.p.keyUp)(k, _pause=False)


class SalidaAutopy(SalidaNula):
    nombre = "autopy"

    def __init__(self, _pantalla=None, sufijo=""):
        super().__init__()
        import autopy
        self.a = autopy
        self.w, self.h = autopy.screen.size()
        K = autopy.key.Code
        self.especiales = {"ENTER": K.RETURN, "ESC": K.ESCAPE, "SPACE": K.SPACE, "TAB": K.TAB,
                           "BACKSPACE": K.BACKSPACE, "DELETE": K.DELETE, "UP": K.UP_ARROW,
                           "DOWN": K.DOWN_ARROW, "LEFT": K.LEFT_ARROW, "RIGHT": K.RIGHT_ARROW,
                           "PAGEUP": K.PAGE_UP, "PAGEDOWN": K.PAGE_DOWN, "HOME": K.HOME,
                           "END": K.END, "LEFTCTRL": K.CONTROL, "RIGHTCTRL": K.CONTROL,
                           "LEFTSHIFT": K.SHIFT, "RIGHTSHIFT": K.SHIFT, "LEFTALT": K.ALT,
                           "RIGHTALT": K.ALT, "LEFTMETA": K.META, "RIGHTMETA": K.META}
        for i in range(1, 13):
            self.especiales[f"F{i}"] = getattr(K, f"F{i}")
        self.botones = {"LEFT": autopy.mouse.Button.LEFT, "RIGHT": autopy.mouse.Button.RIGHT,
                        "MIDDLE": autopy.mouse.Button.MIDDLE}

    def mover_abs(self, fx, fy):
        self.a.mouse.move(fx * (self.w - 1), fy * (self.h - 1))

    def mover_rel(self, dx, dy):
        x, y = self.a.mouse.location()
        self.a.mouse.move(min(max(x + dx, 0), self.w - 1), min(max(y + dy, 0), self.h - 1))

    def rueda(self, v, h=0.0):
        pass                               # autopy no tiene rueda

    def boton(self, nombre, pulsado):
        if nombre not in self.botones:
            return
        super().boton(nombre, pulsado)
        self.a.mouse.toggle(self.botones[nombre], pulsado)

    def tecla(self, nombre, pulsado):
        n = ALIAS.get(nombre.upper(), nombre.upper())
        k = self.especiales.get(n) or (n.lower() if len(n) == 1 else None)
        if k is None:
            raise ValueError(f"autopy no tiene la tecla {n}")
        super().tecla(nombre, pulsado)
        self.a.key.toggle(k, pulsado, [])


SALIDAS = {"uinput": SalidaUinput, "autopy": SalidaAutopy, "pyautogui": SalidaPyautogui,
           "nula": SalidaNula}


def crear(nombre, monitor="", sufijo=""):
    """Crea la salida pedida; si no se puede, devuelve (SalidaNula, motivo)."""
    if nombre not in SALIDAS:
        return SalidaNula(), f"salida desconocida «{nombre}»"
    try:
        return SALIDAS[nombre](Pantalla(monitor), sufijo=sufijo), None
    except PermissionError:
        return SalidaNula(), "sin permiso para /dev/uinput (ejecuta ./instalar.sh)"
    except Exception as e:   # librería sin instalar, sin servidor X, sin ~/.Xauthority…
        motivo = f"{type(e).__name__}: {e}"
        if os.environ.get("WAYLAND_DISPLAY") and nombre != "uinput":
            motivo += " (en Wayland usa la salida uinput)"
        return SalidaNula(), motivo
