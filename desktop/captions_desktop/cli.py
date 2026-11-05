"""`captions` command-line entry point."""

from __future__ import annotations

import argparse
import threading
import time
from typing import Optional, Sequence

from .hub import CaptionHub
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
        default="auto",
        choices=["auto", "faster-whisper", "mlx", "sherpa-onnx"],
        help=(
            "ASR backend (auto: MLX on Apple Silicon, else faster-whisper; "
            "sherpa-onnx: true streaming, Nemotron by default)"
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
    serve.add_argument("--monitor", type=int, default=0, help="monitor index (HDMI out)")
    serve.add_argument("--windowed", action="store_true", help="don't go fullscreen")
    serve.add_argument("--kiosk", action="store_true", help="use Chrome kiosk fallback")
    serve.add_argument("--no-open", action="store_true", help="server only, no window")
    serve.add_argument("--list-monitors", action="store_true", help="list monitors + exit")

    # Background (painted by the page; default solid black suits HDMI capture)
    serve.add_argument(
        "--background", choices=["solid", "chroma", "transparent"], default=None
    )
    serve.add_argument("--bg-color", default=None, help="hex color for solid/chroma")

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        _serve(args)


def _serve(args: argparse.Namespace) -> None:
    from .server import build_app
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

    hub = CaptionHub()
    _apply_background(hub, args)

    if args.demo:
        controller = MockProducer(hub)
        engine_desc = "mock (demo)"
    else:
        from .engines import create_engine

        options = {}
        if args.engine == "sherpa-onnx":
            options = dict(
                decoding=args.decoding,
                hotwords_score=args.hotwords_score,
                num_threads=args.threads,
            )
        engine = create_engine(
            args.engine, model=args.model, device=args.device, **options
        )
        controller = make_streamer(hub, engine, device=args.mic)
        model_name = getattr(engine, "model_name", args.model)
        engine_desc = f"{engine.__class__.__name__} ({model_name})"

    terms = _parse_dictionary(args.dictionary)
    if terms:
        controller.set_dictionary(terms)
        if getattr(controller, "engine", None) is not None and not getattr(
            controller.engine, "supports_hotwords", True
        ):
            print("  note: --decoding greedy ignores the dictionary; use beam")

    web_dir = find_web_dir(args.web)
    app = build_app(hub, controller, web_dir=web_dir)

    transparent = args.background == "transparent"
    base = f"http://{args.host}:{args.port}"
    url = f"{base}/?source=ws"

    print("Caption Guru desktop server")
    print(f"  engine:   {engine_desc}")
    print(f"  frontend: {web_dir or '(not built — see / for help)'}")
    print(f"  display:  {url}")
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
            proc = window.launch_chrome_kiosk(url, monitor=args.monitor)
            if proc is None:
                print("  No Chrome found. Open the display URL manually.")
            _block_until_interrupt(thread)
            if proc is not None:
                proc.terminate()
        else:
            window.run_webview(
                url,
                fullscreen=not args.windowed,
                monitor=args.monitor,
                transparent=transparent,
            )
    finally:
        server.should_exit = True
        thread.join(timeout=3.0)


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
