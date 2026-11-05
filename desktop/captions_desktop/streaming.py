"""Caption producers.

- ``MockProducer``: scripted captions on the event loop — verifies the whole
  server/transport without audio or ASR deps.
- ``LiveStreamer``: real pipeline for utterance engines (faster-whisper, MLX) —
  sounddevice capture in a worker thread, energy VAD endpointing, re-decoding
  the growing utterance for partials, published to the hub.
- ``ContinuousStreamer``: real pipeline for streaming engines (sherpa-onnx) —
  every audio chunk goes straight to the engine, which emits partials as words
  arrive and decides its own endpoints.

All honor the ``Controller`` protocol the server's control channel drives;
``make_streamer`` picks the right live one for an engine.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from typing import Any, Optional, Protocol, Union

from .engines.base import ASREngine, StreamingASREngine
from .hub import CaptionHub
from .protocol import CaptionSegment, EngineStatus
from .vad import EnergyVAD


class Controller(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def set_dictionary(self, terms: list[str]) -> None: ...


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------

_SCRIPT = [
    "Welcome everyone to tonight's event.",
    "These captions are generated entirely on device.",
    "No audio ever leaves this machine.",
    "The desktop build uses WhisperX under the hood.",
    "Let's get started.",
]


class MockProducer:
    def __init__(self, hub: CaptionHub) -> None:
        self.hub = hub
        self._task: Optional[asyncio.Task] = None
        self._counter = 0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def set_dictionary(self, terms: list[str]) -> None:
        pass

    async def _run(self) -> None:
        self.hub.emit_status(
            EngineStatus(state="listening", backend="mock", model="demo", device="cpu")
        )
        clock = 0.0
        while True:
            for sentence in _SCRIPT:
                self._counter += 1
                sid = f"mock-{self._counter}"
                words = sentence.split(" ")
                start = clock
                for i in range(len(words)):
                    seg = CaptionSegment(
                        id=sid,
                        text=" ".join(words[: i + 1]),
                        start=start,
                        end=start + (i + 1) * 0.3,
                    )
                    self.hub.emit_partial(seg)
                    await asyncio.sleep(0.22)
                end = start + len(words) * 0.3
                self.hub.emit_final(
                    CaptionSegment(id=sid, text=sentence, start=start, end=end)
                )
                clock = end + 0.6
                await asyncio.sleep(0.6)


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------


class _MicCapture:
    """Mic capture shared by the live streamers.

    sounddevice delivers blocks on its own thread into a bounded queue; a worker
    thread drains it into ``_on_frame``. Subclasses load their engine in
    ``_load`` and may finish open work in ``_on_stopped``.
    """

    def __init__(
        self,
        hub: CaptionHub,
        sample_rate: int = 16000,
        block_ms: int = 32,
        device: Optional[int] = None,
    ) -> None:
        self.hub = hub
        self.sample_rate = sample_rate
        self.block = int(sample_rate * block_ms / 1000)
        self.device = device
        self.dictionary: list[str] = []

        self._stream: Any = None
        self._worker: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._frames: "Any" = None  # queue.Queue, set on start

    def set_dictionary(self, terms: list[str]) -> None:
        self.dictionary = terms

    def _load(self) -> EngineStatus:  # pragma: no cover - overridden
        raise NotImplementedError

    def _on_frame(self, frame: Any) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _on_stopped(self) -> None:
        pass

    def start(self) -> None:
        import queue

        import numpy as np  # noqa: F401  (ensures numpy present before capture)
        import sounddevice as sd

        status = self._load()
        self.hub.emit_status(status)
        if status.state == "error":
            return

        self._frames = queue.Queue(maxsize=256)
        self._running.set()

        def callback(indata, _frames, _time, _status):  # sounddevice thread
            try:
                self._frames.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.block,
            device=self.device,
            callback=callback,
        )
        self._stream.start()

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._running.clear()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._worker:
            self._worker.join(timeout=2.0)
            self._worker = None
        self._on_stopped()
        self.hub.emit_status(EngineStatus(state="idle"))

    def _run(self) -> None:
        import queue

        while self._running.is_set():
            try:
                frame = self._frames.get(timeout=0.2)
            except queue.Empty:
                continue
            self._on_frame(frame)


class LiveStreamer(_MicCapture):
    """Utterance engines: VAD endpointing + periodic re-decode for partials."""

    def __init__(
        self,
        hub: CaptionHub,
        engine: ASREngine,
        sample_rate: int = 16000,
        block_ms: int = 32,
        device: Optional[int] = None,
    ) -> None:
        super().__init__(hub, sample_rate=sample_rate, block_ms=block_ms, device=device)
        self.engine = engine
        self._vad = EnergyVAD(sample_rate)

        # utterance state
        self._sample_count = 0
        self._preroll: list[Any] = []
        self._preroll_n = 0
        self._utter: list[Any] = []
        self._utter_n = 0
        self._utter_start = 0
        self._in_utter = False
        self._current_id = ""
        self._since_partial = 0

        self._preroll_cap = int(sample_rate * 0.25)
        self._partial_every = int(sample_rate * 0.7)
        self._max_utter = int(sample_rate * 14)

    def _load(self) -> EngineStatus:
        return self.engine.load()

    def _on_frame(self, frame: Any) -> None:
        self._sample_count += len(frame)
        ev = self._vad.process(frame)

        if ev == "start":
            self._start_utterance()

        if self._in_utter:
            self._utter.append(frame)
            self._utter_n += len(frame)
            self._since_partial += len(frame)
        else:
            self._push_preroll(frame)

        if ev == "end":
            self._finish_utterance()
            return

        if self._in_utter and self._utter_n >= self._max_utter:
            self._finish_utterance()
            self._start_utterance()
            return

        if self._in_utter and self._since_partial >= self._partial_every:
            self._since_partial = 0
            self._decode(final=False)

    def _push_preroll(self, frame: Any) -> None:
        self._preroll.append(frame)
        self._preroll_n += len(frame)
        while self._preroll_n > self._preroll_cap and len(self._preroll) > 1:
            self._preroll_n -= len(self._preroll.pop(0))

    def _start_utterance(self) -> None:
        self._in_utter = True
        self._utter = list(self._preroll)
        self._utter_n = self._preroll_n
        self._utter_start = self._sample_count - self._preroll_n
        self._since_partial = 0
        self._current_id = uuid.uuid4().hex
        self._preroll = []
        self._preroll_n = 0

    def _finish_utterance(self) -> None:
        if not self._in_utter:
            return
        self._decode(final=True)
        self._in_utter = False
        self._utter = []
        self._utter_n = 0

    def _decode(self, final: bool) -> None:
        import numpy as np

        if self._utter_n == 0:
            return
        samples = np.concatenate(self._utter).astype(np.float32)
        seg_id = self._current_id
        start = self._utter_start / self.sample_rate
        end = self._sample_count / self.sample_rate
        hotwords = ", ".join(self.dictionary) if self.dictionary else None
        text = self.engine.transcribe(samples, hotwords=hotwords)
        if not text:
            return
        seg = CaptionSegment(id=seg_id, text=text, start=start, end=end)
        if final:
            self.hub.emit_final(seg)
        else:
            self.hub.emit_partial(seg)


class ContinuousStreamer(_MicCapture):
    """Streaming engines: feed every chunk; the engine emits text + endpoints.

    A segment opens when the engine first produces text and closes on the
    engine's endpoint (or on stop). Partials are only emitted when the text
    changes. Dictionary edits apply from the next segment (via ``reset``).
    """

    def __init__(
        self,
        hub: CaptionHub,
        engine: StreamingASREngine,
        sample_rate: int = 16000,
        block_ms: int = 32,
        device: Optional[int] = None,
    ) -> None:
        super().__init__(hub, sample_rate=sample_rate, block_ms=block_ms, device=device)
        self.engine = engine
        self._sample_count = 0
        self._seg_id = ""
        self._seg_start = 0.0
        self._last_text = ""

    def _load(self) -> EngineStatus:
        status = self.engine.load()
        if status.state != "error":
            self.engine.reset(self.dictionary)
        return status

    def _now(self) -> float:
        return self._sample_count / self.sample_rate

    def _on_frame(self, frame: Any) -> None:
        start_of_chunk = self._now()
        self._sample_count += len(frame)
        result = self.engine.accept(frame)
        text = result.text

        if text and not self._seg_id:
            # Text first appeared somewhere in this chunk (or the engine's
            # lookahead before it); the chunk start is a fair estimate.
            self._seg_id = uuid.uuid4().hex
            self._seg_start = start_of_chunk

        if text and text != self._last_text:
            self._last_text = text
            self.hub.emit_partial(self._segment(text))

        if result.endpoint:
            self._finalize(text)
            self.engine.reset(self.dictionary)

    def _finalize(self, text: str) -> None:
        if text and self._seg_id:
            self.hub.emit_final(self._segment(text))
        self._seg_id = ""
        self._last_text = ""

    def _segment(self, text: str) -> CaptionSegment:
        end = max(self._now(), self._seg_start)
        return CaptionSegment(id=self._seg_id, text=text, start=self._seg_start, end=end)

    def _on_stopped(self) -> None:
        text = self.engine.flush()
        if text and not self._seg_id:
            self._seg_id = uuid.uuid4().hex
            self._seg_start = self._now()
        self._finalize(text)


def make_streamer(
    hub: CaptionHub,
    engine: Union[ASREngine, StreamingASREngine],
    device: Optional[int] = None,
) -> Union[LiveStreamer, ContinuousStreamer]:
    """Pick the live pipeline that matches the engine's shape."""
    if isinstance(engine, StreamingASREngine):
        return ContinuousStreamer(hub, engine, device=device)
    return LiveStreamer(hub, engine, device=device)
