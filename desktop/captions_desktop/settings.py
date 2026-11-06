"""Persistent operator settings for the desktop app.

One small JSON file holds what the operator sets on the control page, so the
next launch looks the same:

- ``display``: the DisplayConfig (position, size, colours, background, margins)
- ``dictionary``: custom terms
- ``launch``: engine / model / mic / monitor, used when no CLI flag overrides them

Location: ``$CAPTION_GURU_SETTINGS`` if set, else the per-user config dir
(``%APPDATA%\\CaptionGuru`` on Windows, ``~/Library/Application Support/CaptionGuru``
on macOS, ``$XDG_CONFIG_HOME/caption-guru`` elsewhere). Writes are atomic and
debounced so dragging a slider doesn't hammer the disk.
"""

from __future__ import annotations

import json
import os
import platform
import threading
from pathlib import Path
from typing import Any, Optional

LAUNCH_KEYS = ("engine", "model", "mic", "monitor")


def default_path() -> Path:
    env = os.environ.get("CAPTION_GURU_SETTINGS")
    if env:
        return Path(env)
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "CaptionGuru" / "settings.json"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "CaptionGuru" / "settings.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "caption-guru" / "settings.json"


class Settings:
    """Thread-safe JSON settings with a debounced save."""

    def __init__(self, path: Optional[Path] = None, debounce: float = 0.5) -> None:
        self.path = Path(path) if path else default_path()
        self.debounce = debounce
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self.data: dict[str, Any] = {}
        self.load()

    # --- read ---------------------------------------------------------------

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = raw if isinstance(raw, dict) else {}
        except FileNotFoundError:
            self.data = {}
        except (OSError, ValueError) as exc:
            print(f"  settings: ignoring unreadable {self.path} ({exc})")
            self.data = {}

    @property
    def display(self) -> dict:
        d = self.data.get("display")
        return d if isinstance(d, dict) else {}

    @property
    def dictionary(self) -> list[str]:
        d = self.data.get("dictionary")
        return [str(t) for t in d] if isinstance(d, list) else []

    @property
    def launch(self) -> dict:
        d = self.data.get("launch")
        return d if isinstance(d, dict) else {}

    # --- write --------------------------------------------------------------

    def set_display(self, config: dict) -> None:
        with self._lock:
            self.data["display"] = config
        self._schedule()

    def set_dictionary(self, terms: list[str]) -> None:
        with self._lock:
            self.data["dictionary"] = list(terms)
        self._schedule()

    def update_launch(self, **values: Any) -> None:
        with self._lock:
            launch = dict(self.launch)
            for k, v in values.items():
                if k not in LAUNCH_KEYS:
                    continue
                if v is None or v == "":
                    launch.pop(k, None)
                else:
                    launch[k] = v
            self.data["launch"] = launch
        self._schedule()

    def _schedule(self) -> None:
        if self.debounce <= 0:
            self.flush()
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            payload = json.dumps(self.data, indent=2)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            print(f"  settings: couldn't save {self.path} ({exc})")
