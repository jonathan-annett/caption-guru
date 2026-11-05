"""Streaming engine interface, sherpa-onnx helpers and the ContinuousStreamer.

These run without sherpa-onnx or a model: the streamer is driven by a scripted
fake engine, and the helpers are pure file/string logic. A real end-to-end check
runs only when CAPTION_GURU_SHERPA_MODEL points at a model directory.
"""

from __future__ import annotations

import os
import wave
from pathlib import Path

import pytest

from captions_desktop.engines import StreamingASREngine, StreamResult, create_engine
from captions_desktop.engines.faster_whisper import FasterWhisperEngine
from captions_desktop.engines.sherpa_onnx import (
    DEFAULT_MODEL,
    KNOWN_MODELS,
    SherpaOnnxStreamingEngine,
    ensure_bpe_vocab,
    find_model_files,
    format_hotwords,
    resolve_model_dir,
)
from captions_desktop.protocol import EngineStatus
from captions_desktop.streaming import ContinuousStreamer, LiveStreamer, make_streamer


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class RecordingHub:
    def __init__(self) -> None:
        self.partials: list = []
        self.finals: list = []
        self.statuses: list = []

    def emit_partial(self, seg) -> None:
        self.partials.append(seg)

    def emit_final(self, seg) -> None:
        self.finals.append(seg)

    def emit_status(self, status) -> None:
        self.statuses.append(status)


class ScriptedEngine(StreamingASREngine):
    """Returns the next scripted StreamResult per accepted chunk."""

    def __init__(self, script, flush_text: str = "", status: str = "listening"):
        self.script = list(script)
        self.flush_text = flush_text
        self.status = status
        self.resets: list = []

    def load(self) -> EngineStatus:
        return EngineStatus(state=self.status, backend="fake")

    def reset(self, hotwords=None) -> None:
        self.resets.append(list(hotwords or []))

    def accept(self, samples) -> StreamResult:
        return self.script.pop(0) if self.script else StreamResult(text="")

    def flush(self) -> str:
        return self.flush_text


CHUNK = [0.0] * 1600  # 0.1 s at 16 kHz


def _drive(streamer: ContinuousStreamer, n: int) -> None:
    for _ in range(n):
        streamer._on_frame(CHUNK)


# ---------------------------------------------------------------------------
# ContinuousStreamer
# ---------------------------------------------------------------------------


def test_partials_only_when_text_changes_then_final_on_endpoint():
    hub = RecordingHub()
    engine = ScriptedEngine(
        [
            StreamResult(""),
            StreamResult("Hello"),
            StreamResult("Hello"),  # unchanged: no new partial
            StreamResult("Hello world"),
            StreamResult("Hello world.", endpoint=True),
        ]
    )
    s = ContinuousStreamer(hub, engine)
    _drive(s, 5)

    assert [p.text for p in hub.partials] == ["Hello", "Hello world", "Hello world."]
    assert [f.text for f in hub.finals] == ["Hello world."]
    # partials and the final share one stable id
    assert {p.id for p in hub.partials} == {hub.finals[0].id}
    # the segment starts at the chunk where text first appeared (0.1 s in)
    assert hub.finals[0].start == pytest.approx(0.1)
    assert hub.finals[0].end == pytest.approx(0.5)


def test_each_endpoint_opens_a_new_segment_with_new_id():
    hub = RecordingHub()
    engine = ScriptedEngine(
        [
            StreamResult("One.", endpoint=True),
            StreamResult(""),
            StreamResult("Two.", endpoint=True),
        ]
    )
    s = ContinuousStreamer(hub, engine)
    _drive(s, 3)
    assert [f.text for f in hub.finals] == ["One.", "Two."]
    assert hub.finals[0].id != hub.finals[1].id
    assert hub.finals[1].start == pytest.approx(0.2)


def test_endpoint_without_text_emits_nothing_but_resets():
    hub = RecordingHub()
    engine = ScriptedEngine([StreamResult("", endpoint=True)])
    s = ContinuousStreamer(hub, engine)
    _drive(s, 1)
    assert hub.finals == [] and hub.partials == []
    assert len(engine.resets) == 1


