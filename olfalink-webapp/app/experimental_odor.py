"""Opt-in research endpoint for ONE full baseline→exposure→refresh session.

Protected with EXPERIMENTAL_ODOR_API_KEY, off by default. This intentionally
is not wired to the public/dashboard status before prospective validation.
"""
from __future__ import annotations
from functools import lru_cache
import hmac
import os
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from ml.session_timeseries import CHANNELS, MIN_PHASE_ROWS
from ml.predict import load_model, predict_session
from ml.intensity import load_calibration, predict_intensity

MODEL_FILE = Path(__file__).resolve().parent.parent / "models" / "odor_session_templates.json"
INTENSITY_MODEL_FILE = MODEL_FILE.parent / "intensity_calibration.json"
CNN_MODEL_FILE = MODEL_FILE.parent / "intensity_cnn_v0_6.pt"
MULTITASK_MODEL_FILE = MODEL_FILE.parent / "odor_intensity_multitask_v0_7.pt"
MAX_CSV_BYTES = 2_000_000
router = APIRouter(prefix="/api/v1/experimental/odor", tags=["experimental-odor"])


class CompleteSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline: list[dict[str, float]] = Field(min_length=MIN_PHASE_ROWS["baseline"], max_length=2400)
    exposure: list[dict[str, float]] = Field(min_length=MIN_PHASE_ROWS["exposure"], max_length=2400)
    refresh: list[dict[str, float]] = Field(min_length=MIN_PHASE_ROWS["refresh"], max_length=2400)

    @field_validator("baseline", "exposure", "refresh")
    @classmethod
    def validate_channels(cls, rows: list[dict[str, float]]) -> list[dict[str, float]]:
        from math import isfinite
        for row in rows:
            if any(key not in row or not isfinite(row[key]) for key in CHANNELS):
                raise ValueError("Each sample must contain all seven finite sensor channels")
        return rows


@lru_cache(maxsize=1)
def get_model() -> dict:
    return load_model(MODEL_FILE)


@lru_cache(maxsize=1)
def get_intensity_model() -> dict:
    return load_calibration(INTENSITY_MODEL_FILE)


