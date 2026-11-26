"""Gestos de la mano a partir de los 21 puntos de MediaPipe HandLandmarker.

Puntos: 0 muñeca · pulgar 1-4 · índice 5-8 · corazón 9-12 · anular 13-16 · meñique 17-20
(cada dedo: nudillo, dos falanges, punta).

Las medidas de forma (dedo estirado, pinza) se hacen con los puntos «world»
(3D en metros, centrados en la mano), así no dependen de lo lejos que esté
la mano ni de si apunta a la cámara. La posición se toma de los puntos de
imagen (0..1), ya en espejo.
"""

import time
from collections import deque

import numpy as np

from .filtros import Estable, Histeresis

DEDOS = ("pulgar", "indice", "corazon", "anular", "menique")
_ARTIC = ((1, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20))
_PUNTAS = {"INDICE": 8, "CORAZON": 12, "ANULAR": 16, "MENIQUE": 20}

POSES = ("PALMA", "PUNO", "SENALAR", "PAZ", "TRES", "ROCK", "LLAMAME",
         "PULGAR_ARRIBA", "PULGAR_ABAJO")
PINZAS = tuple(f"PINZA_{d}" for d in _PUNTAS)
DESLIZAR = ("DESLIZAR_IZQ", "DESLIZAR_DER", "DESLIZAR_ARRIBA", "DESLIZAR_ABAJO")
GESTOS = POSES + PINZAS + DESLIZAR

DESCRIPCION = {
    "PALMA": "mano abierta (los cuatro dedos estirados)",
    "PUNO": "puño cerrado",
    "SENALAR": "solo el índice estirado",
    "PAZ": "índice y corazón en V",
    "TRES": "índice, corazón y anular",
    "ROCK": "índice y meñique (cuernos)",
    "LLAMAME": "pulgar y meñique (teléfono)",
    "PULGAR_ARRIBA": "puño con el pulgar hacia arriba",
    "PULGAR_ABAJO": "puño con el pulgar hacia abajo",
    "PINZA_INDICE": "juntar pulgar e índice",
    "PINZA_CORAZON": "juntar pulgar y corazón",
    "PINZA_ANULAR": "juntar pulgar y anular",
    "PINZA_MENIQUE": "juntar pulgar y meñique",
    "DESLIZAR_IZQ": "pasar la mano rápido hacia la izquierda",
    "DESLIZAR_DER": "pasar la mano rápido hacia la derecha",
    "DESLIZAR_ARRIBA": "pasar la mano rápido hacia arriba",
    "DESLIZAR_ABAJO": "pasar la mano rápido hacia abajo",
}


def _d(a, b):
    return float(np.linalg.norm(a - b))


def _rectitud(w, idx):
    """1 = dedo totalmente recto; ~0,3 = doblado del todo."""
    tramos = sum(_d(w[idx[i]], w[idx[i + 1]]) for i in range(3))
    return _d(w[idx[0]], w[idx[3]]) / max(tramos, 1e-6)


