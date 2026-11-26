"""Gestos de la cara a partir de MediaPipe FaceLandmarker (478 puntos + blendshapes).

La imagen llega en espejo, así que «izquierda» es siempre la izquierda de la
persona (lo que se ve a la izquierda de la vista previa).

- Orientación de la cabeza: normal del plano ojos-frente-barbilla con la z de
  MediaPipe. Giro (yaw) + = mirar a tu derecha · cabeceo (pitch) + = mirar
  arriba · ladeo (roll) + = inclinar la cabeza hacia el hombro derecho.
  Todo relativo a la postura neutra (se toma sola al empezar y con «recentrar»).
- Guiños: apertura de cada ojo (EAR) dividida por su propia línea base de ojo
  abierto, medida sobre la marcha. Se usa la geometría y no los blendshapes
  eyeBlinkLeft/Right porque con la imagen en espejo no está claro de qué lado es cada uno.
- Boca, sonrisa, cejas, beso, mejillas: blendshapes (son simétricos).
"""

import math
import time
from statistics import median

import numpy as np

from .filtros import Estable, Histeresis, Percentil

_OJO_A = (33, 160, 158, 133, 153, 144)
_OJO_B = (362, 385, 387, 263, 373, 380)

EXPRESIONES = ("GUINO_IZQ", "GUINO_DER", "OJOS_CERRADOS", "BOCA_ABIERTA", "SONRISA",
               "CEJAS_ARRIBA", "BESO", "MEJILLAS")
CABEZA = ("CABEZA_IZQ", "CABEZA_DER", "CABEZA_ARRIBA", "CABEZA_ABAJO", "LADEAR_IZQ", "LADEAR_DER")
EVENTOS = ("ASENTIR", "NEGAR")
GESTOS = EXPRESIONES + CABEZA + EVENTOS

DESCRIPCION = {
    "GUINO_IZQ": "guiñar el ojo izquierdo",
    "GUINO_DER": "guiñar el ojo derecho",
    "OJOS_CERRADOS": "cerrar los dos ojos un rato (más de medio segundo)",
    "BOCA_ABIERTA": "abrir la boca",
    "SONRISA": "sonreír",
    "CEJAS_ARRIBA": "levantar las cejas",
    "BESO": "morritos (labios hacia delante)",
    "MEJILLAS": "inflar los mofletes",
    "CABEZA_IZQ": "girar la cabeza a la izquierda",
    "CABEZA_DER": "girar la cabeza a la derecha",
    "CABEZA_ARRIBA": "mirar hacia arriba",
    "CABEZA_ABAJO": "mirar hacia abajo",
    "LADEAR_IZQ": "inclinar la cabeza hacia el hombro izquierdo",
    "LADEAR_DER": "inclinar la cabeza hacia el hombro derecho",
    "ASENTIR": "decir que sí con la cabeza",
    "NEGAR": "decir que no con la cabeza",
}

# blendshape -> (encender, apagar)
_BLEND = {
    "BOCA_ABIERTA": (("jawOpen",), 0.40, 0.25),
    "SONRISA": (("mouthSmileLeft", "mouthSmileRight"), 0.55, 0.35),
    "CEJAS_ARRIBA": (("browInnerUp", "browOuterUpLeft", "browOuterUpRight"), 0.45, 0.28),
    "BESO": (("mouthPucker",), 0.60, 0.40),
    "MEJILLAS": (("cheekPuff",), 0.35, 0.20),
}


def _ear(p, idx):
    p1, p2, p3, p4, p5, p6 = (p[i] for i in idx)
    return (np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5)) / (2 * np.linalg.norm(p1 - p4) + 1e-6)


def rasgos(lm, blend, ancho, alto):
    """lm: array (478, 3) normalizado; blend: {nombre: valor}. Ángulos en grados, sin recentrar."""
    p = lm * np.array([ancho, alto, ancho], dtype=float)
    ex = p[263] - p[33]
    if ex[0] < 0:
        ex = -ex
    ey = p[152] - p[10]
    n = np.cross(ex, ey)                        # apunta hacia la nuca (z crece alejándose)
    giro = math.degrees(math.atan2(-n[0], n[2]))
    cabeceo = math.degrees(math.atan2(n[1], n[2]))
    ladeo = math.degrees(math.atan2(ex[1], ex[0]))
    ear_a, ear_b = _ear(p, _OJO_A), _ear(p, _OJO_B)
    a_izq = p[list(_OJO_A), 0].mean() < p[list(_OJO_B), 0].mean()
    return {
        "giro": giro, "cabeceo": cabeceo, "ladeo": ladeo,
        "ear_izq": float(ear_a if a_izq else ear_b),
        "ear_der": float(ear_b if a_izq else ear_a),
        "blend": blend,
        "nariz": (float(lm[1, 0]), float(lm[1, 1])),
    }


