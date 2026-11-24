"""Torrent backend, based on libtorrent (optional dependency)."""

from __future__ import annotations

import asyncio
import time

from ..models import Download, FatalDownloadError
from .base import BackendBase

try:
    import libtorrent as lt
except ImportError:
    lt = None


class TorrentBackend(BackendBase):
    _torrent_session = None  # created on first use

    def _get_torrent_session(self):
        if lt is None:
            raise FatalDownloadError("libtorrent is not installed (python3-libtorrent)")
        if self._torrent_session is None:
            self._torrent_session = lt.session({"listen_interfaces": "0.0.0.0:6881,[::]:6881"})
            if self.cfg.speed_limit_kb:
                self._torrent_session.apply_settings(
                    {"download_rate_limit": self.cfg.speed_limit_kb * 1024})
        return self._torrent_session

    async def _run_torrent(self, d: Download) -> None:
        session = self._get_torrent_session()
        d.category = "Torrents"
        folder = self._folder_for(d, "Torrents")
        d.folder = str(folder)
        try:
            if d.url.startswith("magnet:"):
                params = lt.parse_magnet_uri(d.url)
            else:
                params = lt.add_torrent_params()
                params.ti = lt.torrent_info(d.url)
            params.save_path = str(folder)
            handle = session.add_torrent(params)
        except Exception as e:  # noqa: BLE001
            raise FatalDownloadError(f"Invalid torrent: {e}") from e
        handle.unset_flags(lt.torrent_flags.auto_managed)  # we manage pause/resume ourselves
        d.handle = handle
        try:
            while True:
                status = handle.status()
                if status.errc.value():
                    raise FatalDownloadError(status.errc.message())
                if status.name:
                    d.name = status.name
                d.size = status.total_wanted if status.has_metadata else 0
                d.done = status.total_wanted_done
                d.info = f"{status.num_peers} peers" if status.has_metadata else "Metadata…"
                if status.has_metadata and status.is_finished:
                    break
                await asyncio.sleep(1)
            d.path = str(folder / status.name)
            d.info, d.status, d.finished_at = "", "completed", time.time()
            self.notify(f"✓ Completed: {d.name}")
        finally:
            # Pausing, removing or finishing all end up here: take the torrent out of the session
            d.handle = None
            try:
                if handle.is_valid():
                    if d.delete_files:
                        session.remove_torrent(handle, lt.session.delete_files)
                    else:
                        session.remove_torrent(handle)
            except Exception:  # noqa: BLE001
                pass
