"""Carga y comprueba ~/.config/gestpad/perfiles.toml."""

import os
import tomllib

from . import cara, mano
from .salida import BOTONES, SALIDAS, codigo_tecla

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "gestpad")
RUTA = os.path.join(CONFIG_DIR, "perfiles.toml")
_AQUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_EJEMPLO = os.path.join(_AQUI, "perfiles.toml")

GESTOS_MANO = set(mano.GESTOS)
GESTOS_CARA = set(cara.GESTOS)
EVENTOS = set(mano.DESLIZAR) | set(cara.EVENTOS)       # se «tocan», no se mantienen
FUENTES = {"mano": "mano", "indice": "mano", "cabeza": "cara"}
MODOS_RATON = ("absoluto", "relativo", "joystick")
MODIFICADORES = ("PRECISION", "SCROLL", "CONGELAR")
ACCIONES = ("pausa", "siguiente_perfil", "perfil_anterior", "recentrar")
RUEDA = {"WHEEL_UP": (1, 0), "WHEEL_DOWN": (-1, 0), "WHEEL_LEFT": (0, -1), "WHEEL_RIGHT": (0, 1)}

GENERAL = {
    "perfil_inicial": "",
    "salida": "uinput",
    "camara": 0,              # número de /dev/videoN o ruta
    "ancho": 640,
    "alto": 480,
    "fps": 30,
    "espejo": True,
    "fps_constantes": True,   # no dejar que la webcam baje los fps con poca luz
    "monitor": "",            # nombre del monitor para el modo absoluto ("" = el que tenga el foco)
}

OPCIONES = {
    # ratón
    "modo_raton": "absoluto",            # absoluto · relativo · joystick
    "punto": "palma",                    # mano: palma (estable al hacer pinza) · indice (punta)
    "zona": [0.22, 0.18, 0.78, 0.72],    # mano en modo absoluto: rectángulo de la cámara que cubre la pantalla
    "raton_con": "",                     # mano: solo mueve el cursor con esta pose ("" = siempre)
    "suavizado": 1.2,                    # Hz de corte en reposo (más bajo = menos temblor)
    "suavizado_beta": 12.0,
    "ganancia": 2.2,                     # relativo: pantallas por cada «cámara» de recorrido (mano) o px/grado/40 (cabeza)
    "aceleracion": 1.5,
    "velocidad": 1400.0,                 # joystick: px/s a tope
    "zona_muerta": 0.06,                 # joystick: fracción (mano) · en cabeza se usa zona_muerta_grados
    "zona_muerta_grados": 3.0,
    "rango_giro": 18.0,                  # cabeza absoluto/joystick: grados hasta el borde
    "rango_cabeceo": 12.0,
    "precision": 0.3,
    "congelar_clic_s": 0.18,             # el cursor se queda quieto al empezar un clic (no se mueve al pellizcar)
    "scroll_velocidad": 14.0,            # muescas/s a tope en modo SCROLL
    "repetir_s": 0.12,
    "repetir_retardo_s": 0.35,
    # mano
    "mano": "cualquiera",                # cualquiera · derecha · izquierda
    "enganche": "",                      # pose que hay que enseñar para que la mano empiece a controlar
    "enganche_soltar_s": 0.8,            # sin ver la mano este tiempo, hay que volver a engancharla
    "entrar_s": 0.12,
    "salir_s": 0.08,
    "umbral_dedo": 0.86,                 # rectitud para contar un dedo como estirado (1 = recto)
    "pinza_cerrar": 0.30,
    "pinza_abrir": 0.45,
    "deslizar_con": "",
    "deslizar_distancia": 0.20,
    "deslizar_ventana_s": 0.30,
    "deslizar_enfriar_s": 0.6,
    # cara
    "guino_s": 0.15,
    "ojos_cerrados_s": 0.6,
    "umbral_cabeza": 12.0,
    "asentir_grados": 9.0,
    "negar_grados": 10.0,
    "invertir_guinos": False,
    # pausa
    "pausa": "PAZ",                      # gesto que, mantenido pausa_s, pausa/reanuda ("" = ninguno)
    "pausa_s": 1.5,
}


class ErrorPerfil(ValueError):
    pass


def comprobar_destino(d, donde):
    """Valida un destino («key:A», «mouse:LEFT», «rep:key:UP», …) y lo devuelve normalizado."""
    if isinstance(d, list):
        return [comprobar_destino(x, donde) for x in d]
    if not isinstance(d, str) or ":" not in d:
        raise ErrorPerfil(f"{donde}: destino «{d}» no válido (ej. key:SPACE, mouse:LEFT)")
    tipo, _, valor = d.partition(":")
    if tipo == "rep":
        comprobar_destino(valor, donde)
    elif tipo == "key":
        for parte in valor.split("+"):
            try:
                codigo_tecla(parte)
            except ValueError as e:
                raise ErrorPerfil(f"{donde}: {e}") from None
    elif tipo == "mouse":
        if valor.upper() not in BOTONES and valor.upper() not in RUEDA:
            raise ErrorPerfil(f"{donde}: botón de ratón «{valor}» no existe")
    elif tipo == "mod":
        if valor.upper() not in MODIFICADORES:
            raise ErrorPerfil(f"{donde}: modificador «{valor}» no existe ({', '.join(MODIFICADORES)})")
    elif tipo == "accion":
        if valor not in ACCIONES and not valor.startswith("perfil="):
            raise ErrorPerfil(f"{donde}: acción «{valor}» no existe ({', '.join(ACCIONES)}, perfil=NOMBRE)")
    elif tipo == "exec":
        if not valor.strip():
            raise ErrorPerfil(f"{donde}: exec vacío")
    else:
        raise ErrorPerfil(f"{donde}: tipo de destino «{tipo}» no existe")
    return d