class _Vaiven:
    """Detecta un «sí» (bajar y volver) o un «no» (lado a lado) en una señal en grados."""

    def __init__(self, amplitud, ventana, ida_y_vuelta):
        self.a, self.ventana, self.doble = amplitud, ventana, ida_y_vuelta
        self.picos = []                     # [(t, signo)]
        self.enfriar = 0.0

    def __call__(self, x, t):
        self.picos = [pk for pk in self.picos if t - pk[0] <= self.ventana]
        signo = 1 if x > self.a else -1 if x < -self.a else 0
        if signo and (not self.picos or self.picos[-1][1] != signo):
            self.picos.append((t, signo))
        if t < self.enfriar:
            return False
        if self.doble:
            ok = len(self.picos) >= 2 and self.picos[-1][1] != self.picos[-2][1]
        else:  # asentir: bajar y volver cerca del centro
            ok = bool(self.picos) and self.picos[-1][1] < 0 and abs(x) < self.a * 0.35
        if ok:
            self.picos.clear()
            self.enfriar = t + 0.8
        return ok


class SeguidorCara:
    def __init__(self, opciones):
        self.op = opciones
        ent, sal = opciones.get("entrar_s", 0.12), opciones.get("salir_s", 0.08)
        self.estables = {g: Estable(ent, sal) for g in EXPRESIONES + CABEZA}
        self.estables["GUINO_IZQ"].entrar_s = self.estables["GUINO_DER"].entrar_s = \
            opciones.get("guino_s", 0.15)
        self.estables["OJOS_CERRADOS"].entrar_s = opciones.get("ojos_cerrados_s", 0.6)
        self.hist = {g: Histeresis(on, off) for g, (_, on, off) in _BLEND.items()}
        u = opciones.get("umbral_cabeza", 12.0)
        self.hist_cab = {g: Histeresis(u, u * 0.65) for g in CABEZA}
        self.base_izq, self.base_der = Percentil(), Percentil()
        self.hist_guino = {"izq": Histeresis(0.45, 0.30), "der": Histeresis(0.45, 0.30)}
        self.asentir = _Vaiven(opciones.get("asentir_grados", 9.0), 0.8, False)
        self.negar = _Vaiven(opciones.get("negar_grados", 10.0), 1.0, True)
        self.neutro = None                 # (giro, cabeceo, ladeo)
        self._muestras = []
        self.ultimo = None
        self.angulos = (0.0, 0.0, 0.0)
        self.cierre = (0.0, 0.0)

    def recentrar(self):
        self.neutro = None
        self._muestras = []

    def reiniciar(self):
        for e in self.estables.values():
            e.forzar(False)
        self.ultimo = None

    def actualizar(self, r, t=None):
        t = time.monotonic() if t is None else t
        if r is None:
            self.reiniciar()
            return set(), []
        self.ultimo = r
        crudo = (r["giro"], r["cabeceo"], r["ladeo"])
        if self.neutro is None:
            # postura neutra = mediana de los primeros ~0,7 s con cara
            self._muestras.append(crudo)
            if len(self._muestras) >= 20:
                self.neutro = tuple(median(m[i] for m in self._muestras) for i in range(3))
            return set(), []
        g, c, l = (crudo[i] - self.neutro[i] for i in range(3))
        self.angulos = (g, c, l)

        # ojos: cierre = 1 − apertura / apertura con el ojo abierto
        cierres = []
        for lado, base in (("izq", self.base_izq), ("der", self.base_der)):
            ear = r[f"ear_{lado}"]
            base.meter(ear)
            b = base.valor() or ear
            cierres.append(max(0.0, min(1.0, 1 - ear / max(b, 1e-6))))
        if self.op.get("invertir_guinos"):
            cierres.reverse()
        ci, cd = cierres
        self.cierre = (ci, cd)
        gi, gd = self.hist_guino["izq"](ci), self.hist_guino["der"](cd)
        crudos = {
            "GUINO_IZQ": gi and cd < 0.22,
            "GUINO_DER": gd and ci < 0.22,
            "OJOS_CERRADOS": ci > 0.5 and cd > 0.5,
        }
        for gest, (nombres, _, _) in _BLEND.items():
            v = sum(r["blend"].get(n, 0.0) for n in nombres) / len(nombres)
            crudos[gest] = self.hist[gest](v)
        crudos.update({
            "CABEZA_DER": self.hist_cab["CABEZA_DER"](g),
            "CABEZA_IZQ": self.hist_cab["CABEZA_IZQ"](-g),
            "CABEZA_ARRIBA": self.hist_cab["CABEZA_ARRIBA"](c),
            "CABEZA_ABAJO": self.hist_cab["CABEZA_ABAJO"](-c),
            "LADEAR_DER": self.hist_cab["LADEAR_DER"](l),
            "LADEAR_IZQ": self.hist_cab["LADEAR_IZQ"](-l),
        })
        activos = {k for k, v in crudos.items() if self.estables[k](v, t)}
        eventos = []
        if self.asentir(c, t):
            eventos.append("ASENTIR")
        if self.negar(g, t):
            eventos.append("NEGAR")
        return activos, eventos
