import argparse
import json

from fastapi.testclient import TestClient

from captions_desktop.control import EngineManager
from captions_desktop.hub import CaptionHub
from captions_desktop.server import build_app
from captions_desktop.settings import Settings
from captions_desktop.streaming import MockProducer


class FakeProducer:
    def __init__(self, opts):
        self.opts = dict(opts)
        self.started = 0
        self.stopped = 0
        self.dictionary = []

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def set_dictionary(self, terms):
        self.dictionary = terms


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "sub" / "settings.json"
    s = Settings(path, debounce=0)
    s.set_dictionary(["Steve Gibson"])
    s.update_launch(engine="sherpa-onnx", mic=3, bogus=1)
    s.update_launch(mic=None)
    again = Settings(path)
    assert again.dictionary == ["Steve Gibson"]
    assert again.launch == {"engine": "sherpa-onnx"}


def test_settings_ignores_corrupt_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert Settings(path).data == {}


def test_config_changes_are_saved_and_snapshot_includes_status(tmp_path):
    settings = Settings(tmp_path / "s.json", debounce=0)
    hub = CaptionHub()
    mgr = EngineManager(hub, lambda o: MockProducer(hub), {}, demo=True)
    app = build_app(hub, mgr, web_dir=None, autostart=True, settings=settings)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "setConfig", "config": {"position": "top", "marginY": 10}})
            for _ in range(50):
                m = ws.receive_json()
                if m["type"] == "config" and m["config"]["position"] == "top":
                    break
            assert m["config"]["marginY"] == 10
            ws.send_json({"type": "setDictionary", "terms": ["  Caption Guru ", ""]})
        # A late joiner is told the engine status.
        with client.websocket_connect("/ws") as ws:
            types = [ws.receive_json()["type"] for _ in range(3)]
            assert types == ["config", "history", "status"]
        r = client.get("/api/state")
        assert r.json()["dictionary"] == ["Caption Guru"]
        assert r.json()["demo"] is True
        assert client.post("/api/engine", json={"engine": "auto"}).status_code == 409
    saved = json.loads((tmp_path / "s.json").read_text())
    assert saved["display"]["position"] == "top"
    assert saved["display"]["marginY"] == 10
    assert saved["dictionary"] == ["Caption Guru"]


def test_engine_switch_rebuilds_producer_and_saves_launch(tmp_path):
    settings = Settings(tmp_path / "s.json", debounce=0)
    hub = CaptionHub()
    built = []

    def build(opts):
        p = FakeProducer(opts)
        built.append(p)
        return p

    mgr = EngineManager(hub, build, {"engine": "auto", "model": None, "mic": None})
    mgr.set_dictionary(["Nemotron"])
    app = build_app(hub, mgr, web_dir=None, autostart=True, settings=settings)
    with TestClient(app) as client:
        mgr.join(2)
        assert len(built) == 1 and built[0].started == 1
        r = client.post("/api/engine", json={"engine": "auto", "model": "small.en", "mic": 2})
        assert r.status_code == 200
        mgr.join(2)
        assert built[0].stopped == 1
        assert len(built) == 2
        assert built[1].opts["model"] == "small.en" and built[1].opts["mic"] == 2
        assert built[1].dictionary == ["Nemotron"]
        assert client.post("/api/engine", json={"engine": "nope"}).status_code == 400
        assert client.post("/api/launch", json={"monitor": 1}).json()["launch"]["monitor"] == 1
        r = client.post("/api/display/reset")
        assert r.json()["config"]["position"] == "bottom"
    assert settings.launch == {"engine": "auto", "model": "small.en", "mic": 2, "monitor": 1}


def test_cli_prefers_flags_then_saved_launch(monkeypatch):
    from captions_desktop import cli, control

    monkeypatch.setattr(control, "installed_engines", lambda: ["auto", "sherpa-onnx"])
    monkeypatch.setattr(control, "list_input_devices", lambda: [{"index": 4}])
    ns = lambda **kw: argparse.Namespace(**{"engine": None, "model": None, "mic": None, **kw})
    saved = {"engine": "sherpa-onnx", "model": "nemotron-en-160ms", "mic": 4}

    assert cli._resolve_launch_options(ns(), saved) == {
        "engine": "sherpa-onnx", "model": "nemotron-en-160ms", "mic": 4,
    }
    # An explicit different engine doesn't inherit the other engine's model.
    assert cli._resolve_launch_options(ns(engine="auto"), saved)["model"] is None
    # A saved engine that's no longer installed falls back to auto; a missing mic to default.
    out = cli._resolve_launch_options(ns(), {"engine": "mlx", "mic": 9})
    assert out == {"engine": "auto", "model": None, "mic": None}
