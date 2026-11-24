"""Data model: download segments, downloads and errors."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, fields

from .legacy import migrate_download


class DownloadError(Exception):
    """Temporary error: the operation can be retried."""


class FatalDownloadError(Exception):
    """Permanent error: retrying is pointless."""


def error_message(error: BaseException) -> str:
    return str(error).strip() or error.__class__.__name__


class Segment:
    """Remaining part of a file: bytes [pos, end] still have to be downloaded."""

    __slots__ = ("start", "end", "pos", "active")

    def __init__(self, start: int, end: int, pos: int | None = None):
        self.start = start
        self.end = end
        self.pos = start if pos is None else pos  # next byte to download
        self.active = False  # True while a worker is downloading it

    @property
    def remaining(self) -> int:
        return max(0, self.end - self.pos + 1)


# Fields that only exist while the program runs and are never saved
RUNTIME_FIELDS = {
    "speed", "info", "task", "handle", "cancel_event", "limiter",
    "sample_time", "sample_bytes", "delete_files",
}


@dataclass
class Download:
    id: int
    url: str
    kind: str = "http"  # http | video | torrent
    name: str = ""
    folder: str = ""
    path: str = ""  # final path of the file
    category: str = ""
    status: str = "queued"  # queued | downloading | paused | completed | error
    size: int = 0  # 0 = unknown
    done: int = 0  # bytes downloaded
    supports_ranges: bool = False
    probed: bool = False  # True once the server has been queried
    error: str = ""
    added_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    headers: dict = field(default_factory=dict)
    mirrors: list = field(default_factory=list)
    checksum: str = ""
    verification: str = ""  # "" | ok | ko
    speed_limit_kb: int = 0
    max_segments: int = 0  # 0 = use the configured default
    quality: str = ""
    custom_dir: str = ""
    segments: list = field(default_factory=list)
    # --- runtime only (not saved) ---
    speed: float = field(default=0.0, repr=False)
    info: str = field(default="", repr=False)
    task: object = field(default=None, repr=False)
    handle: object = field(default=None, repr=False)
    cancel_event: object = field(default=None, repr=False)
    limiter: object = field(default=None, repr=False)
    sample_time: object = field(default=None, repr=False)
    sample_bytes: int = field(default=0, repr=False)
    delete_files: bool = field(default=False, repr=False)

    def to_dict(self) -> dict:
        data = {f.name: getattr(self, f.name) for f in fields(self)
                if f.name not in RUNTIME_FIELDS and f.name != "segments"}
        data["segments"] = [[s.start, s.end, s.pos] for s in self.segments]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Download":
        data = migrate_download(data)
        names = {f.name for f in fields(cls)} - RUNTIME_FIELDS - {"segments"}
        download = cls(**{k: v for k, v in data.items() if k in names})
        download.segments = [Segment(a, b, c) for a, b, c in data.get("segments", [])]
        return download
