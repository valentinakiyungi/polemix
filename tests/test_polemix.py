import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from polemix.cli import main
from polemix.config import Settings, load_dotenv
from polemix.engine import DebateEngine, clean_turn, parse_verdict, phase_for
from polemix.llm import DemoClient, ModelError, OpenAICompatibleClient, ServerUnavailable, ThinkFilter
from polemix.personas import PRESETS, get_preset, preset_from_dict
from polemix.server import App, make_handler
from polemix.transcript import Debate, list_saved, load_saved


# ---------------------------------------------------------------- engine --
def run_demo(lineup="optimist-vs-skeptic", rounds=3, judge=False, stop_after=None):
    engine = DebateEngine(DemoClient(delay=0, seed=0), Settings())
    count = {"n": 0}

    def should_stop():
        count["n"] += 1
        return stop_after is not None and count["n"] > stop_after

    events = list(engine.run("Cats beat dogs", PRESETS[lineup], rounds=rounds, judge=judge, should_stop=should_stop))
    return engine.debate, events


def test_every_debater_speaks_every_round():
    debate, events = run_demo("roundtable", rounds=3)
    assert debate.status == "completed"
    assert len(debate.turns) == 9
    assert [t.phase for t in debate.turns[::3]] == ["opening", "rebuttal", "closing"]
    assert debate.synthesis
    assert events[0]["type"] == "start" and events[-1]["type"] == "end"


def test_judge_verdict_in_demo():
    debate, _ = run_demo(judge=True, rounds=1)
    assert debate.verdict["winner"] == "draw"
    assert set(debate.verdict["scores"]) == {"Optimist", "Skeptic"}


def test_stop_keeps_partial_transcript():
    debate, events = run_demo(stop_after=40)
    assert debate.status == "stopped"
    assert 0 < len(debate.turns) < 6
    assert events[-1]["type"] == "end"


def test_phases():
    assert [phase_for(r, 1) for r in [1]] == ["opening"]
    assert [phase_for(r, 4) for r in range(1, 5)] == ["opening", "rebuttal", "rebuttal", "closing"]


def test_empty_topic_rejected():
    with pytest.raises(ValueError):
        list(DebateEngine(DemoClient(delay=0), Settings()).run("  ", PRESETS["classic"]))


def test_clean_turn_and_verdict_parsing():
    assert clean_turn("**Skeptic:** No way.", "Skeptic") == "No way."
    assert clean_turn("Skeptic (round 2): Hmm", "Skeptic") == "Hmm"
    v = parse_verdict('blah {"winner":"SKEPTIC","scores":{"Skeptic":11,"Optimist":"4"},"reasoning":"x"}',
                      ["Optimist", "Skeptic"])
    assert v == {"winner": "Skeptic", "scores": {"Optimist": 4, "Skeptic": 10}, "reasoning": "x"}
    assert parse_verdict("not json", ["A"])["parse_error"]


# ------------------------------------------------------------- llm client --
def test_think_filter_across_chunk_boundaries():
    f = ThinkFilter()
    out = "".join(f.feed(c) for c in ["Hi <th", "ink>secret</th", "ink> there <", "b>"]) + f.flush()
    assert out == "Hi there <b>"


def test_real_client_streams_and_autodetects_model(fake_llm):
    client = OpenAICompatibleClient(Settings(base_url=fake_llm.base_url))
    assert client.model == "fake-chat-7b"  # embedding model skipped
    text = "".join(client.stream_chat([{"role": "user", "content": "hi"}], temperature=0.5, max_tokens=50))
    assert text == "I strongly believe this is right."
    req = fake_llm.requests[-1]
    assert req["stream"] is True and req["model"] == "fake-chat-7b" and req["max_tokens"] == 50


def test_full_debate_over_http(fake_llm):
    settings = Settings(base_url=fake_llm.base_url, rounds=2)
    engine = DebateEngine(OpenAICompatibleClient(settings), settings)
    list(engine.run("Motion", PRESETS["optimist-vs-skeptic"], judge=True))
    d = engine.debate
    assert d.status == "completed" and d.model == "fake-chat-7b"
    assert len(d.turns) == 4 and all("hidden" not in t.text for t in d.turns)
    assert d.verdict["winner"] == "Optimist" and d.verdict["scores"] == {"Optimist": 8, "Skeptic": 7}
    # later speakers see earlier turns
    assert "Optimist (round 1, opening)" in fake_llm.requests[1]["messages"][1]["content"]


def test_server_errors_are_reported(fake_llm):
    fake_llm.fail_status = 500
    settings = Settings(base_url=fake_llm.base_url, model="x")
    engine = DebateEngine(OpenAICompatibleClient(settings), settings)
    events = list(engine.run("Motion", PRESETS["classic"]))
    assert engine.debate.status == "failed"
    assert any(e["type"] == "error" and "model crashed" in e["message"] for e in events)


def test_unreachable_server():
    client = OpenAICompatibleClient(Settings(base_url="http://127.0.0.1:9/v1"))
    with pytest.raises(ServerUnavailable):
        client.list_models()


def test_no_models_loaded(fake_llm):
    fake_llm.models = []
    with pytest.raises(ModelError):
        OpenAICompatibleClient(Settings(base_url=fake_llm.base_url)).model


