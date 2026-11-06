"""Engine manager behind the control page.

Wraps the live caption producer so the control page can switch engine, model
or microphone without restarting the app: the old producer is stopped and a new
one is built and started on a worker thread, so the server (and the settings
page) stays responsive while a model downloads or loads.

Also answers "what can I pick?": installed engines, suggested models, input
devices.
"""

from __future__ import annotations

import importlib.util
import threading
from typing import Any, Callable, Optional

from .hub import CaptionHub
from .protocol import EngineStatus
from .streaming import Controller

ENGINE_LABELS = {
    "auto": "Automatic (Whisper)",
    "faster-whisper": "Whisper (faster-whisper)",
    "mlx": "Whisper on Apple GPU (MLX)",
    "sherpa-onnx": "Streaming — Nemotron (sherpa-onnx)",
}

WHISPER_MODELS = ["tiny.en", "base.en", "small.en", "medium.en", "large-v3"]


def installed_engines() -> list[str]:
    found = ["auto"]
    if importlib.util.find_spec("faster_whisper"):
        found.append("faster-whisper")
    if importlib.util.find_spec("mlx_whisper"):
        found.append("mlx")
    if importlib.util.find_spec("sherpa_onnx"):
        found.append("sherpa-onnx")
    return found


def suggested_models(engine: str) -> list[str]:
    if engine == "sherpa-onnx":
        return ["nemotron-en-80ms", "nemotron-en-160ms", "nemotron-en-560ms", "nemotron-en-1120ms"]
    return list(WHISPER_MODELS)


def default_model(engine: str) -> str:
    return "nemotron-en-560ms" if engine == "sherpa-onnx" else "base.en"


def list_input_devices() -> list[dict]:
    """Microphones on the default host API (avoids Windows' 4x duplicates)."""
    try:
        import sounddevice as sd
    except Exception:
        return []
    try:
        devices = sd.query_devices()
        default_in = sd.default.device[0]
        default_api = sd.query_hostapis(sd.default.hostapi)["name"] if sd.default.hostapi >= 0 else None
        apis = sd.query_hostapis()
    except Exception:
        return []
    out = []
    for i, d in enumerate(devices):
        if d.get("max_input_channels", 0) <= 0:
            continue
        api = apis[d["hostapi"]]["name"]
        if default_api and api != default_api:
            continue
        out.append({"index": i, "name": d["name"], "default": i == default_in})
    return out


class EngineManager:
    """A Controller that can be rebuilt with new engine/model/mic options."""

    def __init__(
        self,
        hub: CaptionHub,
        build: Callable[[dict], Controller],
        options: dict[str, Any],
        demo: bool = False,
    ) -> None:
        self.hub = hub
        self.build = build
        self.options = dict(options)
        self.demo = demo
        self.dictionary: list[str] = []
        self.current: Optional[Controller] = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.running = False

    # --- Controller protocol -------------------------------------------------

    def start(self) -> None:
        if self.demo:
            # The mock producer runs on the event loop; start it inline.
            self._ensure_built()
            self.current.start()  # type: ignore[union-attr]
            self.running = True
            return
        if self.running:
            return
        self._in_background(self._start_live)

    def stop(self) -> None:
        if self.demo:
            self._stop_now()
        else:
            # Don't block the event loop if a model is still loading.
            self._in_background(self._stop_now)

    def _stop_now(self) -> None:
        with self._lock:
            self.running = False
            if self.current is not None:
                try:
                    self.current.stop()
                except Exception as exc:  # pragma: no cover - device teardown
                    print(f"  stop failed: {exc}")

    def set_dictionary(self, terms: list[str]) -> None:
        self.dictionary = list(terms)
        if self.current is not None:
            self.current.set_dictionary(self.dictionary)

    # --- switching -------------------------------------------------------------

    def restart(self, **changes: Any) -> None:
        """Apply new options (engine/model/mic) and start captioning again."""
        if self.demo:
            return
        self.options.update(changes)

        def work() -> None:
            with self._lock:
                self.running = False
                if self.current is not None:
                    try:
                        self.current.stop()
                    except Exception as exc:  # pragma: no cover
                        print(f"  stop failed: {exc}")
                self.current = None  # rebuild with the new engine / model / mic
            self._start_live()

        self._in_background(work)

    def info(self) -> dict:
        engine = self.options.get("engine") or "auto"
        return {
            "demo": self.demo,
            "running": self.running,
            "engine": engine,
            "model": self.options.get("model") or default_model(engine),
            "mic": self.options.get("mic"),
        }

    # --- internals -------------------------------------------------------------

    def _in_background(self, fn: Callable[[], None]) -> None:
        t = threading.Thread(target=fn, daemon=True, name="caption-engine")
        self._thread = t
        t.start()

    def _ensure_built(self) -> None:
        if self.current is None:
            self.current = self.build(self.options)
            self.current.set_dictionary(self.dictionary)

    def _start_live(self) -> None:
        with self._lock:
            if self.running:
                return
            engine = self.options.get("engine") or "auto"
            self.hub.emit_status(
                EngineStatus(
                    state="loading",
                    backend=engine,
                    model=self.options.get("model") or default_model(engine),
                    message="Loading speech model (first use downloads it)…",
                )
            )
            try:
                self._ensure_built()
                self.current.start()  # type: ignore[union-attr]
                # A live streamer whose model failed to load reports an error
                # status and leaves its capture flag clear; let Start retry.
                flag = getattr(self.current, "_running", None)
                self.running = flag is None or flag.is_set()
            except Exception as exc:
                self.running = False
                self.current = None
                print(f"  engine failed to start: {exc}")
                self.hub.emit_status(EngineStatus(state="error", backend=engine, message=str(exc)))

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait for a pending start/restart (tests)."""
        if self._thread is not None:
            self._thread.join(timeout)
