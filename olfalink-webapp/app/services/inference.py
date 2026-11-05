"""AI inference and text-generation extension points.

IMPORTANT: Do not output inferred odor descriptors without a fitted and validated
sensor model. Model weights and prompt injection defences belong here, not in UI.
"""
from __future__ import annotations
from typing import Protocol
from app.schemas import Reading, AnalysisResult, AnalysisStatus, DescriptionEvidence


class OdorModel(Protocol):
    async def infer(self, measurements: list[Reading]) -> DescriptionEvidence: ...


class OdorNarrator(Protocol):
    async def describe(self, evidence: DescriptionEvidence) -> str: ...


class DisabledAnalysis:
    async def analyze(self, measurements: list[Reading]) -> AnalysisResult:
        return AnalysisResult(
            status=AnalysisStatus.DISABLED,
            title="AIによるにおいの説明は準備中",
            message="現在はセンサーの反応レベルのみ表示しています。POM・AI推定・LLM説明は実装後に有効化します。",
            evidence=None,
            generated_by_llm=False,
        )


class AnalysisPipeline:
    """Future production orchestration: OdorModel -> bounded evidence -> OdorNarrator.

    Integrations must validate model version, confidence and provenance before
    exposing any generated description. A narrator must never infer safety.
    """
    def __init__(self, model: OdorModel, narrator: OdorNarrator):
        self.model, self.narrator = model, narrator

    async def analyze(self, measurements: list[Reading]) -> AnalysisResult:
        if not measurements:
            return AnalysisResult(status=AnalysisStatus.NOT_AVAILABLE,
                title="分析できません",message="測定データがありません。")
        evidence = await self.model.infer(measurements)
        if not evidence.descriptor_scores or not evidence.source_reading_ids:
            return AnalysisResult(status=AnalysisStatus.NOT_AVAILABLE,
                title="分析できません",message="推定の根拠が十分ではありません。")
        explanation = await self.narrator.describe(evidence)
        return AnalysisResult(status=AnalysisStatus.READY,title="においの推定特徴",
                              message=explanation,evidence=evidence,generated_by_llm=True)
