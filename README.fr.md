# FDM-TUI

[English](README.md) | Français
Gestionnaire de téléchargements en terminal, écrit en Python (asyncio + [Textual](https://textual.textualize.io/)), inspiré de Free Download Manager.

## Fonctionnalités

- **Téléchargement accéléré** multi-connexions (jusqu'à 32 segments) avec découpage dynamique
- **Pause / reprise**, y compris après redémarrage (état sauvegardé dans `~/.config/fdm_tui/`)
- **File d'attente** avec nombre de téléchargements simultanés réglable et réordonnancement
- **Limite de vitesse** globale et par fichier
- **Réessais automatiques** avec attente croissante, **miroirs** de secours
- **Tri automatique par catégorie** (Vidéos, Musique, Images, Documents, Archives, Programmes, Torrents)
- **Téléchargement par lots** : `http://site/img[001-050].jpg`
- **Aspirateur de page web** avec filtre d'extensions
- **Vidéos** (YouTube, Vimeo…) via [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- **Torrents** (magnets et `.torrent`) via libtorrent
- **Planificateur** : plage horaire autorisée, action de fin (quitter / veille / extinction)
- **Options par lien** : referer, cookies, en-têtes, nom, dossier, segments
- **Vérification de somme de contrôle** (MD5, SHA-1, SHA-256, SHA-512)
- Proxy HTTP / SOCKS5, surveillance du presse-papiers, import / export de listes

## Installation

```bash
git clone https://github.com/<utilisateur>/fdm-tui.git
cd fdm-tui
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Fonctions optionnelles :

```bash
pip install yt-dlp          # vidéos
pip install libtorrent      # torrents (ou : sudo dnf install python3-libtorrent)
```

Sur Fedora avec libtorrent installé par `dnf`, crée le venv avec `--system-site-packages`.
Pour le presse-papiers : `wl-clipboard` (Wayland), `xclip` ou `xsel` (X11).
Les proxys `socks5://` demandent `pip install "httpx[socks]"`.

Installation comme commande : `pip install .` puis `fdm-tui`.

## Utilisation

```bash
python3 fdm_tui.py
python3 fdm_tui.py "https://site/fichier.iso" "magnet:?xt=..." -d ~/Telechargements
python3 fdm_tui.py -f liens.txt
```

Appuie sur `h` dans l'application pour l'aide.

| Touche | Action |
|---|---|
| `a` | Ajouter des liens (multi-lignes, options) |
| `g` | Aspirer les liens d'une page web |
| `m` / `x` | Importer / exporter une liste |
| `p` / `P` | Pause-reprise / tout en pause |
| `r` | Recommencer |
| `d` / `D` | Retirer / retirer + supprimer les fichiers |
| `C` | Vider les téléchargements terminés |
| `[` / `]` | Monter / descendre dans la file |
| `t` | Changer de filtre |
| `l` | Limite de vitesse globale |
| `s` | Réglages |
| `e` | Action de fin |
| `c` | Surveiller le presse-papiers |
| `i` / Entrée | Détails et carte des segments |
| `o` / `f` | Ouvrir le fichier / le dossier |
| `q` | Quitter |

### Syntaxe d'une ligne

```
URL [URL…] [-o nom] [-d dossier] [-r referer] [-c "cookie"] [-H "Clé: valeur"]
           [-n segments] [-m miroir] [--checksum sha256:…] [--limite Ko/s]
           [-v] [-q 720|best|audio]
```

## Tests

```bash
python3 tests/test_moteur.py   # moteur contre un serveur HTTP local (~1 min)
python3 tests/test_tui.py      # interface via le pilote de test de Textual
```

## Limites

Pas d'extension navigateur, pas de FTP, pas de téléchargement de pages complètes ni de conversion de médias.

## Avertissement

N'utilise cet outil que pour télécharger des contenus que tu as le droit de télécharger.

## Licence

MIT — voir [LICENSE](LICENSE).
