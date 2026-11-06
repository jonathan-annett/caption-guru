# PyInstaller spec for the caption-guru desktop app (one-folder bundle).
# Build:  pyinstaller packaging/captions.spec   (run from the desktop/ dir)
#
# Bundles the Python runtime + captions_desktop + the built display frontend
# (as `web/`) + native deps. torch is excluded: mlx-whisper declares it but does
# not load it for transcription, and it would add ~2 GB per artifact.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

spec_dir = Path(SPECPATH).resolve()
repo_root = spec_dir.parents[1]  # .../desktop/packaging -> repo root
web_dist = repo_root / "packages" / "display" / "dist"

datas = []
binaries = []
hiddenimports = []

# Bundle the built frontend, served by the desktop server.
if web_dist.is_dir():
    datas.append((str(web_dist), "web"))
else:
    raise SystemExit(
        f"Frontend not built at {web_dist}. Run: pnpm --filter @captions/display build"
    )

# Native-heavy packages PyInstaller can't fully trace on its own. Missing ones
# (e.g. mlx only on Apple Silicon, ctranslate2 only where faster-whisper is
# installed) are skipped gracefully so one spec works across platforms.
for pkg in [
    "ctranslate2",
    "faster_whisper",
    "av",
    "mlx",
    "mlx_whisper",
    # Streaming engine. sherpa-onnx + sherpa-onnx-core both install into
    # sherpa_onnx/lib (extension + onnxruntime + c-api libs, linked by rpath),
    # so collect the whole package to keep them side by side.
    "sherpa_onnx",
    "sounddevice",
    "tokenizers",
    "tiktoken",
    "huggingface_hub",
    "numba",
    "scipy",
    "webview",
]:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    [str(spec_dir / "entry.py")],
    pathex=[str(repo_root / "desktop")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "onnxruntime",
        "matplotlib",
        "tkinter",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="caption-guru",
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="caption-guru",
)
