"""Validated process configuration. Secrets must never be checked into git."""
from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_mode: str = "demo"           # demo | firestore | aws | on_demand | aws_pull
    device_ingest_key: str | None = None
    device_id: str = "olfalink-01"
    firestore_collection: str = "olfalink_readings"
    ai_provider: str = "disabled"   # connector scaffold, intentionally disabled
    llm_provider: str = "disabled"  # connector scaffold, intentionally disabled
    aws_read_key: str | None = None
    llm_service_url: str | None = None
    llm_enabled: bool = False  # GPU may be stopped for cost control.
    llm_demo_enabled: bool = False  # Opt-in; synthetic demo may invoke paid LLM GPU.
    demo_fallback_enabled: bool = False  # Enable only for explicit presentation/demo use.
    force_demo_enabled: bool = False  # Deliberate presentation override; no AWS request.
    live_ingest_key: str | None = None
    live_device_id: str = "esp1"

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("APP_MODE", "demo").lower()
        if mode not in {"demo", "firestore", "aws", "on_demand", "aws_pull"}:
            raise ValueError("APP_MODE must be demo, firestore, aws, on_demand or aws_pull")
        ai_provider = os.getenv("AI_PROVIDER", "disabled").lower()
        llm_provider = os.getenv("LLM_PROVIDER", "disabled").lower()
        if ai_provider != "disabled" or llm_provider != "disabled":
            raise ValueError("AI/LLM integrations have not been implemented yet: keep providers disabled")
        return cls(
            app_mode=mode,
            device_ingest_key=os.getenv("DEVICE_INGEST_KEY") or None,
            device_id=os.getenv("DEVICE_ID", "olfalink-01"),
            firestore_collection=os.getenv("FIRESTORE_COLLECTION", "olfalink_readings"),
            ai_provider=ai_provider,
            llm_provider=llm_provider,
            aws_read_key=os.getenv("OLFALINK_READ_KEY") or None,
            llm_service_url=os.getenv("LLM_SERVICE_URL") or None,
            llm_enabled=os.getenv("OLFALINK_LLM_ENABLED", "false").lower() in {"true", "1", "yes"},
            llm_demo_enabled=os.getenv("OLFALINK_LLM_DEMO_ENABLED", "false").lower() in {"true", "1", "yes"},
            demo_fallback_enabled=os.getenv("OLFALINK_DEMO_FALLBACK", "false").lower() in {"true", "1", "yes"},
            force_demo_enabled=os.getenv("OLFALINK_FORCE_DEMO", "false").lower() in {"true", "1", "yes"},
            live_ingest_key=os.getenv("LIVE_INGEST_KEY") or None,
            live_device_id=os.getenv("LIVE_DEVICE_ID", "esp1"),
        )
