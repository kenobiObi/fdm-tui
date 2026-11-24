"""Download manager: queue, scheduler, persistence and task lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .backends.http import HttpBackend
from .backends.torrent import TorrentBackend
from .backends.video import VideoBackend
from .config import Config
from .constants import LEGACY_STATE_FILE, STATE_FILE
from .legacy import migrate_state
from .limiter import RateLimiter
from .models import Download, error_message
from .parsing import detect_kind, parse_checksum
from .utils import absolute_path, write_json_atomic


class DownloadManager(HttpBackend, VideoBackend, TorrentBackend):
    def __init__(self, cfg: Config, state_file: Path = STATE_FILE):
        self.cfg = cfg
        self.state_file = state_file
        self.downloads: list[Download] = []
        self.next_id = 1
        self.limiter = RateLimiter(cfg.speed_limit_kb * 1024)
        # Callbacks, replaced by the user interface
        self.notify = lambda message, severity="information": None
        self.on_finish = lambda action: None
        self._ticks = 0
        self._armed = False  # True once something was running: arms the end action

    # ---- persistence ------------------------------------------------------

    def save_state(self) -> None:
        try:
            write_json_atomic(self.state_file, {
                "next_id": self.next_id,
                "downloads": [d.to_dict() for d in self.downloads],
            })
        except OSError:
            pass

    def load_state(self) -> None:
        candidates = [self.state_file]
        if self.state_file == STATE_FILE:  # also look for a file written by an older version
            candidates.append(LEGACY_STATE_FILE)
        data = None
        for candidate in candidates:
            try:
                data = json.loads(candidate.read_text("utf-8"))
                break
            except (OSError, ValueError):
                continue
        if not isinstance(data, dict):
            return
        data = migrate_state(data)
        self.next_id = int(data["next_id"])
        self.downloads = []
        for item in data["downloads"]:
            try:
                d = Download.from_dict(item)
            except (TypeError, ValueError):
                continue
            if d.status == "downloading":
                d.status = "queued"  # automatic resume
            if d.kind == "http" and d.status != "completed" and d.path and not os.path.isabs(d.path):
                # Old relative path (depends on the launch folder): compute it again
                d.probed, d.path, d.segments, d.done = False, "", [], 0
            self.downloads.append(d)
            self.next_id = max(self.next_id, d.id + 1)

    # ---- list management --------------------------------------------------

    def get_by_id(self, download_id: int) -> Download | None:
        return next((d for d in self.downloads if d.id == download_id), None)

    def add(self, url: str, *, name="", custom_dir="", headers=None, max_segments=0,
            mirrors=None, checksum="", speed_limit_kb=0, quality="", video=False) -> Download:
        """Add a download to the queue. Raises ValueError if the input is not acceptable."""
        url = url.strip()
        kind = detect_kind(url, video or bool(quality))
        if kind == "torrent" and not url.startswith("magnet:"):
            torrent_file = Path(url).expanduser()
            if not torrent_file.is_file():
                raise ValueError(".torrent file not found")
            url = str(torrent_file)
        elif kind in ("http", "video"):
            if "://" not in url:
                url = "https://" + url
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                raise ValueError("unsupported protocol (http, https, magnet, video)")
            if not parsed.netloc:
                raise ValueError("invalid URL")
        if any(d.url == url and d.status != "completed" for d in self.downloads):
            raise ValueError("already in the list")
        if checksum:
            parse_checksum(checksum)  # raises ValueError if invalid
        d = Download(
            id=self.next_id, url=url, kind=kind, name=name, custom_dir=custom_dir,
            headers=dict(headers or {}), mirrors=list(mirrors or []), checksum=checksum,
            speed_limit_kb=speed_limit_kb, max_segments=max_segments, quality=quality,
        )
        self.next_id += 1
        self.downloads.append(d)
        self.save_state()
        return d

    def pause(self, d: Download) -> None:
        if d.status in ("downloading", "queued"):
            d.status = "paused"
            if d.task:
                d.task.cancel()

    def resume(self, d: Download) -> None:
        if d.status in ("paused", "error"):
            d.status = "queued"
            d.error = ""

    def toggle(self, d: Download) -> None:
        if d.status in ("downloading", "queued"):
            self.pause(d)
        else:
            self.resume(d)

    def restart(self, d: Download) -> None:
        """Discard the progress and download again from the beginning."""
        if d.task:
            d.task.cancel()
        if d.path:
            Path(d.path + ".fdmpart").unlink(missing_ok=True)
        d.segments, d.done, d.error, d.verification = [], 0, "", ""
        if d.kind == "http":
            d.probed, d.path, d.size = False, "", 0
        d.status = "queued"

    def _is_safe_to_delete(self, path: str, d: Download) -> bool:
        """Only allow deleting what is inside a download folder (never the folder itself)."""
        try:
            target = Path(path).resolve()
            roots = [Path(absolute_path(self.cfg.download_dir, Path.home())).resolve()]
            if d.folder:
                roots.append(Path(d.folder).resolve())
            return any(target != root and target.is_relative_to(root) for root in roots)
        except (OSError, ValueError):
            return False

    def remove(self, d: Download, delete_files: bool = False) -> None:
        d.delete_files = delete_files  # read by the torrent backend when it stops
        if d.task:
            d.task.cancel()
        if d in self.downloads:
            self.downloads.remove(d)
        if delete_files and d.path:
            for path in (d.path, d.path + ".fdmpart"):
                if os.path.lexists(path) and self._is_safe_to_delete(path, d):
                    if os.path.isdir(path) and not os.path.islink(path):
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        Path(path).unlink(missing_ok=True)
        self.save_state()

    def clear_completed(self) -> int:
        before = len(self.downloads)
        self.downloads = [d for d in self.downloads if d.status != "completed"]
        self.save_state()
        return before - len(self.downloads)

    def move(self, d: Download, delta: int) -> None:
        """Move a download up (-1) or down (+1) in the queue."""
        i = self.downloads.index(d)
        j = i + delta
        if 0 <= j < len(self.downloads):
            self.downloads[i], self.downloads[j] = self.downloads[j], self.downloads[i]
            self.save_state()

    def set_speed_limit(self, kb_per_second: int) -> None:
        self.cfg.speed_limit_kb = max(0, kb_per_second)
        self.limiter.set_rate(self.cfg.speed_limit_kb * 1024)
        if self._torrent_session is not None:
            self._torrent_session.apply_settings({"download_rate_limit": self.cfg.speed_limit_kb * 1024})

    # ---- scheduling -----------------------------------------------------------

    def in_time_window(self) -> bool:
        """True if downloads are allowed right now (always true without a time window)."""
        match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", self.cfg.time_window.strip())
        if not match:
            return True
        start = int(match[1]) * 60 + int(match[2])
        end = int(match[3]) * 60 + int(match[4])
        if start == end:
            return True
        now = datetime.now()
        minutes = now.hour * 60 + now.minute
        if start < end:
            return start <= minutes < end
        return minutes >= start or minutes < end  # window crossing midnight

    def schedule(self) -> None:
        """Start queued downloads while there are free slots."""
        if not self.in_time_window():
            return
        free_slots = self.cfg.max_concurrent - sum(1 for d in self.downloads if d.status == "downloading")
        for d in self.downloads:
            if free_slots <= 0:
                break
            if d.status == "queued" and (d.task is None or d.task.done()):
                self._start(d)
                free_slots -= 1

    def _start(self, d: Download) -> None:
        d.status, d.error, d.info, d.speed, d.sample_time = "downloading", "", "", 0.0, None
        d.task = asyncio.get_running_loop().create_task(self._run(d))

    def tick(self) -> None:
        """Called every second: measure speeds, enforce the time window, start downloads."""
        now = time.monotonic()
        for d in self.downloads:
            if d.status == "downloading":
                if d.sample_time is None:
                    d.sample_time, d.sample_bytes = now, d.done
                else:
                    elapsed = now - d.sample_time
                    if elapsed > 0:
                        instant = max(0.0, (d.done - d.sample_bytes) / elapsed)
                        # Smooth the speed so the display does not jump around
                        d.speed = instant if d.speed == 0 else 0.5 * d.speed + 0.5 * instant
                        d.sample_time, d.sample_bytes = now, d.done
            else:
                d.speed, d.sample_time = 0.0, None
        if not self.in_time_window():  # outside the allowed hours: put running downloads back in the queue
            for d in self.downloads:
                if d.status == "downloading":
                    d.status = "queued"
                    if d.task:
                        d.task.cancel()
        self.schedule()
        self._ticks += 1
        if self._ticks % 3 == 0:
            self.save_state()
        pending = any(d.status in ("downloading", "queued") for d in self.downloads)
        if pending:
            self._armed = True
        elif self._armed:  # everything just finished
            self._armed = False
            if self.cfg.end_action != "none":
                self.on_finish(self.cfg.end_action)

    # ---- execution ------------------------------------------------------------

    async def _run(self, d: Download) -> None:
        """Run one download to completion, whatever its kind."""
        try:
            if d.kind == "torrent":
                await self._run_torrent(d)
            elif d.kind == "video":
                await self._run_video(d)
            else:
                await self._run_http(d)
        except Exception as e:  # noqa: BLE001
            d.status, d.error = "error", error_message(e)
            self.notify(f"Failed: {d.name or d.url} — {d.error}", "error")
        finally:
            if d.task is asyncio.current_task():
                d.task = None
            d.speed = 0.0
            self.save_state()
