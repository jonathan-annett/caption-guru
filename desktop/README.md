# captions-desktop

Desktop build of [Caption Guru](../README.md): a Python app that captures audio,
runs on-device ASR (faster-whisper / WhisperX, with an Apple-Silicon GPU backend),
serves the shared web display + control UI over a local server, and shows the
caption surface fullscreen via `pywebview` (HDMI output) with a Chrome-kiosk CLI
fallback.

Audio never leaves the machine.

## Install (dev)

> **Python version:** the real ASR backend (`faster-whisper`/CTranslate2) needs
> **Python 3.11 or 3.12** — there are no wheels for 3.14 yet. The server itself
> (and the `--demo` mode) runs on any 3.11+. Create the venv with a supported
> interpreter, e.g. via [uv](https://docs.astral.sh/uv/): `uv venv --python 3.12`.

```bash
cd desktop
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[server,audio,asr,desktop,dev]"
```

On Apple Silicon, add the GPU backend (`faster-whisper` is CPU-only on macOS):

```bash
pip install -e ".[server,audio,asr,mlx,desktop,dev]"
```

Extras: `server` (FastAPI + numpy), `audio` (sounddevice/PortAudio mic capture),
`asr` (faster-whisper), `desktop` (pywebview), `dev` (pytest + httpx). For just
the server + tests (no PortAudio / CTranslate2): `pip install -e ".[server,dev]"`.

The server serves the built shared frontend, so build it once from the repo root:

```bash
pnpm --filter @captions/display build
```

## Run

```bash
captions serve                       # live captions + fullscreen display window
captions serve --model small.en      # pick a model (tiny/base/small .en)
captions serve --engine mlx          # force the Apple-Silicon GPU backend
captions serve --engine faster-whisper --device cuda
captions serve --demo                # scripted captions, no mic/ASR (great for testing)
```

`--engine auto` (default) uses **MLX** (Apple GPU) on Apple Silicon when
installed, otherwise **faster-whisper** (CPU, or CUDA on NVIDIA). `--model`
accepts a short name (`base.en`) or a full HF repo; for MLX the short name maps
to `mlx-community/whisper-<model>-mlx`.

By default `serve` opens the display **fullscreen via pywebview** (the HDMI output
to feed your switcher). Display options:

```bash
captions serve --list-monitors                 # list monitors, then exit
captions serve --monitor 1                      # put the fullscreen output on monitor 1 (HDMI)
captions serve --background chroma              # green key bg (default #00b140)
captions serve --background chroma --bg-color "#00ff00"
captions serve --background transparent         # transparent (browser-source style)
captions serve --kiosk                          # use Chrome kiosk instead of pywebview
captions serve --windowed                       # don't go fullscreen
captions serve --no-open                         # server only; open the URL yourself
```

The window just renders the page; the background (solid/chroma/transparent) is
painted from config, so HDMI capture vs keying is a flag, not a rebuild. The same
page is also a LAN **browser source**: `http://<host>:8765/?source=ws`. Captions
stream over `/ws`; the history/scrollback substrate is at `/history`.

> pywebview is needed for the fullscreen window (`pip install -e ".[desktop]"`).
> Without it, `serve` automatically falls back to Chrome kiosk.

## Settings page

`serve` opens a **Settings** window next to the captions (on another monitor
when there is one; `--no-settings-window` to skip it). It's also at
`http://127.0.0.1:8765/control.html` while the app runs. From there you can:

- **Position & look:** top / middle / bottom, alignment, distance from the edge,
  side margins, number of captions on screen, size, font, colour, ALL CAPS,
  live (partial) words, and the background (solid, chroma key, transparent),
  with a live preview of the actual output.
- **Captions:** start / stop / clear, custom words, transcript export.
- **Engine:** microphone, engine and model; *Apply* restarts captioning with the
  new choice without restarting the app.
- **Output:** which monitor the captions go on (next launch).

Everything is saved automatically (`%APPDATA%\CaptionGuru\settings.json` on
Windows, `~/Library/Application Support/CaptionGuru/` on macOS,
`~/.config/caption-guru/` on Linux; override with `--settings` or
`$CAPTION_GURU_SETTINGS`) and used next launch. Command-line flags still win
for that run, e.g. `--engine`, `--mic`, `--monitor`, `--background`,
`--dictionary`; `--fresh` ignores saved settings.

## Streaming engine (sherpa-onnx + Nemotron)

`--engine sherpa-onnx` runs a **true streaming** model, by default NVIDIA's
[Nemotron Speech Streaming EN 0.6B](https://k2-fsa.github.io/sherpa/onnx/nemo/nemotron-streaming.html)
via [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx). The Whisper engines
wait for a pause, then re-decode the whole utterance; this one keeps decoder
state, so words appear **as they're spoken**, and it detects its own endpoints.
Output includes punctuation and capitalization.

```bash
pip install -e ".[server,audio,streaming,desktop]"
captions serve --engine sherpa-onnx                       # nemotron-en-560ms, auto-downloaded
captions serve --engine sherpa-onnx --model nemotron-en-160ms   # lower latency
captions serve --engine sherpa-onnx --model /path/to/model-dir  # any streaming transducer
captions serve --engine sherpa-onnx --dictionary "@terms.txt" --hotwords-score 2.0
```

- **Models.** Short names `nemotron-en-80ms`, `-160ms`, `-560ms` (default) and
  `-1120ms` select the chunk size: smaller is lower latency, larger is more
  accurate. Each is a ~460 MB one-time download into
  `~/.cache/caption-guru/models` (override with `CAPTION_GURU_MODELS`). A path
  to a directory with `encoder*.onnx`, `decoder*.onnx`, `joiner*.onnx` and
  `tokens.txt` also works.
- **Dictionary / hotwords.** Needs the default beam decoding (`--decoding auto`).
  Write terms in their **natural case** ("Steve Gibson"): the model's vocabulary
  is mixed case, and upper-casing them, as older sherpa docs suggest, makes the
  model emit upper-case garbage. Start `--hotwords-score` around 1.5–3; too high
  forces the terms in everywhere. Edits from the control page apply from the
  next caption segment, no model reload. `--decoding greedy` is fastest but
  ignores the dictionary.
- **CPU vs GPU.** The PyPI `sherpa-onnx` wheel is CPU-only; use `--threads` to
  tune. For an NVIDIA GPU, install sherpa-onnx's CUDA build (see its install
  docs) and pass `--device cuda`.
- **Tests.** The unit tests run without the model. To also run the real-model
  check: `CAPTION_GURU_SHERPA_MODEL=/path/to/model-dir pytest -k real`.

## Packaging / releases

Bundled with PyInstaller into a one-folder app, zipped per platform. `torch` is
excluded (mlx-whisper declares it but doesn't use it for transcription), saving
~2 GB per build.

Build locally:

```bash
pnpm --filter @captions/display build          # from repo root: build the frontend
cd desktop
uv pip install -e ".[server,audio,asr,mlx,desktop,package]"
python packaging/build.py                       # -> dist/caption-guru-<target>.zip
```

Cut a release: push a tag and CI builds + attaches the per-platform zips to a
GitHub Release (`.github/workflows/release.yml`, matrix: Windows x64, macOS
Intel, macOS Apple Silicon, Linux x64):

```bash
git tag v0.1.0 && git push origin v0.1.0
```

Per platform: macOS arm64 includes the **MLX** GPU backend; Intel mac / Windows
use faster-whisper; the Linux artifact is server + browser/kiosk (no native
window). All include the bundled frontend.

> **macOS Gatekeeper:** the bundles are unsigned, so first launch is blocked.
> Right-click → Open, or run `xattr -dr com.apple.quarantine caption-guru/`.
> Code signing/notarization is a future addition.
