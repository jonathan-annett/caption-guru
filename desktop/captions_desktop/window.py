"""Display output: a fullscreen pywebview window (primary HDMI path) with a
Chrome-kiosk fallback. All GUI imports are lazy so the server and tests install
and run without pywebview.

The window just renders the display page; the *background* (solid / chroma /
transparent) is painted by the page from the pushed DisplayConfig, so feeding a
switcher over HDMI vs keying is a config choice, not a code change.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Optional


# ---------------------------------------------------------------------------
# Monitors
# ---------------------------------------------------------------------------


def list_screens() -> list[dict]:
    """Return available monitors as dicts, or [] if pywebview is unavailable."""
    try:
        import webview
    except Exception:
        return []
    screens = []
    for i, s in enumerate(webview.screens):
        screens.append(
            {
                "index": i,
                "width": getattr(s, "width", None),
                "height": getattr(s, "height", None),
                "x": getattr(s, "x", None),
                "y": getattr(s, "y", None),
            }
        )
    return screens


# ---------------------------------------------------------------------------
# pywebview (primary)
# ---------------------------------------------------------------------------


def run_webview(
    url: str,
    *,
    fullscreen: bool = True,
    monitor: int = 0,
    transparent: bool = False,
    title: str = "Live Captions",
    settings_url: Optional[str] = None,
) -> None:
    """Open the display fullscreen on the chosen monitor. Blocks until closed.

    With ``settings_url``, also opens a normal window with the settings page,
    on another monitor when there is one. Closing the display closes both.
    """
    import webview

    kwargs: dict = {
        "fullscreen": fullscreen,
        "frameless": fullscreen,
        "background_color": "#000000",
    }
    if transparent:
        kwargs["transparent"] = True
    screens = webview.screens
    if 0 <= monitor < len(screens):
        kwargs["screen"] = screens[monitor]

    display = webview.create_window(title, url, **kwargs)

    if settings_url:
        skw: dict = {"width": 1180, "height": 860, "min_size": (720, 560)}
        other = [i for i in range(len(screens)) if i != monitor]
        if other:
            skw["screen"] = screens[other[0]]
        panel = webview.create_window("Caption Guru — Settings", settings_url, **skw)

        def _close_panel() -> None:
            try:
                panel.destroy()
            except Exception:
                pass

        display.events.closed += _close_panel

    webview.start()


def webview_available() -> bool:
    try:
        import webview  # noqa: F401

        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Chrome kiosk (fallback)
# ---------------------------------------------------------------------------

_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "brave-browser",
]


def find_chrome() -> Optional[str]:
    for c in _CHROME_CANDIDATES:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
        found = shutil.which(c)
        if found:
            return found
    return None


def chrome_kiosk_args(
    chrome: str,
    url: str,
    position: Optional[tuple[int, int]] = None,
    user_data_dir: Optional[str] = None,
) -> list[str]:
    """Build the Chrome command line for a borderless fullscreen caption output."""
    args = [
        chrome,
        f"--app={url}",
        "--kiosk",
        "--start-fullscreen",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-translate",
        "--autoplay-policy=no-user-gesture-required",
    ]
    if user_data_dir:
        args.append(f"--user-data-dir={user_data_dir}")
    if position is not None:
        args.append(f"--window-position={position[0]},{position[1]}")
    return args


def launch_chrome_kiosk(url: str, monitor: int = 0) -> Optional[subprocess.Popen]:
    chrome = find_chrome()
    if not chrome:
        return None
    position = None
    screens = list_screens()
    if 0 <= monitor < len(screens):
        s = screens[monitor]
        if s["x"] is not None and s["y"] is not None:
            position = (int(s["x"]), int(s["y"]))
    user_data_dir = tempfile.mkdtemp(prefix="caption-guru-kiosk-")
    args = chrome_kiosk_args(chrome, url, position=position, user_data_dir=user_data_dir)
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
