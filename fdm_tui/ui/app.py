"""Main application screen."""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Static

from ..config import Config
from ..constants import END_ACTION_LABELS, END_ACTIONS
from ..manager import DownloadManager
from ..models import Download, error_message
from ..parsing import LinkExtractor, expand_pattern, filter_links, parse_line
from ..utils import (
    format_eta, format_size, format_speed, open_path, progress_bar, read_clipboard,
)
from .screens import (
    AddScreen, ConfirmScreen, DetailsScreen, HelpScreen, PromptScreen, SettingsScreen,
    escape_markup,
)
from .texts import status_text

# (column key, title)
COLUMNS = [
    ("id", "#"), ("name", "Name"), ("size", "Size"), ("progress", "Progress"),
    ("speed", "Speed"), ("eta", "ETA"), ("status", "Status"), ("category", "Category"),
]

# (label, predicate) - cycled with the "t" key
FILTERS = [
    ("All", lambda d: True),
    ("Active", lambda d: d.status == "downloading"),
    ("Queued", lambda d: d.status == "queued"),
    ("Paused/Errors", lambda d: d.status in ("paused", "error")),
    ("Completed", lambda d: d.status == "completed"),
]


class FdmApp(App):
    TITLE = "FDM-TUI"

    CSS = """
    #toolbar { height: 3; padding: 0 1; }
    #toolbar Button { margin-right: 1; }
    #table { height: 1fr; }
    #status-bar { height: 1; padding: 0 1; background: $boost; }
    ModalScreen { align: center middle; }
    #dialog { width: 100; max-width: 96%; height: auto; max-height: 92%;
              border: thick $accent; background: $surface; padding: 1 2; }
    .title { text-style: bold; margin-bottom: 1; }
    .help { color: $text-muted; margin-bottom: 1; }
    .buttons { height: 3; align-horizontal: right; margin-top: 1; }
    .buttons Button { margin-left: 1; }
    #text-area { height: 8; }
    #fields { height: auto; max-height: 22; }
    """

    BINDINGS = [
        Binding("a", "add", "Add"),
        Binding("p", "pause", "Pause/Resume"),
        Binding("P", "pause_all", "Pause all", show=False),
        Binding("d", "remove(False)", "Remove"),
        Binding("D", "remove(True)", "Remove+files", show=False),
        Binding("r", "restart", "Restart", show=False),
        Binding("C", "clear", "Clear completed", show=False),
        Binding("left_square_bracket", "move(-1)", "Move up", show=False),
        Binding("right_square_bracket", "move(1)", "Move down", show=False),
        Binding("t", "filter", "Filter"),
        Binding("l", "limit", "Limit"),
        Binding("s", "settings", "Settings"),
        Binding("e", "cycle_end_action", "End action", show=False),
        Binding("c", "toggle_clipboard", "Clipboard", show=False),
        Binding("g", "grab", "Grab page"),
        Binding("m", "import_list", "Import", show=False),
        Binding("x", "export_list", "Export", show=False),
        Binding("i", "details", "Details", show=False),
        Binding("o", "open_file", "Open file", show=False),
        Binding("f", "open_folder", "Open folder", show=False),
        Binding("h", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, cfg: Config, manager: DownloadManager, initial_text: str = ""):
        super().__init__()
        self.cfg = cfg
        self.manager = manager
        self.initial_text = initial_text
        self.filter_index = 0
        self._shown_ids: list[int] = []  # ids of the rows currently in the table
        self._row_cache: dict[int, list[str]] = {}  # last values written, to skip useless updates
        self._last_clipboard: str | None = None
        self._pending_end_action: str | None = None
        self._background_tasks: set = set()
        manager.notify = self.show_notice
        manager.on_finish = self._on_finish

    # ---- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="toolbar"):
            yield Button("+ Add", id="btn-add", variant="success")
            yield Button("Pause / Resume", id="btn-pause", variant="warning")
            yield Button("Remove", id="btn-remove", variant="error")
            yield Button("Settings", id="btn-settings", variant="primary")
        yield DataTable(cursor_type="row", zebra_stripes=True, id="table")
        yield Static("", id="status-bar", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for key, title in COLUMNS:
            table.add_column(title, key=key)
        self.manager.load_state()
        if self.initial_text.strip():
            self.add_text(self.initial_text)
        self._rebuild_table()
        self.set_interval(1.0, self._tick)
        self.set_interval(1.5, self._watch_clipboard)
        table.focus()

    # ---- helpers --------------------------------------------------------------------

    def show_notice(self, message: str, severity: str = "information") -> None:
        """Show a notification (escaped, because file names may contain [brackets])."""
        self.notify(escape_markup(message), severity=severity, timeout=8 if severity == "error" else 5)

    def _is_main_screen(self) -> bool:
        return len(self.screen_stack) == 1  # no dialog open

    def _selected(self) -> Download | None:
        if not self._shown_ids:
            return None
        row = self.query_one(DataTable).cursor_row
        return self.manager.get_by_id(self._shown_ids[row]) if 0 <= row < len(self._shown_ids) else None

    def _visible_downloads(self) -> list[Download]:
        predicate = FILTERS[self.filter_index][1]
        return [d for d in self.manager.downloads if predicate(d)]

    def _row_values(self, d: Download) -> list[str]:
        if d.size:
            progress = progress_bar(min(1.0, d.done / d.size))
        else:
            progress = format_size(d.done) if d.done else "—"
        speed = format_speed(d.speed) if d.status == "downloading" and d.speed > 0 else ""
        eta = ""
        if d.status == "downloading" and d.speed > 1 and d.size:
            eta = format_eta((d.size - d.done) / d.speed)
        name = d.name or d.url
        if len(name) > 60:
            name = name[:57] + "…"
        return [str(d.id), name, format_size(d.size) if d.size else "?", progress, speed, eta,
                status_text(d)[:60], d.category or ""]

    def _rebuild_table(self) -> None:
        table = self.query_one(DataTable)
        selected = self._selected()
        table.clear()
        visible = self._visible_downloads()
        self._row_cache = {}
        for d in visible:
            values = self._row_values(d)
            self._row_cache[d.id] = values
            # Text objects, so that [brackets] in names are not read as markup
            table.add_row(*[Text(v) for v in values], key=str(d.id))
        self._shown_ids = [d.id for d in visible]
        if selected and selected.id in self._shown_ids:
            table.move_cursor(row=self._shown_ids.index(selected.id))

    def _update_table(self) -> None:
        visible = self._visible_downloads()
        if [d.id for d in visible] != self._shown_ids:
            self._rebuild_table()
            return
        table = self.query_one(DataTable)
        for d in visible:
            values = self._row_values(d)
            previous = self._row_cache.get(d.id)
            for i, (key, _) in enumerate(COLUMNS):
                if previous is None or previous[i] != values[i]:
                    table.update_cell(str(d.id), key, Text(values[i]), update_width=True)
            self._row_cache[d.id] = values

    def _update_status_bar(self) -> None:
        manager, cfg = self.manager, self.cfg
        total_speed = sum(d.speed for d in manager.downloads if d.status == "downloading")

        def count(status: str) -> int:
            return sum(1 for d in manager.downloads if d.status == status)

        parts = [
            f"↓ {format_speed(total_speed)}",
            "Limit: " + ("unlimited" if not cfg.speed_limit_kb else f"{cfg.speed_limit_kb} KB/s"),
            f"Active: {count('downloading')}/{cfg.max_concurrent}",
            f"Queue: {count('queued')}",
            f"Filter: {FILTERS[self.filter_index][0]}",
        ]
        if cfg.end_action != "none":
            parts.append(f"At the end: {END_ACTION_LABELS[cfg.end_action]}")
        if cfg.time_window:
            parts.append(f"Window: {cfg.time_window}" + ("" if manager.in_time_window() else " (outside window)"))
        if cfg.clipboard_monitor:
            parts.append("Clipboard: on")
        self.query_one("#status-bar", Static).update(Text("  |  ".join(parts)))

    def _tick(self) -> None:
        self.manager.tick()
        self._update_table()
        self._update_status_bar()

    # ---- adding downloads ----------------------------------------------------------------

    def add_text(self, text: str) -> int:
        """Add the links found in `text` (one per line, with options). Returns how many were added."""
        added = 0
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                urls, options = parse_line(line)
            except ValueError as e:
                self.show_notice(f"Ignored line ({e}): {line[:50]}", "warning")
                continue
            for url in urls:
                for expanded_url in expand_pattern(url):
                    try:
                        self.manager.add(expanded_url, **options)
                        added += 1
                    except ValueError as e:
                        self.show_notice(f"{expanded_url[:60]}: {e}", "warning")
        if added:
            self.show_notice(f"{added} download(s) added")
            self.manager.schedule()
            self._rebuild_table()
        return added

    def action_add(self, prefill: str = "") -> None:
        if not self._is_main_screen():
            return
        self.push_screen(AddScreen(prefill), lambda text: self.add_text(text) if text else None)

    def action_import_list(self) -> None:
        if not self._is_main_screen():
            return

        def on_path(path: str) -> None:
            if not path.strip():
                return
            try:
                text = Path(path.strip()).expanduser().read_text("utf-8", errors="replace")
            except OSError as e:
                self.show_notice(f"Cannot read the file: {e}", "error")
                return
            if not self.add_text(text):
                self.show_notice("No link added", "warning")

        self.push_screen(
            PromptScreen("Import a list", "Path of a text file (one link per line)"), on_path)

    def action_export_list(self) -> None:
        if not self._is_main_screen():
            return

        def on_path(path: str) -> None:
            if not path.strip():
                return
            try:
                text = "\n".join(d.url for d in self.manager.downloads) + "\n"
                Path(path.strip()).expanduser().write_text(text, "utf-8")
                self.show_notice(f"{len(self.manager.downloads)} link(s) exported")
            except OSError as e:
                self.show_notice(f"Cannot write the file: {e}", "error")

        self.push_screen(
            PromptScreen("Export the list", value=str(Path.home() / "fdm_links.txt")), on_path)

    def action_grab(self) -> None:
        if not self._is_main_screen():
            return

        def on_value(value: str) -> None:
            if value.strip():
                self.run_worker(self._grab_page(value.strip()), exclusive=False)

        self.push_screen(PromptScreen(
            "Grab the links of a web page",
            "Page URL, then (optional) the wanted extensions. Example: https://site/page pdf,zip,jpg",
            placeholder="https://…"), on_value)

    async def _grab_page(self, value: str) -> None:
        parts = value.split()
        url = parts[0] if "://" in parts[0] else "https://" + parts[0]
        extensions = ({e.strip().lstrip(".").lower() for e in parts[1].split(",") if e.strip()}
                      if len(parts) > 1 else set())
        try:
            async with self.manager._make_client() as client:
                response = await client.get(url)
                response.raise_for_status()
            extractor = LinkExtractor()
            extractor.feed(response.text)
            found = filter_links(str(response.url), extractor.links, extensions)
        except Exception as e:  # noqa: BLE001
            self.show_notice(f"Grabbing failed: {error_message(e)}", "error")
            return
        if not found:
            self.show_notice("No matching file found on this page", "warning")
            return

        def on_decision(yes: bool) -> None:
            if yes:
                self.add_text("\n".join(found))

        self.push_screen(ConfirmScreen(f"{len(found)} file(s) found. Add them?"), on_decision)

    # ---- actions on the selected download ---------------------------------------------------------

    def action_pause(self) -> None:
        if not self._is_main_screen():
            return
        d = self._selected()
        if d:
            self.manager.toggle(d)
            self.manager.schedule()
            self._update_table()

    def action_pause_all(self) -> None:
        if not self._is_main_screen():
            return
        manager = self.manager
        if any(d.status in ("downloading", "queued") for d in manager.downloads):
            for d in manager.downloads:
                manager.pause(d)
            self.show_notice("Everything is paused")
        else:
            for d in manager.downloads:
                manager.resume(d)
            manager.schedule()
            self.show_notice("All downloads resumed")
        self._update_table()

    def action_remove(self, delete_files: bool = False) -> None:
        if not self._is_main_screen():
            return
        d = self._selected()
        if not d:
            return

        def do_remove(yes: bool) -> None:
            if yes:
                self.manager.remove(d, delete_files)
                self._rebuild_table()

        if delete_files:
            self.push_screen(
                ConfirmScreen(f"Remove AND delete the files of “{d.name or d.url}”?"), do_remove)
        else:
            do_remove(True)

    def action_restart(self) -> None:
        if not self._is_main_screen():
            return
        d = self._selected()
        if d:
            self.manager.restart(d)
            self._update_table()

    def action_clear(self) -> None:
        if not self._is_main_screen():
            return
        count = self.manager.clear_completed()
        self.show_notice(f"{count} completed download(s) removed from the list")
        self._rebuild_table()

    def action_move(self, delta: int) -> None:
        if not self._is_main_screen():
            return
        d = self._selected()
        if d:
            self.manager.move(d, delta)
            self._rebuild_table()
            if d.id in self._shown_ids:
                self.query_one(DataTable).move_cursor(row=self._shown_ids.index(d.id))

    def action_filter(self) -> None:
        if not self._is_main_screen():
            return
        self.filter_index = (self.filter_index + 1) % len(FILTERS)
        self._rebuild_table()
        self._update_status_bar()

    def action_details(self) -> None:
        if not self._is_main_screen():
            return
        d = self._selected()
        if d:
            self.push_screen(DetailsScreen(d))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_details()

    def action_open_file(self) -> None:
        d = self._selected()
        if d and self._is_main_screen():
            if not (d.status == "completed" and d.path and open_path(d.path)):
                self.show_notice("File unavailable (download not finished?)", "warning")

    def action_open_folder(self) -> None:
        d = self._selected()
        if d and self._is_main_screen():
            if not (d.folder and open_path(d.folder)):
                self.show_notice("Folder unavailable", "warning")

    def action_help(self) -> None:
        if self._is_main_screen():
            self.push_screen(HelpScreen())

    # ---- settings, limits, end action ------------------------------------------------------------------

    def action_limit(self) -> None:
        if not self._is_main_screen():
            return

        def on_value(value: str) -> None:
            if not value.strip():
                return
            try:
                kb = int(value.strip())
                if kb < 0:
                    raise ValueError
            except ValueError:
                self.show_notice("Enter a positive integer (KB/s)", "error")
                return
            self.manager.set_speed_limit(kb)
            self.cfg.save()
            self._update_status_bar()

        self.push_screen(PromptScreen("Global speed limit", "In KB/s — 0 = unlimited",
                                      value=str(self.cfg.speed_limit_kb)), on_value)

    def action_settings(self) -> None:
        if not self._is_main_screen():
            return

        def on_result(result: dict) -> None:
            if not result:
                return
            for key, value in result.items():
                setattr(self.cfg, key, value)
            self.manager.set_speed_limit(self.cfg.speed_limit_kb)
            self.cfg.save()
            self._last_clipboard = None
            self._update_status_bar()
            self.show_notice("Settings saved")

        self.push_screen(SettingsScreen(self.cfg), on_result)

    def action_cycle_end_action(self) -> None:
        if not self._is_main_screen():
            return
        self._pending_end_action = None  # also cancels a running shutdown/sleep countdown
        index = (END_ACTIONS.index(self.cfg.end_action) + 1) % len(END_ACTIONS)
        self.cfg.end_action = END_ACTIONS[index]
        self.cfg.save()
        self.show_notice(f"When downloads finish: {END_ACTION_LABELS[self.cfg.end_action]}")
        self._update_status_bar()

    def action_toggle_clipboard(self) -> None:
        if not self._is_main_screen():
            return
        self.cfg.clipboard_monitor = not self.cfg.clipboard_monitor
        self._last_clipboard = None
        self.cfg.save()
        if self.cfg.clipboard_monitor and not any(shutil.which(c) for c in ("wl-paste", "xclip", "xsel")):
            self.show_notice("Install wl-clipboard, xclip or xsel for this feature", "warning")
        self._update_status_bar()

    async def _watch_clipboard(self) -> None:
        """Offer to download any link copied to the clipboard."""
        if not self.cfg.clipboard_monitor or not self._is_main_screen():
            return
        text = (await asyncio.to_thread(read_clipboard)).strip()
        if self._last_clipboard is None:  # first read: only remember the current content
            self._last_clipboard = text
            return
        if text != self._last_clipboard:
            self._last_clipboard = text
            if re.fullmatch(r"(https?://|magnet:)\S+", text):
                self.action_add(text)

    def _on_finish(self, action: str) -> None:
        """Called by the manager when everything has finished."""
        task = asyncio.get_running_loop().create_task(self._run_end_action(action))
        self._background_tasks.add(task)  # keep a reference so the task is not garbage-collected
        task.add_done_callback(self._background_tasks.discard)

    async def _run_end_action(self, action: str) -> None:
        if action == "quit":
            await self.action_quit()
        elif action in ("sleep", "shutdown"):
            self._pending_end_action = action
            label = "Shutdown" if action == "shutdown" else "Sleep"
            self.show_notice(f"Downloads finished: {label} in 30 s (press e to cancel)", "warning")
            await asyncio.sleep(30)
            nothing_running = not any(d.status in ("downloading", "queued") for d in self.manager.downloads)
            if self._pending_end_action == action and nothing_running:
                self.manager.save_state()
                self.cfg.save()
                subprocess.Popen(["systemctl", "poweroff" if action == "shutdown" else "suspend"])
            self._pending_end_action = None

    # ---- buttons and exit ---------------------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "btn-add":
            self.action_add()
        elif button_id == "btn-pause":
            self.action_pause()
        elif button_id == "btn-remove":
            self.action_remove(False)
        elif button_id == "btn-settings":
            self.action_settings()
        if self._is_main_screen():
            self.query_one(DataTable).focus()

    async def action_quit(self) -> None:
        tasks = [d.task for d in self.manager.downloads if d.task]
        for task in tasks:
            task.cancel()
        if tasks:  # wait so that partial files are closed properly
            await asyncio.gather(*tasks, return_exceptions=True)
        self.manager.save_state()
        self.cfg.save()
        self.exit()
