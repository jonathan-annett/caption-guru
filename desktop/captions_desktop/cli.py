"""`captions` command-line entry point."""

from __future__ import annotations

import argparse
import threading
import time
from typing import Optional, Sequence

from pathlib import Path

from .hub import CaptionHub
from .settings import Settings
from .web import find_web_dir


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="captions")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="run the caption server + display")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument(
        "--model",
        default=None,
        help=(
            "model name, HF repo or model dir (default: base.en for Whisper "
            "engines, nemotron-en-560ms for sherpa-onnx)"
        ),
    )
    serve.add_argument(
        "--engine",
        default=None,
        choices=["auto", "faster-whisper", "mlx", "sherpa-onnx"],
        help=(
            "ASR backend (auto: MLX on Apple Silicon, else faster-whisper; "
            "sherpa-onnx: true streaming, Nemotron by default). Default: the "
            "last one chosen on the settings page, else auto"
        ),
    )
    serve.add_argument(
        "--device", default="auto", help="auto|cpu|cuda (faster-whisper, sherpa-onnx)"
    )

    # sherpa-onnx streaming options
    serve.add_argument(
        "--decoding",
        default="auto",
        choices=["auto", "beam", "greedy"],
        help="sherpa-onnx: beam (needed for --dictionary) or greedy (fastest)",
    )
    serve.add_argument(
        "--hotwords-score",
        type=float,
        default=1.5,
        help="sherpa-onnx: dictionary boost per token (try 1.5-3; too high garbles)",
    )
    serve.add_argument(
        "--threads", type=int, default=4, help="sherpa-onnx: CPU inference threads"
    )
    serve.add_argument("--mic", type=int, default=None, help="input device index")
    serve.add_argument("--demo", action="store_true", help="mock captions, no audio/ASR")
    serve.add_argument("--web", default=None, help="path to built frontend dir")
    serve.add_argument(
        "--dictionary",
        default=None,
        help="custom terms to bias toward: comma-separated, or @path/to/file",
    )

    # Display output
    serve.add_argument(
        "--monitor", type=int, default=None, help="monitor index (HDMI out; default: saved, else 0)"
    )
    serve.add_argument("--windowed", action="store_true", help="don't go fullscreen")
    serve.add_argument("--kiosk", action="store_true", help="use Chrome kiosk fallback")
    serve.add_argument("--no-open", action="store_true", help="server only, no window")
    serve.add_argument("--list-monitors", action="store_true", help="list monitors + exit")
    serve.add_argument(
        "--no-settings-window",
        action="store_true",
        help="don't open the settings window alongside the display",
    )
    serve.add_argument(
        "--settings",
        default=None,
        help="settings file (default: per-user config dir; see /control.html)",
    )
    serve.add_argument(
        "--fresh", action="store_true", help="ignore saved settings for this run"
    )

    # Background (painted by the page; default solid black suits HDMI capture)
    serve.add_argument(
        "--background", choices=["solid", "chroma", "transparent"], default=None
    )
    serve.add_argument("--bg-color", default=None, help="hex color for solid/chroma")

    doctor = sub.add_parser(
        "doctor", help="check which engines and components this install can load"
    )
    doctor.add_argument(
        "--require",
        default="",
        help="comma-separated components that must load (exit 1 otherwise), "
        "e.g. sherpa-onnx,faster-whisper",
    )

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        _serve(args)
    elif args.cmd == "doctor":
        raise SystemExit(_doctor([r for r in args.require.split(",") if r.strip()]))


