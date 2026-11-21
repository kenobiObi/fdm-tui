#!/usr/bin/env bash
# Lance FDM-TUI : crée l'environnement virtuel au premier lancement,
# installe les dépendances si besoin, puis démarre l'application.
set -euo pipefail

# Se placer dans le dossier du script, d'où qu'il soit lancé
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

# Créer le venv s'il n'existe pas
if [ ! -f venv/bin/activate ]; then
    echo "Création de l'environnement virtuel…"
    python3 -m venv venv
fi

source venv/bin/activate

# Installer les dépendances si elles manquent
if ! python3 -c "import textual, httpx" 2>/dev/null; then
    echo "Installation des dépendances…"
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
fi

# Lancer l'application (les arguments du script sont transmis)
exec python3 fdm_tui.py "$@"

