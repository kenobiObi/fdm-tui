"""Parsing of user input: link patterns, option lines, checksums and web pages."""

from __future__ import annotations

import argparse
import re
import shlex
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urldefrag, urljoin, urlparse

from .constants import ALL_EXTENSIONS, VIDEO_SITES


def expand_pattern(url: str) -> list[str]:
    """Batch downloads: 'http://site/img[001-050].jpg' -> 50 links."""
    match = re.search(r"\[(\d+)-(\d+)\]", url)
    if not match:
        return [url]
    first, last = match.group(1), match.group(2)
    width = len(first) if first.startswith("0") else 0  # keep zero padding
    low, high = int(first), int(last)
    if high < low or high - low > 9999:
        return [url]
    urls: list[str] = []
    for i in range(low, high + 1):
        urls += expand_pattern(url[: match.start()] + str(i).zfill(width) + url[match.end():])
    return urls


def parse_checksum(text: str) -> tuple[str, str]:
    """Parse 'sha256:HEX' (or a bare hex digest, algorithm inferred from its length)."""
    text = text.strip().lower()
    if ":" in text:
        algorithm, digest = text.split(":", 1)
    else:
        algorithm = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}.get(len(text), "")
        digest = text
    if algorithm not in ("md5", "sha1", "sha224", "sha256", "sha384", "sha512"):
        raise ValueError("unknown checksum algorithm (md5, sha1, sha256, sha512)")
    try:
        int(digest, 16)
    except ValueError:
        raise ValueError("invalid checksum (hexadecimal expected)") from None
    return algorithm, digest


def is_video_site(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == site or host.endswith("." + site) for site in VIDEO_SITES)


def detect_kind(url: str, video: bool = False) -> str:
    """Return the download kind: 'torrent', 'video' or 'http'."""
    if url.startswith("magnet:"):
        return "torrent"
    if url.lower().endswith(".torrent") and "://" not in url:  # local .torrent file
        return "torrent"
    if video or is_video_site(url):
        return "video"
    return "http"


class LinkExtractor(HTMLParser):
    """Collects the links and media sources found in an HTML page."""

    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("a", "link"):
            keys = ("href",)
        elif tag in ("img", "source", "video", "audio", "embed"):
            keys = ("src", "data-src")
        else:
            return
        for key in keys:
            if attributes.get(key):
                self.links.append(attributes[key])


def filter_links(base: str, links: list[str], extensions: set[str]) -> list[str]:
    """Resolve, de-duplicate and keep only the links to files with the wanted extensions."""
    seen: set[str] = set()
    result: list[str] = []
    for link in links:
        link = link.strip()
        if not link or link.startswith(("javascript:", "mailto:", "#", "data:")):
            continue
        url, _ = urldefrag(urljoin(base, link))
        if not url.startswith(("http://", "https://")):
            continue
        ext = Path(unquote(urlparse(url).path)).suffix.lower().lstrip(".")
        if not ext or ext not in (extensions or ALL_EXTENSIONS):
            continue
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


class _LineParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parse_line(line: str) -> tuple[list[str], dict]:
    """Parse one input line: URLs followed by options (-o name, -r referer, ...).

    Returns the URLs and the keyword arguments for DownloadManager.add().
    """
    try:
        tokens = shlex.split(line)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        tokens = line.split()
    parser = _LineParser(add_help=False, prog="")
    parser.add_argument("urls", nargs="*")
    parser.add_argument("-o", "--name")
    parser.add_argument("-d", "--dir")
    parser.add_argument("-r", "--referer")
    parser.add_argument("-c", "--cookie")
    parser.add_argument("-H", "--header", action="append", default=[])
    parser.add_argument("-n", "--segments", type=int)
    parser.add_argument("-m", "--mirror", action="append", default=[])
    parser.add_argument("--checksum")
    parser.add_argument("--limit", type=int)
    parser.add_argument("-q", "--quality")
    parser.add_argument("-v", "--video", action="store_true")
    args = parser.parse_intermixed_args(tokens)
    if not args.urls:
        raise ValueError("no link")
    headers: dict[str, str] = {}
    if args.referer:
        headers["Referer"] = args.referer
    if args.cookie:
        headers["Cookie"] = args.cookie
    for header in args.header:
        if ":" not in header:
            raise ValueError(f"invalid header: {header}")
        key, value = header.split(":", 1)
        headers[key.strip()] = value.strip()
    options = dict(
        name=args.name or "",
        custom_dir=args.dir or "",
        headers=headers,
        max_segments=max(1, min(32, args.segments)) if args.segments else 0,
        mirrors=list(args.mirror),
        checksum=args.checksum or "",
        speed_limit_kb=max(0, args.limit or 0),
        quality=args.quality or "",
        video=args.video,
    )
    return list(args.urls), options