def _gesto_valido(g):
    return g in GESTOS_MANO or g in GESTOS_CARA


def cargar(ruta=None):
    ruta = ruta or (RUTA if os.path.exists(RUTA) else RUTA_EJEMPLO)
    with open(ruta, "rb") as f:
        datos = tomllib.load(f)
    general = {**GENERAL, **datos.get("general", {})}
    if general["salida"] not in SALIDAS:
        raise ErrorPerfil(f"[general] salida «{general['salida']}» no existe ({', '.join(SALIDAS)})")
    opciones_base = {**OPCIONES, **datos.get("opciones", {})}
    perfiles = []
    for p in datos.get("perfil", []):
        nombre = p.get("nombre")
        if not nombre:
            raise ErrorPerfil("hay un [[perfil]] sin nombre")
        op = {**opciones_base, **p.get("opciones", {})}
        desconocidas = set(p.get("opciones", {})) - set(OPCIONES)
        if desconocidas:
            raise ErrorPerfil(f"perfil {nombre}: opciones desconocidas {sorted(desconocidas)}")
        if op["modo_raton"] not in MODOS_RATON:
            raise ErrorPerfil(f"perfil {nombre}: modo_raton debe ser {', '.join(MODOS_RATON)}")
        gestos = {}
        for g, d in p.get("gestos", {}).items():
            if not _gesto_valido(g):
                raise ErrorPerfil(f"perfil {nombre}: gesto «{g}» no existe (mira `gestpad gestos`)")
            gestos[g] = comprobar_destino(d, f"perfil {nombre}, {g}")
        ejes = {}
        for fuente, d in p.get("ejes", {}).items():
            if fuente not in FUENTES:
                raise ErrorPerfil(f"perfil {nombre}: fuente de eje «{fuente}» no existe ({', '.join(FUENTES)})")
            if d not in ("raton", "scroll") and not d.startswith("teclas:"):
                raise ErrorPerfil(f"perfil {nombre}: destino de eje «{d}» (raton, scroll, teclas:A,B,C,D)")
            if d.startswith("teclas:"):
                teclas = d[7:].split(",")
                if len(teclas) != 4:
                    raise ErrorPerfil(f"perfil {nombre}: teclas: necesita 4 (arriba,abajo,izq,der)")
                for t in teclas:
                    comprobar_destino(f"key:{t}", f"perfil {nombre}, ejes")
            ejes[fuente] = d
        if op["pausa"] and not _gesto_valido(op["pausa"]):
            raise ErrorPerfil(f"perfil {nombre}: gesto de pausa «{op['pausa']}» no existe")
        if op["enganche"] and op["enganche"] not in mano.POSES:
            raise ErrorPerfil(f"perfil {nombre}: enganche debe ser una pose de la mano")
        if op["raton_con"] and op["raton_con"] not in mano.POSES:
            raise ErrorPerfil(f"perfil {nombre}: raton_con debe ser una pose de la mano")
        usados = set(gestos) | ({op["pausa"]} if op["pausa"] else set())
        if op["raton_con"]:
            usados.add(op["raton_con"])
        necesita = {FUENTES[f] for f in ejes}
        necesita |= {"mano" for g in usados if g in GESTOS_MANO}
        necesita |= {"cara" for g in usados if g in GESTOS_CARA}
        perfiles.append({
            "nombre": nombre,
            "descripcion": p.get("descripcion", ""),
            "gestos": gestos,
            "ejes": ejes,
            "opciones": op,
            "necesita": necesita,
        })
    if not perfiles:
        raise ErrorPerfil("no hay ningún [[perfil]]")
    nombres = [p["nombre"] for p in perfiles]
    if len(set(nombres)) != len(nombres):
        raise ErrorPerfil("hay perfiles con el mismo nombre")
    if general["perfil_inicial"] not in nombres:
        general["perfil_inicial"] = nombres[0]
    for p in perfiles:
        for g, d in p["gestos"].items():
            for x in d if isinstance(d, list) else [d]:
                if x.startswith("accion:perfil=") and x[14:] not in nombres:
                    raise ErrorPerfil(f"perfil {p['nombre']}, {g}: no hay perfil «{x[14:]}»")
    return {"ruta": ruta, "general": general, "perfiles": perfiles}
