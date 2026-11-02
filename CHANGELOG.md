# Changelog

## 1.0.0 — 2026-11-02

First complete release, renamed from *local-debate-simulator* to **Polemix**.

- Installable package with a `polemix` command (`run`, `serve`, `models`, `check`, `lineups`, `history`, `show`).
- Local web app with live streaming, line-up editor, stop button, saved history and Markdown/JSON export.
- Every debater now speaks in every round (opening, rebuttal, closing); previously the skeptic could be skipped in the last round.
- Moderator synthesis plus optional judge's verdict with scores.
- Built-in line-ups and custom JSON/YAML line-ups (2–6 debaters).
- Model auto-detection; works with LM Studio, Ollama, llama.cpp, vLLM and other OpenAI-compatible servers.
- No more hard-coded model, URL or topic: everything is configurable via flags, environment or `.env`.
- Strips `<think>` reasoning blocks from reasoning models.
- Clear errors when the server is down or no model is loaded; demo mode for trying it without a model.
- Replaced LangChain/LangGraph with a small standard-library client: zero required dependencies.
- Test suite (runs without a model) and CI on Linux, macOS and Windows.
