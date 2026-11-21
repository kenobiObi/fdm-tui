#!/usr/bin/env python3
"""
fdm_tui.py — Gestionnaire de téléchargements en terminal (inspiré de Free Download Manager)

Dépendances :
    pip install textual httpx
Optionnel :
    pip install yt-dlp        # vidéos (YouTube, Vimeo, ...)
    libtorrent (python3-libtorrent ou pip install libtorrent)   # magnets / .torrent
    wl-clipboard, xclip ou xsel   # surveillance du presse-papiers

Lancement :
    python3 fdm_tui.py
    python3 fdm_tui.py "https://site/fichier.iso" "magnet:?xt=..." -d ~/Telechargements
    python3 fdm_tui.py -f liens.txt

Touche h dans l'application : aide complète.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, fields
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urldefrag, urljoin, urlparse

import httpx
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    Switch,
    TextArea,
)

try:
    from textual.markup import escape as _echapper
except ImportError:  # anciennes versions de Textual
    from rich.markup import escape as _echapper

try:
    import libtorrent as lt
except ImportError:
    lt = None

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "fdm_tui"
FICHIER_CONFIG = CONFIG_DIR / "config.json"
FICHIER_ETAT = CONFIG_DIR / "etat.json"

MIN_SEGMENT = 1 << 20  # 1 Mio : taille minimale d'un segment
TAILLE_BLOC = 64 * 1024
UNITES = ["o", "Ko", "Mo", "Go", "To"]

CATEGORIES = {
    "Vidéos": {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg", "3gp"},
    "Musique": {"mp3", "flac", "wav", "aac", "ogg", "m4a", "wma", "opus"},
    "Images": {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "tiff", "ico"},
    "Documents": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "epub", "odt", "ods", "csv", "md"},
    "Archives": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "zst", "tgz", "iso"},
    "Programmes": {"exe", "msi", "deb", "rpm", "appimage", "apk", "dmg", "sh", "run", "flatpak"},
    "Torrents": {"torrent"},
}
TOUTES_EXT = set().union(*CATEGORIES.values())

SITES_VIDEO = (
    "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "twitch.tv",
    "tiktok.com", "soundcloud.com", "bilibili.com", "twitter.com", "x.com",
)

ACTIONS_FIN = ["aucune", "quitter", "veille", "eteindre"]
LIBELLES_FIN = {"aucune": "rien", "quitter": "quitter", "veille": "mise en veille", "eteindre": "extinction"}

FILTRES = [
    ("Tous", lambda d: True),
    ("En cours", lambda d: d.statut == "downloading"),
    ("En file", lambda d: d.statut == "queued"),
    ("Pause/Erreurs", lambda d: d.statut in ("paused", "error")),
    ("Terminés", lambda d: d.statut == "completed"),
]

# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------


def fmt_taille(n: float) -> str:
    n = float(n)
    for u in UNITES:
        if n < 1024 or u == UNITES[-1]:
            return f"{int(n)} {u}" if u == "o" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} To"


def fmt_debit(n: float) -> str:
    return fmt_taille(n) + "/s"


def fmt_eta(secondes: float) -> str:
    if secondes < 0 or secondes != secondes:
        return "∞"
    secondes = int(secondes)
    j, reste = divmod(secondes, 86400)
    h, reste = divmod(reste, 3600)
    m, s = divmod(reste, 60)
    if j:
        return f"{j}j {h}h"
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def barre(p: float, largeur: int = 10) -> str:
    plein = int(p * largeur)
    return f"{'█' * plein}{'░' * (largeur - plein)} {p * 100:5.1f}%"


def categorie(nom: str) -> str:
    ext = Path(nom).suffix.lower().lstrip(".")
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return "Autres"


def nettoyer_nom(nom: str) -> str:
    nom = re.sub(r"[\\/\x00-\x1f]", "_", nom).strip()
    if nom in ("", ".", ".."):
        nom = "telechargement"
    return nom[:200]


def nom_depuis_headers(cd: str | None) -> str | None:
    if not cd:
        return None
    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", cd, re.I)
    if m:
        try:
            return unquote(m.group(2).strip().strip('"'), encoding=m.group(1) or "utf-8")
        except LookupError:
            return unquote(m.group(2).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.I) or re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
    return m.group(1).strip() if m else None


def deviner_nom(url: str, url_finale: str = "") -> str:
    candidats = []
    for u in (url, url_finale):
        if u:
            n = unquote(Path(urlparse(u).path).name)
            if n:
                candidats.append(n)
    for n in candidats:
        if Path(n).suffix:
            return n
    return candidats[0] if candidats else "telechargement"


def developper(url: str) -> list[str]:
    """Téléchargement par lots : http://site/img[001-050].jpg -> 50 liens."""
    m = re.search(r"\[(\d+)-(\d+)\]", url)
    if not m:
        return [url]
    a, b = m.group(1), m.group(2)
    largeur = len(a) if a.startswith("0") else 0
    lo, hi = int(a), int(b)
    if hi < lo or hi - lo > 9999:
        return [url]
    res: list[str] = []
    for i in range(lo, hi + 1):
        res += developper(url[: m.start()] + str(i).zfill(largeur) + url[m.end():])
    return res


def parser_checksum(s: str) -> tuple[str, str]:
    s = s.strip().lower()
    if ":" in s:
        algo, h = s.split(":", 1)
    else:
        algo, h = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}.get(len(s), ""), s
    if algo not in ("md5", "sha1", "sha224", "sha256", "sha384", "sha512"):
        raise ValueError("checksum non reconnu (md5, sha1, sha256, sha512)")
    try:
        int(h, 16)
    except ValueError:
        raise ValueError("checksum invalide (hexadécimal attendu)") from None
    return algo, h


def hacher_fichier(chemin: str, algo: str) -> str:
    h = hashlib.new(algo)
    with open(chemin, "rb") as f:
        while bloc := f.read(1 << 20):
            h.update(bloc)
    return h.hexdigest()


def est_site_video(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == s or host.endswith("." + s) for s in SITES_VIDEO)


def detecter_type(url: str, video: bool = False) -> str:
    if url.startswith("magnet:"):
        return "torrent"
    if url.lower().endswith(".torrent") and "://" not in url:
        return "torrent"
    if video or est_site_video(url):
        return "video"
    return "http"


def ecrire_json(chemin: Path, data) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, chemin)


def chemin_absolu(chemin: str, base: str | Path | None = None) -> str:
    """Rend un chemin absolu (~ développé). Un chemin relatif est ancré sur `base` (ou le dossier courant)."""
    p = os.path.expanduser(chemin)
    if not os.path.isabs(p) and base is not None:
        p = os.path.join(str(base), p)
    return os.path.abspath(p)


