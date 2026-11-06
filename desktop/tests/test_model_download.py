"""Model download must survive being interrupted: resume the archive, and never
treat a half-unpacked model folder as finished."""

import io
import os
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from captions_desktop.engines import sherpa_onnx as so

STEM = "fake-model"


def _archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        for name in ["encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"]:
            data = name.encode() + os.urandom(150_000)  # incompressible, so it streams in chunks
            info = tarfile.TarInfo(f"{STEM}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def server():
    body = _archive()
    state = {"cut_after": None, "ranges": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            start = 0
            rng = self.headers.get("Range")
            if rng:
                start = int(rng.split("=")[1].split("-")[0])
                state["ranges"].append(start)
            payload = body[start:]
            self.send_response(206 if rng else 200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            cut = state["cut_after"]
            if cut is not None:
                state["cut_after"] = None  # only the first request is cut short
                self.wfile.write(payload[:cut])
                self.wfile.flush()
                self.connection.close()  # drop mid-transfer, like a killed app
                return
            self.wfile.write(payload)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/{STEM}.tar.bz2", body, state
    httpd.shutdown()


def test_interrupted_download_resumes(tmp_path, server, monkeypatch):
    url, body, state = server
    monkeypatch.setattr(so.time, "sleep", lambda s: None)
    state["cut_after"] = len(body) // 3
    so._download_and_extract(url, tmp_path, STEM)
    assert state["ranges"] and state["ranges"][0] == len(body) // 3  # resumed, not restarted
    files = so.find_model_files(tmp_path / STEM)
    assert files["tokens"].read_bytes().startswith(b"tokens.txt")
    assert not list(tmp_path.glob("*.part")) and not list(tmp_path.glob("unpack-*"))


def test_partial_part_file_from_a_previous_run_is_resumed(tmp_path, server):
    url, body, state = server
    (tmp_path / f"{STEM}.tar.bz2.part").write_bytes(body[:100_000])  # app quit mid-download
    so._download_and_extract(url, tmp_path, STEM)
    assert state["ranges"] == [100_000]
    assert (tmp_path / STEM / "joiner.int8.onnx").exists()


def test_half_unpacked_model_is_downloaded_again(tmp_path, server, monkeypatch):
    url, body, state = server
    monkeypatch.setenv("CAPTION_GURU_MODELS", str(tmp_path))
    monkeypatch.setitem(so.KNOWN_MODELS, "fake", STEM)
    monkeypatch.setattr(so, "RELEASE_BASE", url.rsplit("/", 1)[0] + "/")
    broken = tmp_path / STEM
    broken.mkdir()
    (broken / "encoder.int8.onnx").write_text("half")  # no decoder/joiner/tokens
    assert so.resolve_model_dir("fake") == broken
    assert (broken / "tokens.txt").exists()
