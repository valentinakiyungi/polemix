"""The debate engine: turn-taking, moderator synthesis and optional judging.

`DebateEngine.run()` is a generator of plain-dict events so the same engine
powers the CLI, the web UI and the tests:

    {"type": "start", "debate": {...}}
    {"type": "turn_start", "speaker", "round", "phase", "color"}
    {"type": "token", "text"}
    {"type": "turn_end", "speaker", "text"}
    {"type": "synthesis_start"} / {"type": "synthesis_end", "text"}
    {"type": "verdict", "verdict": {...}}
    {"type": "end", "debate": {...}}
    {"type": "error", "message"}
"""

from __future__ import annotations

import json
import re
from typing import Callable, Iterator

from .config import Settings
from .llm import ChatClient, LLMError
from .personas import Persona, Preset
from .transcript import Debate, Turn


class DebateStopped(Exception):
    pass


def phase_for(round_no: int, total: int) -> str:
    if round_no == 1:
        return "opening"
    if round_no == total:
        return "closing"
    return "rebuttal"


PHASE_INSTRUCTIONS = {
    "opening": "Give your opening statement. Set out your position and your two or three strongest arguments.",
    "rebuttal": "Give your rebuttal. Respond directly to the most recent points made against you, then strengthen your case.",
    "closing": "Give your closing statement. Summarise why your position should prevail, addressing the best counter-argument you faced.",
}


