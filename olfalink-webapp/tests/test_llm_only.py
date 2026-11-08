"""v1.2: a classifier result must never be presented as LLM text without LLM execution."""
from __future__ import annotations

from app.services import odor_narration as narrator


def test_without_llm_url_uses_labeled_dictionary():
    message, llm = narrator.describe("ammonia", 5, 0.99, None)
    assert llm is False
    assert "アンモニア" in message
    assert "説明辞書" in message


def test_no_signal_does_not_call_llm(monkeypatch):
    def fail(*args):
        raise AssertionError("No LLM for absent signal")
    monkeypatch.setattr(narrator, "remote_description", fail)
    text, used = narrator.describe("ammonia", 0, 0.99, "https://example.run.app")
    assert not used and text == narrator.NO_SIGNAL


def test_uncertain_does_not_call_llm(monkeypatch):
    def fail(*args):
        raise AssertionError("No LLM for uncertain classification")
    monkeypatch.setattr(narrator, "remote_description", fail)
    text, used = narrator.describe("ammonia", 4, .3, "https://example.run.app")
    assert not used and narrator.UNCERTAIN in text
    assert "説明辞書" in text


def test_generated_text_only_if_actual_llm_response(monkeypatch):
    monkeypatch.setattr(narrator, "remote_description", lambda *args:
                        ("鼻にツンとくる鋭いにおいに似ています。", True))
    text, used = narrator.describe("ammonia", 5, .95, "https://example.run.app")
    assert used is True
    assert "鼻にツンとくる" in text


def test_remote_failure_uses_explicit_dictionary(monkeypatch):
    def fail(*args):
        raise RuntimeError("test synthetic GPU not available")
    monkeypatch.setattr(narrator, "remote_description", fail)
    text, used = narrator.describe("shoe", 3, .95, "https://example.run.app")
    assert used is False
    assert "靴" in text and "説明辞書" in text


def test_url_validation_rejects_non_https_and_path():
    import pytest
    for url in ("http://example.run.app", "https://example.run.app/secret", "https://u:p@example.run.app"):
        with pytest.raises(ValueError):
            narrator.remote_description(url, "ammonia", 4, .8)
