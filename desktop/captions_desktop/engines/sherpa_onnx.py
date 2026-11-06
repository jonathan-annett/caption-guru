"""sherpa-onnx streaming ASR backend (true word-by-word streaming).

Runs streaming transducer models, by default NVIDIA's Nemotron Speech Streaming
EN 0.6B, through `sherpa-onnx <https://github.com/k2-fsa/sherpa-onnx>`_. Unlike
the Whisper engines it keeps decoder state between chunks, so partial captions
appear as words are spoken instead of after a VAD pause, and it detects its own
endpoints. Output already includes punctuation and capitalization.

Models: pass a directory containing ``encoder*.onnx``, ``decoder*.onnx``,
``joiner*.onnx`` and ``tokens.txt``, or one of the short names in
``KNOWN_MODELS``, which are downloaded once from the sherpa-onnx releases into a
local cache (``$CAPTION_GURU_MODELS`` or ``~/.cache/caption-guru/models``).

Hotwords (the custom dictionary) need ``modified_beam_search`` plus a BPE vocab
file. Nemotron's archives don't ship ``bpe.vocab``, so one is generated from
``tokens.txt``. The vocabulary is mixed case, so terms are passed in their
natural casing (e.g. "Steve Gibson"), not upper-cased as in older sherpa docs.
Hotwords are applied per segment, so dictionary edits take effect at the next
endpoint without reloading the model.

Heavy imports are lazy so the rest of the app runs without sherpa-onnx.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional, Sequence

from ..protocol import EngineStatus
from .base import StreamingASREngine, StreamResult

RELEASE_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"

# Short name -> release archive stem. The chunk size trades latency for
# accuracy (larger chunks are more accurate; 560 ms is sherpa-onnx's default).
KNOWN_MODELS: dict[str, str] = {
    f"nemotron-en-{ms}ms": f"sherpa-onnx-nemotron-speech-streaming-en-0.6b-{ms}ms-int8-2026-04-25"
    for ms in (80, 160, 560, 1120)
}
KNOWN_MODELS["nemotron-en"] = KNOWN_MODELS["nemotron-en-560ms"]

DEFAULT_MODEL = "nemotron-en-560ms"

_BACKEND = "sherpa-onnx"


# ---------------------------------------------------------------------------
# Model files
# ---------------------------------------------------------------------------


def models_cache_dir() -> Path:
    override = os.environ.get("CAPTION_GURU_MODELS")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "caption-guru" / "models"


def resolve_model_dir(model: str, download: bool = True) -> Path:
    """Return a local model directory for a path or a ``KNOWN_MODELS`` name."""
    path = Path(model).expanduser()
    if path.is_dir():
        return path

    stem = KNOWN_MODELS.get(model)
    if stem is None:
        known = ", ".join(sorted(KNOWN_MODELS))
        raise FileNotFoundError(
            f"model {model!r} is not a directory or a known model ({known})"
        )

    target = models_cache_dir() / stem
    if target.is_dir():
        try:
            find_model_files(target)
            return target
        except FileNotFoundError:
            # Left half-unpacked by an older version that was interrupted.
            print(f"  {target.name} is incomplete; downloading it again.", file=sys.stderr)
            shutil.rmtree(target, ignore_errors=True)
    if not download:
        raise FileNotFoundError(f"model {model!r} not downloaded (expected {target})")
    _download_and_extract(RELEASE_BASE + stem + ".tar.bz2", target.parent, stem)
    if not target.is_dir():
        raise FileNotFoundError(f"archive for {model!r} did not contain {stem}/")
    return target


# A stalled connection gives up after this long without data (then retries).
_STALL_TIMEOUT = 30
_ATTEMPTS = 4


def _download_and_extract(url: str, dest_dir: Path, stem: str) -> None:
    """Download (resumable) and unpack so ``dest_dir/stem`` appears all at once.

    The archive is kept as ``<name>.part`` while downloading, so quitting midway
    and starting again resumes instead of starting over. It is unpacked into a
    scratch folder and only moved into place when complete, so an interrupted
    unpack can never look like a finished model.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = url.rsplit("/", 1)[-1]
    part = dest_dir / (name + ".part")
    print(f"Downloading speech model {name} (one time) ...", file=sys.stderr, flush=True)
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            _fetch_resumable(url, part)
            break
        except (OSError, ValueError) as exc:  # URLError/timeouts are OSErrors
            if attempt == _ATTEMPTS:
                raise RuntimeError(
                    f"model download failed ({exc}); press Start to try again - "
                    "it will resume where it stopped"
                ) from exc
            print(f"\n  download interrupted ({exc}); retrying ...", file=sys.stderr, flush=True)
            time.sleep(2 * attempt)

    print("  Unpacking (about a minute) ...", file=sys.stderr, flush=True)
    for old in dest_dir.glob("unpack-*"):  # left by an interrupted unpack
        shutil.rmtree(old, ignore_errors=True)
    scratch = Path(tempfile.mkdtemp(prefix="unpack-", dir=dest_dir))
    try:
        with tarfile.open(part, "r:*") as tar:
            _safe_extract(tar, scratch)
        unpacked = scratch / stem
        if unpacked.is_dir():
            os.replace(unpacked, dest_dir / stem)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    part.unlink(missing_ok=True)
    print("  Model ready.", file=sys.stderr, flush=True)


