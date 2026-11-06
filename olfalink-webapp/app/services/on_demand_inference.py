"""Synchronous, user-triggered CNN inference; no inference on sensor ingest or GET."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from threading import Lock
from app.schemas import Dashboard, DeviceStatus, AnalysisResult, AnalysisStatus, Reading, Source, DescriptionEvidence
from app.services.live_stream import select_30s, select_ambient_30s
from app.services.combined_dashboard import get_model
from app.services.odor_narration import describe
from app.services.on_demand_store import LiveStore, MeasurementUnavailable

MIN_GAP_MS = 30_000
MODEL_LOCK = Lock()


def empty_dashboard(message: str) -> Dashboard:
    return Dashboard(mode="on_demand", latest=None, trend=[],
        analysis=AnalysisResult(status=AnalysisStatus.NOT_AVAILABLE, title="解析待ち", message=message),
        device=DeviceStatus(id="esp1", label="OlfaLink Sensor Unit", connected=False,
            last_seen=None, status="waiting", sensor_labels=["BME688","SGP41","TGS2602","TGS2603"]),
        features={"ai_enabled":True,"llm_enabled":False,"ingestion_enabled":True})


def previous_result(store: LiveStore, device_id: str) -> Dashboard:
    saved = store.latest_result(device_id)
    if not saved:
        return empty_dashboard("「においを解析する」を押すと、測定結果を表示します。")
    try:
        result = Dashboard.model_validate(saved["dashboard"])
        result.mode = "on_demand"
        # The result is historical, even when the sensor is still streaming.
        result.device.connected = False
        result.device.status = "previous_result"
        return result
    except (ValueError, TypeError, KeyError):
        return empty_dashboard("「においを解析する」を押してください。")


def infer_on_demand(store: LiveStore, device_id: str, llm_url: str | None = None) -> Dashboard:
    """A lock prevents duplicate CPU inference inside the single Cloud Run instance.

    Deployment additionally fixes --max-instances=1 and --concurrency=1.
    Results are persisted. A second click in the same 30s returns the cache.
    """
    with MODEL_LOCK:
        rows, session_id, latest_ms = store.window(device_id)
        saved = store.latest_result(device_id)
        if saved and saved.get("session_id") == session_id:
            age = int(time.time()*1000) - int(saved.get("computed_ms", 0))
            if (int(saved.get("end_ms", -1)) == latest_ms) or (0 <= age < MIN_GAP_MS):
                return Dashboard.model_validate(saved["dashboard"])
        try:
            if rows and rows[-1]["led"] == "ambient":
                trace, window_end = select_ambient_30s(rows)
                input_basis = "連続計測の直前60秒を基準にした暫定推定。独立検証なし。"
            else:
                trace, window_end = select_30s(rows)
                input_basis = "事前基準測定と直近30秒曝露を使用。30秒入力での精度未検証。"
        except (ValueError, IndexError, TypeError) as exc:
            raise MeasurementUnavailable(str(exc)) from exc

        from ml.cnn_multitask import predict_trace
        model, scales, _ = get_model()
        prediction = predict_trace(model, scales, trace)
        label = prediction["predicted_odor_label"]
        intensity = prediction["predicted_intensity_0_5"]
        score = prediction["classification_score_uncalibrated"]
        message, used_llm = describe(label, intensity, score, llm_url)
        stamp = datetime.fromtimestamp(window_end/1000, tz=timezone.utc)
        reading_id = f"on-demand-{device_id}-{session_id}-{window_end}"
        dashboard = Dashboard(
            mode="on_demand", latest=Reading(id=reading_id,device_id=device_id,
              timestamp=stamp,intensity=intensity,source=Source.DEVICE,channels={}), trend=[],
            analysis=AnalysisResult(status=AnalysisStatus.READY,
                title="においの特徴（研究用推定）", message=message,
                evidence=DescriptionEvidence(descriptor_scores={},
                    uncertainty=input_basis + " 分類スコアは確率として校正されていません。",
                    model_version=prediction["model_version"], source_reading_ids=[reading_id]),
                generated_by_llm=used_llm),
            device=DeviceStatus(id=device_id,label="OlfaLink Sensor Unit", connected=False,
                last_seen=stamp, status="previous_result",
                sensor_labels=["BME688","SGP41","TGS2602","TGS2603"]),
            features={"ai_enabled":True,"llm_enabled":used_llm,"ingestion_enabled":True},
        )
        store.save_result(device_id, session_id, window_end, dashboard.model_dump(mode="json"))
        return dashboard