def test_dictionary_applies_from_next_segment():
    hub = RecordingHub()
    engine = ScriptedEngine(
        [StreamResult("Hi.", endpoint=True), StreamResult("Bye.", endpoint=True)]
    )
    s = ContinuousStreamer(hub, engine)
    s.set_dictionary(["Steve Gibson"])
    _drive(s, 1)
    s.set_dictionary(["Leo Laporte"])
    _drive(s, 1)
    assert engine.resets == [["Steve Gibson"], ["Leo Laporte"]]


def test_stop_flushes_open_segment_as_final():
    hub = RecordingHub()
    engine = ScriptedEngine([StreamResult("Trailing")], flush_text="Trailing words.")
    s = ContinuousStreamer(hub, engine)
    _drive(s, 1)
    s._on_stopped()
    assert [f.text for f in hub.finals] == ["Trailing words."]
    assert hub.finals[0].id == hub.partials[0].id


def test_stop_flush_with_no_prior_partial_still_finalizes():
    hub = RecordingHub()
    engine = ScriptedEngine([], flush_text="Late words.")
    s = ContinuousStreamer(hub, engine)
    s._on_stopped()
    assert [f.text for f in hub.finals] == ["Late words."]


def test_load_resets_with_current_dictionary():
    hub = RecordingHub()
    engine = ScriptedEngine([])
    s = ContinuousStreamer(hub, engine)
    s.set_dictionary(["Axient"])
    assert s._load().state == "listening"
    assert engine.resets == [["Axient"]]


def test_load_error_does_not_reset():
    engine = ScriptedEngine([], status="error")
    s = ContinuousStreamer(RecordingHub(), engine)
    assert s._load().state == "error"
    assert engine.resets == []


def test_make_streamer_picks_pipeline_by_engine_shape():
    hub = RecordingHub()
    assert isinstance(make_streamer(hub, ScriptedEngine([])), ContinuousStreamer)
    assert isinstance(make_streamer(hub, FasterWhisperEngine()), LiveStreamer)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_create_engine_sherpa_defaults_to_nemotron():
    engine = create_engine("sherpa-onnx")
    assert isinstance(engine, SherpaOnnxStreamingEngine)
    assert engine.model_name == DEFAULT_MODEL == "nemotron-en-560ms"
    assert engine.supports_hotwords


def test_create_engine_sherpa_passes_options():
    engine = create_engine(
        "sherpa-onnx", model="nemotron-en-80ms", decoding="greedy", hotwords_score=2.5
    )
    assert engine.model_name == "nemotron-en-80ms"
    assert not engine.supports_hotwords
    assert engine.hotwords_score == 2.5


def test_whisper_default_model_unchanged_when_model_none():
    assert create_engine("faster-whisper").model_name == "base.en"


# ---------------------------------------------------------------------------
# sherpa-onnx helpers
# ---------------------------------------------------------------------------


def test_format_hotwords_keeps_case_dedupes_and_strips_separators():
    terms = ["Steve Gibson", "  SpinRite ", "Steve Gibson", "TCP/IP", "", "Leo\tLaporte"]
    assert format_hotwords(terms) == "Steve Gibson/SpinRite/TCP IP/Leo Laporte"
    assert format_hotwords(None) == ""


def test_known_models_cover_all_nemotron_chunk_sizes():
    for ms in (80, 160, 560, 1120):
        assert f"nemotron-en-{ms}ms" in KNOWN_MODELS
    assert KNOWN_MODELS["nemotron-en"] == KNOWN_MODELS["nemotron-en-560ms"]


def _fake_model_dir(tmp_path: Path, int8: bool = True) -> Path:
    d = tmp_path / "m"
    d.mkdir()
    for part in ("encoder", "decoder", "joiner"):
        (d / f"{part}.onnx").write_bytes(b"")
        if int8:
            (d / f"{part}.int8.onnx").write_bytes(b"")
    (d / "tokens.txt").write_text("<unk> 0\n▁t 1\n▁th 2\nin 3\n<blk> 4\n", encoding="utf-8")
    return d


def test_find_model_files_prefers_int8(tmp_path):
    files = find_model_files(_fake_model_dir(tmp_path))
    assert files["encoder"].name == "encoder.int8.onnx"
    assert files["tokens"].name == "tokens.txt"


def test_find_model_files_falls_back_to_fp32(tmp_path):
    files = find_model_files(_fake_model_dir(tmp_path, int8=False))
    assert files["joiner"].name == "joiner.onnx"


