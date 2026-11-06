"""Historical AWS session -> pretrained CNN -> grounded Japanese explanation."""
from __future__ import annotations
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from app.schemas import AnalysisResult, AnalysisStatus, Dashboard, DeviceStatus, Reading, Source, DescriptionEvidence
from app.services.aws_sessions import subscriber, ranked_sessions, SourceUnavailable
from app.services.odor_narration import describe

MODEL = Path(__file__).resolve().parent.parent.parent / "models" / "odor_intensity_multitask_v0_7.pt"

@lru_cache(maxsize=1)
def get_model():
    from ml.cnn_multitask import load_model
    return load_model(MODEL)


def make_empty(message: str) -> Dashboard:
    return Dashboard(
        mode="aws", latest=None, trend=[],
        analysis=AnalysisResult(status=AnalysisStatus.NOT_AVAILABLE, title="未取得", message=message),
        device=DeviceStatus(id="esp1", label="OlfaLink Sensor Unit", connected=False,
                            last_seen=None, status="historical", sensor_labels=["BME688", "SGP41", "TGS2602", "TGS2603"]),
        features={"ai_enabled":True, "llm_enabled":False, "ingestion_enabled":False}
    )


def aws_dashboard(read_key: str, llm_url: str | None = None) -> Dashboard:
    if not MODEL.exists():
        return make_empty("分類モデルを読み込めません。")
    try:
        rows, manifest = subscriber.fetch(read_key)
    except SourceUnavailable:
        return make_empty("AWSの測定データを取得できませんでした。")
    from ml.cnn_multitask import predict_trace
    from ml.session_timeseries_v06 import session_trace
    model, scales, _ = get_model()
    for sid, measurements in ranked_sessions(rows):
        try:
            # Do not truncate a session to 30s: v0.7 was trained on whole sessions.
            trace, counts = session_trace(measurements)
            prediction = predict_trace(model, scales, trace)
        except (ValueError, IndexError, TypeError):
            continue
        timestamp = datetime.fromtimestamp(measurements[-1]["epoch_ms"] / 1000.0, tz=timezone.utc)
        sample_id = f"aws-{sid}-{int(measurements[-1]['epoch_ms'])}"
        intensity = prediction["predicted_intensity_0_5"]
        label = prediction["predicted_odor_label"]
        score = prediction["classification_score_uncalibrated"]  # uncalibrated; not a probability
        sentence, llm_used = describe(label, intensity, score, llm_url)
        latest = Reading(
            id=sample_id, device_id="esp1", timestamp=timestamp,
            intensity=intensity, source=Source.DEVICE,
            # Never expose raw training sensor values in the publicly accessible response.
            channels={}
        )
        # The uncalibrated classification score is NOT a probability; do not assign it
        # to descriptor_scores as if it were a calibrated confidence value.
        analysis = AnalysisResult(
            status=AnalysisStatus.READY,
            title="においの特徴（実験的推定）",
            message=sentence,
            evidence=DescriptionEvidence(
                descriptor_scores={}, uncertainty="研究用モデル。未知測定への精度未確立。",
                model_version=prediction["model_version"],
                source_reading_ids=[sample_id]
            ),
            generated_by_llm=llm_used,
        )
        return Dashboard(
            mode="aws", latest=latest, trend=[latest], analysis=analysis,
            device=DeviceStatus(
                id="esp1", label="OlfaLink Sensor Unit", connected=False,
                last_seen=timestamp, status="historical",
                sensor_labels=["BME688", "SGP41", "TGS2602", "TGS2603"]
            ),
            features={"ai_enabled":True, "llm_enabled":llm_used, "ingestion_enabled":False}
        )
    return make_empty("解析できる測定セッションがありません。")



def live_dashboard(live_buffer, llm_url: str | None = None) -> Dashboard | None:
    """Prefer fresh pushed measurements; return None when no recent live packets."""
    rows, fresh, last_ms = live_buffer.snapshot()
    if not fresh:
        return None
    if rows[-1]["led"] != "exposure":
        # A live packet is arriving, but the sensor is not in an odor-exposure
        # interval. Do not present a past odor estimate as a current result.
        dashboard = make_empty("基準測定または回復区間を取得中です。")
        dashboard.mode = "live"
        dashboard.device.connected = True
        dashboard.device.status = "collecting"
        dashboard.device.last_seen = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc)
        return dashboard
    try:
        from app.services.live_stream import select_30s
        from ml.cnn_multitask import predict_trace
        trace, end_ms = select_30s(rows)
        model, scales, _ = get_model()
        prediction = predict_trace(model, scales, trace)
    except (ValueError, IndexError, TypeError, FileNotFoundError) as exc:
        # Sensor is connected, but input is not yet sufficient for a valid CNN inference.
        dashboard = make_empty(str(exc))
        dashboard.mode = "live"
        dashboard.device.connected = True
        dashboard.device.status = "collecting"
        dashboard.device.last_seen = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc)
        return dashboard
    stamp = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc)
    sample_id = f"live-{end_ms}"
    intensity = prediction["predicted_intensity_0_5"]
    label = prediction["predicted_odor_label"]
    score = prediction["classification_score_uncalibrated"]
    sentence, used_llm = describe(label, intensity, score, llm_url)
    return Dashboard(
        mode="live",
        latest=Reading(id=sample_id, device_id="esp1", timestamp=stamp, intensity=intensity,
                       source=Source.DEVICE, channels={}),
        trend=[],
        analysis=AnalysisResult(
            status=AnalysisStatus.READY, title="直近30秒の推定（実験用）",
            message=sentence,
            evidence=DescriptionEvidence(
                descriptor_scores={},
                uncertainty="v0.7モデルの30秒部分区間入力。未知データに対する精度未検証。",
                model_version=prediction["model_version"], source_reading_ids=[sample_id]
            ), generated_by_llm=used_llm),
        device=DeviceStatus(id="esp1",label="OlfaLink Sensor Unit",connected=True,
                            last_seen=stamp,status="connected",sensor_labels=["BME688","SGP41","TGS2602","TGS2603"]),
        features={"ai_enabled":True,"llm_enabled":used_llm,"ingestion_enabled":True}
    )
