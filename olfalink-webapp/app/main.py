"""Single-origin Cloud Run entrypoint for OlfaLink web app + v1 REST API."""
from __future__ import annotations
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import hmac
from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.config import Settings
from app.schemas import Dashboard, DeviceStatus, Reading, ReadingIn
from app.services.inference import DisabledAnalysis
from app.services.storage import DemoStore, FirestoreStore, ReadingStore

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


@lru_cache

def settings() -> Settings:
    return Settings.from_env()


def create_app(configuration: Settings | None = None, store: ReadingStore | None = None) -> FastAPI:
    config = configuration or settings()
    repo = store or (DemoStore() if config.app_mode == "demo" else FirestoreStore(config.firestore_collection))
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

    @app.get("/api/v1/readings", response_model=list[Reading])
    def readings(limit: int = Query(default=60, ge=1, le=240)):
        return repo.recent(limit)

    @app.get("/api/v1/dashboard", response_model=Dashboard)
    async def dashboard():
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
        if not config.device_ingest_key:
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
