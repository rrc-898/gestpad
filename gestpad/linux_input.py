"""Acceso a evdev y uinput con ioctl, sin dependencias externas.

python-evdev necesita compilarse (python3-devel); esto hace lo mismo en
Python puro. Los números de ioctl y los tamaños de las estructuras se
comprobaron con un programa en C contra las cabeceras del kernel
(input_event 24 B, ff_effect 48 B, uinput_setup 92 B, uinput_ff_upload 104 B).
"""

import errno
import fcntl
import os
import struct

from . import codigos as C

# --- ioctl -----------------------------------------------------------------

_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2


def _IOC(d, t, nr, size):
    return (d << 30) | (size << 16) | (ord(t) << 8) | nr


EVIOCGID = _IOC(_IOC_READ, "E", 0x02, 8)
EVIOCSFF = _IOC(_IOC_WRITE, "E", 0x80, 48)
EVIOCRMFF = _IOC(_IOC_WRITE, "E", 0x81, 4)
EVIOCGRAB = _IOC(_IOC_WRITE, "E", 0x90, 4)


def EVIOCGNAME(n):
    return _IOC(_IOC_READ, "E", 0x06, n)


def EVIOCGPHYS(n):
    return _IOC(_IOC_READ, "E", 0x07, n)


def EVIOCGBIT(ev, n):
    return _IOC(_IOC_READ, "E", 0x20 + ev, n)


def EVIOCGABS(axis):
    return _IOC(_IOC_READ, "E", 0x40 + axis, 24)


UI_DEV_CREATE = _IOC(_IOC_NONE, "U", 1, 0)
UI_DEV_DESTROY = _IOC(_IOC_NONE, "U", 2, 0)
UI_DEV_SETUP = _IOC(_IOC_WRITE, "U", 3, 92)
UI_ABS_SETUP = _IOC(_IOC_WRITE, "U", 4, 28)
UI_SET_EVBIT = _IOC(_IOC_WRITE, "U", 100, 4)
UI_SET_KEYBIT = _IOC(_IOC_WRITE, "U", 101, 4)
UI_SET_RELBIT = _IOC(_IOC_WRITE, "U", 102, 4)
UI_SET_ABSBIT = _IOC(_IOC_WRITE, "U", 103, 4)
UI_SET_FFBIT = _IOC(_IOC_WRITE, "U", 107, 4)
UI_SET_PHYS = _IOC(_IOC_WRITE, "U", 108, 8)
UI_SET_PROPBIT = _IOC(_IOC_WRITE, "U", 110, 4)
UI_BEGIN_FF_UPLOAD = _IOC(_IOC_READ | _IOC_WRITE, "U", 200, 104)
UI_END_FF_UPLOAD = _IOC(_IOC_WRITE, "U", 201, 104)
UI_BEGIN_FF_ERASE = _IOC(_IOC_READ | _IOC_WRITE, "U", 202, 12)
UI_END_FF_ERASE = _IOC(_IOC_WRITE, "U", 203, 12)


def UI_GET_SYSNAME(n):
    return _IOC(_IOC_READ, "U", 44, n)


# --- estructuras -----------------------------------------------------------

EVENTO = struct.Struct("llHHi")           # struct input_event (64 bits)
ABSINFO = struct.Struct("6i")             # value min max fuzz flat resolution
# struct ff_effect: type id direction trigger(button interval) replay(length delay) + union de 32 B
FF_EFFECT = struct.Struct("HhHHHHH2x32s")


class Efecto:
    """Un ff_effect decodificado lo justo para saber cuánto debe vibrar."""

    def __init__(self, raw=None):
        self.tipo, self.id, self.duracion = 0, -1, 0
        self.intensidad = 0.0
        if raw:
            t, i, _dir, _b, _int, dur, _delay, u = FF_EFFECT.unpack(raw)
            self.tipo, self.id, self.duracion = t, i, dur
            if t == C.FF_RUMBLE:
                fuerte, debil = struct.unpack_from("HH", u)
                self.intensidad = max(fuerte, debil) / 0xFFFF
            elif t == C.FF_PERIODIC:
                (mag,) = struct.unpack_from("h", u, 4)
                self.intensidad = abs(mag) / 0x7FFF
            elif t == C.FF_CONSTANT:
                (nivel,) = struct.unpack_from("h", u, 0)
                self.intensidad = abs(nivel) / 0x7FFF
            elif t in (C.FF_RAMP,):
                ini, fin = struct.unpack_from("hh", u)
                self.intensidad = max(abs(ini), abs(fin)) / 0x7FFF
            else:  # muelles, fricción…: no tiene sentido en un motor on/off
                self.intensidad = 0.0


