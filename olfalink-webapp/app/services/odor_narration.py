"""Odor descriptions: verified LLM response when available, explicit dictionary fallback.

The label is an unvalidated CNN hypothesis, NOT a chemical identification.
Local templates are authored for OlfaLink and are not external literature quotes.
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

LOG = logging.getLogger(__name__)
NO_SIGNAL = "センサー反応がごく弱いため、特徴的なにおいを推定できませんでした。"
UNCERTAIN = "分類結果が不確かです。以下は予測された種類に対応する参考説明であり、実際のにおいの特定ではありません。"
UNAVAILABLE = "においの種類に対応する説明が見つかりませんでした。"

# CNNの学習ラベルに対応した説明。物質・濃度・安全性の同定はしない。
DESCRIPTIONS: dict[str, str] = {
    "room": "室内の空気に近い特徴が推定されました。目立った特定のにおいを示すものではありません。",
    "vinegar": "酢のような酸味を連想させ、鼻にツンとくるにおいに似た特徴です。",
    "shoe": "蒸れた靴やこもった空気を連想させるにおいに似た特徴です。",
    "ammonia": "アンモニアを連想させる、鼻にツンとくる刺激的なにおいに似た特徴です。",
    "tabasco": "タバスコのような酸味と刺激を連想させるにおいに似た特徴です。",
    "sardine_oil": "魚の油を連想させる、独特の濃いにおいに似た特徴です。",
    "food_waste": "生ごみを連想させる、湿った有機物のようなにおいに似た特徴です。",
}

# This classifier score is not a calibrated probability.
EXPERIMENTAL_SCORE_CUTOFF = 0.75


def local_description(label: str, intensity: int) -> str:
    """Generate a clearly hedged reference description from the prediction label."""
    if intensity <= 0:
        return NO_SIGNAL
    template = DESCRIPTIONS.get(label)
    if template is None:
        return UNAVAILABLE
    return f"【説明辞書による参考情報】{template} CNNの予測に基づく説明であり、成分や安全性は判定していません。"


def remote_description(url: str, label: str, intensity: int, score: float, *,
                       demo_mode: bool = False) -> tuple[str, bool]:
    """Call a private Cloud Run LLM with an identity token; never expose the token."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("LLM_SERVICE_URL must be a bare HTTPS Cloud Run service origin")
    if parsed.path not in ("", "/"):
        raise ValueError("LLM_SERVICE_URL must not contain a path")

    import google.auth.transport.requests
    import google.oauth2.id_token

    origin = url.rstrip("/")
    token = google.oauth2.id_token.fetch_id_token(
        google.auth.transport.requests.Request(), origin
    )
    payload = {"label": label, "intensity": intensity, "uncalibrated_score": score,
               "demo_mode": demo_mode}
    # GPU cold starts are slow. Avoid indefinite blocking of the public UI.
    with httpx.Client(timeout=httpx.Timeout(220.0, connect=15.0), follow_redirects=False) as client:
        response = client.post(origin + "/generate", json=payload,
            headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        content = response.json()
    text = content.get("text")
    if content.get("model_used") is not True or not isinstance(text, str) or not 2 <= len(text.strip()) <= 350:
        raise ValueError("LLM did not return a validated generated description")
    return text.strip(), True


def describe(label: str, intensity: int, score: float, url: str | None = None, *,
             demo_mode: bool = False) -> tuple[str, bool]:
    """Return text and whether an LLM *actually generated* it.

    For missing configuration, low CNN score, GPU timeout/error or invalid LLM
    response, use the local dictionary, never pretend the dictionary is an LLM.
    """
    if intensity <= 0 and not (demo_mode and url):
        return NO_SIGNAL, False
    if label not in DESCRIPTIONS:
        # Never fabricate a category-specific description for an unknown class.
        return UNAVAILABLE, False
    if not 0 <= score <= 1:
        return UNCERTAIN + " " + local_description(label, intensity), False
    if not demo_mode and score < EXPERIMENTAL_SCORE_CUTOFF:
        return UNCERTAIN + " " + local_description(label, intensity), False
    if not url:
        return local_description(label, intensity), False
    try:
        if demo_mode:
            text, generated = remote_description(url, label, intensity, score, demo_mode=True)
        else:
            text, generated = remote_description(url, label, intensity, score)
        # Guard against an incorrectly configured backend claiming a dictionary
        # or static response as model-generated text.
        if generated is not True or not isinstance(text, str) or not 2 <= len(text.strip()) <= 350:
            raise ValueError("LLM response has no verified generation flag")
        return text.strip(), True
    except Exception as exc:
        # Prevent secrets, raw HTTP bodies or internal errors from leaking to UI.
        LOG.warning("LLM failed; using local dictionary: %s", type(exc).__name__)
        return local_description(label, intensity), False


def description_source(label: str, intensity: int, used_llm: bool) -> str:
    """A stable provenance field for UI and API consumers."""
    if used_llm:
        return "llm"
    if intensity <= 0:
        return "no_signal"
    return "dictionary" if label in DESCRIPTIONS else "unavailable"
