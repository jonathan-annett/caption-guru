# What's New

Notable changes per release. (Format loosely follows Keep a Changelog.)

## Unreleased

**Release builds**
- The downloadable desktop zips now include the **sherpa-onnx streaming
  engine** on Windows, macOS (Apple Silicon + Intel) and Linux.
- New `captions doctor` command reports which engines and components an install
  can actually load; CI runs it on each frozen bundle so a missing native
  library fails the build instead of reaching users.

**Desktop — true streaming engine**
- New **`--engine sherpa-onnx`**: streaming ASR via sherpa-onnx, defaulting to
  NVIDIA **Nemotron Speech Streaming EN 0.6B**. Words appear as they're spoken
  instead of after a pause, with built-in endpointing, punctuation and
  capitalization. Models auto-download by short name (`nemotron-en-80ms` …
  `-1120ms`) or load from a directory.
- **Dictionary works as hotwords** (beam decoding), applied per segment so live
  edits take effect without a reload. A `bpe.vocab` is generated from the model's
  tokens, since the Nemotron archives don't ship one.
- New `StreamingASREngine` interface and `ContinuousStreamer` pipeline alongside
  the existing utterance engines; the Whisper path is unchanged.

**Desktop — settings page**
- A **Settings window** opens alongside the captions (also at `/control.html`):
  position, alignment, edge/side margins, lines, size, font, colour, background,
  with a live preview of the real output; start/stop/clear, custom words,
  export; and switch microphone, engine or model without restarting the app.
- Settings are **saved** and reused next launch (CLI flags still override).
- Display config gains `marginX` / `marginY` safe-area insets (defaults match the
  old fixed padding).

**Windows installer**
- `desktop/packaging/windows/install.cmd` installs everything from scratch
  (Python, Node, the app) for any branch or tag, so beta testers can try
  unreleased branches. Adds Start Menu / Desktop shortcuts and an uninstaller.

**PWA — caption quality**
- **Hallucination suppression.** Whisper used to invent text on silence/non-speech
  (repeated symbols like `>>>>`/`[`, or phantom phrases like "I'm sorry"). Now a
  **no-speech gate** skips silent/near-silent audio before it's ever transcribed,
  a **degenerate-output filter** drops symbol-only junk, and the on-air display
  **never renders blank lines**.
- Removed generation params (`no_repeat_ngram_size` / `repetition_penalty`) that
  were derailing Whisper into single-token output (`"["`, `"W"`) on real speech.

**PWA — model picker**
- Dropped **base.en** (consistently the weakest on WebGPU); default is now **small.en**.
- Each model shows its **one-time download size** in the picker, and your
  **selected model + microphone are remembered** across reloads.
- **large-v3-turbo** is available behind **`?experimental=1`**: it transcribes
  accurately but is far too slow for real-time on typical in-browser WebGPU, so
  it's hidden by default (best on a strong GPU). Multilingual models now get the
  required `language`/`task`; the `.en` models are unchanged.

## v0.1.0 — 2026-06-29

First public release.

**PWA** (live at https://caption.guru)
- On-device live captions in the browser — transformers.js, **WebGPU** with WASM fallback.
- Operator control page (mic + model selection) and a fullscreen on-air display.
- Model-download progress UI (percent / bytes / elapsed / ETA / cancel) with a one-time-download explainer.
- Same-origin model proxy (`/hf`) so model loading never hits cross-origin CORS.

**Desktop** (Windows / Linux / macOS)
- Python app: FastAPI server + **faster-whisper** (CPU/CUDA), with an **MLX** GPU backend auto-selected on Apple Silicon.
- Fullscreen HDMI output via pywebview (monitor selection) + Chrome-kiosk fallback.
- Backgrounds: solid / chroma-key / transparent. Live captions stream over `/ws`; `/history` substrate.

**Shared**
- Caption protocol (TS + Python), reusable on-air display surface.
- Transcript export: `txt` / `SRT` / `VTT`.
- Custom dictionary (faster-whisper hotwords; PWA fuzzy correction).

**Packaging**
- PyInstaller bundles, per-platform GitHub Release zips. Models download on first run (kept out of the bundle).
- Artifacts: Windows x64, Linux x64, macOS Apple Silicon. _macOS Intel ships separately as `v0.1.0-intel` (scarce Intel runners)._

[Releases](https://github.com/jonathan-annett/caption-guru/releases)
