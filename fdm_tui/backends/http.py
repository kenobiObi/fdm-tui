"""HTTP/HTTPS backend: multi-connection downloads with resume support."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path

import httpx

from ..constants import CHUNK_SIZE, MIN_SEGMENT
from ..limiter import RateLimiter
from ..models import Download, DownloadError, FatalDownloadError, Segment, error_message
from ..parsing import parse_checksum
from ..utils import (
    filename_from_headers, get_category, guess_filename, hash_file, sanitize_filename,
)
from .base import BackendBase


class HttpBackend(BackendBase):
    def _make_client(self, headers: dict | None = None) -> httpx.AsyncClient:
        merged = {"User-Agent": self.cfg.user_agent}
        merged.update(headers or {})
        kwargs: dict = dict(headers=merged, follow_redirects=True,
                            timeout=httpx.Timeout(30.0, connect=15.0))
        if self.cfg.proxy:
            kwargs["proxy"] = self.cfg.proxy
        return httpx.AsyncClient(**kwargs)

    @staticmethod
    def _check_status(response: httpx.Response, allow_416: bool = False) -> None:
        code = response.status_code
        if code in (408, 429) or code >= 500:
            raise DownloadError(f"HTTP {code}")  # temporary: retry
        if code >= 400 and not (code == 416 and allow_416):
            raise FatalDownloadError(f"HTTP {code} {response.reason_phrase}")

    async def _with_retries(self, attempt, progress=None):
        """Run `attempt(failures)` again after temporary errors, with increasing delays.

        `progress` (optional) returns a number that grows when the attempt made progress;
        the failure counter is reset whenever it does.
        """
        failures = 0
        while True:
            before = progress() if progress else 0
            try:
                return await attempt(failures)
            except FatalDownloadError:
                raise
            except (httpx.HTTPError, httpx.StreamError, DownloadError) as e:
                if isinstance(e, httpx.UnsupportedProtocol):
                    raise FatalDownloadError(error_message(e)) from e
                if progress is not None and progress() > before:
                    failures = 0
                failures += 1
                if failures > self.cfg.max_retries:
                    raise DownloadError(error_message(e)) from e
                await asyncio.sleep(min(2 ** failures, 30))

    async def _throttle(self, d: Download, n: int) -> None:
        """Apply the global and the per-download speed limits."""
        await self.limiter.acquire(n)
        if d.speed_limit_kb > 0:
            rate = d.speed_limit_kb * 1024
            if d.limiter is None:
                d.limiter = RateLimiter(rate)
            elif d.limiter.rate != rate:
                d.limiter.set_rate(rate)
            await d.limiter.acquire(n)

    async def _run_http(self, d: Download) -> None:
        async with self._make_client(d.headers) as client:
            if not d.probed:
                await self._with_retries(lambda failures: self._probe(client, d))
            part = Path(d.path + ".fdmpart")
            if d.supports_ranges and d.size:
                await self._download_segmented(client, d, part)
            else:
                await self._download_simple(client, d, part)
        await self._finalize(d, part)

    async def _probe(self, client: httpx.AsyncClient, d: Download) -> None:
        """Ask the server for the size, resume support and file name."""
        headers = {"Range": "bytes=0-0", "Accept-Encoding": "identity"}
        async with client.stream("GET", d.url, headers=headers) as response:
            self._check_status(response, allow_416=True)
            size, supports_ranges = 0, False
            if response.status_code == 206:
                match = re.match(r"\s*bytes\s+\d+-\d+/(\d+)", response.headers.get("content-range", ""))
                if match:
                    size = int(match.group(1))
                    supports_ranges = size > 0
            elif response.status_code == 200:  # the server ignored the Range header
                length = response.headers.get("content-length", "")
                size = int(length) if length.isdigit() else 0
            content_disposition = response.headers.get("content-disposition")
            final_url = str(response.url)
        d.size, d.supports_ranges = size, supports_ranges
        if not d.path:
            name = sanitize_filename(
                d.name or filename_from_headers(content_disposition) or guess_filename(d.url, final_url))
            d.category = get_category(name)
            folder = self._folder_for(d, d.category)
            free = self._free_path(folder / name, d)
            d.folder, d.path, d.name = str(folder), str(free), free.name
        d.probed = True

    async def _download_segmented(self, client, d: Download, part: Path) -> None:
        if not part.exists() or part.stat().st_size != d.size or not d.segments:
            # Fresh start: allocate the file and begin with a single segment
            try:
                part.parent.mkdir(parents=True, exist_ok=True)
                free_space = shutil.disk_usage(part.parent).free
            except OSError as e:
                raise FatalDownloadError(f"Folder not accessible: {e}") from e
            if free_space < d.size:
                raise FatalDownloadError("Not enough disk space")
            try:
                with open(part, "wb") as f:
                    f.truncate(d.size)
            except OSError as e:
                raise FatalDownloadError(f"Cannot write file: {e}") from e
            d.segments = [Segment(0, d.size - 1, 0)]
            d.done = 0
        else:
            d.done = d.size - sum(s.remaining for s in d.segments)
        for segment in d.segments:
            segment.active = False
        workers = [asyncio.create_task(self._worker(client, d, part))
                   for _ in range(d.max_segments or self.cfg.default_segments)]
        try:
            await asyncio.gather(*workers)
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        if any(s.remaining for s in d.segments):
            raise DownloadError("download incomplete")

    @staticmethod
    def _take_segment(d: Download) -> Segment | None:
        """Take an unassigned segment or, failing that, split the biggest one in two."""
        for segment in d.segments:
            if segment.remaining and not segment.active:
                segment.active = True
                return segment
        victim = max((s for s in d.segments if s.active), key=lambda s: s.remaining, default=None)
        if victim is None or victim.remaining < 2 * MIN_SEGMENT:
            return None
        middle = victim.pos + victim.remaining // 2
        new_segment = Segment(middle, victim.end, middle)
        new_segment.active = True
        victim.end = middle - 1
        d.segments.append(new_segment)
        return new_segment

    async def _worker(self, client, d: Download, part: Path) -> None:
        """One connection: keeps taking segments until there is nothing left to download."""
        while True:
            segment = self._take_segment(d)
            if segment is None:
                return
            try:
                await self._with_retries(
                    lambda failures: self._download_segment(client, d, segment, part, failures),
                    progress=lambda: segment.pos)
            finally:
                segment.active = False

    async def _download_segment(self, client, d: Download, segment: Segment, part: Path, failures: int) -> None:
        if segment.pos > segment.end:
            return
        urls = [d.url] + d.mirrors  # after a failure, try the next mirror
        await self._stream_range(client, d, segment, part, urls[failures % len(urls)])
        if segment.pos <= segment.end:
            raise DownloadError("connection interrupted")

    async def _stream_range(self, client, d: Download, segment: Segment, part: Path, url: str) -> None:
        headers = {"Range": f"bytes={segment.pos}-{segment.end}", "Accept-Encoding": "identity"}
        async with client.stream("GET", url, headers=headers) as response:
            self._check_status(response)
            if response.status_code == 206:
                match = re.match(r"\s*bytes\s+(\d+)-\d+/(\d+|\*)", response.headers.get("content-range", ""))
                if not match or int(match.group(1)) != segment.pos:
                    raise DownloadError("unexpected Content-Range")
                if match.group(2) != "*" and int(match.group(2)) != d.size:
                    raise DownloadError("the remote file has changed (different size)")
            elif not (response.status_code == 200 and segment.pos == 0):
                raise DownloadError(f"the server no longer supports resuming (HTTP {response.status_code})")
            try:
                f = open(part, "r+b")
            except OSError as e:
                raise FatalDownloadError(f"Cannot write file: {e}") from e
            with f:
                f.seek(segment.pos)
                async for block in response.aiter_raw(CHUNK_SIZE):
                    await self._throttle(d, len(block))
                    # The segment may have been shortened by another worker while we waited
                    remaining = segment.end - segment.pos + 1
                    if remaining <= 0:
                        break
                    if len(block) > remaining:
                        block = block[:remaining]
                    try:
                        f.write(block)
                        f.flush()
                    except OSError as e:
                        raise FatalDownloadError(f"Disk write error: {e}") from e
                    segment.pos += len(block)
                    d.done += len(block)
                    if segment.pos > segment.end:
                        break

    async def _download_simple(self, client, d: Download, part: Path) -> None:
        """Single-connection download, for servers without resume support."""
        async def attempt(failures: int) -> None:
            d.done = 0
            urls = [d.url] + d.mirrors
            headers = {"Accept-Encoding": "identity"}
            async with client.stream("GET", urls[failures % len(urls)], headers=headers) as response:
                self._check_status(response)
                try:
                    f = open(part, "wb")
                except OSError as e:
                    raise FatalDownloadError(f"Cannot write file: {e}") from e
                with f:
                    async for block in response.aiter_raw(CHUNK_SIZE):
                        await self._throttle(d, len(block))
                        try:
                            f.write(block)
                        except OSError as e:
                            raise FatalDownloadError(f"Disk write error: {e}") from e
                        d.done += len(block)
            if d.size and d.done != d.size:
                raise DownloadError("incomplete transfer")

        d.segments = []
        try:
            part.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise FatalDownloadError(f"Folder not accessible: {e}") from e
        await self._with_retries(attempt)
        if not d.size:
            d.size = d.done

    async def _finalize(self, d: Download, part: Path) -> None:
        """Move the finished file into place and verify its checksum."""
        if d.size and d.done != d.size:
            raise DownloadError("download incomplete")
        try:
            os.replace(part, d.path)
        except OSError as e:
            raise FatalDownloadError(f"Cannot finalize the file: {e}") from e
        d.segments = []
        try:
            d.done = d.size = os.path.getsize(d.path)
        except OSError as e:
            raise FatalDownloadError(f"Final file not found: {e}") from e
        if d.checksum:
            d.info = "Verifying…"
            algorithm, expected = parse_checksum(d.checksum)
            try:
                actual = await asyncio.to_thread(hash_file, d.path, algorithm)
            except OSError as e:
                raise FatalDownloadError(f"Cannot verify the file: {e}") from e
            d.verification = "ok" if actual == expected else "ko"
            d.info = ""
        d.status, d.finished_at = "completed", time.time()
        if d.verification == "ko":
            self.notify(f"⚠ Checksum mismatch: {d.name}", "error")
        else:
            self.notify(f"✓ Completed: {d.name}")