def rasgos(img, mundo, umbral_dedo=0.86, umbral_pulgar=0.55):
    """img, mundo: arrays (21, 3). Devuelve un dict con la forma de la mano."""
    palma = max(_d(mundo[0], mundo[9]), 1e-4)
    rect = [_rectitud(mundo, a) for a in _ARTIC]
    estirado = [False] * 5
    for i in range(1, 5):
        _mcp, pip, _dip, punta = _ARTIC[i]
        # recto y con la punta más lejos de la muñeca que la falange media
        estirado[i] = rect[i] > umbral_dedo and _d(mundo[punta], mundo[0]) > _d(mundo[pip], mundo[0])
    # pulgar: recto y con la punta lejos del nudillo del índice (en el puño queda encima)
    sep_pulgar = _d(mundo[4], mundo[5]) / palma
    estirado[0] = rect[0] > 0.85 and sep_pulgar > umbral_pulgar

    pinzas = {f"PINZA_{k}": _d(mundo[4], mundo[p]) / palma for k, p in _PUNTAS.items()}

    # hacia dónde apunta el pulgar en la imagen (y crece hacia abajo)
    v = img[4, :2] - img[2, :2]
    n = float(np.linalg.norm(v)) or 1e-6
    pulgar_vertical = float(v[1] / n)          # −1 arriba · +1 abajo

    i, c, a, m = estirado[1:]
    pose = None
    if i and c and a and m:
        pose = "PALMA"
    elif estirado[0] and not (i or c or a or m) and abs(pulgar_vertical) > 0.6:
        pose = "PULGAR_ARRIBA" if pulgar_vertical < 0 else "PULGAR_ABAJO"
    elif not (i or c or a or m):
        pose = "PUNO"
    elif i and not c and not a and not m:
        pose = "SENALAR"
    elif i and c and not a and not m:
        pose = "PAZ"
    elif i and c and a and not m:
        pose = "TRES"
    elif i and m and not c and not a:
        pose = "ROCK"
    elif m and estirado[0] and not (i or c or a):
        pose = "LLAMAME"

    ancla = img[[0, 5, 9, 13, 17], :2].mean(axis=0)
    return {
        "estirado": estirado,
        "rectitud": rect,
        "pose": pose,
        "pinzas": pinzas,
        "ancla": (float(ancla[0]), float(ancla[1])),
        "punta_indice": (float(img[8, 0]), float(img[8, 1])),
        "tam": _d(img[0, :2], img[9, :2]),
    }


class SeguidorMano:
    """Convierte los rasgos de cada fotograma en gestos estables y eventos."""

    def __init__(self, opciones):
        self.op = opciones
        ent, sal = opciones.get("entrar_s", 0.12), opciones.get("salir_s", 0.08)
        self.estables = {g: Estable(ent, sal) for g in POSES + PINZAS}
        on, off = opciones.get("pinza_cerrar", 0.30), opciones.get("pinza_abrir", 0.45)
        self.hist_pinza = {g: Histeresis(-on, -off) for g in PINZAS}   # ratio pequeño = pinza
        self.historial = deque(maxlen=64)       # (t, x, y) del ancla
        self.enfriar_hasta = 0.0
        self.ultimo = None

    def reiniciar(self):
        for e in self.estables.values():
            e.forzar(False)
        for h in self.hist_pinza.values():
            h.valor = False
        self.historial.clear()
        self.ultimo = None

    def actualizar(self, r, t=None):
        """r: salida de rasgos() o None si no hay mano. Devuelve (activos, eventos)."""
        t = time.monotonic() if t is None else t
        if r is None:
            self.reiniciar()
            return set(), []
        self.ultimo = r
        activos = set()
        for g in POSES:
            if self.estables[g](r["pose"] == g, t):
                activos.add(g)
        # solo cuenta el dedo más cercano al pulgar: con la mano abierta las puntas del
        # índice y del corazón están juntas y si no saltarían las dos pinzas a la vez
        cerca = min(PINZAS, key=lambda g: r["pinzas"][g])
        for g in PINZAS:
            crudo = self.hist_pinza[g](-r["pinzas"][g]) and g == cerca
            if self.estables[g](crudo, t):
                activos.add(g)
        return activos, self._deslizar(r, t)

    def _deslizar(self, r, t):
        x, y = r["ancla"]
        self.historial.append((t, x, y))
        requiere = self.op.get("deslizar_con", "")
        if requiere and r["pose"] != requiere:
            return []
        if t < self.enfriar_hasta:
            return []
        ventana = self.op.get("deslizar_ventana_s", 0.30)
        dist = self.op.get("deslizar_distancia", 0.20)
        viejos = [h for h in self.historial if t - h[0] <= ventana]
        if len(viejos) < 3:
            return []
        t0, x0, y0 = viejos[0]
        dx, dy = x - x0, y - y0
        ev = None
        if abs(dx) > dist and abs(dx) > 1.8 * abs(dy):
            ev = "DESLIZAR_DER" if dx > 0 else "DESLIZAR_IZQ"
        elif abs(dy) > dist and abs(dy) > 1.8 * abs(dx):
            ev = "DESLIZAR_ABAJO" if dy > 0 else "DESLIZAR_ARRIBA"
        if ev:
            self.enfriar_hasta = t + self.op.get("deslizar_enfriar_s", 0.6)
            self.historial.clear()
            return [ev]
        return []