def _fetch_resumable(url: str, part: Path) -> None:
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "caption-guru"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    with urllib.request.urlopen(req, timeout=_STALL_TIMEOUT) as resp:
        if have and getattr(resp, "status", None) != 206:  # range ignored: start over
            have = 0
        length = resp.headers.get("Content-Length")
        total = int(length) + have if length else 0
        if have:
            print(f"  resuming at {have / 1e6:.0f} MB", file=sys.stderr, flush=True)
        report = _Progress(total)
        with open(part, "ab" if have else "wb") as fh:
            got = have
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                report(got)
    if total and part.stat().st_size < total:
        raise ValueError("connection closed early")
    print("", file=sys.stderr, flush=True)


class _Progress:
    """Prints 'x / y MB (z%)' on one line, at most a few times a second."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.last = 0.0

    def __call__(self, got: int) -> None:
        now = time.monotonic()
        if now - self.last < 0.5 and got != self.total:
            return
        self.last = now
        if self.total:
            msg = f"  {got / 1e6:6.1f} / {self.total / 1e6:.0f} MB ({got * 100 // self.total}%)"
        else:
            msg = f"  {got / 1e6:6.1f} MB"
        print("\r" + msg, end="", file=sys.stderr, flush=True)


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract without allowing paths that escape ``dest`` (or links/devices)."""
    try:
        tar.extractall(dest, filter="data")  # Python 3.11.4+/3.12+
        return
    except TypeError:  # pragma: no cover - older 3.11 patch releases
        pass
    root = dest.resolve()
    for member in tar.getmembers():  # pragma: no cover - fallback path
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(root)) or not (member.isfile() or member.isdir()):
            raise ValueError(f"unsafe path in model archive: {member.name}")
    tar.extractall(dest)  # pragma: no cover


def find_model_files(model_dir: Path) -> dict[str, Path]:
    """Locate encoder/decoder/joiner (preferring int8) and tokens.txt."""
    files: dict[str, Path] = {}
    for part in ("encoder", "decoder", "joiner"):
        candidates = sorted(model_dir.glob(f"{part}*.onnx"))
        if not candidates:
            raise FileNotFoundError(f"no {part}*.onnx in {model_dir}")
        int8 = [c for c in candidates if ".int8." in c.name]
        files[part] = (int8 or candidates)[0]
    tokens = model_dir / "tokens.txt"
    if not tokens.is_file():
        raise FileNotFoundError(f"no tokens.txt in {model_dir}")
    files["tokens"] = tokens
    return files


def ensure_bpe_vocab(model_dir: Path, tokens: Path) -> Path:
    """Return a ``bpe.vocab`` for hotwords, generating one from tokens.txt.

    sherpa-onnx's built-in BPE encoder reads ``piece<TAB>score`` lines. For a
    SentencePiece BPE vocab the merge priority follows the token id, so
    ``score = -id`` reproduces it. Special tokens (``<unk>``, ``<blk>``...) are
    skipped. Written next to the model, or into the cache if that's read-only.
    """
    existing = model_dir / "bpe.vocab"
    if existing.is_file():
        return existing

    lines = []
    for raw in tokens.read_text(encoding="utf-8").splitlines():
        parts = raw.rsplit(" ", 1)
        if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
            continue
        piece, idx = parts
        if piece.startswith("<") and piece.endswith(">"):
            continue
        lines.append(f"{piece}\t{-int(idx)}")
    content = "\n".join(lines) + "\n"

    for target in (existing, models_cache_dir() / f"{model_dir.name}.bpe.vocab"):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return target
        except OSError:
            continue
    raise OSError(f"could not write a bpe.vocab for {model_dir}")


