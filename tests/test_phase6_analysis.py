"""Focused Phase 6 workflow and streaming contracts (no live providers)."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from finance_terminal.analysis import (
    ANALYSIS_PACKET_VERSION, DEFAULT_ANALYSIS_TIMEOUT_SECONDS, AnalysisMode, AnalysisService, AnalystAssessment,
    AnalystClaim, CompanyAnalysisRequest, ComparisonAnalysis, FundamentalsContextPacket,
    OverviewOutput, SkepticAssessment, SkepticReview, ValuationContextPacket,
    OVERVIEW_PROMPT, VALUATION_PROMPT, _metric_analysis, _refs, role_packet, validate_claims,
)
from finance_terminal.ai_provider import AIUsageLimitError
from finance_terminal.macro import MacroObservation, MacroSeriesDefinition
from finance_terminal.api import create_app
from finance_terminal.query import SeriesPointResult, SeriesResult


REFERENCE = "market:us-xnas-aapl:2025-01-02:close"


def series() -> SeriesResult:
    return SeriesResult(
        id="AAPL:TEST", label="Test", entity="AAPL", metric="REVENUE", unit="USD", frequency="annual",
        observations=[SeriesPointResult(
            date=date(2025, 1, 2), value=Decimal("10"), point_type="SOURCE", observation_id=REFERENCE,
        )],
    )


def packets() -> tuple[FundamentalsContextPacket, ValuationContextPacket]:
    item = series()
    fundamentals = FundamentalsContextPacket(
        mode=AnalysisMode.COMPANY, context={"ticker": "AAPL"}, metrics=[_metric_analysis(item)],
        evidence_ids=[REFERENCE], evidence_fingerprint="fundamentals",
    )
    valuation = ValuationContextPacket(
        mode=AnalysisMode.COMPANY, context={"ticker": "AAPL"}, evidence_ids=[REFERENCE],
        evidence_fingerprint="valuation", latest_market_fact={"price_date": "2025-01-02"},
    )
    return fundamentals, valuation


class ConcurrentProvider:
    def __init__(self) -> None:
        self.base_entered = 0
        self.both_entered = asyncio.Event()

    async def generate_structured(self, prompt, output_schema, *, model=None):
        if " fundamentals\n" in prompt or " valuation\n" in prompt:
            self.base_entered += 1
            if self.base_entered == 2:
                self.both_entered.set()
            await asyncio.wait_for(self.both_entered.wait(), 1)
            return AnalystAssessment(
                stance="NEUTRAL", confidence="MODERATE",
                thesis=AnalystClaim(text="The supplied evidence is mixed.", evidence_refs=[REFERENCE]),
                supporting_claims=[AnalystClaim(text="The supplied value is the main support.", evidence_refs=[REFERENCE])],
            ).model_dump_json()
        if " skeptic\n" in prompt:
            return SkepticReview(assessment=SkepticAssessment.NO_MATERIAL_CHALLENGE).model_dump_json()
        return OverviewOutput(
            stance="NEUTRAL", confidence="MODERATE",
            conclusion=AnalystClaim(text="The overall evidence is mixed.", evidence_refs=[REFERENCE]),
            summary=AnalystClaim(text="The scoped assessments are mixed.", evidence_refs=[REFERENCE]),
            strongest_evidence=[AnalystClaim(text="Both use the supplied deterministic fact.", evidence_refs=[REFERENCE])],
        ).model_dump_json()

    async def close(self):
        return None


def install_packets(service: AnalysisService) -> None:
    fundamentals, valuation = packets()
    service._fundamentals_packet = lambda request: (fundamentals, [series()])  # type: ignore[method-assign]
    service._valuation_packet = lambda request: (valuation, [series()])  # type: ignore[method-assign]


def test_packet_version_unified_thin_refs_and_schema_invariants() -> None:
    point = SeriesPointResult(
        date=date(2025, 1, 2), value=Decimal("1"), point_type="CALCULATED",
        observation_id="source", input_observation_ids=["left", "right", "left"],
    )
    assert ANALYSIS_PACKET_VERSION == 3
    assert _refs(point) == ["source", "left", "right"]
    assert {"stance", "confidence", "synthesis", "key_conclusions", "key_risks"} <= set(OverviewOutput.model_fields)
    assert {"recommendation", "price_target", "fair_value"}.isdisjoint(OverviewOutput.model_fields)
    assert SkepticReview(assessment="NO_MATERIAL_CHALLENGE", challenges=[]).challenges == []
    assert "Do not mechanically average" in OVERVIEW_PROMPT
    assert "Never calculate from raw values" in OVERVIEW_PROMPT
    assert all(term not in OverviewOutput.model_fields for term in ("recommendation", "price_target", "fair_value"))


def test_overview_accepts_all_evidence_stances_including_low_confidence_neutral() -> None:
    for stance in ("POSITIVE", "NEUTRAL", "NEGATIVE"):
        output = OverviewOutput(
            stance=stance, confidence="LOW" if stance == "NEUTRAL" else "MODERATE",
            conclusion=AnalystClaim(text="The supported evidence has been weighed.", evidence_refs=[REFERENCE]),
            summary=AnalystClaim(text="The evidence supports this scoped interpretation.", evidence_refs=[REFERENCE]),
            strongest_evidence=[AnalystClaim(text="A deterministic observation is available.", evidence_refs=[REFERENCE])],
        )
        assert output.stance == stance


def test_base_analysts_overlap_and_downstream_order(tmp_path) -> None:
    from finance_terminal.storage import SQLiteStore

    store = SQLiteStore(tmp_path / "phase6.db")
    provider = ConcurrentProvider()
    service = AnalysisService(store, provider)
    install_packets(service)

    async def collect():
        return [event async for event in service.company_events(CompanyAnalysisRequest(ticker="AAPL"))]

    events = asyncio.run(collect())
    kinds = [(event.event, event.stage) for event in events]
    assert provider.base_entered == 2
    assert kinds[:3] == [("analysis_started", None), ("stage_started", "fundamentals"), ("stage_started", "valuation")]
    skeptic_start = kinds.index(("stage_started", "skeptic"))
    assert sum(kind == "stage_completed" and stage in {"fundamentals", "valuation"} for kind, stage in kinds[:skeptic_start]) == 2
    completed = [event for event in events if event.event == "stage_completed" and event.stage in {"fundamentals", "valuation"}]
    assert all(event.evidence_context and event.evidence_context[0].observations for event in completed)
    assert kinds.index(("stage_started", "overview")) > kinds.index(("stage_completed", "skeptic"))
    assert kinds[-1] == ("analysis_completed", None)
    final = events[-1].result
    assert final.packet_version == 3 and final.failures == []  # type: ignore[union-attr]
    store.close()


def test_company_sse_is_typed_and_emits_completed_base_before_final(tmp_path, monkeypatch) -> None:
    from finance_terminal.storage import SQLiteStore

    fundamentals, valuation = packets()
    monkeypatch.setattr(AnalysisService, "_fundamentals_packet", lambda self, request: (fundamentals, [series()]))
    monkeypatch.setattr(AnalysisService, "_valuation_packet", lambda self, request: (valuation, [series()]))
    store = SQLiteStore(tmp_path / "phase6-sse.db")
    with TestClient(create_app(store=store, provider=ConcurrentProvider())) as client:
        response = client.post("/api/v1/analyze/company/stream", json={"type": "company", "ticker": "AAPL"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [frame for frame in response.text.split("\n\n") if frame]
    payloads = [json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: "))) for frame in frames]
    assert payloads[0]["event"] == "analysis_started"
    assert any(item["event"] == "stage_completed" and item["stage"] in {"fundamentals", "valuation"} for item in payloads[:-1])
    for item in payloads:
        if item["event"] == "stage_completed" and item["stage"] in {"fundamentals", "valuation"}:
            assert item["evidence_context"][0]["observations"]
    assert payloads[-1]["event"] == "analysis_completed"


def test_compaction_recomputes_authorized_evidence_and_rejects_stale_refs() -> None:
    item = series()
    packet = FundamentalsContextPacket(
        mode=AnalysisMode.COMPANY, context={"ticker": "AAPL"}, metrics=[_metric_analysis(item)],
        evidence_ids=[REFERENCE, "stale:removed"], evidence_fingerprint="before",
    )
    compact = role_packet(packet, "fundamentals")
    assert compact.evidence_ids == [REFERENCE]
    stale = AnalystAssessment(
        stance="NEUTRAL", confidence="LOW",
        thesis=AnalystClaim(text="History is limited.", evidence_refs=["stale:removed"]),
        supporting_claims=[AnalystClaim(text="One observation is available.", evidence_refs=[REFERENCE])],
    )
    assert validate_claims(stale, compact) is None


def test_comparison_compaction_keeps_endpoints_and_bounded_trends() -> None:
    points = [
        SeriesPointResult(
            date=date(2019 + index, 1, 2), value=Decimal(str(10 + index)),
            point_type="SOURCE", observation_id=f"source:{index}",
        )
        for index in range(7)
    ]
    item = SeriesResult(
        id="AAPL:REVENUE", label="Revenue", entity="AAPL", metric="REVENUE",
        unit="USD", frequency="annual", observations=points,
    )
    packet = FundamentalsContextPacket(
        mode=AnalysisMode.COMPARISON, context={"tickers": ["AAPL"]},
        metrics=[_metric_analysis(item)], evidence_ids=[f"source:{index}" for index in range(7)],
        evidence_fingerprint="before",
    )

    compact = role_packet(packet, "comparison")

    assert [observation["period"] for observation in compact.metrics[0].observations] == ["2019-01-02", "2025-01-02"]
    assert len(compact.metrics[0].period_changes) == 2
    assert "source:1" not in compact.evidence_ids
    assert set(compact.evidence_ids) == {"source:0", "source:4", "source:5", "source:6"}


def test_default_analysis_timeout_allows_extended_structured_turns(tmp_path) -> None:
    from finance_terminal.storage import SQLiteStore

    store = SQLiteStore(tmp_path / "analysis-timeout.db")
    service = AnalysisService(store, ConcurrentProvider())
    assert service.timeout == DEFAULT_ANALYSIS_TIMEOUT_SECONDS == 180.0
    store.close()


def test_valuation_with_insufficient_history_is_forced_neutral_low(tmp_path) -> None:
    from finance_terminal.storage import SQLiteStore

    class PositiveProvider(ConcurrentProvider):
        async def generate_structured(self, prompt, output_schema, *, model=None):
            if " valuation\n" in prompt:
                return AnalystAssessment(
                    stance="POSITIVE", confidence="HIGH",
                    thesis=AnalystClaim(text="The current observation appears favorable.", evidence_refs=[REFERENCE]),
                    supporting_claims=[AnalystClaim(text="A current observation is available.", evidence_refs=[REFERENCE])],
                ).model_dump_json()
            if " fundamentals\n" in prompt:
                return AnalystAssessment(
                    stance="NEUTRAL", confidence="MODERATE",
                    thesis=AnalystClaim(text="The operating evidence is mixed.", evidence_refs=[REFERENCE]),
                    supporting_claims=[AnalystClaim(text="A reported observation is available.", evidence_refs=[REFERENCE])],
                ).model_dump_json()
            if " skeptic\n" in prompt:
                return SkepticReview(assessment=SkepticAssessment.NO_MATERIAL_CHALLENGE).model_dump_json()
            return OverviewOutput(
                stance="NEUTRAL", confidence="LOW",
                conclusion=AnalystClaim(text="The limited evidence supports a neutral interpretation.", evidence_refs=[REFERENCE]),
                summary=AnalystClaim(text="The evidence is limited.", evidence_refs=[REFERENCE]),
                strongest_evidence=[AnalystClaim(text="A current observation is available.", evidence_refs=[REFERENCE])],
            ).model_dump_json()

    store = SQLiteStore(tmp_path / "valuation-limit.db")
    service = AnalysisService(store, PositiveProvider())
    install_packets(service)
    response = asyncio.run(service.company(CompanyAnalysisRequest(ticker="AAPL")))
    assert response.status == "success" and response.valuation is not None
    assert response.valuation.stance == "NEUTRAL" and response.valuation.confidence == "LOW"
    assert "too limited" in response.valuation.thesis.text
    assert "packets" in VALUATION_PROMPT and "internal state flags" in VALUATION_PROMPT
    assert "interpretation_available=false" not in VALUATION_PROMPT
    store.close()


def test_parallel_base_failures_preserve_usage_limit(tmp_path) -> None:
    from finance_terminal.storage import SQLiteStore

    class FailingProvider:
        async def generate_structured(self, prompt, output_schema, *, model=None):
            raise AIUsageLimitError("limit")

        async def close(self):
            return None

    store = SQLiteStore(tmp_path / "parallel-failure.db")
    service = AnalysisService(store, FailingProvider())
    install_packets(service)
    response = asyncio.run(service.company(CompanyAnalysisRequest(ticker="AAPL")))
    assert response.status == "error" and response.error_code == "AI_USAGE_LIMIT"
    store.close()


def test_comparison_output_has_tradeoffs_without_winner_schema() -> None:
    fields = set(ComparisonAnalysis.model_fields)
    assert {"summary", "fundamentals_comparison", "valuation_comparison", "market_context", "key_tradeoffs", "limitations"} <= fields
    assert fields.isdisjoint({"winner", "ranking", "recommendation", "stance"})


def test_company_valuation_macro_context_is_ten_year_only_and_preformatted(tmp_path) -> None:
    from finance_terminal.storage import SQLiteStore

    store = SQLiteStore(tmp_path / "macro-context.db")
    definition = MacroSeriesDefinition(
        "US_10Y_TREASURY_YIELD", "FRED", "DGS10", "10-year Treasury yield", "percent", "daily",
    )
    store.upsert_macro_series(definition)
    for day, value in ((date(2021, 8, 31), "1.30"), (date(2025, 8, 29), "4.23"), (date(2026, 8, 31), "4.31")):
        store.upsert_macro_observation(MacroObservation(definition.code, day, Decimal(value)))
    service = AnalysisService(store, ConcurrentProvider())
    contexts, evidence, _ = service._macro_context()
    assert service.MACRO_SERIES == ("US_10Y_TREASURY_YIELD",)
    assert len(contexts) == 1 and len(evidence) == 1
    change = contexts[0]["five_year_change"]
    assert change["display_value"].endswith(" pp") and len(change["evidence_refs"]) == 2
    assert all(term not in json.dumps(contexts) for term in ("UNEMPLOYMENT", "INFLATION", "FEDERAL_FUNDS"))
    store.close()
