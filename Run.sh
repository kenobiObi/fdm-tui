#!/usr/bin/env bash
# Lance FDM-TUI dans son environnement virtuel.

# Dossier réel du script (fonctionne aussi via un lien symbolique)
DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
cd "$DIR" || exit 1

if [ ! -f venv/bin/activate ]; then
    echo "Environnement virtuel introuvable dans $DIR/venv" >&2
    echo "Crée-le avec : python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
fi

source venv/bin/activate && python3 fdm_tui.py "$@"
