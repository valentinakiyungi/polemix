"""Shared fixtures: a fake OpenAI-compatible server that streams SSE like LM Studio."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class FakeLLM:
    def __init__(self):
        self.requests = []
        self.models = ["fake-chat-7b", "nomic-embed-text"]
        self.reply = "I strongly <think>hidden reasoning</think>believe this is right."
        self.fail_status = None


@pytest.fixture
def fake_llm():
    state = FakeLLM()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/v1/models":
                body = json.dumps({"data": [{"id": m} for m in state.models]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append(body)
            if state.fail_status:
                msg = json.dumps({"error": {"message": "model crashed"}}).encode()
                self.send_response(state.fail_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
            content = state.reply
            if "judge" in body["messages"][0]["content"]:
                content = '```json\n{"winner": "optimist", "scores": {"Optimist": 8, "Skeptic": 6.6}, "reasoning": "Sharper."}\n```'
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            # stream in awkward 5-char chunks to exercise the <think> filter
            for i in range(0, len(content), 5):
                chunk = {"choices": [{"delta": {"content": content[i:i + 5]}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state.base_url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    yield state
    srv.shutdown()
