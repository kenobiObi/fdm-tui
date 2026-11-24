"""General helpers: formatting, file names, files and system access."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from .constants import CATEGORIES, DEFAULT_CATEGORY, UNITS


def format_size(n: float) -> str:
    """Format a byte count, e.g. 1536 -> '1.5 KB'."""
    n = float(n)
    for unit in UNITS:
        if n < 1024 or unit == UNITS[-1]:
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def format_speed(n: float) -> str:
    return format_size(n) + "/s"


def format_eta(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:  # negative or NaN
        return "∞"
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def progress_bar(fraction: float, width: int = 10) -> str:
    filled = int(fraction * width)
    return f"{'█' * filled}{'░' * (width - filled)} {fraction * 100:5.1f}%"


def get_category(filename: str) -> str:
    """Return the category of a file name, based on its extension."""
    ext = Path(filename).suffix.lower().lstrip(".")
    for category, extensions in CATEGORIES.items():
        if ext in extensions:
            return category
    return DEFAULT_CATEGORY


def sanitize_filename(name: str) -> str:
    """Make a name safe to use as a single path component."""
    name = re.sub(r"[\\/\x00-\x1f]", "_", name).strip()
    if name in ("", ".", ".."):
        name = "download"
    return name[:200]


def filename_from_headers(content_disposition: str | None) -> str | None:
    """Extract the file name from a Content-Disposition header, if any."""
    if not content_disposition:
        return None
    match = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", content_disposition, re.I)
    if match:
        try:
            return unquote(match.group(2).strip().strip('"'), encoding=match.group(1) or "utf-8")
        except LookupError:  # unknown charset
            return unquote(match.group(2).strip().strip('"'))
    match = (re.search(r'filename\s*=\s*"([^"]+)"', content_disposition, re.I)
             or re.search(r"filename\s*=\s*([^;]+)", content_disposition, re.I))
    return match.group(1).strip() if match else None


def guess_filename(url: str, final_url: str = "") -> str:
    """Guess a file name from the URL (and the URL after redirects)."""
    candidates = []
    for candidate_url in (url, final_url):
        if candidate_url:
            name = unquote(Path(urlparse(candidate_url).path).name)
            if name:
                candidates.append(name)
    for name in candidates:  # prefer a name that has an extension
        if Path(name).suffix:
            return name
    return candidates[0] if candidates else "download"


def hash_file(path: str, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, data) -> None:
    """Write JSON through a temporary file so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)


def absolute_path(path: str, base: str | Path | None = None) -> str:
    """Return an absolute path (with ~ expanded). A relative path is anchored on `base`."""
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded) and base is not None:
        expanded = os.path.join(str(base), expanded)
    return os.path.abspath(expanded)


def open_path(path: str) -> bool:
    """Open a file or folder with the system's default application."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(
                ["xdg-open", path],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        return True
    except Exception:  # noqa: BLE001
        return False


def read_clipboard() -> str:
    """Read the clipboard using wl-paste, xclip or xsel (whichever is installed)."""
    commands = (
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "-b", "-o"],
    )
    for command in commands:
        if shutil.which(command[0]):
            try:
                return subprocess.run(command, capture_output=True, text=True, timeout=1).stdout
            except Exception:  # noqa: BLE001
                return ""
    return ""