def ouvrir_chemin(chemin: str) -> bool:
    try:
        if sys.platform.startswith("win"):
            os.startfile(chemin)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", chemin])
        else:
            subprocess.Popen(
                ["xdg-open", chemin],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        return True
    except Exception:  # noqa: BLE001
        return False


def lire_presse_papiers() -> str:
    for cmd in (["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "-b", "-o"]):
        if shutil.which(cmd[0]):
            try:
                return subprocess.run(cmd, capture_output=True, text=True, timeout=1).stdout
            except Exception:  # noqa: BLE001
                return ""
    return ""


class ExtracteurLiens(HTMLParser):
    def __init__(self):
        super().__init__()
        self.liens: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("a", "link"):
            cles = ("href",)
        elif tag in ("img", "source", "video", "audio", "embed"):
            cles = ("src", "data-src")
        else:
            return
        for c in cles:
            if a.get(c):
                self.liens.append(a[c])


def filtrer_liens(base: str, liens: list[str], exts: set[str]) -> list[str]:
    vus: set[str] = set()
    res: list[str] = []
    for lien in liens:
        lien = lien.strip()
        if not lien or lien.startswith(("javascript:", "mailto:", "#", "data:")):
            continue
        url, _ = urldefrag(urljoin(base, lien))
        if not url.startswith(("http://", "https://")):
            continue
        ext = Path(unquote(urlparse(url).path)).suffix.lower().lstrip(".")
        if not ext or ext not in (exts or TOUTES_EXT):
            continue
        if url not in vus:
            vus.add(url)
            res.append(url)
    return res


class _Parseur(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def analyser_ligne(ligne: str) -> tuple[list[str], dict]:
    """Analyse une ligne : URLs + options (-o nom, -r referer, -c cookie, ...)."""
    try:
        jetons = shlex.split(ligne)
    except ValueError:
        jetons = ligne.split()
    p = _Parseur(add_help=False, prog="")
    p.add_argument("urls", nargs="*")
    p.add_argument("-o", "--nom")
    p.add_argument("-d", "--dossier")
    p.add_argument("-r", "--referer")
    p.add_argument("-c", "--cookie")
    p.add_argument("-H", "--header", action="append", default=[])
    p.add_argument("-n", "--segments", type=int)
    p.add_argument("-m", "--miroir", action="append", default=[])
    p.add_argument("--checksum")
    p.add_argument("--limite", type=int)
    p.add_argument("-q", "--qualite")
    p.add_argument("-v", "--video", action="store_true")
    a = p.parse_intermixed_args(jetons)
    if not a.urls:
        raise ValueError("aucun lien")
    entetes: dict[str, str] = {}
    if a.referer:
        entetes["Referer"] = a.referer
    if a.cookie:
        entetes["Cookie"] = a.cookie
    for h in a.header:
        if ":" not in h:
            raise ValueError(f"en-tête invalide : {h}")
        k, v = h.split(":", 1)
        entetes[k.strip()] = v.strip()
    opts = dict(
        nom=a.nom or "",
        dossier_perso=a.dossier or "",
        entetes=entetes,
        max_segments=max(1, min(32, a.segments)) if a.segments else 0,
        miroirs=list(a.miroir),
        checksum=a.checksum or "",
        limite_ko=max(0, a.limite or 0),
        qualite=a.qualite or "",
        video=a.video,
    )
    return list(a.urls), opts


# --------------------------------------------------------------------------
# Modèle
# --------------------------------------------------------------------------


@dataclass
class Config:
    dossier: str = field(default_factory=lambda: str(Path.home() / "Downloads"))
    simultanes: int = 3
    segments: int = 8
    limite_ko: int = 0
    tentatives: int = 5
    tri_auto: bool = True
    proxy: str = ""
    user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) FDM-TUI/1.0"
    plage: str = ""
    action_fin: str = "aucune"
    presse_papiers: bool = False

    @classmethod
    def charger(cls, chemin: Path = FICHIER_CONFIG) -> "Config":
        try:
            data = json.loads(chemin.read_text("utf-8"))
        except (OSError, ValueError):
            data = {}
        noms = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in data.items() if k in noms})
        cfg.dossier = chemin_absolu(cfg.dossier, Path.home())
        return cfg

    def sauver(self, chemin: Path = FICHIER_CONFIG) -> None:
        try:
            ecrire_json(chemin, {f.name: getattr(self, f.name) for f in fields(self)})
        except OSError:
            pass


class Seg:
    """Portion restante d'un fichier : octets [pos, end]."""

    __slots__ = ("start", "end", "pos", "actif")

    def __init__(self, start: int, end: int, pos: int | None = None):
        self.start = start
        self.end = end
        self.pos = start if pos is None else pos
        self.actif = False

    @property
    def restant(self) -> int:
        return max(0, self.end - self.pos + 1)


RUNTIME = {"vitesse", "info", "tache", "handle", "annule", "lim", "t0", "b0", "suppr_fichiers"}


@dataclass
class Download:
    id: int
    url: str
    kind: str = "http"  # http | video | torrent
    nom: str = ""
    dossier: str = ""
    chemin: str = ""
    categorie: str = ""
    statut: str = "queued"  # queued | downloading | paused | completed | error
    taille: int = 0
    fait: int = 0
    ranges: bool = False
    sonde: bool = False
    erreur: str = ""
    ajoute: float = field(default_factory=time.time)
    termine: float = 0.0
    entetes: dict = field(default_factory=dict)
    miroirs: list = field(default_factory=list)
    checksum: str = ""
    verif: str = ""  # "" | ok | ko
    limite_ko: int = 0
    max_segments: int = 0
    qualite: str = ""
    dossier_perso: str = ""
    segments: list = field(default_factory=list)
    # --- exécution (non sauvegardé) ---
    vitesse: float = field(default=0.0, repr=False)
    info: str = field(default="", repr=False)
    tache: object = field(default=None, repr=False)
    handle: object = field(default=None, repr=False)
    annule: object = field(default=None, repr=False)
    lim: object = field(default=None, repr=False)
    t0: object = field(default=None, repr=False)
    b0: int = field(default=0, repr=False)
    suppr_fichiers: bool = field(default=False, repr=False)

    def to_dict(self) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name not in RUNTIME and f.name != "segments"}
        d["segments"] = [[s.start, s.end, s.pos] for s in self.segments]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Download":
        noms = {f.name for f in fields(cls)} - RUNTIME - {"segments"}
        obj = cls(**{k: v for k, v in data.items() if k in noms})
        obj.segments = [Seg(a, b, c) for a, b, c in data.get("segments", [])]
        return obj


class Limiteur:
    """Seau à jetons : limite le débit (octets/s) partagé entre des coroutines."""

    def __init__(self, debit: int = 0):
        self.rate = debit
        self.tokens = float(debit)
        self.t = time.monotonic()

    def regler(self, debit: int) -> None:
        self.rate = debit
        self.tokens = float(debit)
        self.t = time.monotonic()

    async def acquire(self, n: int) -> None:
        if self.rate <= 0:
            return
        now = time.monotonic()
        self.tokens = min(self.tokens + (now - self.t) * self.rate, float(self.rate))
        self.t = now
        self.tokens -= n
        if self.tokens < 0:
            await asyncio.sleep(-self.tokens / self.rate)


class ErreurTelechargement(Exception):
    """Erreur temporaire : on peut réessayer."""


class ErreurFatale(Exception):
    """Erreur définitive : inutile de réessayer."""


def message_erreur(e: BaseException) -> str:
    return str(e).strip() or e.__class__.__name__


# --------------------------------------------------------------------------
# Moteur de téléchargement
# --------------------------------------------------------------------------


