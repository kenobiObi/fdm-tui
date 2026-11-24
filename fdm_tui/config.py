"""User configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path

from .constants import CONFIG_FILE
from .legacy import migrate_config
from .utils import absolute_path, write_json_atomic


@dataclass
class Config:
    download_dir: str = field(default_factory=lambda: str(Path.home() / "Downloads"))
    max_concurrent: int = 3  # simultaneous downloads
    default_segments: int = 8  # connections per file
    speed_limit_kb: int = 0  # global limit in KB/s, 0 = unlimited
    max_retries: int = 5
    auto_sort: bool = True  # sort files into category sub-folders
    proxy: str = ""
    user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) FDM-TUI/1.0"
    time_window: str = ""  # allowed hours, e.g. "23:00-07:00" (empty = always)
    end_action: str = "none"  # none | quit | sleep | shutdown
    clipboard_monitor: bool = False

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> "Config":
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            data = {}
        data = migrate_config(data) if isinstance(data, dict) else {}
        names = {f.name for f in fields(cls)}
        config = cls(**{k: v for k, v in data.items() if k in names})
        # A relative folder would depend on where the program was launched from
        config.download_dir = absolute_path(config.download_dir, Path.home())
        return config

    def save(self, path: Path = CONFIG_FILE) -> None:
        try:
            write_json_atomic(path, {f.name: getattr(self, f.name) for f in fields(self)})
        except OSError:
            pass
