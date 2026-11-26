"""Cámara (cv2) y detectores (MediaPipe Tasks: HandLandmarker y FaceLandmarker)."""

import fcntl
import os
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

_AQUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS_MODELOS = [os.path.join(_AQUI, "modelos"),
                os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
                             "gestpad", "modelos")]
URL_MODELOS = {
    "hand_landmarker.task": "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
                            "hand_landmarker/float16/latest/hand_landmarker.task",
    "face_landmarker.task": "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
                            "face_landmarker/float16/latest/face_landmarker.task",
}


def ruta_modelo(nombre):
    for d in DIRS_MODELOS:
        r = os.path.join(d, nombre)
        if os.path.exists(r):
            return r
    raise FileNotFoundError(f"falta el modelo {nombre}: ejecuta ./instalar.sh (lo descarga en modelos/)")


# V4L2: VIDIOC_G_CTRL / VIDIOC_S_CTRL con struct v4l2_control {u32 id; s32 value}
_VIDIOC_G_CTRL = (3 << 30) | (8 << 16) | (ord("V") << 8) | 27
_VIDIOC_S_CTRL = (3 << 30) | (8 << 16) | (ord("V") << 8) | 28
_CID_PRIORIDAD_EXPOSICION = 0x009A0903      # «exposure_dynamic_framerate» de UVC


class _FpsConstantes:
    """Con poca luz la webcam alarga la exposición y baja a ~10 fps. Esto le
    quita ese permiso mientras gestpad la usa (y lo devuelve al cerrar)."""

    def __init__(self, ruta):
        self.fd, self.antes = None, None
        try:
            self.fd = os.open(ruta, os.O_RDWR | os.O_NONBLOCK)
            buf = bytearray(struct.pack("Ii", _CID_PRIORIDAD_EXPOSICION, 0))
            fcntl.ioctl(self.fd, _VIDIOC_G_CTRL, buf)
            self.antes = struct.unpack("Ii", buf)[1]
            self._poner(0)
        except OSError:
            self.cerrar()

    def _poner(self, v):
        fcntl.ioctl(self.fd, _VIDIOC_S_CTRL, bytearray(struct.pack("Ii", _CID_PRIORIDAD_EXPOSICION, v)))

    def cerrar(self):
        if self.fd is None:
            return
        try:
            if self.antes is not None:
                self._poner(self.antes)
        except OSError:
            pass
        os.close(self.fd)
        self.fd = None


class Camara:
    """Lee la webcam en un hilo y se queda siempre con el último fotograma."""

    def __init__(self, fuente=0, ancho=640, alto=480, fps=30, fps_constantes=True):
        self.fuente = int(fuente) if str(fuente).isdigit() else fuente
        self.ancho, self.alto, self.fps = ancho, alto, fps
        self.fps_constantes = fps_constantes
        self._ctrl = None
        self.cond = threading.Condition()
        self.frame, self.n = None, 0
        self.error = None
        self._parar = False
        self.hilo = threading.Thread(target=self._bucle, name="camara", daemon=True)
        self.hilo.start()

    def _abrir(self):
        cap = cv2.VideoCapture(self.fuente, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.ancho)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.alto)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        if self.fps_constantes and self._ctrl is None:
            ruta = f"/dev/video{self.fuente}" if isinstance(self.fuente, int) else self.fuente
            self._ctrl = _FpsConstantes(ruta)
        return cap

    def _bucle(self):
        cap = None
        while not self._parar:
            if cap is None:
                cap = self._abrir()
                if cap is None:
                    self.error = f"no puedo abrir la cámara {self.fuente}"
                    time.sleep(1.5)
                    continue
            ok, f = cap.read()
            if not ok:
                self.error = "la cámara no da imagen (¿la usa otro programa?)"
                cap.release()
                cap = None
                time.sleep(1.0)
                continue
            self.error = None
            with self.cond:
                self.frame, self.n = f, self.n + 1
                self.cond.notify_all()
        if cap is not None:
            cap.release()
        if self._ctrl is not None:
            self._ctrl.cerrar()

    def esperar(self, ultimo_n, timeout=0.5):
        """Devuelve (frame, n) en cuanto haya uno más nuevo que ultimo_n (o (None, n) al vencer)."""
        with self.cond:
            self.cond.wait_for(lambda: self.n != ultimo_n or self._parar, timeout)
            if self.n == ultimo_n:
                return None, ultimo_n
            return self.frame, self.n

    def cerrar(self):
        self._parar = True
        with self.cond:
            self.cond.notify_all()
        self.hilo.join(timeout=2)


