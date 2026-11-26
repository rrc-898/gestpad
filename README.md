# gestpad

**Hands-free computer control from a webcam.** gestpad tracks your hand and face with
MediaPipe Tasks (HandLandmarker + FaceLandmarker) and turns poses, pinches, swipes, winks,
head angles and facial expressions into mouse and keyboard events through Linux `uinput`,
so it works natively on Wayland. Behaviour is defined per profile in TOML (mouse by hand,
mouse by head, presentations, media, desktop, games) and edited live from a GTK4 app.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![MediaPipe](https://img.shields.io/badge/MediaPipe-Tasks-0097A7)
![OpenCV](https://img.shields.io/badge/OpenCV-cv2-5C3EE8?logo=opencv&logoColor=white)
![Wayland](https://img.shields.io/badge/Wayland-uinput-FFBC00)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Controla el ordenador con la mano y la cara

Lo mismo que [wiipad](https://github.com/rrc-898/wiipad), pero sin mando: la webcam (cv2) y MediaPipe ven la mano y la
cara, y los gestos se convierten en ratón y teclado. Eliges un **perfil** según lo
que vayas a hacer (ratón con la mano, ratón con la cabeza, presentaciones,
música, escritorio, juegos…) y cada perfil se define en un TOML.

```
git clone https://github.com/rrc-898/gestpad.git && cd gestpad
./instalar.sh        # venv con mediapipe/opencv/autopy/pyautogui, modelos, lanzador y entrada de menú
gestpad              # abre la app «Gestpad»
```

## La app

- **Control**: la cámara con los puntos de la mano y la cara encima, el perfil activo,
  los gestos del perfil (se iluminan al hacerlos), lo que se está viendo (pose de la
  mano, ángulos de la cabeza, ojos/boca/cejas), ajustes en vivo y la salida.
- **Gestos**: todos los gestos con su explicación. Sirve para probarlos (verde = ya
  visto, azul = ahora). «Detectar mano y cara a la vez» busca los dos aunque el perfil
  solo use uno.
- **Perfiles**: lo que hace cada perfil, abrir el TOML y recargarlo sin reiniciar.
- **Pausa**: el botón de arriba, mantener el gesto de pausa del perfil (PAZ 1,5 s por
  defecto) o `gestpad alternar`.

## Perfiles de serie

| perfil | qué hace |
|---|---|
| `raton_mano` | la palma mueve el cursor (absoluto: el rectángulo naranja = la pantalla), pinza índice = clic (mantener = arrastrar), pinza corazón = clic derecho, pinza anular = central, puño + subir/bajar = scroll, pinza meñique = precisión |
| `raton_cara` | la cabeza mueve el cursor (relativo, como un ratón), guiño izq./der. = clics, boca abierta + mirar arriba/abajo = scroll, sonrisa = precisión, beso = recentrar. Pausa: cejas arriba 1,5 s |
| `mixto` | la mano mueve el cursor y la cara hace los clics (guiños) y el scroll (boca); puño = congelar el cursor |
| `presentacion` | deslizar con la palma = diapositiva siguiente/anterior, señalar = puntero, pulgar arriba = F5, pulgar abajo = Esc, puño = pantalla negra |
| `multimedia` | puño = play/pausa, deslizar = pista, pulgar arriba/abajo = volumen (repetido), rock = silencio |
| `escritorio` | deslizar = escritorio siguiente/anterior (SUPER+U / SUPER+I), deslizar arriba = ventanas (SUPER+TAB), pulgar arriba = lanzador (SUPER+espacio) |
| `juego_cabeza` | la cabeza son las flechas, boca = C (saltar en Celeste), cejas = X (dash), sonrisa = Z. Pausa: ojos cerrados 1,5 s |

**Enganche**: en los perfiles de mano, la mano no hace nada hasta que enseñas la
**palma abierta**; si sale de la cámara 0,8 s se suelta. Así la mano que tienes en el
ratón o en el teclado no mueve el cursor sin querer (`enganche = ""` lo quita).

Al perder la mano o la cara, en pausa o al cambiar de perfil se suelta todo lo que
estuviera pulsado.

## Gestos

```
gestpad gestos
```

- Mano: `PALMA PUNO SENALAR PAZ TRES ROCK LLAMAME PULGAR_ARRIBA PULGAR_ABAJO`,
  `PINZA_INDICE/CORAZON/ANULAR/MENIQUE`, `DESLIZAR_IZQ/DER/ARRIBA/ABAJO`.
- Cara: `GUINO_IZQ GUINO_DER OJOS_CERRADOS BOCA_ABIERTA SONRISA CEJAS_ARRIBA BESO MEJILLAS`,
  `CABEZA_IZQ/DER/ARRIBA/ABAJO LADEAR_IZQ/DER`, `ASENTIR NEGAR`.

Izquierda y derecha son siempre las tuyas (la imagen va en espejo).

## Escribir un perfil

En `~/.config/gestpad/perfiles.toml` (la cabecera del fichero lo explica todo):

```toml
[[perfil]]
nombre = "mi_perfil"
descripcion = "…"
[perfil.ejes]
mano = "raton"                 # mano | cabeza  →  raton | scroll | teclas:W,S,A,D
[perfil.gestos]
PINZA_INDICE = "mouse:LEFT"
PUNO = "mod:SCROLL"
PULGAR_ARRIBA = "key:SUPER+SPACE"
DESLIZAR_DER = "exec:notify-send hola"
PAZ = ["key:LEFTCTRL", "key:C"]
[perfil.opciones]
modo_raton = "absoluto"        # absoluto | relativo | joystick
```

Destinos: `key:` (y combinaciones con `+`), `mouse:` (botones y `WHEEL_*`),
`mod:PRECISION|SCROLL|CONGELAR`, `accion:pausa|siguiente_perfil|perfil_anterior|recentrar|perfil=NOMBRE`,
`exec:` y `rep:` (repetir mientras dure).

## Terminal y atajos

```
gestpad ejecutar [--perfil X] [--ver] [--prueba]   # sin ventana
gestpad alternar | pausa | reanudar                # para un atajo de Hyprland
gestpad perfil raton_cara | siguiente | anterior
gestpad recentrar | recargar | estado [--json] | parar
gestpad probar-salida [uinput|autopy|pyautogui]    # mueve el cursor en círculo
```

Ejemplo de atajo (en `keybinds.lua`; ruta absoluta porque la sesión no tiene `~/.local/bin` en el PATH):
`b("SUPER + G", sh(os.getenv("HOME") .. "/.local/bin/gestpad alternar"), "Pausar gestpad")`.

## Salidas: uinput, autopy y pyautogui

- **uinput** (por defecto): crea un ratón, un puntero absoluto y un teclado virtuales en
  el kernel. Funciona en Wayland (Hyprland), X11 y donde sea. Comprobado en este equipo.
- **autopy** y **pyautogui** también están (se eligen en la app o con `--salida`), pero
  usan XTest de X11: **en Wayland no mueven el cursor del escritorio**, solo llegan a
  ventanas XWayland (pyautogui ni arranca aquí porque no hay `~/.Xauthority`). Sirven en
  una sesión X11 y, pyautogui, en Windows/macOS.
- **nula** / `--prueba`: ve los gestos sin tocar nada.

## Cómo está hecho

```
gestpad/vision.py   cámara en un hilo (cv2, MJPG 640×480@30) + HandLandmarker/FaceLandmarker (API Tasks)
gestpad/mano.py     poses, pinzas y deslizar con los puntos 3D «world» (no dependen de la distancia)
gestpad/cara.py     orientación de la cabeza, guiños (EAR con línea base propia) y blendshapes
gestpad/motor.py    gestos → destinos, cursor (filtro 1€, absoluto/relativo/joystick), pausa, enganche
gestpad/salida.py   uinput / autopy / pyautogui / nula
gestpad/gui.py      app GTK 4 + libadwaita
gestpad/control.py  socket $XDG_RUNTIME_DIR/gestpad.sock (copiado de wiipad)
```

- Solo se ejecuta el detector que el perfil usa. Con mano y cara a la vez van en paralelo:
  ~21 ms por fotograma, 30 fps en este portátil (CPU, sin GPU).
- Una sola mano por defecto: con `num_hands=2`, MediaPipe busca la segunda mano en cada
  fotograma y pasa de ~20 a ~50 ms. Si pones `mano = "derecha"` o `"izquierda"` se piden 2.
- Con poca luz la webcam bajaba a ~11 fps; gestpad desactiva su «exposure_dynamic_framerate»
  mientras la usa y lo devuelve al cerrar (`fps_constantes = false` para no tocarlo).
- Signos de la cabeza comprobados con la cámara real contra la posición de la nariz
  (giro +0,97, cabeceo −0,84 de correlación, lo esperado).

## Pruebas

```
.venv/bin/python pruebas/prueba_gestos.py    # 48 comprobaciones con manos y caras sintéticas
```

## Sin probar todavía con gestos reales

Los umbrales (dedo estirado 0,86, pinza 0,30/0,45, guiño 0,45, blendshapes) salen de la
geometría y de pruebas sintéticas: afínalos con la pestaña Gestos y los ajustes en vivo.
El lado de los guiños se calcula por geometría; si te salen al revés, `invertir_guinos = true`.
