# FDM-TUI

English | [Français](README.fr.md)

A terminal download manager written in Python (asyncio + [Textual](https://textual.textualize.io/)), inspired by Free Download Manager.

## Features

- **Accelerated downloads** with multiple connections (up to 32 segments) and dynamic splitting
- **Pause / resume**, including after a restart (state saved in `~/.config/fdm_tui/`)
- **Download queue** with an adjustable number of simultaneous downloads and reordering
- **Speed limit**, global and per file
- **Automatic retries** with increasing delays, plus backup **mirrors**
- **Automatic sorting by category** (Videos, Music, Images, Documents, Archives, Programs, Torrents)
- **Batch downloads**: `http://site/img[001-050].jpg`
- **Web page grabber** with an extension filter
- **Videos** (YouTube, Vimeo…) via [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- **Torrents** (magnet links and `.torrent` files) via libtorrent
- **Scheduler**: allowed time window, end-of-queue action (quit / sleep / shutdown)
- **Per-link options**: referer, cookies, headers, file name, folder, segments
- **Checksum verification** (MD5, SHA-1, SHA-256, SHA-512)
- HTTP / SOCKS5 proxy, clipboard monitoring, list import / export

## Installation

```bash
git clone https://github.com/kenobiObi/fdm-tui.git && cd fdm-tui
chmod +x install.sh
bash install.sh
```

Optional features:

```bash
bash install.sh --video adds yt-dlp
bash install.sh --torrent adds libtorrent via pip
bash install.sh --all adds both
bash install.sh --system-site-packages allows the venv to see system packages—useful when python3-libtorrent is installed via dnf
bash install.sh --uninstall performs an uninstallation
```


## Usage

```bash
tfdm
tfdm "https://site/files.iso" -d ~/Downloads
tfdm -f liens.txt
```

Press `h` in the application for help.

| Key | Action |
|---|---|
| `a` | Add links (multi-line, with options) |
| `g` | Grab the links from a web page |
| `m` / `x` | Import / export a list |
| `p` / `P` | Pause-resume / pause all |
| `r` | Restart |
| `d` / `D` | Remove / remove and delete the files |
| `C` | Clear completed downloads |
| `[` / `]` | Move up / down in the queue |
| `t` | Change filter |
| `l` | Global speed limit |
| `s` | Settings |
| `e` | End-of-queue action |
| `c` | Monitor the clipboard |
| `i` / Enter | Details and segment map |
| `o` / `f` | Open the file / the folder |
| `q` | Quit |

### Line syntax

```
URL [URL…] [-o name] [-d folder] [-r referer] [-c "cookie"] [-H "Key: value"]
           [-n segments] [-m mirror] [--checksum sha256:…] [--limite KB/s]
           [-v] [-q 720|best|audio]
```

## Tests

```bash
python3 tests/test_moteur.py   # engine against a local HTTP server (~1 min)
python3 tests/test_tui.py      # interface via Textual's test pilot
```

## Limitations

No browser extension, no FTP, no full web page downloads and no media conversion.

## Disclaimer

Only use this tool to download content you have the right to download.

## License

MIT — see [LICENSE](LICENSE).
