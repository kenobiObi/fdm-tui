#!/usr/bin/env bash
# Installe FDM-TUI et crée la commande « tfdm » qui lance directement l'application.
#
#   bash install.sh                 installation (ou mise à jour si déjà installé)
#   bash install.sh --all           + vidéos (yt-dlp) et torrents (libtorrent)
#   bash install.sh --video         + vidéos (yt-dlp)
#   bash install.sh --torrent       + torrents (libtorrent via pip)
#   bash install.sh --system-site-packages
#                                   le venv voit les paquets du système
#                                   (utile avec python3-libtorrent installé par dnf/apt)
#   bash install.sh --uninstall     désinstallation (la configuration est conservée)
#
# Variables facultatives : TFDM_HOME (dossier d'installation), TFDM_BIN (dossier de la commande)
set -euo pipefail

NOM="tfdm"
SRC="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
INSTALL_DIR="${TFDM_HOME:-$HOME/.local/share/tfdm}"
BIN_DIR="${TFDM_BIN:-$HOME/.local/bin}"
LANCEUR="$BIN_DIR/$NOM"
MARQUEUR="$INSTALL_DIR/.tfdm-install"
EXTRAS=()
VENV_ARGS=()
DESINSTALLER=0

erreur() { echo "Erreur : $*" >&2; exit 1; }
info()   { echo "→ $*"; }

usage() { sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --video)   EXTRAS+=(yt-dlp) ;;
        --torrent) EXTRAS+=(libtorrent) ;;
        --all)     EXTRAS+=(yt-dlp libtorrent) ;;
        --system-site-packages) VENV_ARGS+=(--system-site-packages) ;;
        --uninstall) DESINSTALLER=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Option inconnue : $1" >&2; usage; exit 1 ;;
    esac
    shift
done

# ---------------------------------------------------------------- désinstallation
if [ "$DESINSTALLER" -eq 1 ]; then
    [ -f "$LANCEUR" ] && rm -f "$LANCEUR" && info "Commande supprimée : $LANCEUR"
    if [ -f "$MARQUEUR" ]; then
        rm -rf "$INSTALL_DIR"
        info "Dossier supprimé : $INSTALL_DIR"
    else
        info "Aucune installation trouvée dans $INSTALL_DIR"
    fi
    echo "Ta configuration et ta liste de téléchargements sont conservées dans ~/.config/fdm_tui"
    exit 0
fi

# ---------------------------------------------------------------- vérifications
[ -f "$SRC/fdm_tui.py" ] || erreur "fdm_tui.py introuvable à côté de install.sh (lance le script depuis le dossier du projet)"

PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || erreur "python3 est introuvable. Installe-le d'abord (sudo dnf install python3  /  sudo apt install python3)"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || erreur "Python 3.10 ou plus récent est nécessaire (version actuelle : $("$PYTHON" -V 2>&1))"

# ---------------------------------------------------------------- installation
info "Installation dans $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$BIN_DIR"
touch "$MARQUEUR"
rm -rf "$INSTALL_DIR/venv"

info "Création de l'environnement virtuel…"
if ! "$PYTHON" -m venv ${VENV_ARGS[@]+"${VENV_ARGS[@]}"} "$INSTALL_DIR/venv"; then
    erreur "création du venv impossible. Sur Ubuntu/Debian : sudo apt install python3-venv"
fi

info "Installation des dépendances…"
PIP="$INSTALL_DIR/venv/bin/pip"
"$PIP" install --quiet --upgrade pip
if [ -f "$SRC/requirements.txt" ]; then
    "$PIP" install --quiet -r "$SRC/requirements.txt"
else
    "$PIP" install --quiet textual httpx
fi
for extra in ${EXTRAS[@]+"${EXTRAS[@]}"}; do
    info "Installation de $extra…"
    "$PIP" install --quiet "$extra" || echo "  (échec pour $extra : fonction optionnelle ignorée)" >&2
done

install -m 0644 "$SRC/fdm_tui.py" "$INSTALL_DIR/fdm_tui.py"

# ---------------------------------------------------------------- commande tfdm
cat > "$LANCEUR" <<LANCEUR_EOF
#!/usr/bin/env bash
# Généré par install.sh — lance FDM-TUI
exec "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/fdm_tui.py" "\$@"
LANCEUR_EOF
chmod +x "$LANCEUR"
info "Commande créée : $LANCEUR"

AUTRE="$(command -v "$NOM" 2>/dev/null || true)"
if [ -n "$AUTRE" ] && [ "$AUTRE" != "$LANCEUR" ]; then
    echo "Attention : une autre commande « $NOM » existe déjà ($AUTRE) et passe avant la nouvelle." >&2
fi

# ---------------------------------------------------------------- PATH
NOUVEAU_TERMINAL=0
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        LIGNE="export PATH=\"$BIN_DIR:\$PATH\""
        FAIT=0
        for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
            [ -f "$rc" ] || continue
            grep -qF "$LIGNE" "$rc" || printf '\n# Ajouté par l'"'"'installation de tfdm\n%s\n' "$LIGNE" >> "$rc"
            FAIT=1
        done
        if [ "$FAIT" -eq 0 ]; then
            printf '# Ajouté par l'"'"'installation de tfdm\n%s\n' "$LIGNE" >> "$HOME/.bashrc"
        fi
        NOUVEAU_TERMINAL=1
        ;;
esac

echo
echo "✓ FDM-TUI est installé."
if [ "$NOUVEAU_TERMINAL" -eq 1 ]; then
    echo "  $BIN_DIR vient d'être ajouté à ton PATH : ouvre un nouveau terminal"
    echo "  (ou lance : source ~/.bashrc), puis tape :  $NOM"
    echo "  (si tu utilises fish ou un autre shell, ajoute $BIN_DIR à ton PATH toi-même)"
else
    echo "  Tape simplement :  $NOM"
fi
echo "  Exemples :  $NOM   |   $NOM \"https://site/fichier.iso\"   |   $NOM -f liens.txt"
echo "  Mise à jour : relance  bash install.sh   —   Désinstallation :  bash install.sh --uninstall"
