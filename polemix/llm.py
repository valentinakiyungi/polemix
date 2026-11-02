"""Tiny OpenAI-compatible chat client built on the standard library.

Talks to LM Studio, Ollama (/v1), llama.cpp server, vLLM, LocalAI, etc.
Supports token streaming over Server-Sent Events.
"""

from __future__ import annotations

import json
import random
import re
import socket
import time
import urllib.error
import urllib.request
from typing import Iterator, Protocol

from .config import Settings


class LLMError(RuntimeError):
    """Base error for model/server problems."""


class ServerUnavailable(LLMError):
    """The local inference server could not be reached."""


class ModelError(LLMError):
    """The server answered, but refused or failed the request."""


class ChatClient(Protocol):
    def list_models(self) -> list[str]: ...

    def stream_chat(self, messages: list[dict], *, temperature: float, max_tokens: int,
                    label: str = "") -> Iterator[str]: ...


def _unreachable_hint(base_url: str) -> str:
    return (
        f"Could not reach a model server at {base_url}.\n"
        "  • LM Studio: open the Developer tab, load a model, and press 'Start Server'.\n"
        "  • Ollama: run `ollama serve` and use --base-url http://localhost:11434/v1\n"
        "  • Or try Polemix without a model: add --demo"
    )


class OpenAICompatibleClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._resolved_model: str | None = settings.model

    # -- HTTP helpers -----------------------------------------------------
    def _request(self, path: str, payload: dict | None = None, timeout: float | None = None):
        url = f"{self.settings.base_url}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream, application/json")
        if self.settings.api_key:
            req.add_header("Authorization", f"Bearer {self.settings.api_key}")
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.settings.timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:500]
            try:
                body = json.loads(body).get("error", body)
                if isinstance(body, dict):
                    body = body.get("message", body)
            except (ValueError, AttributeError):
                pass
            raise ModelError(f"Server returned HTTP {e.code}: {body}") from None
        except (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise ServerUnavailable(f"Timed out talking to {url}") from None
            raise ServerUnavailable(_unreachable_hint(self.settings.base_url)) from None

    # -- Public API -------------------------------------------------------
    def list_models(self) -> list[str]:
        with self._request("/models", timeout=10) as resp:
            data = json.loads(resp.read().decode())
        models = [m.get("id") for m in data.get("data", []) if m.get("id")]
        # Embedding models cannot chat; push them to the end.
        return sorted(models, key=lambda m: ("embed" in m.lower(), models.index(m)))

    @property
    def model(self) -> str:
        if self._resolved_model:
            return self._resolved_model
        models = [m for m in self.list_models() if "embed" not in m.lower()]
        if not models:
            raise ModelError("The server is running but no chat model is loaded. Load a model and retry.")
        self._resolved_model = models[0]
        return self._resolved_model

    def stream_chat(self, messages: list[dict], *, temperature: float, max_tokens: int,
                    label: str = "") -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        resp = self._request("/chat/completions", payload)
        think = ThinkFilter()
        with resp:
            ctype = resp.headers.get("Content-Type", "")
            if "text/event-stream" not in ctype:
                # Server ignored stream=True; fall back to a single JSON body.
                data = json.loads(resp.read().decode())
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                out = think.feed(text) + think.flush()
                if out:
                    yield out
                return
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except ValueError:
                    continue
                if "error" in obj:
                    err = obj["error"]
                    raise ModelError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
                choices = obj.get("choices") or []
                if not choices:
                    continue
                piece = (choices[0].get("delta") or {}).get("content") or ""
                out = think.feed(piece)
                if out:
                    yield out
        tail = think.flush()
        if tail:
            yield tail


class ThinkFilter:
    """Strips <think>…</think> reasoning blocks (Qwen3, DeepSeek-R1 …) from a token stream."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.buf = ""
        self.inside = False

    def feed(self, text: str) -> str:
        self.buf += text
        out = []
        while True:
            tag = self.CLOSE if self.inside else self.OPEN
            idx = self.buf.find(tag)
            if idx >= 0:
                if not self.inside:
                    out.append(self.buf[:idx])
                self.buf = self.buf[idx + len(tag):]
                self.inside = not self.inside
                if not self.inside:
                    self.buf = self.buf.lstrip()
                continue
            # keep a possible partial tag at the end of the buffer
            keep = 0
            for k in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(self.buf[-k:]):
                    keep = k
                    break
            emit, self.buf = (self.buf[:-keep], self.buf[-keep:]) if keep else (self.buf, "")
            if not self.inside:
                out.append(emit)
            return "".join(out)

    def flush(self) -> str:
        rest, self.buf = ("" if self.inside else self.buf), ""
        return rest


# ---------------------------------------------------------------------------
# Demo client: lets people try the whole app with no model installed.
# ---------------------------------------------------------------------------

_DEMO_LINES = {
    "for": [
        "The evidence points to real, measurable gains here, and waiting carries its own cost.",
        "Every major technology looked risky before we learned to govern it; this is no different.",
        "My opponent lists hazards, but each one has a known mitigation we can engineer today.",
        "Local, open systems put control in the hands of the people who actually use them.",
        "Progress is not reckless when it is incremental, monitored, and reversible.",
    ],
    "against": [
        "Optimism is not a safety case; we need failure modes, bounds, and a way to stop the system.",
        "The rosy picture skips the edge cases, and in critical systems the edge cases are the whole job.",
        "Who is accountable when an unsupervised agent makes a costly, irreversible decision?",
        "Resource use, security exposure, and silent drift compound quietly until they don't.",
        "I am not against the idea; I am against deploying it before it has earned trust.",
    ],
    "neutral": [
        "Both sides agree the benefits are real; they disagree about how quickly trust can be earned.",
        "The strongest common ground is staged deployment with clear human override points.",
        "The unresolved question is accountability: who owns a decision no human reviewed?",
        "A sensible path is narrow pilots, strong monitoring, and explicit criteria for expansion.",
    ],
}


class DemoClient:
    """Fake model that streams canned-but-varied text. Used by --demo and tests."""

    def __init__(self, delay: float = 0.012, seed: int | None = None):
        self.delay = delay
        self.rng = random.Random(seed)

    def list_models(self) -> list[str]:
        return ["polemix-demo"]

    @property
    def model(self) -> str:
        return "polemix-demo"

    def stream_chat(self, messages, *, temperature, max_tokens, label=""):
        lean = "neutral"
        m = re.search(r"\[stance:(\w+)\]", label)
        if m:
            lean = m.group(1)
        pool = _DEMO_LINES.get(lean, _DEMO_LINES["neutral"])
        if label.startswith("judge"):
            found = re.search(r"Score each participant \((.+?)\)", messages[-1]["content"])
            names = [n.strip() for n in found.group(1).split(",")] if found else []
            text = json.dumps({
                "winner": "draw", "scores": {n: 7 for n in names},
                "reasoning": "Demo mode: both sides argued coherently; no real model was consulted.",
            })
            yield text
            return
        if label.startswith("moderator"):
            text = (
                "**Strongest arguments** — One side stressed concrete benefits and the cost of delay; "
                "the other stressed failure modes, accountability and security.\n\n"
                "**Common ground** — Everyone accepts staged rollout with human override points.\n\n"
                "**Key disagreements** — How fast trust can be earned, and who answers for unreviewed decisions.\n\n"
                "**What would settle it** — Results from monitored pilots with published failure data.\n\n"
                "_(Demo mode — connect a local model for real debates.)_"
            )
        else:
            text = " ".join(self.rng.sample(pool, k=min(3, len(pool))))
        for word in re.findall(r"\S+\s*", text):
            if self.delay:
                time.sleep(self.delay)
            yield word
