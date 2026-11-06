"""Label-conditioned descriptions. Remote LoRA service is optional and authenticated."""
from __future__ import annotations
from functools import lru_cache
import os
import httpx

DESCRIPTIONS = {
    "room": ("室内の空気", "目立ったにおいの特徴は強く推定されません。"),
    "vinegar": ("酢", "酢に似た、鼻に残る酸味を思わせるにおいです。"),
    "shoe": ("靴", "蒸れた靴を連想させる、こもったようなにおいに似ています。"),
    "ammonia": ("アンモニア", "アンモニア系に似た、鼻にツンとくる鋭いにおいです。"),
    "tabasco": ("タバスコ", "酸味と刺激を連想させる、タバスコのようなにおいに似ています。"),
    "sardine_oil": ("イワシ油", "魚の油を連想させる、独特の濃いにおいに似ています。"),
    "food_waste": ("生ごみ", "生ごみのような、湿った有機物を連想させるにおいに似ています。"),
}

# Names are hypotheses; no identification of chemical constituents.

def local_description(label: str, intensity: int) -> str:
    if intensity == 0:
        return "センサーの反応はごく弱く、特徴的なにおいは推定できませんでした。"
    return DESCRIPTIONS.get(label, ("不明", "においの種類を推定できませんでした。"))[1]


def remote_description(url: str, label: str, intensity: int, score: float) -> tuple[str, bool] | None:
    if not url:
        return None
    if not url.startswith("https://"):
        raise ValueError("LLM_SERVICE_URL must use https")
    # Authenticate a private Cloud Run service with an OIDC ID token.
    import google.auth.transport.requests
    import google.oauth2.id_token
    request = google.auth.transport.requests.Request()
    token = google.oauth2.id_token.fetch_id_token(request, url.rstrip("/"))
    payload = {"label": label, "intensity": intensity, "uncalibrated_score": score}
    with httpx.Client(timeout=httpx.Timeout(75, connect=10), follow_redirects=False) as client:
        response = client.post(url.rstrip("/") + "/generate", json=payload, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        content = response.json()
    message = content.get("text", "")
    if not isinstance(message, str) or not 2 <= len(message.strip()) <= 350:
        raise ValueError("Invalid LLM response")
    return message.strip(), content.get("model_used") is True


def describe(label: str, intensity: int, score: float, url: str | None = None) -> tuple[str, bool]:
    if intensity == 0:
        return local_description(label, intensity), False
    try:
        result = remote_description(url or "", label, intensity, score)
        if result:
            return result
    except Exception:
        # A failed LLM request must not interrupt inference or impersonate an LLM response.
        pass
    return local_description(label, intensity), False
