"""Command-line interface:  polemix <command> [options]"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import textwrap
import threading

from . import __version__
from .config import Settings
from .engine import DebateEngine
from .llm import DemoClient, LLMError, OpenAICompatibleClient
from .personas import PRESETS, get_preset
from .transcript import list_saved, load_saved

# ---------------------------------------------------------------- colours --
_USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _hex_to_ansi(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return ""
    return f"\033[38;2;{r};{g};{b}m"


def c(text: str, color: str = "", bold: bool = False, dim: bool = False) -> str:
    if not _USE_COLOR:
        return text
    pre = ("\033[1m" if bold else "") + ("\033[2m" if dim else "") + (_hex_to_ansi(color) if color else "")
    return f"{pre}{text}\033[0m" if pre else text


def _width() -> int:
    return min(100, shutil.get_terminal_size((88, 20)).columns)


class StreamWrapper:
    """Word-wraps text that arrives in arbitrary token-sized chunks."""

    def __init__(self, width: int):
        self.width, self.col, self.word = width, 0, ""

    def _emit_word(self):
        if not self.word:
            return
        if self.col and self.col + 1 + len(self.word) > self.width:
            sys.stdout.write("\n")
            self.col = 0
        sys.stdout.write((" " if self.col else "") + self.word)
        self.col += (1 if self.col else 0) + len(self.word)
        self.word = ""

    def feed(self, text: str):
        for ch in text:
            if ch == "\n":
                self._emit_word()
                sys.stdout.write("\n")
                self.col = 0
            elif ch in " \t":
                self._emit_word()
            else:
                self.word += ch
        sys.stdout.flush()

    def finish(self):
        self._emit_word()
        if self.col:
            sys.stdout.write("\n")
        self.col = 0
        sys.stdout.flush()


# --------------------------------------------------------------- helpers --
def build_client(settings: Settings, demo: bool):
    return DemoClient() if demo else OpenAICompatibleClient(settings)


def settings_from_args(args) -> Settings:
    s = Settings.from_env(getattr(args, "env_file", ".env"))
    return s.with_overrides(
        base_url=getattr(args, "base_url", None), model=getattr(args, "model", None),
        api_key=getattr(args, "api_key", None), temperature=getattr(args, "temperature", None),
        max_tokens=getattr(args, "max_tokens", None), rounds=getattr(args, "rounds", None),
        output_dir=getattr(args, "out", None),
    )


def _add_server_args(p: argparse.ArgumentParser):
    g = p.add_argument_group("model server")
    g.add_argument("--base-url", help="OpenAI-compatible endpoint (default: $POLEMIX_BASE_URL or http://localhost:1234/v1)")
    g.add_argument("--model", help="model id to use (default: the first model loaded on the server)")
    g.add_argument("--api-key", help="API key, if your server needs one")
    g.add_argument("--env-file", default=".env", help="settings file to read (default: .env)")
    g.add_argument("--demo", action="store_true", help="use a built-in fake model (no server needed)")


# -------------------------------------------------------------- commands --
def cmd_run(args) -> int:
    settings = settings_from_args(args)
    preset = get_preset(args.lineup)
    topic = " ".join(args.topic).strip() if args.topic else ""
    if not topic:
        if not sys.stdin.isatty():
            print("error: give a topic, e.g. polemix run \"Cats are better than dogs\"", file=sys.stderr)
            return 2
        hint = f" [{preset.suggested_topic}]" if preset.suggested_topic else ""
        topic = input(c(f"Debate motion{hint}: ", bold=True)).strip() or preset.suggested_topic
        if not topic:
            print("No topic given.", file=sys.stderr)
            return 2

    client = build_client(settings, args.demo)
    engine = DebateEngine(client, settings)
    stop = threading.Event()
    width = _width()

    print()
    print(c("POLEMIX", "#a855f7", bold=True) + c(f"  ·  {preset.title}  ·  {settings.rounds} round(s)", dim=True))
    print(c("Motion: ", bold=True) + topic)
    print(c("─" * width, dim=True))

    wrap = StreamWrapper(width)
    current_color = ""
    try:
        for ev in engine.run(topic, preset, rounds=settings.rounds, judge=args.judge, should_stop=stop.is_set):
            t = ev["type"]
            if t == "start":
                print(c(f"Model: {ev['debate']['model']}", dim=True))
            elif t == "turn_start":
                current_color = ev["color"]
                print("\n" + c(f"▌ {ev['speaker']}", current_color, bold=True)
                      + c(f"  round {ev['round']} · {ev['phase']}", dim=True))
            elif t == "synthesis_start":
                current_color = ev["color"]
                print("\n" + c("─" * width, dim=True))
                print(c(f"▌ {ev['speaker']} — synthesis", current_color, bold=True))
            elif t == "token":
                wrap.feed(ev["text"])
            elif t in ("turn_end", "synthesis_end"):
                wrap.finish()
            elif t == "judge_start":
                print("\n" + c("⚖ Judging…", "#eab308", bold=True))
            elif t == "verdict":
                v = ev["verdict"]
                if v.get("winner"):
                    print(c(f"Winner: {v['winner']}", "#eab308", bold=True))
                for name, score in (v.get("scores") or {}).items():
                    bar = "█" * int(score) + "░" * (10 - int(score))
                    print(f"  {name:<18} {bar} {score}/10")
                if v.get("reasoning"):
                    print(textwrap.fill(v["reasoning"], width))
            elif t == "error":
                print("\n" + c("✖ " + ev["message"], "#ef4444", bold=True), file=sys.stderr)
    except KeyboardInterrupt:
        stop.set()
        wrap.finish()
        engine.debate.status = "stopped"
        print("\n" + c("Stopped.", "#f59e0b", bold=True))

    debate = engine.debate
    print(c("─" * width, dim=True))
    if debate.turns and not args.no_save:
        jp, mp = debate.save(settings.output_dir)
        print(c(f"Saved: {mp}  (and {jp.name})", dim=True))
    return 0 if debate.status == "completed" else 1


def cmd_models(args) -> int:
    settings = settings_from_args(args)
    try:
        models = build_client(settings, args.demo).list_models()
    except LLMError as e:
        print(c(str(e), "#ef4444"), file=sys.stderr)
        return 1
    if not models:
        print("Server is up, but no models are loaded.")
        return 1
    for i, m in enumerate(models):
        print(("* " if i == 0 else "  ") + m)
    print(c("\n* = used by default when --model is not given", dim=True))
    return 0


def cmd_check(args) -> int:
    settings = settings_from_args(args)
    print(f"Server:  {settings.base_url}")
    client = build_client(settings, args.demo)
    try:
        model = client.model
        print(f"Model:   {model}")
        reply = "".join(client.stream_chat([{"role": "user", "content": "Reply with the single word: ready"}],
                                           temperature=0, max_tokens=20, label="check"))
        print(f"Reply:   {reply.strip()[:60]!r}")
        print(c("✔ Everything works. Start a debate with: polemix run \"your motion\"", "#22c55e", bold=True))
        return 0
    except LLMError as e:
        print(c("✖ " + str(e), "#ef4444"), file=sys.stderr)
        return 1


def cmd_lineups(args) -> int:
    for p in PRESETS.values():
        print(c(p.id, "#a855f7", bold=True) + c(f"  — {p.title}", bold=True))
        print("   " + p.description)
        print("   " + ", ".join(c(d.name, d.color) for d in p.debaters))
        print()
    print(c("Use one with:  polemix run --lineup <id> \"motion\"   or pass a .json/.yaml file", dim=True))
    return 0


def cmd_history(args) -> int:
    settings = settings_from_args(args)
    items = list_saved(settings.output_dir)
    if not items:
        print(f"No saved debates in {settings.output_dir}/")
        return 0
    for it in items[: args.limit]:
        print(f"{c(it['created_at'] or '', dim=True)}  {it['topic']}  {c('[' + (it['status'] or '') + ']', dim=True)}")
        print(c(f"    {os.path.join(settings.output_dir, it['file'])}", dim=True))
    return 0


def cmd_show(args) -> int:
    print(load_saved(args.file).to_markdown())
    return 0


def cmd_build_site(args) -> int:
    from .site import build_site
    files = build_site(args.dir)
    print(f"Built the web app into {args.dir}/ ({len(files)} files).")
    print("Upload that folder to any static host (GitHub Pages, Netlify …) or open index.html directly.")
    return 0


def cmd_serve(args) -> int:
    from .server import serve
    settings = settings_from_args(args)
    serve(settings, host=args.host, port=args.port, demo=args.demo, open_browser=not args.no_browser)
    return 0


# ------------------------------------------------------------------ main --
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="polemix",
        description="Polemix — structured multi-persona AI debates on your own machine.",
    )
    p.add_argument("--version", action="version", version=f"polemix {__version__}")
    sub = p.add_subparsers(dest="command")

    r = sub.add_parser("run", help="run a debate in the terminal")
    r.add_argument("topic", nargs="*", help="the motion to debate (asked interactively if omitted)")
    r.add_argument("-l", "--lineup", default="optimist-vs-skeptic", help="preset id or path to a .json/.yaml line-up")
    r.add_argument("-r", "--rounds", type=int, help="number of rounds (default 3)")
    r.add_argument("-j", "--judge", action="store_true", help="add a judge's verdict with scores")
    r.add_argument("-t", "--temperature", type=float, help="debater creativity, 0–2 (default 0.7)")
    r.add_argument("--max-tokens", type=int, help="max tokens per turn (default 600)")
    r.add_argument("-o", "--out", help="folder for saved transcripts (default ./debates)")
    r.add_argument("--no-save", action="store_true", help="don't save the transcript")
    _add_server_args(r)
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("serve", help="open the web app in your browser")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    s.add_argument("-o", "--out", help="folder for saved transcripts (default ./debates)")
    _add_server_args(s)
    s.set_defaults(func=cmd_serve)

    b = sub.add_parser("build-site", help="build the static web app (for GitHub Pages or any web host)")
    b.add_argument("dir", nargs="?", default="site", help="output folder (default: ./site)")
    b.set_defaults(func=cmd_build_site)

    m = sub.add_parser("models", help="list models available on the server")
    _add_server_args(m)
    m.set_defaults(func=cmd_models)

    ch = sub.add_parser("check", help="test the connection to your model server")
    _add_server_args(ch)
    ch.set_defaults(func=cmd_check)

    lu = sub.add_parser("lineups", help="list built-in debater line-ups")
    lu.set_defaults(func=cmd_lineups)

    h = sub.add_parser("history", help="list saved debates")
    h.add_argument("-o", "--out", help="transcripts folder (default ./debates)")
    h.add_argument("-n", "--limit", type=int, default=20)
    h.add_argument("--env-file", default=".env")
    h.set_defaults(func=cmd_history)

    sh = sub.add_parser("show", help="print a saved debate (.json) as Markdown")
    sh.add_argument("file")
    sh.set_defaults(func=cmd_show)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        print(c("\nQuick start:  polemix serve        (web app)", "#a855f7"))
        print(c("              polemix run \"motion\"  (terminal)", "#a855f7"))
        return 0
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError) as e:
        print(c(f"error: {e}", "#ef4444"), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
