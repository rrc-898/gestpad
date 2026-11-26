#!/usr/bin/env bash
# Instala gestpad.
#
#   ./instalar.sh                 parte de usuario (y la de sistema solo si /dev/uinput no es tuyo)
#   ./instalar.sh --usuario       solo lo que no necesita root
#   ./instalar.sh --sistema       regla udev de /dev/uinput (se ejecuta con sudo)
#   ./instalar.sh --desinstalar
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"

usuario() {
    echo "== Parte de usuario =="
    if [[ ! -x "$DIR/.venv/bin/python" ]]; then
        # --system-site-packages: PyGObject (GTK 4) viene de los paquetes de Fedora
        if command -v uv >/dev/null; then
            uv venv --python /usr/bin/python3 --system-site-packages "$DIR/.venv"
        else
            /usr/bin/python3 -m venv --system-site-packages "$DIR/.venv"
        fi
    fi
    if command -v uv >/dev/null; then
        VIRTUAL_ENV="$DIR/.venv" uv pip install -q -r "$DIR/requirements.txt"
    else
        "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
    fi
    echo "  entorno: $DIR/.venv (mediapipe, opencv, autopy, pyautogui)"
    mkdir -p "$DIR/modelos"
    for m in hand_landmarker face_landmarker; do
        if [[ ! -s "$DIR/modelos/$m.task" ]]; then
            curl -fsSL -o "$DIR/modelos/$m.task" \
                "https://storage.googleapis.com/mediapipe-models/$m/$m/float16/latest/$m.task"
        fi
    done
    echo "  modelos: $DIR/modelos"
    mkdir -p "$HOME/.local/bin" "$CONFIG/gestpad" "$HOME/.local/share/applications"
    cat > "$HOME/.local/bin/gestpad" <<LANZ
#!/usr/bin/env bash
# lanzador de gestpad (generado por instalar.sh)
PYTHONPATH="$DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec "$DIR/.venv/bin/python" -m gestpad "\$@"
LANZ
    chmod +x "$HOME/.local/bin/gestpad"
    echo "  lanzador: ~/.local/bin/gestpad"
    if [[ ! -f "$CONFIG/gestpad/perfiles.toml" ]]; then
        command cp "$DIR/perfiles.toml" "$CONFIG/gestpad/perfiles.toml"
        echo "  perfiles: $CONFIG/gestpad/perfiles.toml (edítalo a tu gusto)"
    else
        echo "  perfiles: ya existe $CONFIG/gestpad/perfiles.toml, no lo toco"
    fi
    cat > "$HOME/.local/share/applications/io.github.rrc898.Gestpad.desktop" <<APP
[Desktop Entry]
Type=Application
Name=Gestpad
GenericName=Control por gestos
Comment=Controla el ordenador con la mano y la cara usando la webcam
Exec=$HOME/.local/bin/gestpad gui
Icon=camera-web
Terminal=false
Categories=Utility;Accessibility;
Keywords=gestos;mano;cara;webcam;ratón;mediapipe;accesibilidad;
StartupWMClass=io.github.rrc898.Gestpad
APP
    echo "  aplicación: «Gestpad» en el menú de aplicaciones (o: gestpad)"
}

sistema() {
    echo "== Parte de sistema (root) =="
    install -m 0644 "$DIR/sistema/66-gestpad-uinput.rules" /etc/udev/rules.d/66-gestpad-uinput.rules
    echo uinput > /etc/modules-load.d/gestpad.conf
    modprobe uinput || true
    udevadm control --reload
    udevadm trigger --subsystem-match=misc
    echo "  regla udev: /etc/udev/rules.d/66-gestpad-uinput.rules"
}

desinstalar() {
    rm -f "$HOME/.local/bin/gestpad" "$HOME/.local/share/applications/io.github.rrc898.Gestpad.desktop"
    echo "Quitada la parte de usuario (tus perfiles en $CONFIG/gestpad y el .venv se conservan)."
    if [[ -f /etc/udev/rules.d/66-gestpad-uinput.rules ]]; then
        sudo rm -f /etc/udev/rules.d/66-gestpad-uinput.rules /etc/modules-load.d/gestpad.conf
        sudo udevadm control --reload
        echo "Quitada la regla udev."
    fi
}

case "${1:-}" in
    --usuario) usuario ;;
    --sistema) sistema ;;
    --desinstalar) desinstalar ;;
    "")
        usuario
        if [[ -w /dev/uinput ]]; then
            echo "  /dev/uinput: ya tienes permiso (no hace falta sudo)"
        else
            echo
            sudo "$DIR/instalar.sh" --sistema
        fi
        echo
        echo "Listo. Abre «Gestpad» o ejecuta: gestpad"
        ;;
    *) sed -n '2,8p' "$0"; exit 1 ;;
esac
