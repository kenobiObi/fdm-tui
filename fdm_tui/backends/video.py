"""Video backend, based on yt-dlp (optional dependency)."""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
import time
from pathlib import Path

from ..models import Download, FatalDownloadError
from .base import BackendBase

try:
    import yt_dlp
except ImportError:
    yt_dlp = None


class VideoBackend(BackendBase):
    @staticmethod
    def _video_format(quality: str) -> str:
        """Build the yt-dlp format selector for a quality such as '720', 'best' or 'audio'."""
        has_ffmpeg = shutil.which("ffmpeg") is not None  # needed to merge video + audio streams
        if quality == "audio":
            return "bestaudio/best"
        if not quality.isdigit():
            return "bestvideo+bestaudio/best" if has_ffmpeg else "best"
        height = int(quality)
        if has_ffmpeg:
            return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
        return f"best[height<={height}]/best"

    async def _run_video(self, d: Download) -> None:
        if yt_dlp is None:
            raise FatalDownloadError("yt-dlp is not installed (pip install yt-dlp)")
        category = "Music" if d.quality == "audio" else "Videos"
        folder = self._folder_for(d, category)
        d.folder, d.category = str(folder), category
        cancel_event = threading.Event()
        d.cancel_event = cancel_event
        cancelled_error = getattr(yt_dlp.utils, "DownloadCancelled", Exception)

        def progress_hook(info: dict) -> None:
            if cancel_event.is_set():
                raise cancelled_error()  # stops the yt-dlp thread
            title = (info.get("info_dict") or {}).get("title")
            if title:
                d.name = title
            if info.get("status") == "downloading":
                d.done = info.get("downloaded_bytes") or 0
                d.size = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
                d.info = ""
            elif info.get("status") == "finished":
                d.info = "Finalizing…"

        def work() -> str:
            options = {
                "format": self._video_format(d.quality),
                "outtmpl": str(folder / "%(title).150B [%(id)s].%(ext)s"),
                "noplaylist": True, "quiet": True, "no_warnings": True, "noprogress": True,
                "continuedl": True, "progress_hooks": [progress_hook],
            }
            if self.cfg.proxy:
                options["proxy"] = self.cfg.proxy
            if self.cfg.speed_limit_kb:
                options["ratelimit"] = self.cfg.speed_limit_kb * 1024
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(d.url, download=True)
                downloads = info.get("requested_downloads") or []
                return downloads[0]["filepath"] if downloads else ydl.prepare_filename(info)

        try:
            path = await asyncio.to_thread(work)
        except asyncio.CancelledError:
            cancel_event.set()
            raise
        d.path, d.name = path, Path(path).name
        d.folder = str(Path(path).parent)
        d.done = d.size = os.path.getsize(path)
        d.info, d.status, d.finished_at = "", "completed", time.time()
        self.notify(f"✓ Completed: {d.name}")
