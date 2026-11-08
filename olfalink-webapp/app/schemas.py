"""Versioned API contracts shared conceptually with the frontend.

The 0-5 level is a product-specific, *provisional* ordinal signal indicator.
It is not an air-quality index or a human sensory measurement until validated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from typing import Annotated, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator


class Source(str, Enum):
    DEMO = "demo"
    DEVICE = "device"


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    device_id: str
    timestamp: datetime
    intensity: Annotated[int, Field(ge=0, le=5, strict=True)]
    source: Source
    channels: dict[str, float] = Field(default_factory=dict)


class ReadingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    timestamp: datetime | None = None
    intensity: Annotated[int, Field(ge=0, le=5, strict=True)]
    channels: dict[Annotated[str, Field(max_length=64, pattern=r"^[a-zA-Z0-9_]+$")], float] = Field(default_factory=dict, max_length=32)

    @field_validator("channels")
    @classmethod
    def finite_readings(cls, values: dict[str, float]) -> dict[str, float]:
        if not all(isfinite(value) for value in values.values()):
            raise ValueError("channels must contain only finite values")
        return values

    @field_validator("timestamp")
    @classmethod
    def valid_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value


class AnalysisStatus(str, Enum):
    DISABLED = "disabled"
    NOT_AVAILABLE = "not_available"
    READY = "ready"


class DescriptionEvidence(BaseModel):
    """Future AI outputs: text generation must be grounded on these features."""
    descriptor_scores: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]] = Field(default_factory=dict)
    uncertainty: str | None = None
    model_version: str | None = None
    source_reading_ids: list[str] = Field(default_factory=list)


class PhaseWindow(BaseModel):
    """Acquisition-time slot, NOT a verified olfactory exposure annotation."""
    label: str
    start_ms: int = Field(ge=1)
    end_ms: int = Field(ge=1)
    sample_count: int = Field(ge=0)


class PhaseAssignment(BaseModel):
    method: str = "time_split_3x30s"
    is_ground_truth: bool = False
    used_led_signal: bool = False
    windows: list[PhaseWindow] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    status: AnalysisStatus
    title: str
    message: str
    evidence: DescriptionEvidence | None = None
    generated_by_llm: bool = False
    # Distinguish actual GPU generation from deterministic reference templates.
    description_source: Literal["llm", "dictionary", "no_signal", "unavailable"] = "unavailable"
    # CNN outputs must remain visible even when GPU LLM is intentionally stopped.
    predicted_odor_label: str | None = None
    predicted_odor_ja: str | None = None
    classification_score_uncalibrated: float | None = None
    phase_assignment: PhaseAssignment | None = None
    # A demo prediction must never be mistaken for a device observation.
    input_origin: Literal["device", "synthetic_demo"] = "device"


class DeviceStatus(BaseModel):
    id: str
    label: str
    connected: bool
    last_seen: datetime | None
    status: str
    sensor_labels: list[str]


class Dashboard(BaseModel):
    mode: str
    level_min: int = 0
    level_max: int = 5
    level_basis: str = "暫定的な0〜5の反応レベル。安全性は判定できません。"
    latest: Reading | None
    trend: list[Reading]
    analysis: AnalysisResult
    device: DeviceStatus
    features: dict[str, bool]
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
