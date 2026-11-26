"""Filtros pequeños: suavizado del cursor y estabilizado de gestos."""

import math
from collections import deque


class UnoEuro:
    """Filtro «1€» (Casiez et al., 2012): poco retraso al moverse rápido y
    mucho suavizado al estar quieto, que es justo lo que pide un cursor.

    min_corte: Hz de corte en reposo (más bajo = menos temblor, más retraso)
    beta:      cuánto sube el corte con la velocidad (más alto = menos retraso)
    """

    def __init__(self, min_corte=1.0, beta=10.0, corte_d=1.0):
        self.min_corte, self.beta, self.corte_d = min_corte, beta, corte_d
        self.x = self.dx = self.t = None

    @staticmethod
    def _alfa(corte, dt):
        tau = 1.0 / (2 * math.pi * corte)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t):
        if self.t is None or t <= self.t:
            self.x, self.dx, self.t = x, 0.0, t
            return x
        dt = t - self.t
        self.t = t
        dx = (x - self.x) / dt
        a_d = self._alfa(self.corte_d, dt)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        corte = self.min_corte + self.beta * abs(self.dx)
        a = self._alfa(corte, dt)
        self.x = a * x + (1 - a) * self.x
        return self.x

    def reiniciar(self):
        self.x = self.dx = self.t = None


class Estable:
    """Un booleano que solo cambia si el valor nuevo se mantiene un rato.

    Evita que un fotograma suelto (un parpadeo, una mano a medio cerrar)
    pulse o suelte una tecla.
    """

    def __init__(self, entrar_s=0.12, salir_s=0.08):
        self.entrar_s, self.salir_s = entrar_s, salir_s
        self.valor = False
        self.desde = None          # instante en que empezó a verse el valor contrario
        self.cambio = 0.0          # instante del último cambio confirmado

    def __call__(self, crudo, t):
        if crudo == self.valor:
            self.desde = None
            return self.valor
        if self.desde is None:
            self.desde = t
        if t - self.desde >= (self.entrar_s if crudo else self.salir_s):
            self.valor = crudo
            self.desde = None
            self.cambio = t
        return self.valor

    def forzar(self, valor=False, t=0.0):
        self.valor, self.desde, self.cambio = valor, None, t


class Histeresis:
    """Umbral con dos niveles: se enciende por encima de `alto` y no se apaga
    hasta bajar de `bajo` (o al revés con invertido=True)."""

    def __init__(self, alto, bajo, invertido=False):
        self.alto, self.bajo, self.invertido = alto, bajo, invertido
        self.valor = False

    def __call__(self, x):
        if self.invertido:
            x, alto, bajo = -x, -self.alto, -self.bajo
        else:
            alto, bajo = self.alto, self.bajo
        if self.valor:
            self.valor = x > bajo
        else:
            self.valor = x > alto
        return self.valor


class Percentil:
    """Percentil móvil de los últimos `n` valores (línea base de un ojo abierto)."""

    def __init__(self, n=300, p=80, minimo=20):
        self.datos = deque(maxlen=n)
        self.p, self.minimo = p, minimo

    def meter(self, x):
        self.datos.append(x)

    def valor(self):
        if len(self.datos) < self.minimo:
            return None
        orden = sorted(self.datos)
        i = min(len(orden) - 1, int(len(orden) * self.p / 100))
        return orden[i]
