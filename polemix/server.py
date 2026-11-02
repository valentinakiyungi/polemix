"""Local web app — a zero-dependency HTTP server with live (SSE) debate streaming."""

from __future__ import annotations

import json
import re
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import __version__
from .config import Settings
from .engine import DebateEngine
from .llm import DemoClient, LLMError, OpenAICompatibleClient
from .personas import PRESETS, get_preset, preset_from_dict
from .site import render_index
from .transcript import list_saved, load_saved

_SAFE_FILE = re.compile(r"^[A-Za-z0-9._-]+\.json$")


def _static(name: str) -> bytes:
    return resources.files("polemix").joinpath("web").joinpath(name).read_bytes()


class App:
    def __init__(self, settings: Settings, demo: bool = False):
        self.settings = settings
        self.demo = demo

    def client(self, model: str | None = None):
        if self.demo:
            return DemoClient()
        s = self.settings.with_overrides(model=model or None)
        return OpenAICompatibleClient(s)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"Polemix/{__version__}"

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        # -- helpers ------------------------------------------------------
        def _send(self, status: int, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200):
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _error(self, msg: str, status: int = 400):
            self._json({"error": msg}, status)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                raise ValueError("Request too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                raise ValueError("Body must be JSON") from None
            if not isinstance(data, dict):
                raise ValueError("Body must be a JSON object")
            return data

        def _history_path(self, name: str) -> Path:
            name = unquote(name)
            if not _SAFE_FILE.match(name):
                raise ValueError("Bad file name")
            return Path(app.settings.output_dir) / name

        # -- routes -------------------------------------------------------
        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, render_index(), "text/html; charset=utf-8")
                if path == "/favicon.svg":
                    return self._send(200, _static("favicon.svg"), "image/svg+xml")
                if path == "/api/info":
                    return self._json({"version": __version__, "demo": app.demo,
                                       "base_url": "demo" if app.demo else app.settings.base_url,
                                       "rounds": app.settings.rounds, "model": app.settings.model})
                if path == "/api/lineups":
                    return self._json([p.to_dict() for p in PRESETS.values()])
                if path == "/api/models":
                    try:
                        return self._json({"ok": True, "models": app.client().list_models()})
                    except LLMError as e:
                        return self._json({"ok": False, "models": [], "error": str(e)})
                if path == "/api/history":
                    return self._json(list_saved(app.settings.output_dir))
                if path.startswith("/api/history/"):
                    p = self._history_path(path.rsplit("/", 1)[1])
                    if not p.is_file():
                        return self._error("Not found", 404)
                    d = load_saved(p)
                    out = d.to_dict()
                    out["markdown"] = d.to_markdown()
                    return self._json(out)
                return self._error("Not found", 404)
            except ValueError as e:
                return self._error(str(e))

        def do_DELETE(self):
            path = urlparse(self.path).path
            try:
                if path.startswith("/api/history/"):
                    p = self._history_path(path.rsplit("/", 1)[1])
                    if not p.is_file():
                        return self._error("Not found", 404)
                    p.unlink()
                    md = p.with_suffix(".md")
                    if md.is_file():
                        md.unlink()
                    return self._json({"ok": True})
                return self._error("Not found", 404)
            except ValueError as e:
                return self._error(str(e))

        def do_POST(self):
            path = urlparse(self.path).path
            if path != "/api/debate":
                return self._error("Not found", 404)
            try:
                body = self._body()
                topic = str(body.get("topic", "")).strip()
                if not topic:
                    raise ValueError("Please enter a debate motion")
                if len(topic) > 500:
                    raise ValueError("Motion is too long (500 characters max)")
                if isinstance(body.get("custom"), dict):
                    preset = preset_from_dict(body["custom"])
                else:
                    preset = get_preset(str(body.get("lineup") or "optimist-vs-skeptic"))
                settings = app.settings.with_overrides(
                    rounds=int(body["rounds"]) if body.get("rounds") else None,
                    temperature=float(body["temperature"]) if body.get("temperature") is not None else None,
                )
                client = app.client(body.get("model"))
            except (ValueError, TypeError) as e:
                return self._error(str(e))

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            stop = threading.Event()
            engine = DebateEngine(client, settings)
            try:
                for ev in engine.run(topic, preset, rounds=settings.rounds,
                                     judge=bool(body.get("judge")), should_stop=stop.is_set):
                    if ev["type"] == "end" and engine.debate.turns:
                        jp, _ = engine.debate.save(settings.output_dir)
                        ev["file"] = jp.name
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # Browser pressed Stop / closed the tab: stop generating, keep what we have.
                stop.set()
                d = getattr(engine, "debate", None)
                if d is not None and d.turns:
                    d.status = "stopped"
                    d.save(settings.output_dir)

    return Handler


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8765, demo: bool = False,
          open_browser: bool = True):
    app = App(settings, demo=demo)
    try:
        httpd = ThreadingHTTPServer((host, port), make_handler(app))
    except OSError as e:
        raise ValueError(f"Could not start on {host}:{port} ({e.strerror}). Try --port {port + 1}") from None
    httpd.daemon_threads = True
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}"
    print(f"Polemix {__version__} is running at {url}")
    print(f"Model server: {'demo mode (no model)' if demo else settings.base_url}")
    print(f"Transcripts:  {Path(settings.output_dir).resolve()}")
    print("Press Ctrl+C to quit.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBye!")
    finally:
        httpd.server_close()
