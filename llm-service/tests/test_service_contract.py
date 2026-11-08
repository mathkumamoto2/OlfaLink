"""GPU-independent checks for the fine-tuned inference service contract."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

import service


def test_adapter_is_real_and_matches_base():
    root = Path(__file__).resolve().parents[1]
    adapter_path = root / "adapter" / "adapter_model.safetensors"
    if not adapter_path.exists():
        # The public GitHub source intentionally excludes private LoRA weights.
        # Validate the adapter when the owner installs it for a private GPU build.
        pytest.skip("private LoRA adapter is not distributed in GitHub source")
    assert adapter_path.stat().st_size > 1_000_000
    config = json.loads((root / "adapter" / "adapter_config.json").read_text())
    assert config["peft_type"] == "LORA"
    assert config["base_model_name_or_path"] == "unsloth/Qwen3.5-2B"


def test_llm_service_rejects_unsupported_label():
    with pytest.raises(Exception) as exc:
        service.generate(service.Request(label="not_supported", intensity=4, uncalibrated_score=.98))
    assert getattr(exc.value, "status_code", None) == 422


def test_zero_level_avoids_gpu(monkeypatch):
    monkeypatch.setattr(service, "load", lambda: (_ for _ in ()).throw(AssertionError("GPU must not start")))
    out = service.generate(service.Request(label="ammonia", intensity=0, uncalibrated_score=.99))
    assert out["model_used"] is False


def test_high_score_uses_real_generation_path(monkeypatch):
    import src.inference
    monkeypatch.setattr(service, "load", lambda: (object(), object()))
    seen = {}
    def fake_describe(model, processor, payload, allowed):
        seen["payload"] = payload
        return "アンモニアのような鋭いにおいに似ています。"
    monkeypatch.setattr(src.inference, "describe", fake_describe)
    out = service.generate(service.Request(label="ammonia", intensity=5, uncalibrated_score=.96))
    assert out["model_used"] is True
    assert seen["payload"]["prediction"] == "NH3"
    assert "アンモニア" in out["text"]
