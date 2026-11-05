"""Pluggable ASR engine interfaces.

Two shapes of engine:

- ``ASREngine`` (utterance-based): a whole utterance (16 kHz mono float32) goes
  in, text comes out. The caller does VAD endpointing and re-decodes the growing
  utterance for partials. faster-whisper and MLX-Whisper work this way.
- ``StreamingASREngine`` (true streaming): audio is fed in small chunks and the
  engine keeps decoder state, emitting partial text as words are spoken and
  signalling its own endpoints. Streaming transducers (e.g. NVIDIA Nemotron via
  sherpa-onnx) work this way, with lower latency and no repeated re-decoding.

Keeping both behind small interfaces lets the transport code stay independent of
the backend, so engines (or a mock) can be swapped freely.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from ..protocol import EngineStatus


class ASREngine(ABC):
    @abstractmethod
    def load(self) -> EngineStatus:
        """Load the model. Returns a 'listening' status (or 'error')."""

    @abstractmethod
    def transcribe(self, samples: Any, hotwords: Optional[str] = None) -> str:
        """Transcribe a 16 kHz mono float32 numpy array to text.

        ``hotwords`` optionally biases decoding toward event-specific
        names/jargon (the custom dictionary).
        """

    def unload(self) -> None:  # optional override
        pass


@dataclass(frozen=True)
class StreamResult:
    """What a streaming engine reports after accepting a chunk of audio.

    ``text`` is the full hypothesis for the current segment so far (not a
    delta). ``endpoint`` is True when the engine judges the segment finished;
    the caller then finalizes ``text`` and calls ``reset()``.
    """

    text: str
    endpoint: bool = False


class StreamingASREngine(ABC):
    """A stateful engine fed audio incrementally (one live segment at a time)."""

    sample_rate: int = 16000

    @abstractmethod
    def load(self) -> EngineStatus:
        """Load the model. Returns a 'listening' status (or 'error')."""

    @abstractmethod
    def reset(self, hotwords: Optional[Sequence[str]] = None) -> None:
        """Start a fresh segment, optionally biased toward ``hotwords``.

        Called before the first chunk and after every endpoint, so dictionary
        changes take effect from the next segment without reloading the model.
        """

    @abstractmethod
    def accept(self, samples: Any) -> StreamResult:
        """Feed a chunk of 16 kHz mono float32 audio; return the hypothesis."""

    @abstractmethod
    def flush(self) -> str:
        """Signal end of input and return the final text of the open segment."""

    def unload(self) -> None:  # optional override
        pass