def require_key(key: str | None) -> None:
    expected = os.getenv("EXPERIMENTAL_ODOR_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="Experimental inference disabled")
    if not key or not hmac.compare_digest(key, expected):
        raise HTTPException(status_code=401, detail="Invalid experimental API credential")


def to_chronological_rows(body: CompleteSession) -> list[dict]:
    rows = []
    ts = 0
    for phase in ("baseline", "exposure", "refresh"):
        for row in getattr(body, phase):
            rows.append({**row, "led": phase, "epoch_ms": ts})
            ts += 2000
    return rows


@router.get("/status")
def status() -> dict:
    if not MODEL_FILE.exists():
        return {"model_present": False, "production_validated": False}
    model = get_model()
    return {"model_present": True, "production_validated": False,
            "session_count": len(model["templates"]),
            "observed_intensity_levels": model["observed_levels"],
            "model_version": model["version"], "required_channels": CHANNELS,
            "required_phases": ("baseline", "exposure", "refresh"),
            "intensity_model_present": INTENSITY_MODEL_FILE.exists(),
            "intensity_model_validated": False,
            "cnn_model_present": CNN_MODEL_FILE.exists(),
            "cnn_model_validated_on_independent_real_data": False,
            "multitask_model_present": MULTITASK_MODEL_FILE.exists(),
            "multitask_model_validated_on_independent_real_data": False}


@router.post("/predict")
def predict(body: CompleteSession, x_odor_api_key: str | None = Header(default=None)) -> dict:
    require_key(x_odor_api_key)
    if not MODEL_FILE.exists():
        raise HTTPException(status_code=503, detail="Model not installed")
    # The API payload is one full chronological recording, not independent windows.
    try:
        result = predict_session(get_model(), to_chronological_rows(body))
        if INTENSITY_MODEL_FILE.exists():
            strength = predict_intensity(get_intensity_model(), to_chronological_rows(body))
            result["predicted_intensity_0_5"] = strength["predicted_intensity_0_5"]
            result["predicted_intensity_continuous"] = strength["predicted_intensity_continuous"]
            result["intensity_basis"] = "exploratory_monotone_sensor_response_regression"
            result["intensity_warning_ja"] = strength["warning_ja"]
            result["warning_ja"] = "においの種類は3セッションのテンプレート照合で未検証。" + strength["warning_ja"]
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/intensity/predict")
def predict_intensity_json(body: CompleteSession, x_odor_api_key: str | None = Header(default=None)) -> dict:
    """JSON input in 3 phases; intensity estimate only, no forced odor identity."""
    require_key(x_odor_api_key)
    if not INTENSITY_MODEL_FILE.exists():
        raise HTTPException(status_code=503, detail="Intensity calibration not installed")
    try:
        return predict_intensity(get_intensity_model(), to_chronological_rows(body))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/intensity/predict-csv")
async def predict_intensity_raw_csv(request: Request, x_odor_api_key: str | None = Header(default=None)) -> dict:
    """Upload exactly one original ESP1 CSV as text/csv bytes. Data not persisted."""
    from ml.session_timeseries import parse_csv
    require_key(x_odor_api_key)
    if not INTENSITY_MODEL_FILE.exists():
        raise HTTPException(status_code=503, detail="Intensity calibration not installed")
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_CSV_BYTES:
            raise HTTPException(status_code=413, detail="CSV exceeds 2 MB")
    try:
        text = payload.decode("utf-8-sig")
        rows = parse_csv(text, "uploaded session")
        return predict_intensity(get_intensity_model(), rows)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@lru_cache(maxsize=1)
def get_cnn_model():
    # Lazy import keeps the original lightweight demo working when PyTorch is
    # not installed; the research-only CNN route needs requirements-ml.txt.
    from ml.cnn_intensity import load_model
    return load_model(CNN_MODEL_FILE)


@router.post("/intensity/cnn/predict-csv")
async def predict_cnn_intensity_raw_csv(
    request: Request, x_odor_api_key: str | None = Header(default=None)
) -> dict:
    """One complete measurement -> research 1D-CNN, no raw data persistence."""
    require_key(x_odor_api_key)
    if not CNN_MODEL_FILE.is_file():
        raise HTTPException(status_code=503, detail="CNN checkpoint not installed")
    try:
        from ml.cnn_intensity import predict_csv_text
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Install requirements-ml.txt for CNN inference") from exc
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_CSV_BYTES:
            raise HTTPException(status_code=413, detail="CSV exceeds 2 MB")
    try:
        model, scales, _metadata = get_cnn_model()
        return predict_csv_text(model, scales, payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@lru_cache(maxsize=1)
def get_multitask_model():
    # Keep demo mode usable without the optional ML runtime dependency.
    from ml.cnn_multitask import load_model
    return load_model(MULTITASK_MODEL_FILE)


@router.post('/multitask/cnn/predict-csv')
async def predict_cnn_multitask_raw_csv(
    request: Request, x_odor_api_key: str | None = Header(default=None)
) -> dict:
    """Return classified odor label (for LLM) and 0..5 intensity (for UI).

    One entire session, no raw CSV storage. Strongly experimental.
    """
    require_key(x_odor_api_key)
    if not MULTITASK_MODEL_FILE.is_file():
        raise HTTPException(status_code=503, detail='Multitask model checkpoint not installed')
    try:
        from ml.cnn_multitask import predict_csv_text
    except ImportError as exc:
        raise HTTPException(status_code=503, detail='Install requirements-ml.txt for AI inference') from exc
    payload=bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload)>MAX_CSV_BYTES:
            raise HTTPException(status_code=413, detail='CSV exceeds 2 MB')
    try:
        model,scales,_=get_multitask_model()
        return predict_csv_text(model,scales,payload.decode('utf-8-sig'))
    except (UnicodeDecodeError,ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
