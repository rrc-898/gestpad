"""gestpad — controla el ordenador con gestos de la mano y de la cara (webcam).

  gestpad                     abre la aplicación (igual que `gestpad gui`)
  gestpad ejecutar [--ver]    arranca sin ventana; --ver muestra los gestos en la terminal
  gestpad perfiles            lista los perfiles y comprueba el fichero
  gestpad gestos              lista todos los gestos que se pueden usar
  gestpad probar-salida       mueve el cursor en círculo para ver si la salida funciona

Con gestpad ya en marcha (la app o `ejecutar`):
  gestpad alternar | pausa | reanudar      pausar / seguir (ideal para un atajo de teclado)
  gestpad perfil NOMBRE | siguiente | anterior
  gestpad recentrar | recargar | estado | parar
"""

import argparse
import json
import os
import sys
import time

# MediaPipe (glog/TFLite) llena la terminal de avisos internos al cargar los modelos
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

from . import config
from .control import Cliente

ORDENES_REMOTAS = ("alternar", "pausa", "reanudar", "perfil", "siguiente", "anterior",
                   "recentrar", "recargar", "estado", "parar")


def _remota(orden, arg):
    c = Cliente()
    if orden == "alternar":
        r = c.pedir("pausa")
    elif orden in ("pausa", "reanudar"):
        r = c.pedir("pausa", valor=orden == "pausa")
    elif orden == "perfil":
        if not arg:
            print("Uso: gestpad perfil NOMBRE", file=sys.stderr)
            return 2
        r = c.pedir("perfil", nombre=arg)
    elif orden == "parar":
        r = c.pedir("salir")
    else:
        r = c.pedir(orden)
    if r is None:
        print("gestpad no está en marcha (abre la app o usa `gestpad ejecutar`).", file=sys.stderr)
        return 1
    if not r.get("ok"):
        print(f"Error: {r.get('error')}", file=sys.stderr)
        return 1
    if orden == "estado":
        print(f"perfil: {r['perfil']}  {'(EN PAUSA)' if r['pausado'] else ''}")
        print(f"salida: {r['salida']}  fps: {r['fps']}")
        print(f"activos: {' '.join(r['activos']) or '—'}")
        if r.get("error"):
            print(f"error: {r['error']}")
    return 0


def _ejecutar(cfg, a):
    from .control import en_hilo
    from .motor import Motor
    if Cliente().pedir("estado") is not None:
        print("Ya hay un gestpad en marcha (`gestpad parar` para cerrarlo).", file=sys.stderr)
        return 1
    m = Motor(cfg, salida=a.salida, prueba=a.prueba)
    if m.error_salida:
        print(f"Aviso: la salida «{m.nombre_salida}» no funciona: {m.error_salida}", file=sys.stderr)
    salir = []

    def atender(o):
        if o.get("orden") == "salir":
            salir.append(1)
            return {"ok": True}
        return m.orden(o)

    parar_socket = en_hilo(atender)
    m.iniciar()
    print(f"gestpad en marcha con el perfil «{m.perfil['nombre']}» (Ctrl+C para salir)")
    try:
        while not salir and m.hilo.is_alive():
            time.sleep(0.1)
            if a.ver:
                e = m.instantanea()
                if not e:
                    continue
                cab = e["cara"]["angulos"] if e.get("cara") else None
                linea = (f"\r{e['perfil']:<13} {'PAUSA ' if e['pausado'] else ''}{e['fps']:>4.0f} fps  "
                         f"mano: {(e['mano'] or {}).get('pose') or '—':<13} "
                         f"cabeza: {cab or '—'}  activos: {' '.join(e['activos']) or '—'}")
                print(linea[:160].ljust(160), end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        parar_socket()
        m.detener()
        print()
    return 0


def _probar_salida(nombre):
    import math
    from .salida import crear
    s, error = crear(nombre)
    if error:
        print(f"La salida «{nombre}» no funciona: {error}", file=sys.stderr)
        return 1
    print(f"Moviendo el cursor en círculo con «{nombre}» durante 2 s…")
    t0 = time.monotonic()
    while (t := time.monotonic() - t0) < 2.0:
        a = t * math.pi
        s.mover_abs(0.5 + 0.15 * math.cos(a), 0.5 + 0.2 * math.sin(a))
        time.sleep(1 / 60)
    s.cerrar()
    print("Hecho. Si el cursor no se ha movido, esa salida no llega a tu escritorio.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gestpad", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("orden", nargs="?", default="gui",
                    choices=["gui", "ejecutar", "perfiles", "gestos", "probar-salida", *ORDENES_REMOTAS])
    ap.add_argument("arg", nargs="?", help="nombre del perfil (con `perfil`) o de la salida (con `probar-salida`)")
    ap.add_argument("--perfil", help="perfil con el que empezar")
    ap.add_argument("--salida", choices=["uinput", "autopy", "pyautogui", "nula"])
    ap.add_argument("--prueba", action="store_true", help="detecta gestos pero no toca el ratón ni el teclado")
    ap.add_argument("--config", help="fichero de perfiles (por defecto ~/.config/gestpad/perfiles.toml)")
    ap.add_argument("--ver", action="store_true", help="con `ejecutar`: muestra el estado en vivo")
    ap.add_argument("--json", action="store_true", help="con `estado`: respuesta completa en JSON")
    a = ap.parse_args(argv)

    if a.orden in ORDENES_REMOTAS:
        if a.orden == "estado" and a.json:
            r = Cliente().pedir("estado")
            print(json.dumps(r, ensure_ascii=False, indent=1))
            return 0 if r else 1
        return _remota(a.orden, a.arg)
    if a.orden == "gestos":
        from . import cara, mano
        print("MANO")
        for g in mano.GESTOS:
            print(f"  {g:<16} {mano.DESCRIPCION[g]}")
        print("CARA")
        for g in cara.GESTOS:
            print(f"  {g:<16} {cara.DESCRIPCION[g]}")
        return 0
    if a.orden == "probar-salida":
        return _probar_salida(a.arg or a.salida or "uinput")

    try:
        cfg = config.cargar(a.config)
    except (config.ErrorPerfil, OSError, ValueError) as e:
        print(f"Error en los perfiles: {e}", file=sys.stderr)
        return 2
    if a.perfil:
        if a.perfil not in [p["nombre"] for p in cfg["perfiles"]]:
            print(f"No existe el perfil «{a.perfil}»", file=sys.stderr)
            return 2
        cfg["general"]["perfil_inicial"] = a.perfil

    if a.orden == "gui":
        from .gui import main as gui
        return gui(cfg, prueba=a.prueba, salida=a.salida)
    if a.orden == "ejecutar":
        return _ejecutar(cfg, a)
    if a.orden == "perfiles":
        print(f"Perfiles en {cfg['ruta']}:")
        for i, p in enumerate(cfg["perfiles"], 1):
            marca = "*" if p["nombre"] == cfg["general"]["perfil_inicial"] else " "
            usa = "+".join(sorted(p["necesita"])) or "—"
            print(f" {marca}{i}. {p['nombre']:<13} [{usa:<9}] {p['descripcion']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