def format_hotwords(terms: Optional[Sequence[str]]) -> str:
    """Join dictionary terms into sherpa-onnx's per-stream hotwords string.

    Phrases are separated by ``/``; casing is kept (Nemotron's vocab is mixed
    case). Whitespace is collapsed, ``/`` removed from terms, duplicates dropped.
    """
    seen: set[str] = set()
    out: list[str] = []
    for term in terms or ():
        clean = re.sub(r"\s+", " ", term.replace("/", " ")).strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return "/".join(out)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class SherpaOnnxStreamingEngine(StreamingASREngine):
    def __init__(
        self,
        model: Optional[str] = None,
        device: str = "auto",
        num_threads: int = 4,
        decoding: str = "auto",
        hotwords_score: float = 1.5,
        endpoint_trailing_silence: float = 1.2,
        endpoint_max_utterance: float = 20.0,
        download: bool = True,
    ) -> None:
        self.model_name = model or DEFAULT_MODEL
        self.device = device
        self.num_threads = num_threads
        # "auto"/"beam" -> modified_beam_search (required for hotwords);
        # "greedy" -> greedy_search (fastest, dictionary ignored).
        self.decoding = decoding
        self.hotwords_score = hotwords_score
        self.endpoint_trailing_silence = endpoint_trailing_silence
        self.endpoint_max_utterance = endpoint_max_utterance
        self.download = download

        self._recognizer: Any = None
        self._stream: Any = None
        self._beam = decoding in ("auto", "beam")

    @property
    def supports_hotwords(self) -> bool:
        return self._beam

    def load(self) -> EngineStatus:
        try:
            import sherpa_onnx  # lazy
        except Exception as exc:  # pragma: no cover - env dependent
            return EngineStatus(
                state="error",
                backend=_BACKEND,
                model=self.model_name,
                message=f"sherpa-onnx not installed: {exc}",
            )

        provider = "cuda" if self.device == "cuda" else "cpu"
        try:
            model_dir = resolve_model_dir(self.model_name, download=self.download)
            files = find_model_files(model_dir)
            kwargs: dict[str, Any] = dict(
                tokens=str(files["tokens"]),
                encoder=str(files["encoder"]),
                decoder=str(files["decoder"]),
                joiner=str(files["joiner"]),
                num_threads=self.num_threads,
                sample_rate=self.sample_rate,
                enable_endpoint_detection=True,
                rule1_min_trailing_silence=2.4,
                rule2_min_trailing_silence=self.endpoint_trailing_silence,
                rule3_min_utterance_length=self.endpoint_max_utterance,
                provider=provider,
            )
            if self._beam:
                kwargs.update(
                    decoding_method="modified_beam_search",
                    max_active_paths=4,
                    modeling_unit="bpe",
                    bpe_vocab=str(ensure_bpe_vocab(model_dir, files["tokens"])),
                    hotwords_score=self.hotwords_score,
                )
            else:
                kwargs["decoding_method"] = "greedy_search"
            self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(**kwargs)
        except Exception as exc:  # pragma: no cover - env dependent
            return EngineStatus(
                state="error",
                backend=_BACKEND,
                model=self.model_name,
                device=provider,
                message=str(exc),
            )

        self.reset()
        return EngineStatus(
            state="listening",
            backend=_BACKEND,
            model=self.model_name,
            device=provider,
        )

    def reset(self, hotwords: Optional[Sequence[str]] = None) -> None:
        if self._recognizer is None:
            return
        spec = format_hotwords(hotwords) if self._beam else ""
        self._stream = (
            self._recognizer.create_stream(spec)
            if spec
            else self._recognizer.create_stream()
        )

    def accept(self, samples: Any) -> StreamResult:
        if self._recognizer is None or self._stream is None:
            return StreamResult(text="")
        rec, stream = self._recognizer, self._stream
        stream.accept_waveform(self.sample_rate, samples)
        while rec.is_ready(stream):
            rec.decode_stream(stream)
        return StreamResult(text=self._text(), endpoint=bool(rec.is_endpoint(stream)))

    def flush(self) -> str:
        if self._recognizer is None or self._stream is None:
            return ""
        import numpy as np

        rec, stream = self._recognizer, self._stream
        # A short silent tail lets the last chunk's words come out.
        stream.accept_waveform(
            self.sample_rate, np.zeros(int(self.sample_rate * 0.8), dtype=np.float32)
        )
        stream.input_finished()
        while rec.is_ready(stream):
            rec.decode_stream(stream)
        return self._text()

    def unload(self) -> None:
        self._stream = None
        self._recognizer = None

    def _text(self) -> str:
        result = self._recognizer.get_result(self._stream)
        if not isinstance(result, str):  # older bindings return an object
            result = getattr(result, "text", "") or ""
        return result.strip()
