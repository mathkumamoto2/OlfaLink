"""Shared, explicit contract for OlfaLink description generation.

Input comes from a classifier. A high confidence number is NOT proof of a molecule.
The LLM must never decide whether an atmosphere is safe to breathe.
"""
from __future__ import annotations

import json
from pathlib import Path

CONFIDENCE_THRESHOLD = 0.75
SYSTEM = (
    "あなたはOlfaLinkのにおい説明アシスタントです。"
    "入力JSONの分類結果と特徴語だけを根拠に、においの印象を自然な日本語で1〜2文で説明してください。"
    "物質・発生源の存在を断定せず、『〜に似た』『〜を連想させる』を使ってください。"
    "センサー反応レベルは人間の嗅覚強度や安全性の指標ではありません。"
    "classification_confidenceが0.75未満、またはsensor_reaction_levelが0のときは種類を断定せず、"
    "十分な特徴を説明できないと述べてください。"
    "健康、安全、危険、原因の断定や、入力にない性質を付け加えてはいけません。"
    "input_originがsynthetic_demoのときは実測ではない合成データによる例示です。"
    "classification_confidenceが低くても、demo_mode=trueなら不確実性を明記した慎重な例示文を生成してください。"
    "sensor_reaction_levelが0のときは、特徴を特定できない旨だけを述べてください。"
    "箇条書き・前置き・推論過程は不要です。"
)

NO_SIGNAL = "現在の反応レベルでは、においの種類や特徴を特定できません。"
UNCERTAIN = "現在の推定結果だけでは、においの種類を十分な確かさで特定できません。"
UNKNOWN = "対応していない分類ラベルのため、においの特徴を説明できません。"


def load_jsonl(path: str | Path) -> list[dict]:
    """Read UTF-8 JSONL with line-specific diagnostics."""
    rows = []
    with Path(path).open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{lineno}: invalid JSON") from exc
    return rows


def prompt_messages(payload: dict) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
    ]


def to_prompt(tokenizer, payload: dict) -> str:
    # Qwen3.5 may return a multimodal processor. Use its TEXT tokenizer.
    text_tok = getattr(tokenizer, "tokenizer", tokenizer)
    return text_tok.apply_chat_template(
        prompt_messages(payload), tokenize=False, add_generation_prompt=True
    )


def known_ids(catalog_file: str | Path) -> set[str]:
    doc = json.loads(Path(catalog_file).read_text(encoding="utf-8"))
    return {c["odor_id"] for c in doc["classes"] if c["status"] == "used"}


def guardrail(payload: dict, allowed_ids: set[str]) -> str | None:
    """Deterministic policy gate before prompting the model."""
    level = payload.get("sensor_reaction_level")
    confidence = payload.get("classification_confidence")
    prediction = payload.get("prediction")

    if type(level) is not int or not 0 <= level <= 5:
        raise ValueError("sensor_reaction_level must be an integer 0..5")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        raise ValueError("classification_confidence must be a number 0..1")
    if level == 0:
        return NO_SIGNAL
    if confidence < CONFIDENCE_THRESHOLD:
        return UNCERTAIN
    if prediction not in allowed_ids:
        return UNKNOWN
    return None
