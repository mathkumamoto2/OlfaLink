"""Single-origin Cloud Run entrypoint for OlfaLink web app + v1 REST API."""
from __future__ import annotations
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import hmac
from fastapi import FastAPI, Header, HTTPException, Query, status
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.config import Settings
from app.schemas import Dashboard, DeviceStatus, Reading, ReadingIn
from app.services.inference import DisabledAnalysis
from app.services.storage import DemoStore, FirestoreStore, ReadingStore
from app.services.combined_dashboard import aws_dashboard, live_dashboard
from app.services.live_stream import LiveBatch, LiveBuffer
from app.services.on_demand_store import FirestoreOnDemandStore, MemoryOnDemandStore, LiveStore, MeasurementUnavailable
from app.services.on_demand_inference import infer_on_demand, previous_result
from app.services.aws_live_data import AWSDataOnDemandStore

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


@lru_cache

def settings() -> Settings:
    return Settings.from_env()


def create_app(configuration: Settings | None = None, store: ReadingStore | None = None,
               live_store: LiveStore | None = None) -> FastAPI:
    config = configuration or settings()
    repo = store or (FirestoreStore(config.firestore_collection) if config.app_mode == "firestore" else DemoStore())
    analyzer = DisabledAnalysis()
    app = FastAPI(title="OlfaLink API", version="1.9.2", docs_url="/api/docs", redoc_url=None)
    live_buffer = LiveBuffer()
    if config.app_mode == "on_demand":
        # Legacy push-to-Firestore mode.
        live_store = live_store or FirestoreOnDemandStore()
    elif config.app_mode == "aws_pull":
        # No AWS Lambda bridge: fetch latest measurements only on POST.
        # Presentation-safe startup: no mandatory Firestore initialization.
        # Results stay in RAM and are lost when this Cloud Run instance stops.
        live_store = live_store or AWSDataOnDemandStore(config.aws_read_key, MemoryOnDemandStore(), demo_fallback=config.demo_fallback_enabled,
                                                       force_demo=config.force_demo_enabled)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/api/v1/healthz")
    def healthz():
        return {"status":"ok","version":"1.9.2"}

    @app.get("/api/v1/integration/status")
    async def integration_status():
        """Perform an actual authorized AWS fetch in AWS mode; never expose the key.

        This is intentionally a diagnostic of the upstream named-session archive,
        not a health or real-time sensor connection claim.
        """
        if config.app_mode == "aws_pull":
            return {"mode": "aws_pull", "aws_live_api_configured": bool(config.aws_read_key),
                    "data_source": "AWS /data (button-triggered; no background requests)",
                    "model_available": (ROOT / "models" / "odor_intensity_multitask_v0_7.pt").is_file(),
                    "inference_mode": "button_only", "input_strategy": "time_split_3x30s", "llm_enabled": config.llm_enabled,
                    "synthetic_demo_fallback": config.demo_fallback_enabled,
                    "force_synthetic_demo": config.force_demo_enabled,
                    "llm_demo_enabled": config.llm_demo_enabled and config.llm_enabled,
                    "result_storage": "instance_memory_only"}
        if config.app_mode not in {"aws", "on_demand"}:
            return {"mode": config.app_mode, "aws_reachable": False,
                    "reason": "APP_MODE is not aws or on_demand"}
        from app.services.aws_sessions import subscriber, ranked_sessions, SourceUnavailable
        from ml.session_timeseries_v06 import session_trace
        if not config.aws_read_key:
            return {"mode": "aws", "aws_reachable": False,
                    "reason": "The AWS read key is not configured on Cloud Run"}
        try:
            rows, manifest = await run_in_threadpool(subscriber.fetch, config.aws_read_key)
        except SourceUnavailable:
            return {"mode": "aws", "aws_reachable": False,
                    "reason": "AWS request failed. Check the secret, network, API permissions and logs."}
        sessions = ranked_sessions(rows)
        compatible = 0
        for _, samples in sessions:
            try:
                session_trace(samples)
                compatible += 1
            except (ValueError, TypeError, IndexError):
                continue
        return {"mode": "aws", "aws_reachable": True,
                "dataset_version": manifest.get("version"),
                "named_session_rows": len(rows),
                "named_sessions": len(sessions),
                "model_compatible_sessions": compatible,
                "model_available": (ROOT / "models" / "odor_intensity_multitask_v0_7.pt").is_file(),
                "live_stream": live_store.status(config.live_device_id)["connected"] if config.app_mode == "on_demand" else live_buffer.snapshot()[1],
                "live_push_configured": bool(config.live_ingest_key),
                "data_source": "AWS historical published sessions plus optional live push",
                "inference_mode": "on_demand" if config.app_mode == "on_demand" else "legacy"}

    @app.post("/api/v1/live/ingest", status_code=202)
    def live_ingest(batch: LiveBatch, x_device_key: str | None = Header(default=None)):
        """Stream authentic sensor samples to the rolling 30-second inference window.

        Call this endpoint once per new sensor reading or with a batch of recent
        real samples. Requires an independent write-only secret, never the AWS read key.
        """
        if config.app_mode not in {"aws", "on_demand"} or not config.live_ingest_key:
            raise HTTPException(status_code=503, detail="live ingestion is not configured")
        if not x_device_key or not hmac.compare_digest(x_device_key, config.live_ingest_key):
            raise HTTPException(status_code=401, detail="invalid device credential")
        if batch.device_id != config.live_device_id:
            raise HTTPException(status_code=403, detail="unknown device")
        try:
            if config.app_mode == "on_demand":
                n = live_store.ingest(batch)
            else:
                n = live_buffer.append(batch)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"accepted": n, "live": True}

    @app.get("/api/v1/live/status")
    def live_status():
        if config.app_mode == "aws_pull":
            return {"live_feed_connected": None, "configured": bool(config.aws_read_key),
                    "state_storage": "aws_data_pull_no_raw_storage", "window_seconds": 90,
                    "inference_mode": "button_only",
                    "message": "AWS /data is queried only when the analyze button is pressed."}
        if config.app_mode == "on_demand":
            state = live_store.status(config.live_device_id)
            ms = state["last_ms"]
            return {"live_feed_connected": state["connected"],
                    "last_seen": datetime.fromtimestamp(ms/1000,timezone.utc).isoformat() if ms else None,
                    "phase":state["phase"], "window_seconds":30,
                    "state_storage":"firestore_persistent",
                    "configured":bool(config.live_ingest_key),
                    "inference_mode":"button_only"}
        _, fresh, last_ms = live_buffer.snapshot()
        return {"live_feed_connected": fresh,
                "last_seen": datetime.fromtimestamp(last_ms/1000, timezone.utc).isoformat() if last_ms else None,
                "state_storage": "ephemeral_instance_memory",
                "window_seconds": 30,
                "configured": config.app_mode == "aws" and bool(config.live_ingest_key)}

    @app.get("/api/v1/readings", response_model=list[Reading])
    def readings(limit: int = Query(default=60, ge=1, le=240)):
        if config.app_mode in {"aws", "on_demand", "aws_pull"}:
            return []  # Raw measurements and training data are not publicly exposed.
        return repo.recent(limit)

    @app.get("/api/v1/dashboard", response_model=Dashboard)
    async def dashboard():
        if config.app_mode in {"on_demand", "aws_pull"}:
            # This is a read-only cache access: no CNN, AWS, or LLM call.
            return await run_in_threadpool(previous_result, live_store, config.live_device_id)
        if config.app_mode == "aws":
            live_result = await run_in_threadpool(live_dashboard, live_buffer, config.llm_service_url)
            if live_result is not None:
                return live_result
            return await run_in_threadpool(aws_dashboard, config.aws_read_key or "", config.llm_service_url)
        rows = repo.recent(72)
        latest = rows[0] if rows else None
        now = datetime.now(timezone.utc)
        # Live state is determined by the age of the *device* data only.
        fresh = bool(latest and latest.source.value == "device" and
                     abs((now - latest.timestamp).total_seconds()) <= 300)
        device = DeviceStatus(
            id=config.device_id,
            label="OlfaLink Sensor Unit 01",
            connected=fresh,
            last_seen=latest.timestamp if latest else None,
            status="connected" if fresh else ("demo" if config.app_mode == "demo" else "offline"),
            sensor_labels=["BME688", "SGP41", "TGS2602", "TGS2603"],
        )
        return Dashboard(mode=config.app_mode, latest=latest, trend=list(reversed(rows)),
                         analysis=await analyzer.analyze(rows),device=device,
                         features={"ai_enabled":False,"llm_enabled":False,"ingestion_enabled":bool(config.device_ingest_key)})

    @app.post("/api/v1/inference/on-demand", response_model=Dashboard)
    async def inference_on_demand():
        if config.app_mode not in {"on_demand", "aws_pull"}:
            raise HTTPException(status_code=503, detail="on-demand inference is not enabled")
        # Only this explicitly invoked POST triggers CNN and optional LLM.
        try:
            return await run_in_threadpool(infer_on_demand, live_store,
                                           config.live_device_id,
                                           config.llm_service_url if config.llm_enabled else None,
                                           llm_demo_enabled=config.llm_demo_enabled and config.llm_enabled)
        except MeasurementUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/readings", status_code=status.HTTP_201_CREATED, response_model=Reading)
    def ingest(reading: ReadingIn, x_device_key: str | None = Header(default=None)):
        if config.app_mode in {"aws", "on_demand", "aws_pull"} or not config.device_ingest_key:
            raise HTTPException(status_code=503, detail="device ingestion is disabled")
        if not x_device_key or not hmac.compare_digest(x_device_key, config.device_ingest_key):
            raise HTTPException(status_code=401, detail="invalid device credential")
        if reading.device_id != config.device_id:
            raise HTTPException(status_code=403, detail="unknown device")
        # Prevent a device clock bug from making stale data appear forever fresh.
        if reading.timestamp and (reading.timestamp-datetime.now(timezone.utc)).total_seconds() > 60:
            raise HTTPException(status_code=422, detail="timestamp cannot be in the future")
        return repo.insert(reading)

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html",headers={"Cache-Control":"no-store"})

    app.mount("/assets", StaticFiles(directory=WEB), name="assets")
    return app


app = create_app()
