"""Texts and text builders used by the interface."""

from __future__ import annotations

from datetime import datetime

from ..models import Download
from ..utils import format_eta, format_size, format_speed

ADD_HELP = (
    "One link per line: http(s)://…  magnet:?…  local .torrent file  video (YouTube…)\n"
    "Batches: http://site/img[001-050].jpg\n"
    "Options: -o name  -d dir  -r referer  -c \"cookie\"  -H \"Key: value\"  -n segments\n"
    "         -m mirror  --checksum sha256:…  --limit KB/s  -v (video)  -q 720|best|audio"
)

HELP_TEXT = """\
a  Add links (multi-line, with options)          i / Enter  Download details
g  Grab the links of a web page                  m  Import a list     x  Export the list
p  Pause / Resume                                P  Pause all / resume all
r  Restart                                       [ / ]  Move up / down in the queue
d  Remove from the list                          D  Remove + delete the files
C  Clear completed downloads                     t  Filter (all, active, queued, ...)
l  Global speed limit                            s  Settings (folder, proxy, time window...)
e  End action (quit / sleep / shutdown)          c  Monitor the clipboard
o  Open the file                                 f  Open the folder
h  This help                                     q  Quit (downloads resume at the next launch)
"""


def status_text(d: Download) -> str:
    """Short status shown in the table."""
    if d.status == "downloading":
        return d.info or "Downloading"
    if d.status == "queued":
        return "Queued"
    if d.status == "paused":
        return "Paused"
    if d.status == "completed":
        suffix = " ✓" if d.verification == "ok" else " ⚠ checksum" if d.verification == "ko" else ""
        return "Completed" + suffix
    return "Error: " + d.error


def segment_map(d: Download, width: int = 60) -> str:
    """Draw which parts of the file are already downloaded (█ done, ▒ partial, ░ missing)."""
    if not d.size:
        return ""
    if d.status == "completed":
        return "█" * width
    if not d.segments:  # no segments (single connection): simple proportional bar
        filled = int(min(1.0, d.done / d.size) * width)
        return "█" * filled + "░" * (width - filled)
    missing = [(s.pos, s.end) for s in d.segments if s.pos <= s.end]
    cells = []
    for i in range(width):
        first = d.size * i // width
        last = d.size * (i + 1) // width - 1
        if last < first:
            cells.append("█")
            continue
        missing_bytes = sum(max(0, min(last, end) - max(first, pos) + 1) for pos, end in missing)
        if missing_bytes == 0:
            cells.append("█")
        elif missing_bytes == last - first + 1:
            cells.append("░")
        else:
            cells.append("▒")
    return "".join(cells)


def details_text(d: Download) -> str:
    """Multi-line description of a download, for the details screen."""
    eta = ""
    if d.status == "downloading" and d.speed > 1 and d.size:
        eta = format_eta((d.size - d.done) / d.speed)
    active_connections = sum(1 for s in d.segments if s.active)
    lines = [
        f"Name        : {d.name or '(waiting)'}",
        f"URL         : {d.url}",
        f"Type        : {d.kind}",
        f"File        : {d.path or '—'}",
        f"Category    : {d.category or '—'}",
        f"Size        : {format_size(d.size) if d.size else 'unknown'}",
        f"Downloaded  : {format_size(d.done)}" + (f" ({d.done * 100 / d.size:.1f} %)" if d.size else ""),
        f"Speed       : {format_speed(d.speed)}   ETA: {eta or '—'}",
        f"Status      : {status_text(d)}",
        f"Resume      : {'yes' if d.supports_ranges else 'no / unknown'}   "
        f"Active connections: {active_connections}",
        f"Added on    : {datetime.fromtimestamp(d.added_at):%Y-%m-%d %H:%M:%S}",
    ]
    if d.finished_at:
        lines.append(f"Finished on : {datetime.fromtimestamp(d.finished_at):%Y-%m-%d %H:%M:%S}")
    if d.checksum:
        lines.append(f"Checksum    : {d.checksum} → {d.verification or 'not verified'}")
    if d.mirrors:
        lines.append("Mirrors     : " + ", ".join(d.mirrors))
    if d.speed_limit_kb:
        lines.append(f"Limit       : {d.speed_limit_kb} KB/s")
    segment_bar = segment_map(d)
    if segment_bar:
        lines += ["", "Segment map:", segment_bar]
    return "\n".join(lines)
