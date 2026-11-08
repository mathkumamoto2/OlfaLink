"""Text-only generation path for a LoRA-tuned Qwen3.5 model.

The Qwen3.5 model may expose a multimodal PROCESSOR. Passing a text string
positionally to the processor can make it try base64 image decoding. Always
unwrap .tokenizer first. That avoids the previous 'Incorrect padding' error.
"""
from __future__ import annotations

import re

import torch

from src.common import guardrail, to_prompt


def decode_text(model, processor_or_tokenizer, payload: dict, *, max_new_tokens: int = 120) -> str:
    text_tok = getattr(processor_or_tokenizer, "tokenizer", processor_or_tokenizer)
    model.eval()
    prompt = to_prompt(text_tok, payload)
    encoded = text_tok(text=prompt, return_tensors="pt", add_special_tokens=False)
    device = next(model.parameters()).device
    encoded = {key: tensor.to(device) for key, tensor in encoded.items()}

    with torch.inference_mode():
        result = model.generate(
            **encoded,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=text_tok.pad_token_id or text_tok.eos_token_id,
            eos_token_id=text_tok.eos_token_id,
        )
    completion_ids = result[0, encoded["input_ids"].shape[-1]:]
    output = text_tok.decode(completion_ids, skip_special_tokens=True)
    # Even though the small checkpoint defaults to no-thinking, strip any
    # accidentally exposed thought markup before showing user-visible text.
    output = re.sub(r"<think>.*?</think>", "", output, flags=re.S)
    output = output.replace("<|im_end|>", "").strip()
    return output


def describe(model, processor, payload: dict, allowed_ids: set[str], *, demo_mode: bool = False) -> str:
    """Rule-based refusal cases bypass generation; valid cases use the fine-tuned LLM."""
    fallback = guardrail(payload, allowed_ids)
    if fallback is not None and not demo_mode:
        return fallback
    if demo_mode:
        # Bypass only the uncertainty/no-signal abstention for a clearly
        # identified *synthetic demonstration*. Never bypass unknown label IDs.
        if payload.get("prediction") not in allowed_ids:
            return fallback or "対応していない分類ラベルです。"
    return decode_text(model, processor, payload)
