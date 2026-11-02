"""Settings loaded from (in order of priority): CLI flags > environment > .env file > defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

DEFAULT_BASE_URL = "http://localhost:1234/v1"  # LM Studio's default server address


def load_dotenv(path: str | os.PathLike = ".env") -> dict[str, str]:
    """Minimal .env reader (KEY=VALUE lines, # comments, optional quotes).

    Values are only placed into os.environ when not already set, so real
    environment variables always win.
    """
    p = Path(path)
    loaded: dict[str, str] = {}
    if not p.is_file():
        return loaded
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


def _env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        val = os.environ.get(name)
        if val not in (None, ""):
            return val
    return default


@dataclass
class Settings:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = "not-needed"
    model: str | None = None  # None = auto-detect the model loaded in the server
    temperature: float = 0.7
    moderator_temperature: float = 0.2
    max_tokens: int = 600
    timeout: float = 300.0
    rounds: int = 3
    history_chars: int = 12000  # how much prior transcript each speaker sees
    output_dir: str = "debates"
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, dotenv_path: str | os.PathLike = ".env") -> "Settings":
        load_dotenv(dotenv_path)
        s = cls()
        s.base_url = _env("POLEMIX_BASE_URL", "OPENAI_API_BASE", "OPENAI_BASE_URL", default=s.base_url)
        s.api_key = _env("POLEMIX_API_KEY", "OPENAI_API_KEY", default=s.api_key)
        s.model = _env("POLEMIX_MODEL", default=None)
        s.output_dir = _env("POLEMIX_OUTPUT_DIR", default=s.output_dir)
        for name, attr, conv in [
            ("POLEMIX_TEMPERATURE", "temperature", float),
            ("POLEMIX_MODERATOR_TEMPERATURE", "moderator_temperature", float),
            ("POLEMIX_MAX_TOKENS", "max_tokens", int),
            ("POLEMIX_TIMEOUT", "timeout", float),
            ("POLEMIX_ROUNDS", "rounds", int),
            ("POLEMIX_HISTORY_CHARS", "history_chars", int),
        ]:
            val = _env(name)
            if val is not None:
                try:
                    setattr(s, attr, conv(val))
                except ValueError:
                    raise ValueError(f"{name} must be a number, got {val!r}") from None
        return s.validated()

    def with_overrides(self, **kwargs) -> "Settings":
        known = {f.name for f in fields(self)}
        clean = {k: v for k, v in kwargs.items() if k in known and v is not None}
        return replace(self, **clean).validated()

    def validated(self) -> "Settings":
        self.base_url = self.base_url.rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError(f"Base URL must start with http:// or https:// (got {self.base_url!r})")
        if not 1 <= self.rounds <= 20:
            raise ValueError("Rounds must be between 1 and 20")
        if not 0 <= self.temperature <= 2:
            raise ValueError("Temperature must be between 0 and 2")
        if self.max_tokens < 16:
            raise ValueError("max_tokens must be at least 16")
        return self
