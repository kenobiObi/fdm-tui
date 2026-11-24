"""Migration of data saved by the earlier French-named versions.

Older versions stored their configuration and download list with French keys.
These helpers convert them to the current English keys so nothing is lost.
"""

LEGACY_CONFIG_KEYS = {
    "dossier": "download_dir",
    "simultanes": "max_concurrent",
    "segments": "default_segments",
    "limite_ko": "speed_limit_kb",
    "tentatives": "max_retries",
    "tri_auto": "auto_sort",
    "plage": "time_window",
    "action_fin": "end_action",
    "presse_papiers": "clipboard_monitor",
}

LEGACY_END_ACTIONS = {"aucune": "none", "quitter": "quit", "veille": "sleep", "eteindre": "shutdown"}

LEGACY_DOWNLOAD_KEYS = {
    "nom": "name",
    "dossier": "folder",
    "chemin": "path",
    "categorie": "category",
    "statut": "status",
    "taille": "size",
    "fait": "done",
    "ranges": "supports_ranges",
    "sonde": "probed",
    "erreur": "error",
    "ajoute": "added_at",
    "termine": "finished_at",
    "entetes": "headers",
    "miroirs": "mirrors",
    "verif": "verification",
    "limite_ko": "speed_limit_kb",
    "qualite": "quality",
    "dossier_perso": "custom_dir",
}

LEGACY_CATEGORIES = {"Vidéos": "Videos", "Musique": "Music", "Programmes": "Programs", "Autres": "Other"}


def migrate_config(data: dict) -> dict:
    """Return a configuration dict using the current key names."""
    migrated = {LEGACY_CONFIG_KEYS.get(key, key): value for key, value in data.items()}
    if migrated.get("end_action") in LEGACY_END_ACTIONS:
        migrated["end_action"] = LEGACY_END_ACTIONS[migrated["end_action"]]
    return migrated


def migrate_download(data: dict) -> dict:
    """Return a saved download dict using the current key names."""
    migrated = {LEGACY_DOWNLOAD_KEYS.get(key, key): value for key, value in data.items()}
    if migrated.get("category") in LEGACY_CATEGORIES:
        migrated["category"] = LEGACY_CATEGORIES[migrated["category"]]
    return migrated


def migrate_state(data: dict) -> dict:
    """Return the top-level state dict using the current key names."""
    return {
        "next_id": data.get("next_id", data.get("prochain", 1)),
        "downloads": data.get("downloads", data.get("liste", [])),
    }