class DebateEngine:
    def __init__(self, client: ChatClient, settings: Settings):
        self.client = client
        self.settings = settings

    # -- prompt building --------------------------------------------------
    def _speaker_messages(self, debate: Debate, persona: Persona, preset: Preset, phase: str) -> list[dict]:
        others = [p.name for p in preset.debaters if p.name != persona.name]
        history = debate.to_text_history(self.settings.history_chars)
        user = (
            (f"Debate so far:\n\n{history}\n\n" if history else "You speak first.\n\n")
            + PHASE_INSTRUCTIONS[phase]
        )
        return [
            {"role": "system", "content": persona.system_prompt(debate.topic, others)},
            {"role": "user", "content": user},
        ]

    def _moderator_messages(self, debate: Debate, preset: Preset) -> list[dict]:
        m = preset.moderator
        names = ", ".join(p.name for p in preset.debaters)
        return [
            {"role": "system", "content": (
                f"You are {m.name}, {m.role}. {m.instructions}\n"
                "You never take sides and never invent arguments that were not made."
            )},
            {"role": "user", "content": (
                f"Motion: \"{debate.topic}\"\nParticipants: {names}\n\n"
                f"Full transcript:\n\n{debate.to_text_history(self.settings.history_chars * 2)}\n\n"
                "Write a balanced synthesis using these Markdown sections:\n"
                "**Strongest arguments** (one short paragraph per participant), **Common ground**, "
                "**Key disagreements**, **What would settle it**. Keep it under 350 words."
            )},
        ]

    def _judge_messages(self, debate: Debate, preset: Preset) -> list[dict]:
        names = [p.name for p in preset.debaters]
        example = json.dumps({"winner": names[0], "scores": {n: 7 for n in names},
                              "reasoning": "One or two sentences."})
        return [
            {"role": "system", "content": (
                "You are a strict, impartial debate judge. You score on reasoning quality, use of evidence "
                "and how well each side answered its opponents — not on which view you personally hold. "
                "You reply with JSON only."
            )},
            {"role": "user", "content": (
                f"Motion: \"{debate.topic}\"\n\nTranscript:\n\n"
                f"{debate.to_text_history(self.settings.history_chars * 2)}\n\n"
                f"Score each participant ({', '.join(names)}) from 1 to 10 and pick a winner "
                f"(a participant name, or \"draw\"). Reply with exactly one JSON object like:\n{example}"
            )},
        ]

    # -- streaming helper -------------------------------------------------
    def _stream(self, messages, temperature, label, should_stop) -> Iterator[str]:
        for piece in self.client.stream_chat(messages, temperature=temperature,
                                             max_tokens=self.settings.max_tokens, label=label):
            if should_stop():
                raise DebateStopped()
            yield piece

    # -- main loop --------------------------------------------------------
    def run(self, topic: str, preset: Preset, *, rounds: int | None = None, judge: bool = False,
            should_stop: Callable[[], bool] = lambda: False) -> Iterator[dict]:
        topic = (topic or "").strip()
        if not topic:
            raise ValueError("A debate needs a topic (motion)")
        rounds = rounds or self.settings.rounds
        debate = Debate(topic=topic, lineup=preset.to_dict(), rounds=rounds)
        self.debate = debate
        try:
            debate.model = getattr(self.client, "model", "") or ""
            yield {"type": "start", "debate": debate.to_dict()}

            for r in range(1, rounds + 1):
                phase = phase_for(r, rounds)
                for persona in preset.debaters:
                    yield {"type": "turn_start", "speaker": persona.name, "round": r,
                           "phase": phase, "color": persona.color}
                    temp = persona.temperature if persona.temperature is not None else self.settings.temperature
                    msgs = self._speaker_messages(debate, persona, preset, phase)
                    parts: list[str] = []
                    for piece in self._stream(msgs, temp, f"{persona.name} [stance:{persona.stance}]", should_stop):
                        parts.append(piece)
                        yield {"type": "token", "text": piece}
                    text = clean_turn("".join(parts), persona.name)
                    if not text:
                        text = "(no response)"
                    debate.turns.append(Turn(persona.name, r, phase, text, persona.color))
                    yield {"type": "turn_end", "speaker": persona.name, "text": text}

            yield {"type": "synthesis_start", "speaker": preset.moderator.name, "color": preset.moderator.color}
            parts = []
            mod_temp = preset.moderator.temperature
            if mod_temp is None:
                mod_temp = self.settings.moderator_temperature
            for piece in self._stream(self._moderator_messages(debate, preset), mod_temp,
                                      "moderator [stance:neutral]", should_stop):
                parts.append(piece)
                yield {"type": "token", "text": piece}
            debate.synthesis = "".join(parts).strip()
            yield {"type": "synthesis_end", "text": debate.synthesis}

            if judge:
                yield {"type": "judge_start"}
                raw = "".join(self._stream(self._judge_messages(debate, preset), 0.0, "judge", should_stop))
                debate.verdict = parse_verdict(raw, [p.name for p in preset.debaters])
                yield {"type": "verdict", "verdict": debate.verdict}

            debate.status = "completed"
        except DebateStopped:
            debate.status = "stopped"
        except LLMError as e:
            debate.status = "failed"
            debate.error = str(e)
            yield {"type": "error", "message": str(e)}
        yield {"type": "end", "debate": debate.to_dict()}


def clean_turn(text: str, speaker: str) -> str:
    """Remove a leading 'Name:' echo that small models like to add."""
    text = text.strip()
    text = re.sub(rf"^\**\s*{re.escape(speaker)}\s*\**\s*(\([^)]*\))?\s*:\s*\**", "", text, flags=re.I).strip()
    return text


def parse_verdict(raw: str, names: list[str]) -> dict:
    match = re.search(r"\{.*\}", raw, re.S)
    data = None
    if match:
        try:
            data = json.loads(match.group(0))
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return {"winner": None, "scores": {}, "reasoning": raw.strip()[:1000], "parse_error": True}
    scores = {}
    for name in names:
        val = (data.get("scores") or {}).get(name)
        try:
            scores[name] = max(1, min(10, round(float(val))))
        except (TypeError, ValueError):
            continue
    winner = data.get("winner")
    lookup = {n.lower(): n for n in names}
    if isinstance(winner, str):
        winner = lookup.get(winner.strip().lower(), "draw" if winner.strip().lower() == "draw" else winner.strip())
    return {"winner": winner, "scores": scores, "reasoning": str(data.get("reasoning", "")).strip()}
