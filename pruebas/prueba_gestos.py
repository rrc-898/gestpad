"""Pruebas sin cámara: manos y caras sintéticas → gestos → salidas.

    .venv/bin/python pruebas/prueba_gestos.py

No toca el escritorio: el motor usa la salida «nula» (que apunta lo pulsado).
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gestpad import cara, config, mano  # noqa: E402
from gestpad.motor import Motor  # noqa: E402
from gestpad.salida import SalidaNula  # noqa: E402

FALLOS = []
N = 0


def comprobar(cond, texto):
    global N
    N += 1
    print(("  ok  " if cond else "  MAL ") + texto)
    if not cond:
        FALLOS.append(texto)


# ------------------------------------------------------------ mano sintética
def _dedo(base, direccion, largos, curva):
    """Cuatro puntos desde `base`, doblando `curva` rad en cada articulación hacia la palma (+z)."""
    pts = [np.array(base, float)]
    ang = 0.0
    d = np.array(direccion, float) / np.linalg.norm(direccion)
    for largo in largos:
        ang += curva
        # gira la dirección en el plano (d, z)
        v = d * math.cos(ang) + np.array([0, 0, 1.0]) * math.sin(ang)
        pts.append(pts[-1] + v * largo)
    return pts


def mano_sintetica(estirados=(True,) * 5, pulgar="lado", pinza=None, centro=(0.5, 0.5), escala=1.0):
    """Devuelve (img, mundo) de 21 puntos. Mundo en metros, imagen en 0..1 (y hacia abajo)."""
    w = np.zeros((21, 3))
    xs = (-0.03, -0.01, 0.01, 0.03)
    for i, x in enumerate(xs):
        base = (x, -0.09 + abs(x) * 0.3, 0)
        curva = 0.0 if estirados[i + 1] else 1.45
        w[5 + 4 * i:9 + 4 * i] = _dedo(base, (0, -1, 0), (0.035, 0.025, 0.02), curva)
    # pulgar: 1 CMC, 2 MCP, 3 IP, 4 punta
    w[1] = (-0.035, -0.02, 0)
    if pulgar == "arriba":
        d, curva = (0, -1, 0), 0.0
        w[1] = (-0.045, -0.04, 0)
    elif pulgar == "abajo":
        d, curva = (0, 1, 0), 0.0
        w[1] = (-0.045, -0.02, 0)
    elif estirados[0]:
        d, curva = (-1, -0.6, 0), 0.0
    else:
        d, curva = (1, -0.4, 0), 0.9         # cruzado sobre la palma
    pts = _dedo(w[1], d, (0.035, 0.03, 0.025), curva)
    w[2:5] = pts[1:]
    if pinza:
        punta = {"INDICE": 8, "CORAZON": 12, "ANULAR": 16, "MENIQUE": 20}[pinza]
        w[4] = w[punta] + np.array([0.004, 0.004, 0])
    img = np.zeros((21, 3))
    img[:, 0] = centro[0] + w[:, 0] * 2.2 * escala
    img[:, 1] = centro[1] + 0.1 + w[:, 1] * 2.2 * escala * (640 / 480)
    img[:, 2] = w[:, 2]
    return img.astype(np.float32), w.astype(np.float32)


def pose_de(**k):
    img, w = mano_sintetica(**k)
    return mano.rasgos(img, w)["pose"]


print("Poses de la mano")
comprobar(pose_de() == "PALMA", "mano abierta → PALMA")
comprobar(pose_de(estirados=(False,) * 5) == "PUNO", "todo doblado → PUNO")
comprobar(pose_de(estirados=(False, True, False, False, False)) == "SENALAR", "solo índice → SENALAR")
comprobar(pose_de(estirados=(False, True, True, False, False)) == "PAZ", "índice + corazón → PAZ")
comprobar(pose_de(estirados=(False, True, True, True, False)) == "TRES", "tres dedos → TRES")
comprobar(pose_de(estirados=(False, True, False, False, True)) == "ROCK", "índice + meñique → ROCK")
comprobar(pose_de(estirados=(True, False, False, False, True)) == "LLAMAME", "pulgar + meñique → LLAMAME")
comprobar(pose_de(estirados=(True, False, False, False, False), pulgar="arriba") == "PULGAR_ARRIBA",
          "puño con pulgar arriba → PULGAR_ARRIBA")
comprobar(pose_de(estirados=(True, False, False, False, False), pulgar="abajo") == "PULGAR_ABAJO",
          "puño con pulgar abajo → PULGAR_ABAJO")
img, w = mano_sintetica(pinza="INDICE")
r = mano.rasgos(img, w)
comprobar(r["pinzas"]["PINZA_INDICE"] < 0.15 and r["pinzas"]["PINZA_MENIQUE"] > 0.45,
          f"pinza índice: {r['pinzas']['PINZA_INDICE']:.2f} cerca, meñique {r['pinzas']['PINZA_MENIQUE']:.2f} lejos")
r_lejos = mano.rasgos(*mano_sintetica(escala=0.5))
comprobar(r_lejos["pose"] == "PALMA", "la pose no depende del tamaño en la imagen")

print("Seguidor de la mano")
s = mano.SeguidorMano(config.OPCIONES)
t = 0.0
act = set()
for _ in range(3):           # 0,1 s: aún no
    act, _ = s.actualizar(mano.rasgos(*mano_sintetica(estirados=(False,) * 5)), t)
    t += 1 / 30
comprobar("PUNO" not in act, "un puño de 3 fotogramas no cuenta todavía")
for _ in range(5):
    act, _ = s.actualizar(mano.rasgos(*mano_sintetica(estirados=(False,) * 5)), t)
    t += 1 / 30
comprobar("PUNO" in act, "puño mantenido 0,25 s → activo")
act, _ = s.actualizar(None, t)
comprobar(act == set(), "sin mano → nada activo")
ev_total = []
for i in range(10):          # pasa de x=0,3 a x=0,7 en 0,3 s
    r = mano.rasgos(*mano_sintetica(centro=(0.3 + 0.04 * i, 0.5)))
    _, ev = s.actualizar(r, t)
    ev_total += ev
    t += 1 / 30
comprobar(ev_total == ["DESLIZAR_DER"], f"mano rápida a la derecha → {ev_total}")
ev_total = []
for i in range(30):          # lenta: 0,4 en 1 s
    r = mano.rasgos(*mano_sintetica(centro=(0.7 - 0.013 * i, 0.5)))
    _, ev = s.actualizar(r, t)
    ev_total += ev
    t += 1 / 30
comprobar(ev_total == [], "mano lenta → no es deslizar")

# ------------------------------------------------------------ cara
print("Cara")


def rasgo_cara(giro=0.0, cabeceo=0.0, ladeo=0.0, ear_i=0.30, ear_d=0.30, **blend):
    return {"giro": giro, "cabeceo": cabeceo, "ladeo": ladeo, "ear_izq": ear_i, "ear_der": ear_d,
            "blend": blend, "nariz": (0.5, 0.5)}


sc = cara.SeguidorCara(config.OPCIONES)
t = 0.0


def pasar(n, **k):
    global t
    act, evs = set(), []
    for _ in range(n):
        act, ev = sc.actualizar(rasgo_cara(**k), t)
        evs += ev
        t += 1 / 30
    return act, evs


pasar(25, giro=5.0, cabeceo=-3.0)               # postura neutra torcida
comprobar(sc.neutro is not None and abs(sc.neutro[0] - 5.0) < 1e-6, "toma la postura neutra sola")
pasar(30, giro=5.0, cabeceo=-3.0)               # rellena la línea base de los ojos
act, _ = pasar(10, giro=5.0 + 15, cabeceo=-3.0)
comprobar("CABEZA_DER" in act, "girar 15° a la derecha (sobre el neutro) → CABEZA_DER")
act, _ = pasar(10, giro=5.0, cabeceo=-3.0 + 15)
comprobar("CABEZA_ARRIBA" in act and "CABEZA_DER" not in act, "mirar arriba → CABEZA_ARRIBA")
act, _ = pasar(10, giro=5.0, cabeceo=-3.0, ear_i=0.08)
comprobar("GUINO_IZQ" in act and "GUINO_DER" not in act, "cerrar solo el izquierdo → GUINO_IZQ")
pasar(10, giro=5.0, cabeceo=-3.0)
vistos = set()
for _ in range(4):                               # parpadeo normal: ~0,13 s con los dos ojos
    act, _ = pasar(1, giro=5.0, cabeceo=-3.0, ear_i=0.08, ear_d=0.08)
    vistos |= act
comprobar(not vistos & {"GUINO_IZQ", "GUINO_DER"}, "parpadeo (los dos) → ningún guiño")
act, _ = pasar(25, giro=5.0, cabeceo=-3.0, ear_i=0.08, ear_d=0.08)
comprobar("OJOS_CERRADOS" in act, "los dos cerrados más de 0,6 s → OJOS_CERRADOS")
act, _ = pasar(10, giro=5.0, cabeceo=-3.0, jawOpen=0.7)
comprobar("BOCA_ABIERTA" in act, "jawOpen 0,7 → BOCA_ABIERTA")
_, ev = pasar(6, giro=5.0, cabeceo=-3.0 - 14)
_, ev2 = pasar(6, giro=5.0, cabeceo=-3.0)
comprobar("ASENTIR" in ev + ev2, "bajar y subir la cabeza → ASENTIR")
_, ev = pasar(5, giro=5.0 + 14, cabeceo=-3.0)
_, ev2 = pasar(5, giro=5.0 - 14, cabeceo=-3.0)
comprobar("NEGAR" in ev + ev2, "derecha-izquierda → NEGAR")

# rasgos geométricos: con la cara girada a la derecha (nariz a la derecha) el giro sale positivo
lm = np.zeros((478, 3), np.float32)
lm[:, :2] = 0.5
lm[33] = (0.42, 0.45, 0.0)
lm[263] = (0.58, 0.45, 0.0)
lm[10] = (0.5, 0.35, 0.0)
lm[152] = (0.5, 0.65, 0.0)
r0 = cara.rasgos(lm, {}, 640, 480)
lm2 = lm.copy()
lm2[33, 2], lm2[263, 2] = -0.03, 0.03     # el ojo izquierdo (imagen) se acerca: la cara mira a su derecha
r1 = cara.rasgos(lm2, {}, 640, 480)
comprobar(abs(r0["giro"]) < 1e-3 and r1["giro"] > 5, f"giro geométrico: {r0['giro']:.1f}° → {r1['giro']:.1f}°")

# ------------------------------------------------------------ configuración
print("Configuración")
cfg = config.cargar(config.RUTA_EJEMPLO)
comprobar(len(cfg["perfiles"]) >= 6, f"{len(cfg['perfiles'])} perfiles de ejemplo cargan sin errores")
comprobar(cfg["perfiles"][0]["necesita"] == {"mano"}, "raton_mano solo necesita la mano")
try:
    config.comprobar_destino("key:NOEXISTE", "prueba")
    comprobar(False, "tecla inexistente rechazada")
except config.ErrorPerfil:
    comprobar(True, "tecla inexistente rechazada")
comprobar(config.comprobar_destino("rep:key:SUPER+TAB", "x") == "rep:key:SUPER+TAB", "rep: con combinación vale")


# ------------------------------------------------------------ motor completo
print("Motor (salida nula)")


class Grabadora(SalidaNula):
    def __init__(self):
        super().__init__()
        self.abs, self.rel, self.ruedas, self.historial = [], [], [], []

    def mover_abs(self, fx, fy):
        self.abs.append((fx, fy))

    def mover_rel(self, dx, dy):
        self.rel.append((dx, dy))

    def rueda(self, v, h=0.0):
        self.ruedas.append((v, h))

    def tecla(self, n, p):
        super().tecla(n, p)
        self.historial.append(("key", n, p))

    def boton(self, n, p):
        super().boton(n, p)
        self.historial.append(("mouse", n, p))


def motor_con(perfil):
    cfg = config.cargar(config.RUTA_EJEMPLO)
    cfg["general"]["perfil_inicial"] = perfil
    m = Motor(cfg, prueba=True)
    m.salida = Grabadora()
    return m


FRAME = np.zeros((480, 640, 3), np.uint8)
m = motor_con("raton_mano")
t = 100.0


def paso(m, mano_k=None, cara_res=None, n=1):
    global t
    for _ in range(n):
        manos = [(*mano_sintetica(**mano_k), "derecha", 0.9)] if mano_k is not None else []
        m._paso({"manos": manos, "cara": cara_res}, FRAME, t, m.perfil["necesita"])
        t += 1 / 30


paso(m, {"centro": (0.5, 0.45), "pinza": "INDICE", "estirados": (True, True, False, False, False)}, n=10)
comprobar(not m.salida.abs and "mouse:LEFT" not in m.salida.pulsados,
          "sin enganchar (no ha enseñado la palma): ni cursor ni clic")
paso(m, None, n=30)
paso(m, {"centro": (0.5, 0.45)}, n=10)
comprobar(m.enganchado and len(m.salida.abs) > 0, "enseña la palma → enganchada, mueve el cursor")
fx, fy = m.salida.abs[-1]
comprobar(abs(fx - 0.5) < 0.1, f"palma en el centro de la zona → cursor cerca del centro ({fx:.2f}, {fy:.2f})")
paso(m, {"centro": (0.5, 0.45), "pinza": "INDICE"}, n=8)
comprobar("mouse:LEFT" in m.salida.pulsados, "pinza índice → clic izquierdo pulsado")
comprobar("mouse:RIGHT" not in m.salida.pulsados, "…y no el derecho (el corazón está cerca, pero menos)")
paso(m, None, n=2)
comprobar("mouse:LEFT" not in m.salida.pulsados, "se pierde la mano → el clic se suelta")
comprobar(m.enganchado, "un corte de 2 fotogramas no la desengancha")
paso(m, {"estirados": (False,) * 5, "centro": (0.5, 0.45)}, n=8)
comprobar(m.acc.mod("SCROLL"), "puño → modo scroll")
paso(m, {"estirados": (False,) * 5, "centro": (0.5, 0.25)}, n=10)
comprobar(any(v > 0 for v, _h in m.salida.ruedas), "puño y subir la mano → scroll hacia arriba")
paso(m, None, n=2)
# pausa con PAZ mantenida 1,5 s
paso(m, {"estirados": (False, True, True, False, False)}, n=52)
comprobar(m.pausado, "PAZ 1,5 s → pausa")
paso(m, {"pinza": "INDICE"}, n=8)
comprobar("mouse:LEFT" not in m.salida.pulsados, "en pausa la pinza no hace clic")
paso(m, None, n=2)
paso(m, {"estirados": (False, True, True, False, False)}, n=52)
comprobar(not m.pausado, "PAZ otra vez → reanuda")

m = motor_con("escritorio")
paso(m, None, n=2)
m.salida.historial.clear()
for i in range(10):
    paso(m, {"centro": (0.3 + 0.04 * i, 0.5)})
teclas = [(n, p) for tipo, n, p in m.salida.historial if tipo == "key"]
comprobar(teclas == [("LEFTMETA", True), ("U", True), ("U", False), ("LEFTMETA", False)],
          f"deslizar a la derecha → SUPER+U en orden ({teclas})")
comprobar(not m.salida.pulsados, "después del toque no queda nada pulsado")

m = motor_con("multimedia")
paso(m, {}, n=6)                                  # enseñar la palma = enganchar
m.salida.historial.clear()
paso(m, {"estirados": (True, False, False, False, False), "pulgar": "arriba"}, n=30)
vol = [p for tipo, n, p in m.salida.historial if n == "VOLUMEUP" and p]
comprobar(len(vol) >= 3, f"pulgar arriba 1 s → volumen repetido ({len(vol)} toques)")

m = motor_con("juego_cabeza")
lm_cara = lm.copy()
paso(m, None, cara_res=(lm_cara, {}), n=25)
comprobar(m.seg_cara.neutro is not None, "juego_cabeza calibra el neutro con la cara")
paso(m, None, cara_res=(lm_cara, {"jawOpen": 0.8}), n=6)
comprobar("key:C" in m.salida.pulsados, "boca abierta → C pulsada (saltar)")
paso(m, None, cara_res=None, n=2)
comprobar("key:C" not in m.salida.pulsados, "se pierde la cara → C se suelta")

print()
if FALLOS:
    print(f"{len(FALLOS)} de {N} comprobaciones FALLAN:")
    for f in FALLOS:
        print("   -", f)
    sys.exit(1)
print(f"Todo bien: {N} comprobaciones.")