def _bits(buf):
    out = []
    for byte_i, b in enumerate(buf):
        for bit in range(8):
            if b >> bit & 1:
                out.append(byte_i * 8 + bit)
    return out


# --- lectura de dispositivos ----------------------------------------------

class DispositivoEntrada:
    """Un /dev/input/eventN abierto en modo no bloqueante."""

    def __init__(self, ruta, escritura=False):
        self.ruta = ruta
        modo = (os.O_RDWR if escritura else os.O_RDONLY) | os.O_NONBLOCK
        self.fd = os.open(ruta, modo)
        self.nombre = self._cadena(EVIOCGNAME(256))
        self.phys = self._cadena(EVIOCGPHYS(256))
        self.agarrado = False

    def _cadena(self, req):
        buf = bytearray(256)
        try:
            fcntl.ioctl(self.fd, req, buf)
        except OSError:
            return ""
        return buf.split(b"\0", 1)[0].decode(errors="replace")

    def capacidades(self, tipo):
        buf = bytearray(96)
        fcntl.ioctl(self.fd, EVIOCGBIT(tipo, len(buf)), buf)
        return _bits(buf)

    def absinfo(self, eje):
        buf = bytearray(ABSINFO.size)
        fcntl.ioctl(self.fd, EVIOCGABS(eje), buf)
        return ABSINFO.unpack(buf)

    def agarrar(self, si=True):
        """EVIOCGRAB: mientras está agarrado, nadie más recibe sus eventos."""
        fcntl.ioctl(self.fd, EVIOCGRAB, 1 if si else 0)
        self.agarrado = si

    def leer(self):
        """Devuelve los eventos pendientes [(tipo, codigo, valor)].

        Lanza OSError(ENODEV) si el dispositivo ha desaparecido.
        """
        eventos = []
        while True:
            try:
                datos = os.read(self.fd, EVENTO.size * 64)
            except BlockingIOError:
                return eventos
            if not datos:
                raise OSError(errno.ENODEV, "dispositivo cerrado")
            for off in range(0, len(datos), EVENTO.size):
                _s, _us, t, c, v = EVENTO.unpack_from(datos, off)
                eventos.append((t, c, v))

    def escribir(self, tipo, codigo, valor):
        os.write(self.fd, EVENTO.pack(0, 0, tipo, codigo, valor))

    def subir_vibracion(self, fuerte=0xFFFF, debil=0xFFFF):
        """Sube un efecto FF_RUMBLE de duración infinita; devuelve su id."""
        union = struct.pack("HH", fuerte, debil).ljust(32, b"\0")
        buf = bytearray(FF_EFFECT.pack(C.FF_RUMBLE, -1, 0, 0, 0, 0, 0, union))
        fcntl.ioctl(self.fd, EVIOCSFF, buf)
        return FF_EFFECT.unpack(buf)[1]

    def cerrar(self):
        try:
            if self.agarrado:
                self.agarrar(False)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.fd = -1

    def fileno(self):
        return self.fd


# --- dispositivos virtuales -----------------------------------------------

