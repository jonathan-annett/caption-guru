"""Caption hub: the single source of truth the server fans out from.

Holds the current display config plus a rolling, append-only log of finalized
segments (default last 30 min). Every connected client (on-air display, operator,
and future audience-scrollback viewers) subscribes here. `submit()` is
thread-safe so the audio/ASR worker thread can publish onto the asyncio loop.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from .protocol import (
    CaptionSegment,
    ClearMessage,
    ConfigMessage,
    DEFAULT_DISPLAY_CONFIG,
    DisplayConfig,
    FinalMessage,
    HistoryMessage,
    PartialMessage,
    ServerMessage,
    StatusMessage,
)

# Drop subscribers' oldest messages rather than grow unbounded if one stalls.
_QUEUE_MAX = 256


class CaptionHub:
    def __init__(self, window_seconds: float = 1800.0) -> None:
        self.window_seconds = window_seconds
        self.config: DisplayConfig = DEFAULT_DISPLAY_CONFIG.model_copy(deep=True)
        self._finals: list[CaptionSegment] = []
        self._subscribers: set[asyncio.Queue[ServerMessage]] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._start = time.monotonic()
        self.last_status: Optional[StatusMessage] = None
        # Called with the new DisplayConfig after every change (persistence).
        self.on_config: Optional[Callable[[DisplayConfig], None]] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._start = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    # --- subscriptions ------------------------------------------------------

    def subscribe(self) -> "asyncio.Queue[ServerMessage]":
        q: asyncio.Queue[ServerMessage] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: "asyncio.Queue[ServerMessage]") -> None:
        self._subscribers.discard(q)

    # --- history ------------------------------------------------------------

    def history(self, since: Optional[float] = None) -> list[CaptionSegment]:
        if since is None:
            return list(self._finals)
        return [s for s in self._finals if s.end >= since]

    # --- publishing ---------------------------------------------------------

    def submit(self, msg: ServerMessage) -> None:
        """Thread-safe entry point (callable from the ASR worker thread)."""
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._dispatch, msg)

    def _dispatch(self, msg: ServerMessage) -> None:
        # Record + prune the rolling log on finals.
        if isinstance(msg, FinalMessage):
            self._finals.append(msg.segment)
            self._prune()
        elif isinstance(msg, ClearMessage):
            self._finals.clear()
        elif isinstance(msg, StatusMessage):
            self.last_status = msg

        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()  # drop oldest
                    q.put_nowait(msg)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def _prune(self) -> None:
        cutoff = self.elapsed() - self.window_seconds
        if cutoff <= 0:
            return
        self._finals = [s for s in self._finals if s.end >= cutoff]

    # --- config / control ---------------------------------------------------

    def set_config(self, patch: dict) -> DisplayConfig:
        merged = self.config.model_dump(by_alias=True)
        merged.update(patch)
        self.config = DisplayConfig.model_validate(merged)
        self.submit(ConfigMessage(config=self.config))
        if self.on_config is not None:
            self.on_config(self.config)
        return self.config

    def reset_config(self) -> DisplayConfig:
        self.config = DEFAULT_DISPLAY_CONFIG.model_copy(deep=True)
        self.submit(ConfigMessage(config=self.config))
        if self.on_config is not None:
            self.on_config(self.config)
        return self.config

    def clear(self) -> None:
        self.submit(ClearMessage())

    # Convenience constructors used by producers ----------------------------

    def emit_partial(self, segment: CaptionSegment) -> None:
        self.submit(PartialMessage(segment=segment))

    def emit_final(self, segment: CaptionSegment) -> None:
        self.submit(FinalMessage(segment=segment))

    def emit_status(self, status) -> None:  # EngineStatus
        self.submit(StatusMessage(status=status))

    def snapshot_for_new_client(self) -> list[ServerMessage]:
        """Messages a freshly connected client should receive to catch up."""
        msgs: list[ServerMessage] = [
            ConfigMessage(config=self.config),
            HistoryMessage(segments=list(self._finals)),
        ]
        if self.last_status is not None:
            msgs.append(self.last_status)
        return msgs