def test_find_model_files_reports_missing(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(FileNotFoundError):
        find_model_files(d)


def test_ensure_bpe_vocab_generated_from_tokens(tmp_path):
    d = _fake_model_dir(tmp_path)
    vocab = ensure_bpe_vocab(d, d / "tokens.txt")
    assert vocab.read_text(encoding="utf-8").splitlines() == ["▁t\t-1", "▁th\t-2", "in\t-3"]
    # existing vocab is reused, not regenerated
    vocab.write_text("custom\t0\n", encoding="utf-8")
    assert ensure_bpe_vocab(d, d / "tokens.txt").read_text(encoding="utf-8") == "custom\t0\n"


def test_resolve_model_dir_accepts_directory(tmp_path):
    d = _fake_model_dir(tmp_path)
    assert resolve_model_dir(str(d)) == d


def test_resolve_model_dir_unknown_name_errors(tmp_path):
    with pytest.raises(FileNotFoundError, match="not a directory or a known model"):
        resolve_model_dir("no-such-model")


def test_resolve_model_dir_known_without_download(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTION_GURU_MODELS", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="not downloaded"):
        resolve_model_dir("nemotron-en-80ms", download=False)
    (tmp_path / KNOWN_MODELS["nemotron-en-80ms"]).mkdir()
    assert resolve_model_dir("nemotron-en-80ms", download=False).is_dir()


def test_resolve_model_dir_downloads_and_extracts(tmp_path, monkeypatch):
    import tarfile

    from captions_desktop.engines import sherpa_onnx as mod

    stem = KNOWN_MODELS["nemotron-en-160ms"]
    src = tmp_path / "src" / stem
    src.mkdir(parents=True)
    (src / "tokens.txt").write_text("a 0\n", encoding="utf-8")
    archive = tmp_path / "srv" / f"{stem}.tar.bz2"
    archive.parent.mkdir()
    with tarfile.open(archive, "w:bz2") as tar:
        tar.add(src, arcname=stem)

    cache = tmp_path / "cache"
    monkeypatch.setenv("CAPTION_GURU_MODELS", str(cache))
    monkeypatch.setattr(mod, "RELEASE_BASE", archive.parent.as_uri() + "/")
    got = resolve_model_dir("nemotron-en-160ms")
    assert got == cache / stem
    assert (got / "tokens.txt").read_text(encoding="utf-8") == "a 0\n"
    assert [p.name for p in cache.iterdir()] == [stem]  # temp dir cleaned up


def test_engine_load_reports_missing_model_as_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTION_GURU_MODELS", str(tmp_path))
    engine = SherpaOnnxStreamingEngine(model="nemotron-en-80ms", download=False)
    status = engine.load()
    assert status.state == "error"
    assert status.backend == "sherpa-onnx"


def test_engine_calls_are_safe_before_load():
    engine = SherpaOnnxStreamingEngine()
    engine.reset(["x"])
    assert engine.accept(CHUNK) == StreamResult(text="")
    assert engine.flush() == ""


# ---------------------------------------------------------------------------
# Optional end-to-end (needs sherpa-onnx + a downloaded model)
# ---------------------------------------------------------------------------

MODEL_DIR = os.environ.get("CAPTION_GURU_SHERPA_MODEL")


@pytest.mark.skipif(not MODEL_DIR, reason="set CAPTION_GURU_SHERPA_MODEL to a model dir")
def test_real_model_streams_test_wav():
    import numpy as np

    wav = Path(MODEL_DIR) / "test_wavs" / "0.wav"
    with wave.open(str(wav)) as w:
        sr = w.getframerate()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    audio = audio.astype(np.float32) / 32768

    hub = RecordingHub()
    engine = SherpaOnnxStreamingEngine(model=MODEL_DIR, num_threads=2)
    s = ContinuousStreamer(hub, engine, sample_rate=sr)
    s.set_dictionary(["nightfall"])
    assert s._load().state == "listening"
    step = int(sr * 0.032)
    for i in range(0, len(audio), step):
        s._on_frame(audio[i : i + step])
    s._on_stopped()

    text = " ".join(f.text for f in hub.finals).lower()
    assert "yellow lamps" in text and "brothels" in text
    assert len(hub.partials) >= 3  # words arrived incrementally
