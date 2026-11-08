"""Private, optional Qwen3.5-2B LoRA narrator for OlfaLink Cloud Run GPU.

Cloud Run IAM, not an in-app shared secret, authenticates callers. Never publish this
service with --allow-unauthenticated. The actual model and LoRA must be available.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from threading import Lock
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import os

from src.common import NO_SIGNAL, UNCERTAIN

LABELS = {
    "room": ("MYROOM", "室内の空気", []),
    "vinegar": ("CH3COOH_Z02", "酢", ["酸っぱい", "酢のような", "ツンとする"]),
    "shoe": ("SHOE", "靴", ["蒸れた靴のような", "こもった", "汗を連想させる"]),
    "ammonia": ("NH3", "アンモニア", ["鼻にツンとくる", "鋭い", "刺激的"]),
    "tabasco": ("TABASCO", "タバスコ", ["酢のような酸味", "香辛料を連想させる", "刺激的な印象"]),
    "sardine_oil": ("OIL_SARDINE", "イワシ油", ["魚のような", "油っぽい", "生臭さを連想させる"]),
    "food_waste": ("FOOD_WASTE", "生ごみ", ["こもった", "発酵したような", "混ざり合った生活臭"]),
}

class Request(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    intensity: int = Field(ge=0, le=5)
    uncalibrated_score: float = Field(ge=0.0, le=1.0)
    demo_mode: bool = False


@lru_cache(maxsize=1)
def load():
    import torch
    from transformers import AutoTokenizer, Qwen3_5ForConditionalGeneration
    from peft import PeftModel
    if not torch.cuda.is_available():
        raise RuntimeError("LLM service needs Cloud Run NVIDIA L4 GPU")
    base = os.environ.get("BASE_MODEL_PATH", "/srv/base")
    model = Qwen3_5ForConditionalGeneration.from_pretrained(
        base, dtype=torch.bfloat16, device_map="cuda", attn_implementation="sdpa"
    )
    model = PeftModel.from_pretrained(model, "/srv/adapter", is_trainable=False)
    model.eval()
    tokenizer = AutoTokenizer.from_pretrained("/srv/adapter", use_fast=True)
    return model, tokenizer

lock = Lock()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-load weights for predictable readiness. Build-time model download is used.
    load()
    yield

app = FastAPI(title="OlfaLink Private Narrator", docs_url=None, redoc_url=None, lifespan=lifespan)

@app.get('/healthz')
def healthz():
    return {"status": "ok", "adapter": "OlfaLink-v0.2", "gpu_required": True}

@app.post('/generate')
def generate(body: Request):
    if body.label not in LABELS:
        raise HTTPException(status_code=422, detail="Unsupported label")
    if body.intensity == 0 and not body.demo_mode:
        return {"text": NO_SIGNAL, "model_used": False}
    # Note: CNN softmax is NOT calibrated. This threshold is an experimental
    # abstention heuristic, not a validated physical uncertainty boundary.
    if body.uncalibrated_score < 0.75 and not body.demo_mode:
        return {"text": UNCERTAIN, "model_used": False}
    key, name, tags = LABELS[body.label]
    if body.label == "vinegar" and body.intensity <= 2:
        key, tags = "CH3COOH_Z05", ["酸っぱい", "酢のような", "すっきりした酸味"]
    payload = {
        "prediction": key,
        "prediction_label": name,
        "sensor_reaction_level": body.intensity,
        "classification_confidence": body.uncalibrated_score,
        "descriptor_candidates": tags,
        "input_origin": "synthetic_demo" if body.demo_mode else "device",
        "demo_mode": body.demo_mode,
    }
    from src.inference import describe
    with lock:  # avoid simultaneous generation spikes on one GPU
        model, processor = load()
        ids = set(x[0] for x in LABELS.values()) | {"CH3COOH_Z05"}
        if body.demo_mode:
            text = describe(model, processor, payload, ids, demo_mode=True)
        else:
            text = describe(model, processor, payload, ids)
    if not text or len(text) > 350 or any(x in text for x in ("安全です", "危険です", "健康に問題", "毒性は")):
        raise HTTPException(status_code=502, detail="Narrator output failed validation")
    return {"text": text, "model_used": True}