# ---------------------------------------------------------- config/personas --
def test_settings_from_env(tmp_path, monkeypatch):
    for k in ["POLEMIX_BASE_URL", "OPENAI_API_BASE", "OPENAI_BASE_URL", "POLEMIX_ROUNDS", "POLEMIX_MODEL"]:
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text('# comment\nPOLEMIX_BASE_URL="http://localhost:11434/v1/"\nPOLEMIX_ROUNDS=5\nPOLEMIX_MODEL=llama3\n')
    s = Settings.from_env(env)
    assert (s.base_url, s.rounds, s.model) == ("http://localhost:11434/v1", 5, "llama3")
    assert s.with_overrides(rounds=2, model=None).model == "llama3"
    with pytest.raises(ValueError):
        s.with_overrides(rounds=0)
    assert load_dotenv(tmp_path / "missing") == {}


def test_custom_lineup_file(tmp_path):
    f = tmp_path / "team.json"
    f.write_text(json.dumps({"title": "Team", "debaters": [
        {"name": "A", "instructions": "x", "stance": "for"}, {"name": "B"}]}))
    p = get_preset(str(f))
    assert [d.name for d in p.debaters] == ["A", "B"] and p.debaters[0].stance == "for"
    with pytest.raises(ValueError):
        preset_from_dict({"debaters": [{"name": "A"}, {"name": "A"}]})
    with pytest.raises(ValueError):
        get_preset("nope")


def test_example_lineup_loads():
    from pathlib import Path
    p = get_preset(str(Path(__file__).parent.parent / "examples" / "startup-board.json"))
    assert len(p.debaters) == 3


# ------------------------------------------------------------- transcript --
def test_save_load_roundtrip(tmp_path):
    debate, _ = run_demo(rounds=1, judge=True)
    jp, mp = debate.save(tmp_path)
    assert load_saved(jp).to_dict() == debate.to_dict()
    md = mp.read_text()
    assert "# Debate: Cats beat dogs" in md and "## Moderator synthesis" in md and "Judge's verdict" in md
    assert list_saved(tmp_path)[0]["topic"] == "Cats beat dogs"
    assert Debate.from_dict({**debate.to_dict(), "unknown": 1}).topic == "Cats beat dogs"


# -------------------------------------------------------------------- CLI --
def test_cli_run_demo(tmp_path, capsys):
    assert main(["run", "--demo", "-r", "1", "-j", "-o", str(tmp_path), "Pineapple", "on", "pizza"]) == 0
    out = capsys.readouterr().out
    assert "Pineapple on pizza" in out and "Saved:" in out
    assert len(list(tmp_path.glob("*.md"))) == 1
    assert main(["history", "-o", str(tmp_path)]) == 0
    assert main(["show", str(next(tmp_path.glob("*.json")))]) == 0


def test_cli_against_fake_server(fake_llm, tmp_path, capsys):
    assert main(["check", "--base-url", fake_llm.base_url]) == 0
    assert main(["models", "--base-url", fake_llm.base_url]) == 0
    assert "fake-chat-7b" in capsys.readouterr().out
    assert main(["run", "--base-url", fake_llm.base_url, "-r", "1", "--no-save", "Topic"]) == 0


def test_cli_errors(capsys):
    assert main(["check", "--base-url", "http://127.0.0.1:9/v1"]) == 1
    assert main(["run", "--demo", "-l", "nope", "x"]) == 2
    assert main(["lineups"]) == 0
    assert main([]) == 0


# -------------------------------------------------------------- web server --
@pytest.fixture
def web(tmp_path):
    app = App(Settings(output_dir=str(tmp_path)), demo=True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.read()


def _post(url, data):
    req = urllib.request.Request(url, json.dumps(data).encode(), {"Content-Type": "application/json"})
    return urllib.request.urlopen(req)


def test_web_pages_and_api(web):
    status, html = _get(web + "/")
    assert status == 200 and b"Polemix" in html
    assert _get(web + "/favicon.svg")[0] == 200
    assert json.loads(_get(web + "/api/info")[1])["demo"] is True
    assert len(json.loads(_get(web + "/api/lineups")[1])) == len(PRESETS)
    assert json.loads(_get(web + "/api/models")[1])["models"] == ["polemix-demo"]


def test_web_debate_stream_history_delete(web):
    with _post(web + "/api/debate", {"topic": "Test motion", "rounds": 1, "judge": True,
                                     "custom": {"debaters": [{"name": "Ann"}, {"name": "Bo"}]}}) as r:
        assert r.headers["Content-Type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in r.read().decode().split("\n") if line.startswith("data: ")]
    types = [e["type"] for e in events]
    assert types[0] == "start" and types[-1] == "end" and "verdict" in types
    file = events[-1]["file"]
    hist = json.loads(_get(web + "/api/history")[1])
    assert hist[0]["file"] == file
    saved = json.loads(_get(web + "/api/history/" + file)[1])
    assert [t["speaker"] for t in saved["turns"]] == ["Ann", "Bo"] and "markdown" in saved
    req = urllib.request.Request(web + "/api/history/" + file, method="DELETE")
    assert json.loads(urllib.request.urlopen(req).read())["ok"]
    assert json.loads(_get(web + "/api/history")[1]) == []


def test_web_rejects_bad_input(web):
    import urllib.error
    for body in [{"topic": ""}, {"topic": "x", "lineup": "nope"}, {"topic": "x", "rounds": 99}]:
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(web + "/api/debate", body)
        assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(web + "/api/history/..%2F..%2Fsecret.json")
    assert e.value.code == 400