class Gestionnaire:
    def __init__(self, cfg: Config, fichier_etat: Path = FICHIER_ETAT):
        self.cfg = cfg
        self.fichier_etat = fichier_etat
        self.liste: list[Download] = []
        self.prochain = 1
        self.limiteur = Limiteur(cfg.limite_ko * 1024)
        self.notifier = lambda msg, severite="information": None
        self.sur_fin = lambda action: None
        self._lt = None
        self._ticks = 0
        self._arme = False

    # ---- persistance -----------------------------------------------------

    def sauvegarder(self) -> None:
        try:
            ecrire_json(self.fichier_etat, {"prochain": self.prochain, "liste": [d.to_dict() for d in self.liste]})
        except OSError:
            pass

    def charger(self) -> None:
        try:
            data = json.loads(self.fichier_etat.read_text("utf-8"))
        except (OSError, ValueError):
            return
        self.prochain = int(data.get("prochain", 1))
        self.liste = []
        for item in data.get("liste", []):
            try:
                d = Download.from_dict(item)
            except (TypeError, ValueError):
                continue
            if d.statut == "downloading":
                d.statut = "queued"  # reprise automatique
            if d.kind == "http" and d.statut != "completed" and d.chemin and not os.path.isabs(d.chemin):
                # ancien chemin relatif (dépend du dossier de lancement) : on le recalcule
                d.sonde, d.chemin, d.segments, d.fait = False, "", [], 0
            self.liste.append(d)
            self.prochain = max(self.prochain, d.id + 1)

    # ---- gestion de la liste ----------------------------------------------

    def par_id(self, ident: int) -> Download | None:
        return next((d for d in self.liste if d.id == ident), None)

    def ajouter(self, url: str, *, nom="", dossier_perso="", entetes=None, max_segments=0,
                miroirs=None, checksum="", limite_ko=0, qualite="", video=False) -> Download:
        url = url.strip()
        kind = detecter_type(url, video or bool(qualite))
        if kind == "torrent" and not url.startswith("magnet:"):
            p = Path(url).expanduser()
            if not p.is_file():
                raise ValueError("fichier .torrent introuvable")
            url = str(p)
        elif kind in ("http", "video"):
            if "://" not in url:
                url = "https://" + url
            u = urlparse(url)
            if u.scheme not in ("http", "https"):
                raise ValueError("protocole non pris en charge (http, https, magnet, vidéo)")
            if not u.netloc:
                raise ValueError("URL invalide")
        if any(d.url == url and d.statut != "completed" for d in self.liste):
            raise ValueError("déjà dans la liste")
        if checksum:
            parser_checksum(checksum)
        d = Download(
            id=self.prochain, url=url, kind=kind, nom=nom, dossier_perso=dossier_perso,
            entetes=dict(entetes or {}), miroirs=list(miroirs or []), checksum=checksum,
            limite_ko=limite_ko, max_segments=max_segments, qualite=qualite,
        )
        self.prochain += 1
        self.liste.append(d)
        self.sauvegarder()
        return d

    def pause(self, d: Download) -> None:
        if d.statut in ("downloading", "queued"):
            d.statut = "paused"
            if d.tache:
                d.tache.cancel()

    def reprendre(self, d: Download) -> None:
        if d.statut in ("paused", "error"):
            d.statut = "queued"
            d.erreur = ""

    def basculer(self, d: Download) -> None:
        if d.statut in ("downloading", "queued"):
            self.pause(d)
        else:
            self.reprendre(d)

    def recommencer(self, d: Download) -> None:
        if d.tache:
            d.tache.cancel()
        for p in (d.chemin + ".fdmpart",) if d.chemin else ():
            Path(p).unlink(missing_ok=True)
        d.segments, d.fait, d.erreur, d.verif = [], 0, "", ""
        if d.kind == "http":
            d.sonde, d.chemin, d.taille = False, "", 0
        d.statut = "queued"

    def _sur(self, chemin: str, d: Download) -> bool:
        """Sécurité : on ne supprime que ce qui est sous le dossier de téléchargement."""
        try:
            p = Path(chemin).resolve()
            racines = [Path(chemin_absolu(self.cfg.dossier, Path.home())).resolve()]
            if d.dossier:
                racines.append(Path(d.dossier).resolve())
            return any(p != r and p.is_relative_to(r) for r in racines)
        except (OSError, ValueError):
            return False

    def supprimer(self, d: Download, fichiers: bool = False) -> None:
        d.suppr_fichiers = fichiers
        if d.tache:
            d.tache.cancel()
        if d in self.liste:
            self.liste.remove(d)
        if fichiers and d.chemin:
            for p in (d.chemin, d.chemin + ".fdmpart"):
                if os.path.lexists(p) and self._sur(p, d):
                    if os.path.isdir(p) and not os.path.islink(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        Path(p).unlink(missing_ok=True)
        self.sauvegarder()

    def vider_termines(self) -> int:
        n = len(self.liste)
        self.liste = [d for d in self.liste if d.statut != "completed"]
        self.sauvegarder()
        return n - len(self.liste)

    def deplacer(self, d: Download, delta: int) -> None:
        i = self.liste.index(d)
        j = i + delta
        if 0 <= j < len(self.liste):
            self.liste[i], self.liste[j] = self.liste[j], self.liste[i]
            self.sauvegarder()

    def appliquer_limite(self, ko: int) -> None:
        self.cfg.limite_ko = max(0, ko)
        self.limiteur.regler(self.cfg.limite_ko * 1024)
        if self._lt is not None:
            self._lt.apply_settings({"download_rate_limit": self.cfg.limite_ko * 1024})

    # ---- planification -------------------------------------------------------

    def dans_plage(self) -> bool:
        p = self.cfg.plage.strip()
        m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", p)
        if not m:
            return True
        a = int(m[1]) * 60 + int(m[2])
        b = int(m[3]) * 60 + int(m[4])
        if a == b:
            return True
        now = datetime.now()
        t = now.hour * 60 + now.minute
        return a <= t < b if a < b else (t >= a or t < b)

    def planifier(self) -> None:
        if not self.dans_plage():
            return
        libres = self.cfg.simultanes - sum(1 for d in self.liste if d.statut == "downloading")
        for d in self.liste:
            if libres <= 0:
                break
            if d.statut == "queued" and (d.tache is None or d.tache.done()):
                self._lancer(d)
                libres -= 1

    def _lancer(self, d: Download) -> None:
        d.statut, d.erreur, d.info, d.vitesse, d.t0 = "downloading", "", "", 0.0, None
        d.tache = asyncio.get_running_loop().create_task(self._executer(d))

    def tick(self) -> None:
        now = time.monotonic()
        for d in self.liste:
            if d.statut == "downloading":
                if d.t0 is None:
                    d.t0, d.b0 = now, d.fait
                else:
                    dt = now - d.t0
                    if dt > 0:
                        inst = max(0.0, (d.fait - d.b0) / dt)
                        d.vitesse = inst if d.vitesse == 0 else 0.5 * d.vitesse + 0.5 * inst
                        d.t0, d.b0 = now, d.fait
            else:
                d.vitesse, d.t0 = 0.0, None
        if not self.dans_plage():  # hors plage horaire : on remet en file
            for d in self.liste:
                if d.statut == "downloading":
                    d.statut = "queued"
                    if d.tache:
                        d.tache.cancel()
        self.planifier()
        self._ticks += 1
        if self._ticks % 3 == 0:
            self.sauvegarder()
        en_attente = any(d.statut in ("downloading", "queued") for d in self.liste)
        if en_attente:
            self._arme = True
        elif self._arme:
            self._arme = False
            if self.cfg.action_fin != "aucune":
                self.sur_fin(self.cfg.action_fin)

    # ---- exécution -----------------------------------------------------------

    async def _executer(self, d: Download) -> None:
        try:
            if d.kind == "torrent":
                await self._torrent(d)
            elif d.kind == "video":
                await self._video(d)
            else:
                await self._http(d)
        except Exception as e:  # noqa: BLE001
            d.statut, d.erreur = "error", message_erreur(e)
            self.notifier(f"Échec : {d.nom or d.url} — {d.erreur}", "error")
        finally:
            if d.tache is asyncio.current_task():
                d.tache = None
            d.vitesse = 0.0
            self.sauvegarder()

    def _client(self, entetes: dict | None = None) -> httpx.AsyncClient:
        h = {"User-Agent": self.cfg.user_agent}
        h.update(entetes or {})
        kw: dict = dict(headers=h, follow_redirects=True, timeout=httpx.Timeout(30.0, connect=15.0))
        if self.cfg.proxy:
            kw["proxy"] = self.cfg.proxy
        return httpx.AsyncClient(**kw)

    @staticmethod
    def _verifier_statut(r: httpx.Response, tolere_416: bool = False) -> None:
        c = r.status_code
        if c in (408, 429) or c >= 500:
            raise ErreurTelechargement(f"HTTP {c}")
        if c >= 400 and not (c == 416 and tolere_416):
            raise ErreurFatale(f"HTTP {c} {r.reason_phrase}")

    async def _reessayer(self, tentative, progres=None) -> None:
        echecs = 0
        while True:
            avant = progres() if progres else 0
            try:
                return await tentative(echecs)
            except ErreurFatale:
                raise
            except (httpx.HTTPError, httpx.StreamError, ErreurTelechargement) as e:
                if isinstance(e, httpx.UnsupportedProtocol):
                    raise ErreurFatale(message_erreur(e)) from e
                if progres is not None and progres() > avant:
                    echecs = 0
                echecs += 1
                if echecs > self.cfg.tentatives:
                    raise ErreurTelechargement(message_erreur(e)) from e
                await asyncio.sleep(min(2 ** echecs, 30))

    async def _limiter(self, d: Download, n: int) -> None:
        await self.limiteur.acquire(n)
        if d.limite_ko > 0:
            if d.lim is None:
                d.lim = Limiteur(d.limite_ko * 1024)
            elif d.lim.rate != d.limite_ko * 1024:
                d.lim.regler(d.limite_ko * 1024)
            await d.lim.acquire(n)

    def _dossier_pour(self, d: Download, cat: str) -> Path:
        if d.dossier_perso:
            base = Path(chemin_absolu(d.dossier_perso))
        else:
            base = Path(chemin_absolu(self.cfg.dossier, Path.home()))
            if self.cfg.tri_auto:
                base = base / cat
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ErreurFatale(f"Dossier inaccessible : {e}") from e
        return base

    def _chemin_libre(self, voulu: Path, d: Download) -> Path:
        pris = {x.chemin for x in self.liste if x is not d and x.chemin}

        def occupe(p: Path) -> bool:
            return p.exists() or Path(str(p) + ".fdmpart").exists() or str(p) in pris

        if not occupe(voulu):
            return voulu
        i = 1
        while True:
            c = voulu.with_name(f"{voulu.stem} ({i}){voulu.suffix}")
            if not occupe(c):
                return c
            i += 1

    # ---- HTTP ------------------------------------------------------------------

    async def _http(self, d: Download) -> None:
        async with self._client(d.entetes) as client:
            if not d.sonde:
                await self._reessayer(lambda e: self._sonder(client, d))
            part = Path(d.chemin + ".fdmpart")
            if d.ranges and d.taille:
                await self._http_segments(client, d, part)
            else:
                await self._http_simple(client, d, part)
        await self._finaliser(d, part)

    async def _sonder(self, client: httpx.AsyncClient, d: Download) -> None:
        async with client.stream("GET", d.url, headers={"Range": "bytes=0-0", "Accept-Encoding": "identity"}) as r:
            self._verifier_statut(r, tolere_416=True)
            taille, ranges = 0, False
            if r.status_code == 206:
                m = re.match(r"\s*bytes\s+\d+-\d+/(\d+)", r.headers.get("content-range", ""))
                if m:
                    taille = int(m.group(1))
                    ranges = taille > 0
            elif r.status_code == 200:
                cl = r.headers.get("content-length", "")
                taille = int(cl) if cl.isdigit() else 0
            cd = r.headers.get("content-disposition")
            url_finale = str(r.url)
        d.taille, d.ranges = taille, ranges
        if not d.chemin:
            nom = nettoyer_nom(d.nom or nom_depuis_headers(cd) or deviner_nom(d.url, url_finale))
            d.categorie = categorie(nom)
            dossier = self._dossier_pour(d, d.categorie)
            libre = self._chemin_libre(dossier / nom, d)
            d.dossier, d.chemin, d.nom = str(dossier), str(libre), libre.name
        d.sonde = True

    async def _http_segments(self, client, d: Download, part: Path) -> None:
        if not part.exists() or part.stat().st_size != d.taille or not d.segments:
            try:
                part.parent.mkdir(parents=True, exist_ok=True)
                libre = shutil.disk_usage(part.parent).free
            except OSError as e:
                raise ErreurFatale(f"Dossier inaccessible : {e}") from e
            if libre < d.taille:
                raise ErreurFatale("Espace disque insuffisant")
            try:
                with open(part, "wb") as f:
                    f.truncate(d.taille)
            except OSError as e:
                raise ErreurFatale(f"Écriture impossible : {e}") from e
            d.segments = [Seg(0, d.taille - 1, 0)]
            d.fait = 0
        else:
            d.fait = d.taille - sum(s.restant for s in d.segments)
        for s in d.segments:
            s.actif = False
        n = d.max_segments or self.cfg.segments
        ouvriers = [asyncio.create_task(self._ouvrier(client, d, part)) for _ in range(n)]
        try:
            await asyncio.gather(*ouvriers)
        finally:
            for t in ouvriers:
                t.cancel()
            await asyncio.gather(*ouvriers, return_exceptions=True)
        if any(s.restant for s in d.segments):
            raise ErreurTelechargement("téléchargement incomplet")

    @staticmethod
    def _prendre(d: Download) -> Seg | None:
        """Prend un segment libre, sinon coupe en deux le plus gros segment en cours."""
        for s in d.segments:
            if s.restant and not s.actif:
                s.actif = True
                return s
        victime = max((s for s in d.segments if s.actif), key=lambda s: s.restant, default=None)
        if victime is None or victime.restant < 2 * MIN_SEGMENT:
            return None
        milieu = victime.pos + victime.restant // 2
        nouveau = Seg(milieu, victime.end, milieu)
        nouveau.actif = True
        victime.end = milieu - 1
        d.segments.append(nouveau)
        return nouveau

    async def _ouvrier(self, client, d: Download, part: Path) -> None:
        while True:
            seg = self._prendre(d)
            if seg is None:
                return
            try:
                await self._reessayer(lambda e: self._telecharger(client, d, seg, part, e), progres=lambda: seg.pos)
            finally:
                seg.actif = False

    async def _telecharger(self, client, d: Download, seg: Seg, part: Path, echecs: int) -> None:
        if seg.pos > seg.end:
            return
        urls = [d.url] + d.miroirs
        await self._flux(client, d, seg, part, urls[echecs % len(urls)])
        if seg.pos <= seg.end:
            raise ErreurTelechargement("connexion interrompue")

    async def _flux(self, client, d: Download, seg: Seg, part: Path, url: str) -> None:
        headers = {"Range": f"bytes={seg.pos}-{seg.end}", "Accept-Encoding": "identity"}
        async with client.stream("GET", url, headers=headers) as r:
            self._verifier_statut(r)
            if r.status_code == 206:
                m = re.match(r"\s*bytes\s+(\d+)-\d+/(\d+|\*)", r.headers.get("content-range", ""))
                if not m or int(m.group(1)) != seg.pos:
                    raise ErreurTelechargement("Content-Range inattendu")
                if m.group(2) != "*" and int(m.group(2)) != d.taille:
                    raise ErreurTelechargement("le fichier distant a changé (taille différente)")
            elif not (r.status_code == 200 and seg.pos == 0):
                raise ErreurTelechargement(f"le serveur n'accepte pas la reprise (HTTP {r.status_code})")
            try:
                f = open(part, "r+b")
            except OSError as e:
                raise ErreurFatale(f"Écriture impossible : {e}") from e
            with f:
                f.seek(seg.pos)
                async for bloc in r.aiter_raw(TAILLE_BLOC):
                    await self._limiter(d, len(bloc))
                    restant = seg.end - seg.pos + 1
                    if restant <= 0:
                        break
                    if len(bloc) > restant:
                        bloc = bloc[:restant]
                    try:
                        f.write(bloc)
                        f.flush()
                    except OSError as e:
                        raise ErreurFatale(f"Écriture disque : {e}") from e
                    seg.pos += len(bloc)
                    d.fait += len(bloc)
                    if seg.pos > seg.end:
                        break

    async def _http_simple(self, client, d: Download, part: Path) -> None:
        async def tentative(echecs: int) -> None:
            d.fait = 0
            urls = [d.url] + d.miroirs
            async with client.stream("GET", urls[echecs % len(urls)], headers={"Accept-Encoding": "identity"}) as r:
                self._verifier_statut(r)
                try:
                    f = open(part, "wb")
                except OSError as e:
                    raise ErreurFatale(f"Écriture impossible : {e}") from e
                with f:
                    async for bloc in r.aiter_raw(TAILLE_BLOC):
                        await self._limiter(d, len(bloc))
                        try:
                            f.write(bloc)
                        except OSError as e:
                            raise ErreurFatale(f"Écriture disque : {e}") from e
                        d.fait += len(bloc)
            if d.taille and d.fait != d.taille:
                raise ErreurTelechargement("transfert incomplet")

        d.segments = []
        try:
            part.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ErreurFatale(f"Dossier inaccessible : {e}") from e
        await self._reessayer(tentative)
        if not d.taille:
            d.taille = d.fait

    async def _finaliser(self, d: Download, part: Path) -> None:
        if d.taille and d.fait != d.taille:
            raise ErreurTelechargement("téléchargement incomplet")
        try:
            os.replace(part, d.chemin)
        except OSError as e:
            raise ErreurFatale(f"Impossible de finaliser le fichier : {e}") from e
        d.segments = []
        try:
            d.fait = d.taille = os.path.getsize(d.chemin)
        except OSError as e:
            raise ErreurFatale(f"Fichier final introuvable : {e}") from e
        if d.checksum:
            d.info = "Vérification…"
            algo, attendu = parser_checksum(d.checksum)
            try:
                obtenu = await asyncio.to_thread(hacher_fichier, d.chemin, algo)
            except OSError as e:
                raise ErreurFatale(f"Vérification impossible : {e}") from e
            d.verif = "ok" if obtenu == attendu else "ko"
            d.info = ""
        d.statut, d.termine = "completed", time.time()
        if d.verif == "ko":
            self.notifier(f"⚠ Somme de contrôle incorrecte : {d.nom}", "error")
        else:
            self.notifier(f"✓ Terminé : {d.nom}")

    # ---- Vidéos (yt-dlp) ------------------------------------------------------------

    @staticmethod
    def _format_video(q: str) -> str:
        ffmpeg = shutil.which("ffmpeg") is not None
        if q == "audio":
            return "bestaudio/best"
        if not q.isdigit():
            return "bestvideo+bestaudio/best" if ffmpeg else "best"
        h = int(q)
        if ffmpeg:
            return f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
        return f"best[height<={h}]/best"

    async def _video(self, d: Download) -> None:
        if yt_dlp is None:
            raise ErreurFatale("yt-dlp n'est pas installé (pip install yt-dlp)")
        cat = "Musique" if d.qualite == "audio" else "Vidéos"
        dossier = self._dossier_pour(d, cat)
        d.dossier, d.categorie = str(dossier), cat
        annule = threading.Event()
        d.annule = annule
        arret = getattr(yt_dlp.utils, "DownloadCancelled", Exception)

        def hook(h):
            if annule.is_set():
                raise arret()
            titre = (h.get("info_dict") or {}).get("title")
            if titre:
                d.nom = titre
            if h.get("status") == "downloading":
                d.fait = h.get("downloaded_bytes") or 0
                d.taille = h.get("total_bytes") or h.get("total_bytes_estimate") or 0
                d.info = ""
            elif h.get("status") == "finished":
                d.info = "Finalisation…"

        def travail() -> str:
            opts = {
                "format": self._format_video(d.qualite),
                "outtmpl": str(dossier / "%(title).150B [%(id)s].%(ext)s"),
                "noplaylist": True, "quiet": True, "no_warnings": True, "noprogress": True,
                "continuedl": True, "progress_hooks": [hook],
            }
            if self.cfg.proxy:
                opts["proxy"] = self.cfg.proxy
            if self.cfg.limite_ko:
                opts["ratelimit"] = self.cfg.limite_ko * 1024
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(d.url, download=True)
                rd = info.get("requested_downloads") or []
                return rd[0]["filepath"] if rd else ydl.prepare_filename(info)

        try:
            chemin = await asyncio.to_thread(travail)
        except asyncio.CancelledError:
            annule.set()
            raise
        d.chemin, d.nom = chemin, Path(chemin).name
        d.dossier = str(Path(chemin).parent)
        d.fait = d.taille = os.path.getsize(chemin)
        d.info, d.statut, d.termine = "", "completed", time.time()
        self.notifier(f"✓ Terminé : {d.nom}")

    # ---- Torrents (libtorrent) ---------------------------------------------------------

    def _session_lt(self):
        if lt is None:
            raise ErreurFatale("libtorrent n'est pas installé (python3-libtorrent)")
        if self._lt is None:
            self._lt = lt.session({"listen_interfaces": "0.0.0.0:6881,[::]:6881"})
            if self.cfg.limite_ko:
                self._lt.apply_settings({"download_rate_limit": self.cfg.limite_ko * 1024})
        return self._lt

    async def _torrent(self, d: Download) -> None:
        ses = self._session_lt()
        d.categorie = "Torrents"
        dossier = self._dossier_pour(d, "Torrents")
        d.dossier = str(dossier)
        try:
            if d.url.startswith("magnet:"):
                params = lt.parse_magnet_uri(d.url)
            else:
                params = lt.add_torrent_params()
                params.ti = lt.torrent_info(d.url)
            params.save_path = str(dossier)
            h = ses.add_torrent(params)
        except Exception as e:  # noqa: BLE001
            raise ErreurFatale(f"Torrent invalide : {e}") from e
        h.unset_flags(lt.torrent_flags.auto_managed)
        d.handle = h
        try:
            while True:
                s = h.status()
                if s.errc.value():
                    raise ErreurFatale(s.errc.message())
                if s.name:
                    d.nom = s.name
                d.taille = s.total_wanted if s.has_metadata else 0
                d.fait = s.total_wanted_done
                d.info = f"{s.num_peers} pairs" if s.has_metadata else "Métadonnées…"
                if s.has_metadata and s.is_finished:
                    break
                await asyncio.sleep(1)
            d.chemin = str(dossier / s.name)
            d.info, d.statut, d.termine = "", "completed", time.time()
            self.notifier(f"✓ Terminé : {d.nom}")
        finally:
            d.handle = None
            try:
                if h.is_valid():
                    if d.suppr_fichiers:
                        ses.remove_torrent(h, lt.session.delete_files)
                    else:
                        ses.remove_torrent(h)
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------
# Interface
# --------------------------------------------------------------------------

COLONNES = [
    ("id", "#"), ("nom", "Nom"), ("taille", "Taille"), ("prog", "Progression"),
    ("vit", "Vitesse"), ("eta", "ETA"), ("statut", "Statut"), ("cat", "Catégorie"),
]

AIDE_AJOUT = (
    "Un lien par ligne : http(s)://…  magnet:?…  fichier .torrent local  vidéo (YouTube…)\n"
    "Lots : http://site/img[001-050].jpg\n"
    "Options : -o nom  -d dossier  -r referer  -c \"cookie\"  -H \"Clé: valeur\"  -n segments\n"
    "          -m miroir  --checksum sha256:…  --limite Ko/s  -v (vidéo)  -q 720|best|audio"
)

TEXTE_AIDE = """\
a  Ajouter des liens (multi-lignes, options)     i / Entrée  Détails du téléchargement
g  Aspirer les liens d'une page web              m  Importer une liste    x  Exporter la liste
p  Pause / Reprise                               P  Tout en pause / tout reprendre
r  Recommencer                                   [ / ]  Monter / descendre dans la file
d  Retirer de la liste                           D  Retirer + supprimer les fichiers
C  Vider les téléchargements terminés            t  Filtre (tous, en cours, file, ...)
l  Limite de vitesse globale                     s  Réglages (dossier, proxy, plage horaire...)
e  Action de fin (quitter / veille / extinction) c  Surveiller le presse-papiers
o  Ouvrir le fichier                             f  Ouvrir le dossier
h  Cette aide                                    q  Quitter (les téléchargements reprennent au prochain lancement)
"""


def statut_texte(d: Download) -> str:
    if d.statut == "downloading":
        return d.info or "Téléchargement"
    if d.statut == "queued":
        return "En file"
    if d.statut == "paused":
        return "En pause"
    if d.statut == "completed":
        return "Terminé" + (" ✓" if d.verif == "ok" else " ⚠ checksum" if d.verif == "ko" else "")
    return "Erreur : " + d.erreur


def carte_segments(d: Download, largeur: int = 60) -> str:
    if not d.taille:
        return ""
    if d.statut == "completed":
        return "█" * largeur
    if not d.segments:
        plein = int(min(1.0, d.fait / d.taille) * largeur)
        return "█" * plein + "░" * (largeur - plein)
    reste = [(s.pos, s.end) for s in d.segments if s.pos <= s.end]
    out = []
    for i in range(largeur):
        a = d.taille * i // largeur
        b = d.taille * (i + 1) // largeur - 1
        if b < a:
            out.append("█")
            continue
        manque = sum(max(0, min(b, e) - max(a, p) + 1) for p, e in reste)
        out.append("█" if manque == 0 else "░" if manque == b - a + 1 else "▒")
    return "".join(out)


def texte_details(d: Download) -> str:
    eta = ""
    if d.statut == "downloading" and d.vitesse > 1 and d.taille:
        eta = fmt_eta((d.taille - d.fait) / d.vitesse)
    lignes = [
        f"Nom        : {d.nom or '(en attente)'}",
        f"URL        : {d.url}",
        f"Type       : {d.kind}",
        f"Fichier    : {d.chemin or '—'}",
        f"Catégorie  : {d.categorie or '—'}",
        f"Taille     : {fmt_taille(d.taille) if d.taille else 'inconnue'}",
        f"Téléchargé : {fmt_taille(d.fait)}" + (f" ({d.fait * 100 / d.taille:.1f} %)" if d.taille else ""),
        f"Vitesse    : {fmt_debit(d.vitesse)}   ETA : {eta or '—'}",
        f"Statut     : {statut_texte(d)}",
        f"Reprise    : {'oui' if d.ranges else 'non / inconnue'}   Connexions actives : "
        f"{sum(1 for s in d.segments if s.actif)}",
        f"Ajouté le  : {datetime.fromtimestamp(d.ajoute):%d/%m/%Y %H:%M:%S}",
    ]
    if d.termine:
        lignes.append(f"Terminé le : {datetime.fromtimestamp(d.termine):%d/%m/%Y %H:%M:%S}")
    if d.checksum:
        lignes.append(f"Checksum   : {d.checksum} → {d.verif or 'non vérifié'}")
    if d.miroirs:
        lignes.append("Miroirs    : " + ", ".join(d.miroirs))
    if d.limite_ko:
        lignes.append(f"Limite     : {d.limite_ko} Ko/s")
    carte = carte_segments(d)
    if carte:
        lignes += ["", "Carte des segments :", carte]
    return "\n".join(lignes)


class AjoutScreen(ModalScreen[str]):
    BINDINGS = [
        Binding("escape", "annuler", "Annuler", priority=True),
        Binding("ctrl+s", "valider", "Ajouter", priority=True),
    ]

    def __init__(self, texte: str = ""):
        super().__init__()
        self.texte = texte

    def compose(self) -> ComposeResult:
        with Vertical(id="dialogue"):
            yield Label("Ajouter des téléchargements", classes="titre")
            yield Static(AIDE_AJOUT, markup=False, classes="aide")
            yield TextArea(self.texte, id="zone")
            with Horizontal(classes="boutons"):
                yield Button("Ajouter (Ctrl+S)", id="ok", variant="success")
                yield Button("Annuler (Échap)", id="non")

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    def action_valider(self) -> None:
        self.dismiss(self.query_one(TextArea).text)

    def action_annuler(self) -> None:
        self.dismiss("")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(self.query_one(TextArea).text if event.button.id == "ok" else "")


class SaisieScreen(ModalScreen[str]):
    BINDINGS = [Binding("escape", "annuler", "Annuler", priority=True)]

    def __init__(self, titre: str, aide: str = "", valeur: str = "", placeholder: str = ""):
        super().__init__()
        self.titre, self.aide, self.valeur, self.placeholder = titre, aide, valeur, placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="dialogue"):
            yield Label(self.titre, classes="titre")
            if self.aide:
                yield Static(self.aide, markup=False, classes="aide")
            yield Input(self.valeur, placeholder=self.placeholder, id="champ")
            with Horizontal(classes="boutons"):
                yield Button("OK", id="ok", variant="success")
                yield Button("Annuler", id="non")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    def action_annuler(self) -> None:
        self.dismiss("")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(self.query_one(Input).value if event.button.id == "ok" else "")


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "non", "Non", priority=True)]

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialogue"):
            yield Static(self.question, markup=False)
            with Horizontal(classes="boutons"):
                yield Button("Oui", id="oui", variant="error")
                yield Button("Non", id="non")

    def action_non(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "oui")


