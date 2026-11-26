"""Canal de control: un socket Unix con un JSON por línea.

Lo abre quien tenga el motor en marcha (la app o `gestpad ejecutar`), para
que `gestpad pausa`, `gestpad perfil X`… funcionen desde la terminal o desde
un atajo de Hyprland.

    → {"orden": "estado"}
    ← {"ok": true, "perfil": "raton_mano", ...}

(Copiado de wiipad/control.py, más el hilo que lo atiende.)
"""

import json
import os
import select
import socket
import threading

# GESTPAD_SOCKET permite a las pruebas usar otro socket sin tocar el servicio de verdad
RUTA = os.environ.get("GESTPAD_SOCKET") or os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "gestpad.sock")


class Servidor:
    def __init__(self, atender, ruta=RUTA):
        """atender(dict) -> dict: lo implementa el servicio."""
        self.atender = atender
        self.ruta = ruta
        try:
            os.unlink(ruta)
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(ruta)
        os.chmod(ruta, 0o600)
        self.sock.listen(4)
        self.sock.setblocking(False)
        self.clientes = {}       # fd -> (socket, búfer)

    def descriptores(self):
        return [self.sock.fileno(), *self.clientes]

    def listo(self, fd):
        if fd == self.sock.fileno():
            try:
                c, _ = self.sock.accept()
            except BlockingIOError:
                return
            c.setblocking(False)
            self.clientes[c.fileno()] = (c, bytearray())
            return
        c, buf = self.clientes[fd]
        try:
            datos = c.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            datos = b""
        if not datos:
            self._cerrar(fd)
            return
        buf += datos
        while b"\n" in buf:
            linea, _, resto = bytes(buf).partition(b"\n")
            buf[:] = resto
            try:
                resp = self.atender(json.loads(linea))
            except Exception as e:  # una orden mal formada no debe tumbar el servicio
                resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            try:
                c.setblocking(True)
                c.sendall(json.dumps(resp).encode() + b"\n")
                c.setblocking(False)
            except OSError:
                self._cerrar(fd)
                return

    def _cerrar(self, fd):
        c, _ = self.clientes.pop(fd)
        c.close()

    def cerrar(self):
        for fd in list(self.clientes):
            self._cerrar(fd)
        self.sock.close()
        try:
            os.unlink(self.ruta)
        except FileNotFoundError:
            pass


def en_hilo(atender, ruta=RUTA):
    """Arranca el Servidor en un hilo propio. Devuelve una función para pararlo."""
    srv = Servidor(atender, ruta)
    parar = threading.Event()

    def bucle():
        while not parar.is_set():
            listos, _, _ = select.select(srv.descriptores(), [], [], 0.3)
            for fd in listos:
                srv.listo(fd)
        srv.cerrar()

    threading.Thread(target=bucle, name="control", daemon=True).start()
    return parar.set


class Cliente:
    """Cliente síncrono (las respuestas tardan menos de 1 ms)."""

    def __init__(self, ruta=RUTA):
        self.ruta = ruta
        self.sock = None
        self.buf = b""

    def conectado(self):
        return self.sock is not None

    def pedir(self, orden, **datos):
        """Devuelve la respuesta o None si el servicio no está."""
        try:
            if self.sock is None:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(1.0)
                self.sock.connect(self.ruta)
                self.buf = b""
            self.sock.sendall(json.dumps({"orden": orden, **datos}).encode() + b"\n")
            while b"\n" not in self.buf:
                trozo = self.sock.recv(262144)
                if not trozo:
                    raise OSError("cerrado")
                self.buf += trozo
            linea, _, self.buf = self.buf.partition(b"\n")
            return json.loads(linea)
        except (OSError, ValueError):
            self.cerrar()
            return None

    def cerrar(self):
        if self.sock:
            self.sock.close()
        self.sock = None
