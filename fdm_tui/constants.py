"""Constants shared across the application."""

import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "fdm_tui"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_FILE = CONFIG_DIR / "state.json"
LEGACY_STATE_FILE = CONFIG_DIR / "etat.json"  # file name used by versions written in French

MIN_SEGMENT = 1 << 20  # 1 MiB: minimum size of a download segment
CHUNK_SIZE = 64 * 1024  # bytes read from the network at a time
UNITS = ["B", "KB", "MB", "GB", "TB"]

CATEGORIES = {
    "Videos": {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg", "3gp"},
    "Music": {"mp3", "flac", "wav", "aac", "ogg", "m4a", "wma", "opus"},
    "Images": {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "tiff", "ico"},
    "Documents": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "epub", "odt", "ods", "csv", "md"},
    "Archives": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "zst", "tgz", "iso"},
    "Programs": {"exe", "msi", "deb", "rpm", "appimage", "apk", "dmg", "sh", "run", "flatpak"},
    "Torrents": {"torrent"},
}
DEFAULT_CATEGORY = "Other"
ALL_EXTENSIONS = set().union(*CATEGORIES.values())

# Sites handled by yt-dlp
VIDEO_SITES = (
    "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "twitch.tv",
    "tiktok.com", "soundcloud.com", "bilibili.com", "twitter.com", "x.com",
)

END_ACTIONS = ["none", "quit", "sleep", "shutdown"]
END_ACTION_LABELS = {"none": "nothing", "quit": "quit", "sleep": "sleep", "shutdown": "shutdown"}
