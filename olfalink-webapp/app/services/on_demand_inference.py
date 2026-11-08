"""Synchronous, user-triggered CNN inference; no inference on sensor ingest or GET."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from threading import Lock
from app.schemas import Dashboard, DeviceStatus, AnalysisResult, AnalysisStatus, Reading, Source, DescriptionEvidence, PhaseAssignment
from app.services.live_stream import select_time_split_90s, time_split_metadata
from app.services.combined_dashboard import get_model
from app.services.odor_narration import describe, description_source
from app.services.on_demand_store import LiveStore, MeasurementUnavailable

MIN_GAP_MS = 30_000
MODEL_LOCK = Lock()
PIPELINE_VERSION = "cnn-timesplit-v1.9.2-adc-negative-zero-clamp"


def is_current_result(saved: dict | None) -> bool:
    if not saved:
        return False
    dashboard = saved.get("dashboard") or {}
    analysis = dashboard.get("analysis") or {}
    evidence = analysis.get("evidence") or {}
    return PIPELINE_VERSION in str(evidence.get("model_version", ""))


def empty_dashboard(message: str) -> Dashboard:
    return Dashboard(mode="on_demand", latest=None, trend=[],
        analysis=AnalysisResult(status=AnalysisStatus.NOT_AVAILABLE, title="解析待ち", message=message),
        device=DeviceStatus(id="esp1", label="OlfaLink Sensor Unit", connected=False,
            last_seen=None, status="waiting", sensor_labels=["BME688","SGP41","TGS2602","TGS2603"]),
        features={"ai_enabled":True,"llm_enabled":False,"ingestion_enabled":True})


def previous_result(store: LiveStore, device_id: str) -> Dashboard:
    saved = store.latest_result(device_id)
    if not is_current_result(saved):
        return empty_dashboard("「においを解析する」を押すと、測定結果を表示します。")
    try:
        result = Dashboard.model_validate(saved["dashboard"])
        result.mode = "on_demand"
        # The result is historical, even when the sensor is still streaming.
        result.device.connected = False
        result.device.status = "synthetic_demo" if result.analysis.input_origin == "synthetic_demo" else "previous_result"
        return result
    except (ValueError, TypeError, KeyError):
        return empty_dashboard("「においを解析する」を押してください。")


def infer_on_demand(store: LiveStore, device_id: str, llm_url: str | None = None, *,
                    llm_demo_enabled: bool = False) -> Dashboard:
    """A lock prevents duplicate CPU inference inside the single Cloud Run instance.

    Deployment additionally fixes --max-instances=1 and --concurrency=1.
    Results are persisted. A second click in the same 30s returns the cache.
    """
    with MODEL_LOCK:
        rows, session_id, latest_ms = store.window(device_id)
        is_synthetic = session_id.startswith("synthetic_")
        saved = store.latest_result(device_id)
        # Synthetic sessions get a unique identifier on each request. Avoid
        # accidentally starting a billable GPU on every button repeat.
        if is_synthetic and is_current_result(saved):
            old_dashboard = Dashboard.model_validate(saved["dashboard"])
            age_ms = int(time.time()*1000) - int(saved.get("computed_ms", 0))
            if (old_dashboard.analysis.input_origin == "synthetic_demo" and
                    0 <= age_ms < MIN_GAP_MS):
                return old_dashboard
        if is_current_result(saved) and saved.get("session_id") == session_id:
            age = int(time.time()*1000) - int(saved.get("computed_ms", 0))
            old_dashboard = Dashboard.model_validate(saved["dashboard"])
            # If GPU has been enabled since a dictionary-only analysis, allow a
            # deliberate button click to retry after the 30s cooldown.
            previous_source = old_dashboard.analysis.description_source
            now_disabled = not llm_url and previous_source == "llm"
            retry_llm = bool(llm_url) and previous_source == "dictionary" and age >= MIN_GAP_MS
            same_data = int(saved.get("end_ms", -1)) == latest_ms
            if not now_disabled and not retry_llm and (same_data or 0 <= age < MIN_GAP_MS):
                return old_dashboard
        try:
            # Temporal thirds are a provisional format adapter, not real
            # baseline/exposure/refresh measurements. Ignore LED and stage.
            trace, window_end = select_time_split_90s(rows)
            input_basis = ("直近90秒の実測値を時刻順に30秒×3分割。"
                           "baseline/exposure/refreshは旧CNNへの入力用の仮区間であり、"
                           "実際の曝露・回復を意味しません。精度未検証。")
            clipped_counts = {
                channel: sum(channel in row.get("_negative_adc_clipped", []) for row in rows)
                for channel in ("tgs2602_adc", "tgs2603_adc")
            }
            clipped_counts = {key: n for key, n in clipped_counts.items() if n}
            if clipped_counts:
                counts = "、".join(f"{key}: {n}/{len(rows)}件" for key, n in clipped_counts.items())
                input_basis += (
                    f" 【暫定補正】負のADC値を0に丸めました（{counts}）。"
                    "基準値が0のADCチャンネルは正規化系列を0に固定しています。"
                    "当該チャンネルの信号情報が失われるため、分類結果の信頼性は未検証です。"
                )
        except (ValueError, IndexError, TypeError) as exc:
            raise MeasurementUnavailable(str(exc)) from exc

        from ml.cnn_multitask import predict_trace
        model, scales, _ = get_model()
        prediction = predict_trace(model, scales, trace)
        label = prediction["predicted_odor_label"]
        intensity = prediction["predicted_intensity_0_5"]
        score = prediction["classification_score_uncalibrated"]
        # Synthetic GPU generation is strictly opt-in, preserving cost control.
        # The actual, uncalibrated CNN score is passed to the GPU unchanged.
        # Cloud Run LLM v1.9 explicitly supports cautious demo generation even
        # for a low score or level 0; older LLM revisions will use dictionary.
        can_use_llm = bool(llm_url) and (not is_synthetic or llm_demo_enabled)
        if is_synthetic and llm_demo_enabled:
            message, used_llm = describe(label, intensity, score,
                                         llm_url if can_use_llm else None,
                                         demo_mode=True)
        else:
            message, used_llm = describe(label, intensity, score,
                                         llm_url if can_use_llm else None)
        source = description_source(label, intensity, used_llm)
        if is_synthetic:
            message = "【合成データによるデモ・実測ではありません】" + message
            input_basis = ("【合成データ】AWSから有効な実測値が取得できなかったため、" +
                           "乱数から作成した90秒の疑似センサー値を使用しました。" + input_basis)

        # A working CNN must still report its class and strength when the
        # separately deployed GPU LLM is deliberately stopped.
        stamp = datetime.fromtimestamp(window_end/1000, tz=timezone.utc)
        reading_id = f"on-demand-{device_id}-{session_id}-{window_end}"
        dashboard = Dashboard(
            mode="on_demand", latest=Reading(id=reading_id,device_id=device_id,
              timestamp=stamp,intensity=intensity,
              source=Source.DEMO if is_synthetic else Source.DEVICE,channels={}), trend=[],
            analysis=AnalysisResult(
                status=AnalysisStatus.READY,
                title=("合成データによるCNN＋LLMデモ（実測ではありません）" if used_llm else
                       "合成データによるCNN＋辞書デモ（実測ではありません）") if is_synthetic else
                      "CNN＋LLM解析結果（研究用）" if used_llm else
                      "CNN分類結果＋辞書説明（参考）" if source == "dictionary" else
                      "CNN分類結果（参考値）",
                message=message,
                predicted_odor_label=label,
                predicted_odor_ja=prediction["predicted_odor_ja"],
                classification_score_uncalibrated=score,
                phase_assignment=PhaseAssignment.model_validate({
                    **time_split_metadata(rows, window_end),
                    "method": "synthetic_demo_time_split_3x30s" if is_synthetic else "time_split_3x30s",
                }),
                evidence=DescriptionEvidence(descriptor_scores={},
                    uncertainty=input_basis + " 分類スコアは確率として校正されていません。",
                    model_version=prediction["model_version"] + "+" + PIPELINE_VERSION, source_reading_ids=[reading_id]),
                generated_by_llm=used_llm, description_source=source,
                input_origin="synthetic_demo" if is_synthetic else "device"),
            device=DeviceStatus(id=device_id,label="OlfaLink Sensor Unit", connected=False,
                last_seen=stamp, status="synthetic_demo" if is_synthetic else "previous_result",
                sensor_labels=["BME688","SGP41","TGS2602","TGS2603"]),
            features={"ai_enabled":True,"llm_enabled":used_llm,"ingestion_enabled":True,
                      "synthetic_fallback":is_synthetic,
                      "sensor_correction_applied":bool(clipped_counts)},
        )
        store.save_result(device_id, session_id, window_end, dashboard.model_dump(mode="json"))
        return dashboard