class ReglagesScreen(ModalScreen[dict]):
    BINDINGS = [
        Binding("escape", "annuler", "Annuler", priority=True),
        Binding("ctrl+s", "valider", "Enregistrer", priority=True),
    ]
    CHAMPS = [
        ("dossier", "Dossier de téléchargement"),
        ("simultanes", "Téléchargements simultanés (1-20)"),
        ("segments", "Connexions par fichier (1-32)"),
        ("limite_ko", "Limite globale en Ko/s (0 = illimitée)"),
        ("tentatives", "Tentatives en cas d'échec (0-50)"),
        ("proxy", "Proxy (http://hôte:port ou socks5://hôte:port)"),
        ("plage", "Plage horaire autorisée HH:MM-HH:MM (vide = toujours)"),
        ("user_agent", "User-Agent"),
    ]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg

    def compose(self) -> ComposeResult:
        with Vertical(id="dialogue"):
            yield Label("Réglages", classes="titre")
            with VerticalScroll(id="champs"):
                for cle, titre in self.CHAMPS:
                    yield Label(titre)
                    yield Input(str(getattr(self.cfg, cle)), id=f"r-{cle}")
                yield Label("Classer automatiquement par catégorie")
                yield Switch(value=self.cfg.tri_auto, id="r-tri_auto")
                yield Label("Surveiller le presse-papiers")
                yield Switch(value=self.cfg.presse_papiers, id="r-presse_papiers")
            with Horizontal(classes="boutons"):
                yield Button("Enregistrer (Ctrl+S)", id="ok", variant="success")
                yield Button("Annuler", id="non")

    def action_annuler(self) -> None:
        self.dismiss({})

    def action_valider(self) -> None:
        def val(cle: str) -> str:
            return self.query_one(f"#r-{cle}", Input).value.strip()

        try:
            res: dict = {}
            bornes = {"simultanes": (1, 20), "segments": (1, 32), "limite_ko": (0, 10**9), "tentatives": (0, 50)}
            for cle, (lo, hi) in bornes.items():
                n = int(val(cle) or 0)
                if not lo <= n <= hi:
                    raise ValueError(f"{cle} doit être entre {lo} et {hi}")
                res[cle] = n
            if not val("dossier"):
                raise ValueError("le dossier est obligatoire")
            if val("plage") and not re.fullmatch(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}", val("plage")):
                raise ValueError("plage horaire invalide (exemple : 23:00-07:00)")
        except ValueError as e:
            self.app.notify(_echapper(f"Valeur invalide : {e}"), severity="error")
            return
        res.update(dossier=chemin_absolu(val("dossier"), Path.home()), proxy=val("proxy"), plage=val("plage"),
                   user_agent=val("user_agent") or self.cfg.user_agent,
                   tri_auto=self.query_one("#r-tri_auto", Switch).value,
                   presse_papiers=self.query_one("#r-presse_papiers", Switch).value)
        self.dismiss(res)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "ok":
            self.action_valider()
        else:
            self.dismiss({})


class DetailsScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "fermer", "Fermer", priority=True)]

    def __init__(self, d: Download):
        super().__init__()
        self.d = d

    def compose(self) -> ComposeResult:
        with Vertical(id="dialogue"):
            yield Label("Détails du téléchargement", classes="titre")
            yield Static(Text(texte_details(self.d)), id="contenu", markup=False)
            with Horizontal(classes="boutons"):
                yield Button("Fermer (Échap)", id="non")

    def on_mount(self) -> None:
        self.set_interval(0.5, self._maj)

    def _maj(self) -> None:
        self.query_one("#contenu", Static).update(Text(texte_details(self.d)))

    def action_fermer(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)


class AideScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "fermer", "Fermer", priority=True)]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialogue"):
            yield Label("Aide — raccourcis", classes="titre")
            yield Static(Text(TEXTE_AIDE), markup=False)
            yield Static(Text(AIDE_AJOUT), markup=False, classes="aide")
            with Horizontal(classes="boutons"):
                yield Button("Fermer", id="non")

    def action_fermer(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)


class FdmApp(App):
    TITLE = "FDM-TUI"

    CSS = """
    #barre { height: 3; padding: 0 1; }
    #barre Button { margin-right: 1; }
    #table { height: 1fr; }
    #etat { height: 1; padding: 0 1; background: $boost; }
    ModalScreen { align: center middle; }
    #dialogue { width: 100; max-width: 96%; height: auto; max-height: 92%;
                border: thick $accent; background: $surface; padding: 1 2; }
    .titre { text-style: bold; margin-bottom: 1; }
    .aide { color: $text-muted; margin-bottom: 1; }
    .boutons { height: 3; align-horizontal: right; margin-top: 1; }
    .boutons Button { margin-left: 1; }
    #zone { height: 8; }
    #champs { height: auto; max-height: 22; }
    """

    BINDINGS = [
        Binding("a", "ajouter", "Ajouter"),
        Binding("p", "pause", "Pause/Reprise"),
        Binding("P", "tout_pause", "Tout pause", show=False),
        Binding("d", "retirer(False)", "Retirer"),
        Binding("D", "retirer(True)", "Retirer+fichiers", show=False),
        Binding("r", "recommencer", "Recommencer", show=False),
        Binding("C", "vider", "Vider terminés", show=False),
        Binding("left_square_bracket", "deplacer(-1)", "Monter", show=False),
        Binding("right_square_bracket", "deplacer(1)", "Descendre", show=False),
        Binding("t", "filtre", "Filtre"),
        Binding("l", "limite", "Limite"),
        Binding("s", "reglages", "Réglages"),
        Binding("e", "action_fin", "Action de fin", show=False),
        Binding("c", "presse_papiers", "Presse-papiers", show=False),
        Binding("g", "aspirer", "Aspirer page"),
        Binding("m", "importer", "Importer", show=False),
        Binding("x", "exporter", "Exporter", show=False),
        Binding("i", "details", "Détails", show=False),
        Binding("o", "ouvrir", "Ouvrir fichier", show=False),
        Binding("f", "ouvrir_dossier", "Ouvrir dossier", show=False),
        Binding("h", "aide", "Aide"),
        Binding("q", "quit", "Quitter"),
    ]

    def __init__(self, cfg: Config, gest: Gestionnaire, texte_initial: str = ""):
        super().__init__()
        self.cfg = cfg
        self.gest = gest
        self.texte_initial = texte_initial
        self.filtre = 0
        self._ids: list[int] = []
        self._cache: dict[int, list[str]] = {}
        self._dernier_pp: str | None = None
        self._fin_en_cours: str | None = None
        self._taches: set = set()
        gest.notifier = self.avis
        gest.sur_fin = self._sur_fin

    # ---- construction ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="barre"):
            yield Button("+ Ajouter", id="b-ajouter", variant="success")
            yield Button("Pause / Reprise", id="b-pause", variant="warning")
            yield Button("Retirer", id="b-retirer", variant="error")
            yield Button("Réglages", id="b-reglages", variant="primary")
        yield DataTable(cursor_type="row", zebra_stripes=True, id="table")
        yield Static("", id="etat", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        for cle, titre in COLONNES:
            t.add_column(titre, key=cle)
        self.gest.charger()
        if self.texte_initial.strip():
            self.ajouter_texte(self.texte_initial)
        self._reconstruire()
        self.set_interval(1.0, self._tick)
        self.set_interval(1.5, self._surveiller_pp)
        t.focus()

    # ---- utilitaires -------------------------------------------------------------

    def avis(self, msg: str, severite: str = "information") -> None:
        self.notify(_echapper(msg), severity=severite, timeout=8 if severite == "error" else 5)

    def _principal(self) -> bool:
        return len(self.screen_stack) == 1

    def _courant(self) -> Download | None:
        if not self._ids:
            return None
        i = self.query_one(DataTable).cursor_row
        return self.gest.par_id(self._ids[i]) if 0 <= i < len(self._ids) else None

    def _visibles(self) -> list[Download]:
        f = FILTRES[self.filtre][1]
        return [d for d in self.gest.liste if f(d)]

    def _valeurs(self, d: Download) -> list[str]:
        prog = barre(min(1.0, d.fait / d.taille)) if d.taille else (fmt_taille(d.fait) if d.fait else "—")
        vit = fmt_debit(d.vitesse) if d.statut == "downloading" and d.vitesse > 0 else ""
        eta = ""
        if d.statut == "downloading" and d.vitesse > 1 and d.taille:
            eta = fmt_eta((d.taille - d.fait) / d.vitesse)
        nom = d.nom or d.url
        if len(nom) > 60:
            nom = nom[:57] + "…"
        return [str(d.id), nom, fmt_taille(d.taille) if d.taille else "?", prog, vit, eta,
                statut_texte(d)[:60], d.categorie or ""]

    def _reconstruire(self) -> None:
        t = self.query_one(DataTable)
        courant = self._courant()
        t.clear()
        visibles = self._visibles()
        self._cache = {}
        for d in visibles:
            vals = self._valeurs(d)
            self._cache[d.id] = vals
            t.add_row(*[Text(v) for v in vals], key=str(d.id))
        self._ids = [d.id for d in visibles]
        if courant and courant.id in self._ids:
            t.move_cursor(row=self._ids.index(courant.id))

    def _maj_table(self) -> None:
        visibles = self._visibles()
        if [d.id for d in visibles] != self._ids:
            self._reconstruire()
            return
        t = self.query_one(DataTable)
        for d in visibles:
            vals = self._valeurs(d)
            ancien = self._cache.get(d.id)
            for i, (cle, _) in enumerate(COLONNES):
                if ancien is None or ancien[i] != vals[i]:
                    t.update_cell(str(d.id), cle, Text(vals[i]), update_width=True)
            self._cache[d.id] = vals

    def _maj_etat(self) -> None:
        g, c = self.gest, self.cfg
        total = sum(d.vitesse for d in g.liste if d.statut == "downloading")

        def n(s: str) -> int:
            return sum(1 for d in g.liste if d.statut == s)

        parts = [f"↓ {fmt_debit(total)}", "Limite : " + ("illimitée" if not c.limite_ko else f"{c.limite_ko} Ko/s"),
                 f"Actifs : {n('downloading')}/{c.simultanes}", f"File : {n('queued')}",
                 f"Filtre : {FILTRES[self.filtre][0]}"]
        if c.action_fin != "aucune":
            parts.append(f"À la fin : {LIBELLES_FIN[c.action_fin]}")
        if c.plage:
            parts.append(f"Plage : {c.plage}" + ("" if g.dans_plage() else " (hors plage)"))
        if c.presse_papiers:
            parts.append("Presse-papiers : actif")
        self.query_one("#etat", Static).update(Text("  |  ".join(parts)))

    def _tick(self) -> None:
        self.gest.tick()
        self._maj_table()
        self._maj_etat()

    # ---- ajout --------------------------------------------------------------------

    def ajouter_texte(self, texte: str) -> int:
        n = 0
        for ligne in texte.splitlines():
            ligne = ligne.strip()
            if not ligne or ligne.startswith("#"):
                continue
            try:
                urls, opts = analyser_ligne(ligne)
            except ValueError as e:
                self.avis(f"Ligne ignorée ({e}) : {ligne[:50]}", "warning")
                continue
            for u in urls:
                for u2 in developper(u):
                    try:
                        self.gest.ajouter(u2, **opts)
                        n += 1
                    except ValueError as e:
                        self.avis(f"{u2[:60]} : {e}", "warning")
        if n:
            self.avis(f"{n} téléchargement(s) ajouté(s)")
            self.gest.planifier()
            self._reconstruire()
        return n

    def action_ajouter(self, prerempli: str = "") -> None:
        if not self._principal():
            return
        self.push_screen(AjoutScreen(prerempli), lambda t: self.ajouter_texte(t) if t else None)

    def action_importer(self) -> None:
        if not self._principal():
            return

        def apres(chemin: str) -> None:
            if not chemin.strip():
                return
            try:
                texte = Path(chemin.strip()).expanduser().read_text("utf-8", errors="replace")
            except OSError as e:
                self.avis(f"Lecture impossible : {e}", "error")
                return
            if not self.ajouter_texte(texte):
                self.avis("Aucun lien ajouté", "warning")

        self.push_screen(SaisieScreen("Importer une liste", "Chemin d'un fichier texte (un lien par ligne)"), apres)

    def action_exporter(self) -> None:
        if not self._principal():
            return

        def apres(chemin: str) -> None:
            if not chemin.strip():
                return
            try:
                Path(chemin.strip()).expanduser().write_text("\n".join(d.url for d in self.gest.liste) + "\n", "utf-8")
                self.avis(f"{len(self.gest.liste)} lien(s) exporté(s)")
            except OSError as e:
                self.avis(f"Écriture impossible : {e}", "error")

        self.push_screen(SaisieScreen("Exporter la liste", valeur=str(Path.home() / "fdm_liens.txt")), apres)

    def action_aspirer(self) -> None:
        if not self._principal():
            return

        def apres(val: str) -> None:
            if val.strip():
                self.run_worker(self._aspirer(val.strip()), exclusive=False)

        self.push_screen(SaisieScreen(
            "Aspirer les liens d'une page web",
            "URL de la page, puis (optionnel) les extensions voulues. Exemple : https://site/page pdf,zip,jpg",
            placeholder="https://…"), apres)

    async def _aspirer(self, val: str) -> None:
        parts = val.split()
        url = parts[0] if "://" in parts[0] else "https://" + parts[0]
        exts = {e.strip().lstrip(".").lower() for e in parts[1].split(",") if e.strip()} if len(parts) > 1 else set()
        try:
            async with self.gest._client() as c:
                r = await c.get(url)
                r.raise_for_status()
            p = ExtracteurLiens()
            p.feed(r.text)
            trouves = filtrer_liens(str(r.url), p.liens, exts)
        except Exception as e:  # noqa: BLE001
            self.avis(f"Aspiration impossible : {message_erreur(e)}", "error")
            return
        if not trouves:
            self.avis("Aucun fichier correspondant trouvé sur cette page", "warning")
            return

        def decision(oui: bool) -> None:
            if oui:
                self.ajouter_texte("\n".join(trouves))

        self.push_screen(ConfirmScreen(f"{len(trouves)} fichier(s) trouvé(s). Les ajouter ?"), decision)

    # ---- actions sur la sélection -----------------------------------------------------

    def action_pause(self) -> None:
        if not self._principal():
            return
        d = self._courant()
        if d:
            self.gest.basculer(d)
            self.gest.planifier()
            self._maj_table()

    def action_tout_pause(self) -> None:
        if not self._principal():
            return
        g = self.gest
        if any(d.statut in ("downloading", "queued") for d in g.liste):
            for d in g.liste:
                g.pause(d)
            self.avis("Tout est en pause")
        else:
            for d in g.liste:
                g.reprendre(d)
            g.planifier()
            self.avis("Reprise de tous les téléchargements")
        self._maj_table()

    def action_retirer(self, avec_fichiers: bool = False) -> None:
        if not self._principal():
            return
        d = self._courant()
        if not d:
            return

        def faire(oui: bool) -> None:
            if oui:
                self.gest.supprimer(d, avec_fichiers)
                self._reconstruire()

        if avec_fichiers:
            self.push_screen(ConfirmScreen(f"Retirer ET supprimer les fichiers de « {d.nom or d.url} » ?"), faire)
        else:
            faire(True)

    def action_recommencer(self) -> None:
        if not self._principal():
            return
        d = self._courant()
        if d:
            self.gest.recommencer(d)
            self._maj_table()

    def action_vider(self) -> None:
        if not self._principal():
            return
        n = self.gest.vider_termines()
        self.avis(f"{n} téléchargement(s) terminé(s) retiré(s) de la liste")
        self._reconstruire()

    def action_deplacer(self, delta: int) -> None:
        if not self._principal():
            return
        d = self._courant()
        if d:
            self.gest.deplacer(d, delta)
            self._reconstruire()
            if d.id in self._ids:
                self.query_one(DataTable).move_cursor(row=self._ids.index(d.id))

    def action_filtre(self) -> None:
        if not self._principal():
            return
        self.filtre = (self.filtre + 1) % len(FILTRES)
        self._reconstruire()
        self._maj_etat()

    def action_details(self) -> None:
        if not self._principal():
            return
        d = self._courant()
        if d:
            self.push_screen(DetailsScreen(d))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_details()

    def action_ouvrir(self) -> None:
        d = self._courant()
        if d and self._principal():
            if not (d.statut == "completed" and d.chemin and ouvrir_chemin(d.chemin)):
                self.avis("Fichier indisponible (téléchargement non terminé ?)", "warning")

    def action_ouvrir_dossier(self) -> None:
        d = self._courant()
        if d and self._principal():
            if not (d.dossier and ouvrir_chemin(d.dossier)):
                self.avis("Dossier indisponible", "warning")

    def action_aide(self) -> None:
        if self._principal():
            self.push_screen(AideScreen())

    # ---- réglages / limites / fin -----------------------------------------------------------

    def action_limite(self) -> None:
        if not self._principal():
            return

        def apres(val: str) -> None:
            if not val.strip():
                return
            try:
                ko = int(val.strip())
                if ko < 0:
                    raise ValueError
            except ValueError:
                self.avis("Entrez un nombre entier positif (Ko/s)", "error")
                return
            self.gest.appliquer_limite(ko)
            self.cfg.sauver()
            self._maj_etat()

        self.push_screen(SaisieScreen("Limite de vitesse globale", "En Ko/s — 0 = illimitée",
                                      valeur=str(self.cfg.limite_ko)), apres)

    def action_reglages(self) -> None:
        if not self._principal():
            return

        def apres(res: dict) -> None:
            if not res:
                return
            for k, v in res.items():
                setattr(self.cfg, k, v)
            self.gest.appliquer_limite(self.cfg.limite_ko)
            self.cfg.sauver()
            self._dernier_pp = None
            self._maj_etat()
            self.avis("Réglages enregistrés")

        self.push_screen(ReglagesScreen(self.cfg), apres)

    def action_action_fin(self) -> None:
        if not self._principal():
            return
        self._fin_en_cours = None  # annule un compte à rebours éventuel
        i = (ACTIONS_FIN.index(self.cfg.action_fin) + 1) % len(ACTIONS_FIN)
        self.cfg.action_fin = ACTIONS_FIN[i]
        self.cfg.sauver()
        self.avis(f"À la fin des téléchargements : {LIBELLES_FIN[self.cfg.action_fin]}")
        self._maj_etat()

    def action_presse_papiers(self) -> None:
        if not self._principal():
            return
        self.cfg.presse_papiers = not self.cfg.presse_papiers
        self._dernier_pp = None
        self.cfg.sauver()
        if self.cfg.presse_papiers and not any(shutil.which(x) for x in ("wl-paste", "xclip", "xsel")):
            self.avis("Installez wl-clipboard, xclip ou xsel pour cette fonction", "warning")
        self._maj_etat()

    async def _surveiller_pp(self) -> None:
        if not self.cfg.presse_papiers or not self._principal():
            return
        texte = (await asyncio.to_thread(lire_presse_papiers)).strip()
        if self._dernier_pp is None:  # première lecture : simple référence
            self._dernier_pp = texte
            return
        if texte != self._dernier_pp:
            self._dernier_pp = texte
            if re.fullmatch(r"(https?://|magnet:)\S+", texte):
                self.action_ajouter(texte)

    def _sur_fin(self, action: str) -> None:
        t = asyncio.get_running_loop().create_task(self._action_fin(action))
        self._taches.add(t)
        t.add_done_callback(self._taches.discard)

    async def _action_fin(self, action: str) -> None:
        if action == "quitter":
            await self.action_quit()
            return
        if action in ("veille", "eteindre"):
            self._fin_en_cours = action
            nom = "Extinction" if action == "eteindre" else "Mise en veille"
            self.avis(f"Téléchargements terminés : {nom} dans 30 s (touche e pour annuler)", "warning")
            await asyncio.sleep(30)
            if self._fin_en_cours == action and not any(
                    d.statut in ("downloading", "queued") for d in self.gest.liste):
                self.gest.sauvegarder()
                self.cfg.sauver()
                subprocess.Popen(["systemctl", "poweroff" if action == "eteindre" else "suspend"])
            self._fin_en_cours = None

    # ---- boutons / sortie ------------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "b-ajouter":
            self.action_ajouter()
        elif bid == "b-pause":
            self.action_pause()
        elif bid == "b-retirer":
            self.action_retirer(False)
        elif bid == "b-reglages":
            self.action_reglages()
        if self._principal():
            self.query_one(DataTable).focus()

    async def action_quit(self) -> None:
        taches = [d.tache for d in self.gest.liste if d.tache]
        for t in taches:
            t.cancel()
        if taches:
            await asyncio.gather(*taches, return_exceptions=True)
        self.gest.sauvegarder()
        self.cfg.sauver()
        self.exit()


# --------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description="Gestionnaire de téléchargements TUI (inspiré de FDM)")
    p.add_argument("sources", nargs="*", help="liens à ajouter (chaque argument = une ligne, options possibles)")
    p.add_argument("-d", "--dossier", help="dossier de téléchargement")
    p.add_argument("-f", "--fichier", help="fichier texte de liens à importer")
    p.add_argument("--proxy", help="proxy http:// ou socks5://")
    p.add_argument("--simultanes", type=int, help="nombre de téléchargements simultanés")
    p.add_argument("--limite", type=int, help="limite globale en Ko/s")
    args = p.parse_args()

    cfg = Config.charger()
    if args.dossier:
        cfg.dossier = chemin_absolu(args.dossier)
    if args.proxy:
        cfg.proxy = args.proxy
    if args.simultanes:
        cfg.simultanes = max(1, args.simultanes)
    if args.limite is not None:
        cfg.limite_ko = max(0, args.limite)

    texte = "\n".join(args.sources)
    if args.fichier:
        try:
            texte += "\n" + Path(args.fichier).expanduser().read_text("utf-8", errors="replace")
        except OSError as e:
            sys.exit(f"Lecture impossible : {e}")

    FdmApp(cfg, Gestionnaire(cfg), texte).run()


if __name__ == "__main__":
    main()
