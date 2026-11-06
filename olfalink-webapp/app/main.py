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
from app.services.combined_dashboard import aws_dashboard

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


@lru_cache

def settings() -> Settings:
    return Settings.from_env()


def create_app(configuration: Settings | None = None, store: ReadingStore | None = None) -> FastAPI:
    config = configuration or settings()
    repo = store or (FirestoreStore(config.firestore_collection) if config.app_mode == "firestore" else DemoStore())
    analyzer = DisabledAnalysis()
    app = FastAPI(title="OlfaLink API", version="0.1.0", docs_url="/api/docs", redoc_url=None)

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
        return {"status":"ok","version":"0.1.0"}

    @app.get("/api/v1/integration/status")
    async def integration_status():
        """Perform an actual authorized AWS fetch in AWS mode; never expose the key.

        This is intentionally a diagnostic of the upstream named-session archive,
        not a health or real-time sensor connection claim.
        """
        if config.app_mode != "aws":
            return {"mode": config.app_mode, "aws_reachable": False,
                    "reason": "APP_MODE is not aws"}
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
                "live_stream": False,
                "data_source": "AWS historical published sessions"}

    @app.get("/api/v1/readings", response_model=list[Reading])
    def readings(limit: int = Query(default=60, ge=1, le=240)):
        if config.app_mode == "aws":
            return []  # Raw training measurements and mock data are not publicly exposed.
        return repo.recent(limit)

    @app.get("/api/v1/dashboard", response_model=Dashboard)
    async def dashboard():
        if config.app_mode == "aws":
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

    @app.post("/api/v1/readings", status_code=status.HTTP_201_CREATED, response_model=Reading)
    def ingest(reading: ReadingIn, x_device_key: str | None = Header(default=None)):
        if config.app_mode == "aws" or not config.device_ingest_key:
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
