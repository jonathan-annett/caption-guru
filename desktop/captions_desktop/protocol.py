"""Python mirror of ``packages/protocol`` (the shared caption protocol).

Field names are snake_case in Python but (de)serialize to the camelCase JSON the
TypeScript side uses, via a camelCase alias generator. Keep this file in lockstep
with ``packages/protocol/src/index.ts``.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.alias_generators import to_camel

# Bumped on breaking changes to the message shapes below.
PROTOCOL_VERSION = 1


class _Model(BaseModel):
    """Base: serialize to camelCase JSON, accept either casing on input."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------


class Word(_Model):
    text: str
    start: float  # seconds from session start
    end: float
    confidence: Optional[float] = None


class CaptionSegment(_Model):
    id: str  # stable; a partial keeps its id until finalized
    text: str
    start: float
    end: float
    speaker: Optional[str] = None
    lang: Optional[str] = None
    words: Optional[list[Word]] = None


# ---------------------------------------------------------------------------
# Display configuration
# ---------------------------------------------------------------------------


class TransparentBackground(_Model):
    kind: Literal["transparent"] = "transparent"


class SolidBackground(_Model):
    kind: Literal["solid"] = "solid"
    color: str


class ChromaBackground(_Model):
    kind: Literal["chroma"] = "chroma"
    color: str


Background = Annotated[
    Union[TransparentBackground, SolidBackground, ChromaBackground],
    Field(discriminator="kind"),
]


class DisplayConfig(_Model):
    font_family: str
    font_size: float  # viewport-height units (vh)
    color: str
    background: Background
    position: Literal["top", "center", "bottom"]
    text_align: Literal["left", "center", "right"]
    max_lines: int
    mode: Literal["rolling", "scroll"]
    show_partial: bool
    uppercase: bool
    # Safe-area insets: horizontal in viewport-width units (vw), vertical in
    # viewport-height units (vh). Older configs without them get the defaults.
    margin_x: float = Field(default=6.0, ge=0, le=45)
    margin_y: float = Field(default=4.0, ge=0, le=45)


DEFAULT_DISPLAY_CONFIG = DisplayConfig(
    font_family="'Inter', 'Helvetica Neue', Arial, system-ui, sans-serif",
    font_size=6,
    color="#ffffff",
    background=SolidBackground(color="#000000"),
    position="bottom",
    text_align="center",
    max_lines=2,
    mode="rolling",
    show_partial=True,
    uppercase=False,
)


# ---------------------------------------------------------------------------
# Engine status
# ---------------------------------------------------------------------------


class EngineStatus(_Model):
    state: Literal["idle", "loading", "listening", "error"]
    backend: Optional[str] = None
    model: Optional[str] = None
    device: Optional[str] = None
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Server -> client messages
# ---------------------------------------------------------------------------


class PartialMessage(_Model):
    type: Literal["partial"] = "partial"
    segment: CaptionSegment


class FinalMessage(_Model):
    type: Literal["final"] = "final"
    segment: CaptionSegment


class ClearMessage(_Model):
    type: Literal["clear"] = "clear"


class ConfigMessage(_Model):
    type: Literal["config"] = "config"
    config: DisplayConfig


class StatusMessage(_Model):
    type: Literal["status"] = "status"
    status: EngineStatus


class HistoryMessage(_Model):
    """Replay of finalized segments — for late joiners and audience scrollback."""

    type: Literal["history"] = "history"
    segments: list[CaptionSegment]


ServerMessage = Annotated[
    Union[
        PartialMessage,
        FinalMessage,
        ClearMessage,
        ConfigMessage,
        StatusMessage,
        HistoryMessage,
    ],
    Field(discriminator="type"),
]

ServerMessageAdapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)


# ---------------------------------------------------------------------------
# Client -> server (control) messages
# ---------------------------------------------------------------------------


class SetConfigMessage(_Model):
    type: Literal["setConfig"] = "setConfig"
    # partial config patch
    config: dict


class SetDictionaryMessage(_Model):
    type: Literal["setDictionary"] = "setDictionary"
    terms: list[str]


class ControlCommand(_Model):
    type: Literal["command"] = "command"
    command: Literal["start", "stop", "clear"]


class RequestHistoryMessage(_Model):
    type: Literal["requestHistory"] = "requestHistory"
    since: Optional[float] = None


ClientMessage = Annotated[
    Union[
        SetConfigMessage,
        SetDictionaryMessage,
        ControlCommand,
        RequestHistoryMessage,
    ],
    Field(discriminator="type"),
]

ClientMessageAdapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def dump_message(msg: _Model) -> str:
    """Serialize any protocol model to wire JSON (camelCase keys)."""
    return msg.model_dump_json(by_alias=True, exclude_none=True)


def parse_server_message(data: str | bytes | dict) -> ServerMessage:
    if isinstance(data, dict):
        return ServerMessageAdapter.validate_python(data)
    return ServerMessageAdapter.validate_json(data)


def parse_client_message(data: str | bytes | dict) -> ClientMessage:
    if isinstance(data, dict):
        return ClientMessageAdapter.validate_python(data)
    return ClientMessageAdapter.validate_json(data)