def _serve(args: argparse.Namespace) -> None:
    from .server import build_app
    from .control import EngineManager
    from .streaming import MockProducer, make_streamer
    from . import window

    if args.list_monitors:
        screens = window.list_screens()
        if not screens:
            print("No monitor info (pywebview not installed).")
        for s in screens:
            print(
                f"  [{s['index']}] {s['width']}x{s['height']} @ ({s['x']},{s['y']})"
            )
        return

    settings = Settings(Path(args.settings) if args.settings else None)
    if args.fresh:
        settings.data = {}
    launch = settings.launch

    hub = CaptionHub()
    if settings.display:
        try:
            hub.set_config(settings.display)
        except Exception as exc:
            print(f"  settings: ignoring saved display settings ({exc})")
    _apply_background(hub, args)  # CLI flags override saved settings for this run

    options = _resolve_launch_options(args, launch)

    def build(opts: dict):
        if args.demo:
            return MockProducer(hub)
        from .engines import create_engine

        extra = {}
        if opts["engine"] == "sherpa-onnx":
            extra = dict(
                decoding=args.decoding,
                hotwords_score=args.hotwords_score,
                num_threads=args.threads,
            )
        engine = create_engine(
            opts["engine"], model=opts.get("model"), device=args.device, **extra
        )
        if args.decoding == "greedy" and opts["engine"] == "sherpa-onnx":
            print("  note: --decoding greedy ignores the dictionary; use beam")
        return make_streamer(hub, engine, device=opts.get("mic"))

    controller = EngineManager(hub, build, options, demo=args.demo)
    terms = _parse_dictionary(args.dictionary) if args.dictionary else settings.dictionary
    if terms:
        controller.set_dictionary(terms)

    screens = window.list_screens()
    monitor = args.monitor if args.monitor is not None else launch.get("monitor", 0)
    if not isinstance(monitor, int) or not (0 <= monitor < max(len(screens), 1)):
        monitor = 0

    web_dir = find_web_dir(args.web)
    app = build_app(hub, controller, web_dir=web_dir, settings=settings, screens=screens)

    transparent = hub.config.background.kind == "transparent"
    base = f"http://{args.host}:{args.port}"
    url = f"{base}/?source=ws"
    settings_url = f"{base}/control.html"
    info = controller.info()

    print("Caption Guru desktop server")
    print(f"  engine:   {'mock (demo)' if args.demo else info['engine'] + ' (' + info['model'] + ')'}")
    print(f"  frontend: {web_dir or '(not built — see / for help)'}")
    print(f"  display:  {url}")
    print(f"  settings: {settings_url}")
    print(f"            saved in {settings.path}")
    print(f"  ws:       {base}/ws   history: {base}/history")

    if args.no_open:
        import uvicorn

        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
        return

    # Window needs the main thread (esp. macOS), so run uvicorn in a thread.
    server, thread = _run_server_threaded(app, args.host, args.port)
    try:
        if args.kiosk or not window.webview_available():
            if not args.kiosk:
                print("  (pywebview not installed — using Chrome kiosk)")
            proc = window.launch_chrome_kiosk(url, monitor=monitor)
            if proc is None:
                print("  No Chrome found. Open the display URL manually.")
            if not args.no_settings_window:
                import webbrowser

                webbrowser.open(settings_url)
            _block_until_interrupt(thread)
            if proc is not None:
                proc.terminate()
        else:
            window.run_webview(
                url,
                fullscreen=not args.windowed,
                monitor=monitor,
                transparent=transparent,
                settings_url=None if args.no_settings_window else settings_url,
            )
    finally:
        settings.flush()
        server.should_exit = True
        thread.join(timeout=3.0)


def _resolve_launch_options(args: argparse.Namespace, launch: dict) -> dict:
    """Engine/model/mic: CLI flag, else what was last picked on the settings page."""
    from .control import installed_engines, list_input_devices

    saved_engine = launch.get("engine")
    engine = args.engine or saved_engine or "auto"
    if not args.engine and engine not in installed_engines():
        print(f"  note: saved engine {engine!r} isn't installed; using auto")
        engine = "auto"

    model = args.model
    if model is None and engine == saved_engine:
        model = launch.get("model")

    mic = args.mic
    if mic is None and isinstance(launch.get("mic"), int):
        mic = launch["mic"]
        if mic not in {d["index"] for d in list_input_devices()}:
            print(f"  note: saved microphone #{mic} not found; using the default")
            mic = None
    return {"engine": engine, "model": model, "mic": mic}


# component -> module to import. Importing loads the native libraries, which
# is what breaks in a frozen bundle when a package's .so/.dylib/.dll is missing.
_COMPONENTS = {
    "server": "uvicorn",
    "faster-whisper": "faster_whisper",
    "ctranslate2": "ctranslate2",
    "sherpa-onnx": "sherpa_onnx",
    "mlx": "mlx_whisper",
    "audio": "sounddevice",
    "window": "webview",
}


def _doctor(require: list[str]) -> int:
    import importlib
    import platform
    import sys

    print("Caption Guru doctor")
    print(f"  python:   {sys.version.split()[0]} ({platform.system()} {platform.machine()})")
    print(f"  frozen:   {bool(getattr(sys, 'frozen', False))}")
    print(f"  frontend: {find_web_dir() or 'NOT FOUND'}")
    failed = []
    for name, module in _COMPONENTS.items():
        try:
            mod = importlib.import_module(module)
            version = getattr(mod, "__version__", "")
            print(f"  ok    {name:15} {version}")
        except BaseException as exc:  # some native libs raise OSError, not ImportError
            msg = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
            print(f"  --    {name:15} {msg}")
            failed.append(name)
    missing = [r for r in require if r not in _COMPONENTS or r in failed]
    if missing:
        print(f"  REQUIRED BUT NOT LOADABLE: {', '.join(missing)}")
        return 1
    return 0


def _parse_dictionary(value: Optional[str]) -> list[str]:
    if not value:
        return []
    if value.startswith("@"):
        with open(value[1:], encoding="utf-8") as fh:
            raw = fh.read()
    else:
        raw = value
    return [t.strip() for t in raw.replace("\n", ",").split(",") if t.strip()]


def _apply_background(hub: CaptionHub, args: argparse.Namespace) -> None:
    if not args.background:
        return
    if args.background == "transparent":
        hub.set_config({"background": {"kind": "transparent"}})
    elif args.background == "chroma":
        hub.set_config(
            {"background": {"kind": "chroma", "color": args.bg_color or "#00b140"}}
        )
    else:  # solid
        hub.set_config(
            {"background": {"kind": "solid", "color": args.bg_color or "#000000"}}
        )


def _run_server_threaded(app, host: str, port: int):
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started and thread.is_alive():
        time.sleep(0.05)
    return server, thread


def _block_until_interrupt(thread: threading.Thread) -> None:
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