class Detectores:
    """Crea los landmarkers según haga falta y los pasa por cada fotograma."""

    def __init__(self, num_manos=1):
        # con 2, MediaPipe busca la segunda mano en CADA fotograma mientras solo vea una
        # (≈50 ms en vez de ≈20): solo se piden 2 si el perfil elige mano derecha/izquierda
        self.num_manos = num_manos
        self.mano = self.cara = None
        self.t_ms = 0
        self._hilo_cara = ThreadPoolExecutor(1, thread_name_prefix="cara")

    def _crear(self, cual):
        from mediapipe.tasks.python import BaseOptions, vision
        if cual == "mano":
            return vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=ruta_modelo("hand_landmarker.task")),
                running_mode=vision.RunningMode.VIDEO, num_hands=self.num_manos,
                min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.6,
                min_tracking_confidence=0.5))
        return vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=ruta_modelo("face_landmarker.task")),
            running_mode=vision.RunningMode.VIDEO, num_faces=1,
            output_face_blendshapes=True, min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5, min_tracking_confidence=0.5))

    def poner_num_manos(self, n):
        if n != self.num_manos:
            self.num_manos = n
            if self.mano is not None:
                self.mano.close()
                self.mano = None

    def procesar(self, frame_bgr, necesita):
        """Devuelve {'manos': [(img, mundo, lado, confianza)], 'cara': (lm, blend) | None}."""
        import mediapipe as mp
        # VIDEO exige marcas de tiempo estrictamente crecientes
        self.t_ms = max(self.t_ms + 1, int(time.monotonic() * 1000))
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        res = {"manos": [], "cara": None}
        if self.cara is None and "cara" in necesita:
            self.cara = self._crear("cara")
        if self.mano is None and "mano" in necesita:
            self.mano = self._crear("mano")
        # la cara va en otro hilo a la vez que la mano (MediaPipe suelta el GIL): ~27 ms en vez de ~37
        futura = self._hilo_cara.submit(self.cara.detect_for_video, img, self.t_ms) \
            if "cara" in necesita else None
        if "mano" in necesita:
            r = self.mano.detect_for_video(img, self.t_ms)
            for lm, wl, lado in zip(r.hand_landmarks, r.hand_world_landmarks, r.handedness):
                res["manos"].append((
                    np.array([[p.x, p.y, p.z] for p in lm], dtype=np.float32),
                    np.array([[p.x, p.y, p.z] for p in wl], dtype=np.float32),
                    # con la imagen en espejo, MediaPipe acierta el lado tal cual
                    "derecha" if lado[0].category_name == "Right" else "izquierda",
                    lado[0].score))
        if futura is not None:
            r = futura.result()
            if r.face_landmarks:
                lm = np.array([[p.x, p.y, p.z] for p in r.face_landmarks[0]], dtype=np.float32)
                blend = {b.category_name: b.score for b in r.face_blendshapes[0]} if r.face_blendshapes else {}
                res["cara"] = (lm, blend)
        return res

    def cerrar(self):
        self._hilo_cara.shutdown(wait=True)
        for d in (self.mano, self.cara):
            if d is not None:
                d.close()
        self.mano = self.cara = None


# --- dibujo de la vista previa ------------------------------------------------

_CONEX_MANO = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
               (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17),
               (17, 18), (18, 19), (19, 20)]
_OJOS = (33, 160, 158, 133, 153, 144, 362, 385, 387, 263, 373, 380)
_BOCA = (61, 40, 37, 0, 267, 270, 291, 321, 314, 17, 84, 91)


def dibujar(frame, res, principal=None, zona=None, textos=(), pausado=False):
    """Pinta encima del fotograma (BGR, se modifica) los puntos detectados."""
    h, w = frame.shape[:2]
    if zona:
        x0, y0, x1, y1 = zona
        cv2.rectangle(frame, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), (90, 200, 255), 1)
    for i, (lm, _wl, _lado, _c) in enumerate(res.get("manos", [])):
        color = (80, 255, 120) if i == principal else (150, 150, 150)
        pts = [(int(p[0] * w), int(p[1] * h)) for p in lm]
        for a, b in _CONEX_MANO:
            cv2.line(frame, pts[a], pts[b], color, 2, cv2.LINE_AA)
        for p in pts:
            cv2.circle(frame, p, 3, (255, 255, 255), -1, cv2.LINE_AA)
    if res.get("cara") is not None:
        lm = res["cara"][0]
        for idx in _OJOS + _BOCA:
            cv2.circle(frame, (int(lm[idx, 0] * w), int(lm[idx, 1] * h)), 2, (255, 200, 80), -1, cv2.LINE_AA)
        n = lm[1]
        cv2.circle(frame, (int(n[0] * w), int(n[1] * h)), 4, (80, 120, 255), -1, cv2.LINE_AA)
    y = 26
    for t in textos:
        cv2.putText(frame, t, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(frame, t, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        y += 24
    if pausado:
        cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (0, 160, 255), 6)
        cv2.putText(frame, "PAUSA", (w - 120, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 160, 255), 2, cv2.LINE_AA)
    return frame
