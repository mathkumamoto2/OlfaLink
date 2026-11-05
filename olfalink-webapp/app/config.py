"""Validated process configuration. Secrets must never be checked into git."""
from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_mode: str = "demo"           # demo | firestore
    device_ingest_key: str | None = None
    device_id: str = "olfalink-01"
    firestore_collection: str = "olfalink_readings"
    ai_provider: str = "disabled"   # connector scaffold, intentionally disabled
    llm_provider: str = "disabled"  # connector scaffold, intentionally disabled

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("APP_MODE", "demo").lower()
        if mode not in {"demo", "firestore"}:
            raise ValueError("APP_MODE must be 'demo' or 'firestore'")
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
        )
