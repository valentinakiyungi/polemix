"""Debater personas and ready-made line-ups (presets)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Persona:
    name: str
    role: str  # one-line description shown in UIs
    instructions: str  # how this persona argues
    stance: str = "neutral"  # "for", "against" or "neutral" relative to the motion
    color: str = "#6b7280"
    temperature: float | None = None  # overrides the global temperature if set

    def system_prompt(self, topic: str, others: list[str]) -> str:
        stance_line = {
            "for": "You argue IN FAVOR of the motion.",
            "against": "You argue AGAINST the motion.",
        }.get(self.stance, "You take your own independent position on the motion.")
        opponents = ", ".join(others) if others else "the other participants"
        return (
            f"You are {self.name}, {self.role}, taking part in a structured debate.\n"
            f"Motion: \"{topic}\"\n"
            f"{stance_line}\n"
            f"Your character: {self.instructions}\n\n"
            "Debate rules:\n"
            f"- Speak only as {self.name}. Never write lines for {opponents}.\n"
            "- Engage directly with the strongest points made against you; quote or name them.\n"
            "- Make concrete arguments with reasons and examples, not slogans.\n"
            "- Keep each turn under 200 words. Plain prose, no headings, no bullet lists.\n"
            "- Do not prefix your answer with your name."
        )


@dataclass
class Preset:
    id: str
    title: str
    description: str
    debaters: list[Persona]
    moderator: Persona = field(default_factory=lambda: DEFAULT_MODERATOR)
    suggested_topic: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "description": self.description,
            "suggested_topic": self.suggested_topic,
            "debaters": [asdict(p) for p in self.debaters],
            "moderator": asdict(self.moderator),
        }


DEFAULT_MODERATOR = Persona(
    name="Moderator",
    role="an impartial debate moderator",
    instructions=(
        "You are scrupulously neutral. You identify the strongest arguments on each side, "
        "points of genuine agreement, unresolved disagreements, and what evidence would settle them."
    ),
    color="#a855f7",
    temperature=0.2,
)

PRESETS: dict[str, Preset] = {}


def _add(p: Preset):
    PRESETS[p.id] = p


_add(Preset(
    id="optimist-vs-skeptic",
    title="Optimist vs. Skeptic",
    description="The original line-up: a techno-optimist faces a hard-nosed systems architect.",
    suggested_topic="Fully autonomous local AI agents should run critical enterprise infrastructure without human supervision.",
    debaters=[
        Persona("Optimist", "an extreme technological optimist",
                "You believe AI and decentralized local agents will remove human bottlenecks, create abundance "
                "and transform society for the better. You are passionate, visionary, and point to historical "
                "precedents of technology improving lives.", stance="for", color="#10b981"),
        Persona("Skeptic", "a pragmatic, highly skeptical systems architect",
                "You focus on edge-case failures, runaway resource consumption, security vulnerabilities, "
                "accountability gaps and systemic risk. You attack weak assumptions precisely.",
                stance="against", color="#f43f5e"),
    ],
))

_add(Preset(
    id="classic",
    title="Proposition vs. Opposition",
    description="A formal, balanced two-sided debate suitable for any motion.",
    suggested_topic="Remote work is better for society than office work.",
    debaters=[
        Persona("Proposition", "the lead speaker for the proposition",
                "You build a clear, well-structured case with evidence and anticipate rebuttals.",
                stance="for", color="#3b82f6"),
        Persona("Opposition", "the lead speaker for the opposition",
                "You dismantle the proposition's case point by point and offer a credible alternative.",
                stance="against", color="#f59e0b"),
    ],
))

_add(Preset(
    id="roundtable",
    title="Expert Roundtable",
    description="Three experts examine a question from economic, ethical and engineering angles.",
    suggested_topic="Cities should ban private cars from their centers by 2035.",
    debaters=[
        Persona("Economist", "a pragmatic economist",
                "You reason about incentives, costs, trade-offs, and who wins and loses.",
                color="#0ea5e9"),
        Persona("Ethicist", "a moral philosopher",
                "You weigh fairness, rights, duties and long-term consequences for the vulnerable.",
                color="#d946ef"),
        Persona("Engineer", "a hands-on systems engineer",
                "You care about feasibility, implementation details, timelines and failure modes.",
                color="#84cc16"),
    ],
))

_add(Preset(
    id="devils-advocate",
    title="Believer vs. Devil's Advocate",
    description="Stress-test an idea: one side believes in it, the other attacks it relentlessly.",
    suggested_topic="Our startup should open-source its core product.",
    debaters=[
        Persona("Believer", "a committed champion of the idea",
                "You genuinely believe in the idea and defend it with conviction and specifics.",
                stance="for", color="#22c55e"),
        Persona("Devil's Advocate", "a professional devil's advocate",
                "You find every weakness, hidden cost and failure scenario, however uncomfortable.",
                stance="against", color="#ef4444"),
    ],
))


def _persona_from(d: dict, default_color: str) -> Persona:
    if not isinstance(d, dict) or not d.get("name"):
        raise ValueError("Each persona needs at least a 'name'")
    return Persona(
        name=str(d["name"]),
        role=str(d.get("role", "a debater")),
        instructions=str(d.get("instructions", d.get("prompt", ""))),
        stance=str(d.get("stance", "neutral")).lower(),
        color=str(d.get("color", default_color)),
        temperature=d.get("temperature"),
    )


def preset_from_dict(data: dict, source: str = "custom") -> Preset:
    """Build a Preset from a dict (as loaded from JSON/YAML or sent by the web UI)."""
    palette = ["#10b981", "#f43f5e", "#3b82f6", "#f59e0b", "#d946ef", "#84cc16"]
    raw = data.get("debaters") or data.get("personas")
    if not raw or not isinstance(raw, list):
        raise ValueError("Custom line-up must contain a non-empty 'debaters' list")
    if len(raw) > 6:
        raise ValueError("At most 6 debaters are supported")
    debaters = [_persona_from(d, palette[i % len(palette)]) for i, d in enumerate(raw)]
    names = [p.name for p in debaters]
    if len(set(names)) != len(names):
        raise ValueError("Debater names must be unique")
    moderator = _persona_from(data["moderator"], "#a855f7") if data.get("moderator") else DEFAULT_MODERATOR
    return Preset(
        id=str(data.get("id", source)), title=str(data.get("title", "Custom line-up")),
        description=str(data.get("description", "")), debaters=debaters, moderator=moderator,
        suggested_topic=str(data.get("suggested_topic", data.get("topic", ""))),
    )


def load_preset_file(path: str | Path) -> Preset:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # optional dependency
        except ImportError:
            raise ValueError("Reading YAML needs PyYAML: pip install pyyaml (or use a .json file)") from None
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return preset_from_dict(data, source=p.stem)


def get_preset(name_or_path: str) -> Preset:
    if name_or_path in PRESETS:
        return PRESETS[name_or_path]
    if Path(name_or_path).is_file():
        return load_preset_file(name_or_path)
    raise ValueError(f"Unknown preset '{name_or_path}'. Choose one of: {', '.join(PRESETS)} "
                     "or pass a path to a .json/.yaml line-up file.")
