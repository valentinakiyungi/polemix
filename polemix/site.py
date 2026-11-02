"""Renders the web app, and builds the static version for GitHub Pages or any web host."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from .personas import PRESETS

STATIC_FILES = ["favicon.svg"]


def _asset(name: str) -> bytes:
    return resources.files("polemix").joinpath("web").joinpath(name).read_bytes()


def render_index() -> bytes:
    """index.html with the built-in line-ups embedded, so the page also works without the Python server."""
    lineups = json.dumps([p.to_dict() for p in PRESETS.values()], ensure_ascii=False)
    lineups = lineups.replace("</", "<\\/")  # never close the <script> tag early
    html = _asset("index.html").decode("utf-8")
    if "/*LINEUPS*/" not in html:
        raise RuntimeError("index.html is missing the /*LINEUPS*/ placeholder")
    return html.replace("/*LINEUPS*/", lineups).encode("utf-8")


def build_site(out_dir: str | Path) -> list[Path]:
    """Write a self-contained static site (index.html, favicon, .nojekyll) to out_dir."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = [out / "index.html"]
    written[0].write_bytes(render_index())
    for name in STATIC_FILES:
        (out / name).write_bytes(_asset(name))
        written.append(out / name)
    (out / ".nojekyll").write_text("")
    written.append(out / ".nojekyll")
    return written
