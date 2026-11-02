"""Debate records and export to Markdown / JSON."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Turn:
    speaker: str
    round: int
    phase: str  # opening | rebuttal | closing
    text: str
    color: str = "#6b7280"


@dataclass
class Debate:
    topic: str
    lineup: dict
    rounds: int
    model: str = ""
    id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    turns: list[Turn] = field(default_factory=list)
    synthesis: str = ""
    verdict: dict | None = None
    status: str = "running"  # running | completed | stopped | failed
    error: str = ""

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Debate":
        d = dict(d)
        d["turns"] = [Turn(**t) for t in d.get("turns", [])]
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    def to_markdown(self) -> str:
        names = ", ".join(p["name"] for p in self.lineup.get("debaters", []))
        out = [
            f"# Debate: {self.topic}",
            "",
            f"- **Line-up:** {self.lineup.get('title', 'Custom')} ({names})",
            f"- **Rounds:** {self.rounds}",
            f"- **Model:** {self.model or 'unknown'}",
            f"- **Date:** {self.created_at.replace('T', ' ')}",
            f"- **Status:** {self.status}",
            "",
        ]
        current_round = 0
        for t in self.turns:
            if t.round != current_round:
                current_round = t.round
                out += [f"## Round {t.round}", ""]
            out += [f"### {t.speaker} — {t.phase}", "", t.text.strip(), ""]
        if self.synthesis:
            out += ["## Moderator synthesis", "", self.synthesis.strip(), ""]
        if self.verdict:
            out += ["## Judge's verdict", ""]
            if self.verdict.get("winner"):
                out.append(f"**Winner:** {self.verdict['winner']}")
                out.append("")
            for name, score in (self.verdict.get("scores") or {}).items():
                out.append(f"- {name}: {score}/10")
            if self.verdict.get("reasoning"):
                out += ["", self.verdict["reasoning"].strip()]
            out.append("")
        if self.error:
            out += ["> **Error:** " + self.error, ""]
        return "\n".join(out).rstrip() + "\n"

    def to_text_history(self, limit_chars: int | None = None) -> str:
        """Plain-text transcript fed to the models."""
        parts = [f"{t.speaker} (round {t.round}, {t.phase}):\n{t.text.strip()}" for t in self.turns]
        text = "\n\n".join(parts)
        if limit_chars and len(text) > limit_chars:
            text = "[…earlier turns omitted…]\n\n" + text[-limit_chars:]
        return text

    # -- storage ----------------------------------------------------------
    def slug(self) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", self.topic.lower()).strip("-")[:50].rstrip("-")
        return f"{self.id}-{s or 'debate'}"

    def save(self, directory: str | Path) -> tuple[Path, Path]:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        base = d / self.slug()
        jp, mp = base.with_suffix(".json"), base.with_suffix(".md")
        jp.write_text(self.to_json(), encoding="utf-8")
        mp.write_text(self.to_markdown(), encoding="utf-8")
        return jp, mp


def list_saved(directory: str | Path) -> list[dict]:
    d = Path(directory)
    if not d.is_dir():
        return []
    items = []
    for p in sorted(d.glob("*.json"), reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items.append({
                "file": p.name, "id": data.get("id"), "topic": data.get("topic"),
                "created_at": data.get("created_at"), "status": data.get("status"),
                "lineup": (data.get("lineup") or {}).get("title"),
            })
        except (ValueError, OSError):
            continue
    return items


def load_saved(path: str | Path) -> Debate:
    return Debate.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
