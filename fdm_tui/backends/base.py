"""Helpers shared by all backends."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..config import Config
from ..limiter import RateLimiter
from ..models import Download, FatalDownloadError
from ..utils import absolute_path


class BackendBase:
    """Attributes and helpers every backend relies on (provided by DownloadManager)."""

    cfg: Config
    downloads: list[Download]
    limiter: RateLimiter
    notify: Callable[..., None]

    def _folder_for(self, d: Download, category: str) -> Path:
        """Return (and create) the destination folder of a download."""
        if d.custom_dir:
            base = Path(absolute_path(d.custom_dir))
        else:
            base = Path(absolute_path(self.cfg.download_dir, Path.home()))
            if self.cfg.auto_sort:
                base = base / category
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise FatalDownloadError(f"Folder not accessible: {e}") from e
        return base

    def _free_path(self, wanted: Path, d: Download) -> Path:
        """Return `wanted`, or 'name (1).ext', 'name (2).ext'... if it is already taken."""
        reserved = {other.path for other in self.downloads if other is not d and other.path}

        def taken(p: Path) -> bool:
            return p.exists() or Path(str(p) + ".fdmpart").exists() or str(p) in reserved

        if not taken(wanted):
            return wanted
        i = 1
        while True:
            candidate = wanted.with_name(f"{wanted.stem} ({i}){wanted.suffix}")
            if not taken(candidate):
                return candidate
            i += 1