class DispositivoVirtual:
    """Dispositivo creado con /dev/uinput.

    teclas: lista de códigos KEY_/BTN_
    ejes:   {codigo: (min, max, fuzz, flat)}
    rel:    lista de códigos REL_
    ff:     True para aceptar vibración (los juegos suben efectos aquí)
    """

    def __init__(self, nombre, teclas=(), ejes=None, rel=(), ff=False,
                 bus=0x03, vendor=0x1234, product=0x5678, version=1,
                 phys="", props=()):
        ejes = ejes or {}
        self.nombre = nombre
        self.fd = os.open("/dev/uinput", os.O_RDWR | os.O_NONBLOCK)
        self.efectos = {}          # id -> Efecto (solo si ff)
        try:
            io = fcntl.ioctl
            if teclas:
                io(self.fd, UI_SET_EVBIT, C.EV_KEY)
                for k in teclas:
                    io(self.fd, UI_SET_KEYBIT, k)
            if rel:
                io(self.fd, UI_SET_EVBIT, C.EV_REL)
                for r in rel:
                    io(self.fd, UI_SET_RELBIT, r)
            if ejes:
                io(self.fd, UI_SET_EVBIT, C.EV_ABS)
                for codigo in ejes:
                    io(self.fd, UI_SET_ABSBIT, codigo)
            if ff:
                io(self.fd, UI_SET_EVBIT, C.EV_FF)
                for f in (C.FF_RUMBLE, C.FF_PERIODIC, C.FF_CONSTANT, C.FF_RAMP,
                          C.FF_GAIN):
                    io(self.fd, UI_SET_FFBIT, f)
            for p in props:
                io(self.fd, UI_SET_PROPBIT, p)
            if phys:
                io(self.fd, UI_SET_PHYS, phys.encode() + b"\0")
            for codigo, (mn, mx, fuzz, flat) in ejes.items():
                io(self.fd, UI_ABS_SETUP,
                   struct.pack("H2x6i", codigo, 0, mn, mx, fuzz, flat, 0))
            setup = struct.pack("4H80sI", bus, vendor, product, version,
                                nombre.encode()[:79], 16 if ff else 0)
            io(self.fd, UI_DEV_SETUP, setup)
            io(self.fd, UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise

    def nodo(self):
        """Ruta /dev/input/eventN del dispositivo creado (útil para pruebas)."""
        buf = bytearray(64)
        fcntl.ioctl(self.fd, UI_GET_SYSNAME(64), buf)
        sysname = buf.split(b"\0", 1)[0].decode()
        base = f"/sys/devices/virtual/input/{sysname}"
        for e in os.listdir(base):
            if e.startswith("event"):
                return f"/dev/input/{e}"
        return None

    def emitir(self, tipo, codigo, valor):
        os.write(self.fd, EVENTO.pack(0, 0, tipo, codigo, valor))

    def sincronizar(self):
        self.emitir(C.EV_SYN, C.SYN_REPORT, 0)

    def atender_ff(self):
        """Procesa subidas/borrados de efectos y devuelve las órdenes de
        reproducción [(id_efecto, veces)] que haya mandado el juego."""
        ordenes = []
        while True:
            try:
                datos = os.read(self.fd, EVENTO.size * 16)
            except BlockingIOError:
                return ordenes
            for off in range(0, len(datos), EVENTO.size):
                _s, _us, t, c, v = EVENTO.unpack_from(datos, off)
                if t == C.EV_UINPUT and c == C.UI_FF_UPLOAD:
                    buf = bytearray(struct.pack("Ii", v, 0)) + bytearray(96)
                    fcntl.ioctl(self.fd, UI_BEGIN_FF_UPLOAD, buf)
                    ef = Efecto(bytes(buf[8:56]))
                    self.efectos[ef.id] = ef
                    struct.pack_into("i", buf, 4, 0)
                    fcntl.ioctl(self.fd, UI_END_FF_UPLOAD, buf)
                elif t == C.EV_UINPUT and c == C.UI_FF_ERASE:
                    buf = bytearray(struct.pack("IiI", v, 0, 0))
                    fcntl.ioctl(self.fd, UI_BEGIN_FF_ERASE, buf)
                    self.efectos.pop(struct.unpack_from("I", buf, 8)[0], None)
                    struct.pack_into("i", buf, 4, 0)
                    fcntl.ioctl(self.fd, UI_END_FF_ERASE, buf)
                elif t == C.EV_FF and c != C.FF_GAIN:
                    ordenes.append((c, v))

    def cerrar(self):
        if self.fd < 0:
            return
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)
        self.fd = -1

    def fileno(self):
        return self.fd
