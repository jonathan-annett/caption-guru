"""ASR engines + a factory that picks the best backend for the platform."""

from __future__ import annotations

import importlib.util
import platform
from typing import Optional, Union

from .base import ASREngine, StreamingASREngine, StreamResult

__all__ = [
    "ASREngine",
    "StreamingASREngine",
    "StreamResult",
    "create_engine",
    "is_apple_silicon",
    "mlx_available",
]

STREAMING_ENGINES = ("sherpa-onnx",)


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def mlx_available() -> bool:
    return importlib.util.find_spec("mlx_whisper") is not None


def create_engine(
    name: str = "auto",
    model: Optional[str] = None,
    device: str = "auto",
    language: str = "en",
    **options,
) -> Union[ASREngine, StreamingASREngine]:
    """Build an ASR engine.

    name: "auto" | "faster-whisper" | "mlx" | "sherpa-onnx". "auto" prefers MLX
    (Apple GPU) on Apple Silicon when installed, otherwise faster-whisper
    (CPU/CUDA). "sherpa-onnx" is a true streaming engine (Nemotron by default).
    ``model`` defaults per engine (Whisper: base.en; sherpa-onnx: nemotron).
    Extra ``options`` are passed to the sherpa-onnx engine only.
    """
    name = (name or "auto").lower()

    if name == "sherpa-onnx":
        from .sherpa_onnx import SherpaOnnxStreamingEngine

        return SherpaOnnxStreamingEngine(model=model, device=device, **options)

    model = model or "base.en"

    if name == "mlx" or (name == "auto" and is_apple_silicon() and mlx_available()):
        from .mlx import MLXWhisperEngine

        return MLXWhisperEngine(model=model, language=language)

    from .faster_whisper import FasterWhisperEngine

    return FasterWhisperEngine(model=model, device=device, language=language)
