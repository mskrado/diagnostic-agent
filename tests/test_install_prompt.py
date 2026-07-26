"""Unit tests for the validated console prompts used by `diag install`."""
from __future__ import annotations

import pytest

from app.install.prompt import (
    PromptAborted,
    Prompter,
    container_rewrite,
    normalize_url,
)


def _answers(monkeypatch, *values: str) -> None:
    seq = iter(values)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(seq))


def test_normalize_url_upgrades_bare_host_port():
    assert normalize_url("127.0.0.1:9090") == "http://127.0.0.1:9090"
    assert normalize_url("http://loki:3100/") == "http://loki:3100"
    assert normalize_url("https://grafana.example.com") == "https://grafana.example.com"


def test_normalize_url_rejects_garbage():
    assert normalize_url("") is None
    assert normalize_url("   ") is None
    assert normalize_url("ftp://prometheus:9090") is None
    assert normalize_url("http://") is None


def test_container_rewrite_only_touches_loopback():
    assert container_rewrite("http://127.0.0.1:9090") == (
        "http://host.docker.internal:9090"
    )
    assert container_rewrite("http://localhost:3100") == (
        "http://host.docker.internal:3100"
    )
    assert container_rewrite("http://prometheus:9090") is None
    assert container_rewrite("") is None


def test_url_prompt_reasks_until_valid(monkeypatch, capsys):
    _answers(monkeypatch, "not a url", "still::bad", "http://prom:9090")
    prompter = Prompter()
    assert prompter.url("Prometheus URL") == "http://prom:9090"
    assert "not a valid http(s) URL" in capsys.readouterr().out


def test_port_prompt_rejects_non_numeric(monkeypatch):
    _answers(monkeypatch, "abc", "99999", "1025")
    assert Prompter().port("SMTP port", default=587) == 1025


def test_choice_prompt_is_case_insensitive(monkeypatch):
    _answers(monkeypatch, "SPRING-MICROMETER")
    prompter = Prompter()
    value = prompter.choice(
        "Preset", ["generic-prometheus", "spring-micrometer"], default="generic-prometheus"
    )
    assert value == "spring-micrometer"


def test_choice_prompt_reasks_on_unknown(monkeypatch):
    _answers(monkeypatch, "postgres", "ollama")
    assert Prompter().choice("Provider", ["ollama", "openai"], default="ollama") == (
        "ollama"
    )


def test_yes_no_defaults_on_empty_input(monkeypatch):
    _answers(monkeypatch, "", "")
    prompter = Prompter()
    assert prompter.yes_no("Enable?", default=True) is True
    assert prompter.yes_no("Enable?", default=False) is False


def test_empty_input_keeps_default(monkeypatch):
    _answers(monkeypatch, "")
    assert Prompter().url("Loki URL", default="http://loki:3100") == (
        "http://loki:3100"
    )


def test_required_text_aborts_when_never_provided(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "")
    with pytest.raises(PromptAborted):
        Prompter().text("AWS region", allow_empty=False)


def test_non_interactive_returns_defaults_without_stdin():
    def _explode(*_a, **_k):  # pragma: no cover - must not be called
        raise AssertionError("non-interactive prompter must not read stdin")

    import builtins

    original = builtins.input
    builtins.input = _explode
    try:
        prompter = Prompter(interactive=False)
        assert prompter.url("Prometheus", default="http://prom:9090") == (
            "http://prom:9090"
        )
        assert prompter.yes_no("Enable?", default=True) is True
        assert prompter.secret("TOKEN") == ""
    finally:
        builtins.input = original


def test_accept_defaults_echoes_choice(capsys):
    prompter = Prompter(accept_defaults=True)
    assert prompter.port("SMTP port", default=1025) == 1025
    assert "accepted default" in capsys.readouterr().out


def test_accept_defaults_aborts_on_invalid_default():
    with pytest.raises(PromptAborted):
        Prompter(accept_defaults=True).url("Prometheus URL", default="")


def test_eof_falls_back_to_default(monkeypatch):
    def _eof(*_a, **_k):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert Prompter().url("Loki", default="http://loki:3100") == "http://loki:3100"
