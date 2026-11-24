"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .manager import DownloadManager
from .ui.app import FdmApp
from .utils import absolute_path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fdm-tui", description="Terminal download manager (inspired by Free Download Manager)")
    parser.add_argument("sources", nargs="*",
                        help="links to add (each argument is one line, options allowed)")
    parser.add_argument("-d", "--dir", help="download folder")
    parser.add_argument("-f", "--file", help="text file of links to import")
    parser.add_argument("--proxy", help="http:// or socks5:// proxy")
    parser.add_argument("--concurrent", type=int, help="number of simultaneous downloads")
    parser.add_argument("--limit", type=int, help="global speed limit in KB/s")
    args = parser.parse_args()

    cfg = Config.load()
    if args.dir:
        cfg.download_dir = absolute_path(args.dir)  # relative to the current shell folder
    if args.proxy:
        cfg.proxy = args.proxy
    if args.concurrent:
        cfg.max_concurrent = max(1, args.concurrent)
    if args.limit is not None:
        cfg.speed_limit_kb = max(0, args.limit)

    text = "\n".join(args.sources)
    if args.file:
        try:
            text += "\n" + Path(args.file).expanduser().read_text("utf-8", errors="replace")
        except OSError as e:
            sys.exit(f"Cannot read the file: {e}")

    FdmApp(cfg, DownloadManager(cfg), text).run()
