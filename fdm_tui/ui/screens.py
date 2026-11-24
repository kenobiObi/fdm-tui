"""Modal screens (dialogs) of the interface."""

from __future__ import annotations

import re
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static, Switch, TextArea

from ..config import Config
from ..models import Download
from ..utils import absolute_path
from .texts import ADD_HELP, HELP_TEXT, details_text

try:
    from textual.markup import escape as escape_markup
except ImportError:  # older Textual versions
    from rich.markup import escape as escape_markup


class AddScreen(ModalScreen[str]):
    """Dialog to type or paste links. Dismisses with the typed text ('' if cancelled)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+s", "submit", "Add", priority=True),
    ]

    def __init__(self, text: str = ""):
        super().__init__()
        self.initial_text = text

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Add downloads", classes="title")
            yield Static(ADD_HELP, markup=False, classes="help")
            yield TextArea(self.initial_text, id="text-area")
            with Horizontal(classes="buttons"):
                yield Button("Add (Ctrl+S)", id="ok", variant="success")
                yield Button("Cancel (Esc)", id="cancel")

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    def action_submit(self) -> None:
        self.dismiss(self.query_one(TextArea).text)

    def action_cancel(self) -> None:
        self.dismiss("")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(self.query_one(TextArea).text if event.button.id == "ok" else "")


class PromptScreen(ModalScreen[str]):
    """Dialog asking for a single line of text."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    def __init__(self, title: str, help_text: str = "", value: str = "", placeholder: str = ""):
        super().__init__()
        self.title_text = title
        self.help_text = help_text
        self.initial_value = value
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.title_text, classes="title")
            if self.help_text:
                yield Static(self.help_text, markup=False, classes="help")
            yield Input(self.initial_value, placeholder=self.placeholder, id="field")
            with Horizontal(classes="buttons"):
                yield Button("OK", id="ok", variant="success")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss("")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(self.query_one(Input).value if event.button.id == "ok" else "")


class ConfirmScreen(ModalScreen[bool]):
    """Yes/No question."""

    BINDINGS = [Binding("escape", "no", "No", priority=True)]

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.question, markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Yes", id="yes", variant="error")
                yield Button("No", id="no")

    def action_no(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "yes")


class SettingsScreen(ModalScreen[dict]):
    """Settings dialog. Dismisses with the new values ({} if cancelled)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+s", "submit", "Save", priority=True),
    ]

    # (Config attribute, label)
    FIELDS = [
        ("download_dir", "Download folder"),
        ("max_concurrent", "Simultaneous downloads (1-20)"),
        ("default_segments", "Connections per file (1-32)"),
        ("speed_limit_kb", "Global speed limit in KB/s (0 = unlimited)"),
        ("max_retries", "Retries on failure (0-50)"),
        ("proxy", "Proxy (http://host:port or socks5://host:port)"),
        ("time_window", "Allowed time window HH:MM-HH:MM (empty = always)"),
        ("user_agent", "User-Agent"),
    ]
    INTEGER_BOUNDS = {
        "max_concurrent": (1, 20),
        "default_segments": (1, 32),
        "speed_limit_kb": (0, 10**9),
        "max_retries": (0, 50),
    }

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Settings", classes="title")
            with VerticalScroll(id="fields"):
                for key, label in self.FIELDS:
                    yield Label(label)
                    yield Input(str(getattr(self.cfg, key)), id=f"s-{key}")
                yield Label("Sort files into category folders")
                yield Switch(value=self.cfg.auto_sort, id="s-auto_sort")
                yield Label("Monitor the clipboard")
                yield Switch(value=self.cfg.clipboard_monitor, id="s-clipboard_monitor")
            with Horizontal(classes="buttons"):
                yield Button("Save (Ctrl+S)", id="ok", variant="success")
                yield Button("Cancel", id="cancel")

    def action_cancel(self) -> None:
        self.dismiss({})

    def action_submit(self) -> None:
        def value(key: str) -> str:
            return self.query_one(f"#s-{key}", Input).value.strip()

        try:
            result: dict = {}
            for key, (low, high) in self.INTEGER_BOUNDS.items():
                number = int(value(key) or 0)
                if not low <= number <= high:
                    raise ValueError(f"{key} must be between {low} and {high}")
                result[key] = number
            if not value("download_dir"):
                raise ValueError("the download folder is required")
            if value("time_window") and not re.fullmatch(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}", value("time_window")):
                raise ValueError("invalid time window (example: 23:00-07:00)")
        except ValueError as e:
            self.app.notify(escape_markup(f"Invalid value: {e}"), severity="error")
            return
        result.update(
            download_dir=absolute_path(value("download_dir"), Path.home()),
            proxy=value("proxy"),
            time_window=value("time_window"),
            user_agent=value("user_agent") or self.cfg.user_agent,
            auto_sort=self.query_one("#s-auto_sort", Switch).value,
            clipboard_monitor=self.query_one("#s-clipboard_monitor", Switch).value,
        )
        self.dismiss(result)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "ok":
            self.action_submit()
        else:
            self.dismiss({})


class DetailsScreen(ModalScreen[None]):
    """Live details of one download, including the segment map."""

    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    def __init__(self, download: Download):
        super().__init__()
        self.download = download

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Download details", classes="title")
            yield Static(Text(details_text(self.download)), id="content", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Close (Esc)", id="close")

    def on_mount(self) -> None:
        self.set_interval(0.5, self._refresh)

    def _refresh(self) -> None:
        self.query_one("#content", Static).update(Text(details_text(self.download)))

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """List of keyboard shortcuts."""

    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Help — keyboard shortcuts", classes="title")
            yield Static(Text(HELP_TEXT), markup=False)
            yield Static(Text(ADD_HELP), markup=False, classes="help")
            with Horizontal(classes="buttons"):
                yield Button("Close", id="close")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)
