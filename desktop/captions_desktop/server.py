"""FastAPI server: serves the shared frontend, streams captions over a
multi-client WebSocket, and exposes the history endpoint (export in v1,
audience scrollback in v2)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .export import export_transcript
from .hub import CaptionHub
from .protocol import (
    ConfigMessage,
    ControlCommand,
    HistoryMessage,
    RequestHistoryMessage,
    SetConfigMessage,
    SetDictionaryMessage,
    dump_message,
    parse_client_message,
)
from .control import (
    ENGINE_LABELS,
    EngineManager,
    installed_engines,
    list_input_devices,
    suggested_models,
)
from .settings import Settings
from .streaming import Controller


def build_app(
    hub: CaptionHub,
    controller: Controller,
    web_dir: Optional[Path] = None,
    autostart: bool = True,
    settings: Optional[Settings] = None,
    screens: Optional[list[dict]] = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hub.bind_loop(asyncio.get_running_loop())
        if autostart:
            controller.start()
        yield
        controller.stop()

    app = FastAPI(title="Caption Guru", lifespan=lifespan)

    if settings is not None:
        hub.on_config = lambda cfg: settings.set_display(cfg.model_dump(by_alias=True))

    def set_dictionary(terms: list[str]) -> list[str]:
        terms = [t.strip() for t in terms if t and t.strip()]
        controller.set_dictionary(terms)
        if settings is not None:
            settings.set_dictionary(terms)
        state["dictionary"] = terms
        return terms

    state: dict[str, Any] = {"dictionary": list(getattr(controller, "dictionary", []) or [])}

    # --- control page API ------------------------------------------------------

    @app.get("/api/state")
    async def api_state() -> JSONResponse:
        info = controller.info() if isinstance(controller, EngineManager) else {
            "demo": True, "running": True, "engine": "demo", "model": "demo", "mic": None,
        }
        engines = installed_engines()
        launch = settings.launch if settings is not None else {}
        return JSONResponse({
            **info,
            "dictionary": state["dictionary"],
            "engines": [{"id": e, "label": ENGINE_LABELS.get(e, e)} for e in engines],
            "models": {e: suggested_models(e) for e in engines},
            "mics": list_input_devices(),
            "screens": screens or [],
            "monitor": launch.get("monitor", info.get("monitor", 0)),
            "settingsPath": str(settings.path) if settings is not None else None,
        })

    @app.post("/api/dictionary")
    async def api_dictionary(body: dict = Body(...)) -> JSONResponse:
        terms = body.get("terms")
        if not isinstance(terms, list):
            raise HTTPException(400, "terms must be a list")
        return JSONResponse({"dictionary": set_dictionary([str(t) for t in terms])})

    @app.post("/api/engine")
    async def api_engine(body: dict = Body(...)) -> JSONResponse:
        if not isinstance(controller, EngineManager) or controller.demo:
            raise HTTPException(409, "engine can't be changed in demo mode")
        changes: dict[str, Any] = {}
        if "engine" in body:
            engine = str(body["engine"] or "auto")
            if engine not in installed_engines():
                raise HTTPException(400, f"engine {engine!r} is not installed")
            changes["engine"] = engine
        if "model" in body:
            changes["model"] = str(body["model"]).strip() or None
        if "mic" in body:
            mic = body["mic"]
            changes["mic"] = int(mic) if mic not in (None, "") else None
        controller.restart(**changes)
        if settings is not None:
            settings.update_launch(**{k: v for k, v in changes.items()})
        return JSONResponse(controller.info())

    @app.post("/api/launch")
    async def api_launch(body: dict = Body(...)) -> JSONResponse:
        if settings is None:
            raise HTTPException(409, "settings are not being saved")
        if "monitor" in body:
            settings.update_launch(monitor=int(body["monitor"]))
        return JSONResponse({"launch": settings.launch})

    @app.post("/api/display/reset")
    async def api_display_reset() -> JSONResponse:
        cfg = hub.reset_config()
        return JSONResponse({"config": cfg.model_dump(by_alias=True)})

    @app.get("/history")
    async def history(since: Optional[float] = None) -> JSONResponse:
        segs = hub.history(since)
        return JSONResponse(
            {"segments": [s.model_dump(by_alias=True, exclude_none=True) for s in segs]}
        )

    @app.get("/export")
    async def export(format: str = "txt") -> Response:
        body, mime, filename = export_transcript(hub.history(), format)
        return Response(
            content=body,
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        await sock.accept()
        q = hub.subscribe()

        # Catch a freshly connected client up: config + recent history.
        for m in hub.snapshot_for_new_client():
            await sock.send_text(dump_message(m))

        async def reader() -> None:
            try:
                while True:
                    data = await sock.receive_text()
                    try:
                        _handle_client(hub, controller, data, q, set_dictionary)
                    except Exception as exc:
                        # A rejected change (e.g. an out-of-range value) must not
                        # drop the connection; resync this client instead.
                        print(f"  ignored control message: {exc.__class__.__name__}: {exc}".splitlines()[0])
                        try:
                            q.put_nowait(ConfigMessage(config=hub.config))
                        except asyncio.QueueFull:
                            pass
            except WebSocketDisconnect:
                pass

        async def writer() -> None:
            try:
                while True:
                    msg = await q.get()
                    await sock.send_text(dump_message(msg))
            except (WebSocketDisconnect, RuntimeError):
                pass

        rt = asyncio.create_task(reader())
        wt = asyncio.create_task(writer())
        try:
            _, pending = await asyncio.wait(
                {rt, wt}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
        finally:
            hub.unsubscribe(q)

    if web_dir is not None:
        # Mounted last so /ws and /history take precedence.
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    else:

        @app.get("/")
        async def root() -> HTMLResponse:
            return HTMLResponse(
                "<h1>Caption Guru server</h1><p>Frontend not built. Run "
                "<code>pnpm --filter @captions/display build</code>, then reload. "
                "WebSocket: <code>/ws</code> · History: <code>/history</code></p>"
            )

    return app


def _handle_client(
    hub: CaptionHub,
    controller: Controller,
    data: str,
    q: "asyncio.Queue",
    set_dictionary=None,
) -> None:
    try:
        msg = parse_client_message(data)
    except Exception:
        return

    if isinstance(msg, SetConfigMessage):
        hub.set_config(msg.config)
    elif isinstance(msg, ControlCommand):
        if msg.command == "clear":
            hub.clear()
        elif msg.command == "start":
            controller.start()
        elif msg.command == "stop":
            controller.stop()
    elif isinstance(msg, RequestHistoryMessage):
        # Reply only to the requesting client.
        try:
            q.put_nowait(HistoryMessage(segments=hub.history(msg.since)))
        except asyncio.QueueFull:
            pass
    elif isinstance(msg, SetDictionaryMessage):
        if set_dictionary is not None:
            set_dictionary(msg.terms)
        else:
            controller.set_dictionary(msg.terms)
