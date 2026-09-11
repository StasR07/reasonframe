from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from finance_terminal.analysis import (
    AnalysisService, AnalystAssessment, AnalystClaim, CompanyAnalysisRequest,
    ComparisonAnalysisRequest, Confidence, FocusedAnalysisRequest,
    FocusedInterpretation, MetricClass, OverviewOutput, Stance, metric_class_for,
    period_changes_for_series, statistics_for_series, validate_claims,
)
from finance_terminal.ai_provider import AIUnavailableError, AIUsageLimitError, FakeAIProvider, LazyCodexProvider
from finance_terminal.api import create_app
from finance_terminal.metrics import DerivedMetricCode, MetricCode
from finance_terminal.models import DerivationKind, DurationScope, FinancialObservation, FiscalPeriod, PeriodKind
from finance_terminal.query import CompanyQuery, QueryEngine, SeriesPointResult, SeriesResult
from finance_terminal.storage import Company, SQLiteStore


def fact(ticker: str, value: str, year: int, period: FiscalPeriod = FiscalPeriod.FY) -> FinancialObservation:
    end = date(year, 12, 31) if period is FiscalPeriod.FY else date(year, {FiscalPeriod.Q1: 3, FiscalPeriod.Q2: 6, FiscalPeriod.Q3: 9, FiscalPeriod.Q4: 12}[period], 28)
    return FinancialObservation(
        ticker=ticker, cik={"AAPL": "1", "MSFT": "2"}[ticker], metric=MetricCode.REVENUE,
        value=Decimal(value), unit="USD", currency="USD", period_kind=PeriodKind.DURATION,
        period_start=date(year - 1, 1, 1), period_end=end, fiscal_year=year, fiscal_period=period,
        duration_scope=DurationScope.ANNUAL if period is FiscalPeriod.FY else DurationScope.STANDALONE_QUARTER,
        derivation=DerivationKind.DIRECT, source_concept="us-gaap:Revenue",
        accession_number=f"{ticker}-{year}-{period}", filing_form="10-K", filing_date=end,
    )


@pytest.fixture
def phase3_store(tmp_path):
    store = SQLiteStore(tmp_path / "phase4.db")
    store.upsert_company(Company("AAPL", "1", "Apple Inc."))
    store.upsert_company(Company("MSFT", "2", "Microsoft Corporation"))
    for item in (
        fact("AAPL", "100", 2020), fact("AAPL", "120", 2021),
        fact("AAPL", "25", 2021, FiscalPeriod.Q1), fact("MSFT", "30", 2021, FiscalPeriod.Q1),
    ):
        store.upsert_financial_observation(item)
    yield store
    store.close()


def run(coro):
    return asyncio.run(coro)


def evidence_id(store, query: CompanyQuery) -> str:
    result = QueryEngine(store).execute(query)
    return result.series[0].observations[0].evidence[0].id


def claim(reference: str, text: str = "Grounded observation.") -> AnalystClaim:
    return AnalystClaim(text=text, evidence_refs=[reference])


def assessment(reference: str, stance: Stance = Stance.NEUTRAL) -> AnalystAssessment:
    return AnalystAssessment(
        stance=stance, confidence=Confidence.MODERATE, thesis=claim(reference),
        supporting_claims=[claim(reference, "Supported deterministic evidence.")],
    )


def test_level_statistics_are_deterministic_and_cagr_is_validated(phase3_store) -> None:
    result = QueryEngine(phase3_store).execute(CompanyQuery(
        tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual",
    ))
    stats = {item.name: item for item in statistics_for_series(result.series[0])}
    assert stats["first_value"].value == "100"
    assert stats["last_value"].value == "120"
    assert stats["absolute_change"].value == "20"
    assert Decimal(stats["percentage_change"].value) == Decimal("20.0")
    assert Decimal(stats["change_multiple"].value) == Decimal("1.2")
    assert stats["change_multiple"].display_value == "1.2×"
    assert len(stats["change_multiple"].evidence_refs) == 2
    assert Decimal(stats["cagr_percent"].value).quantize(Decimal("0.1")) == Decimal("20.0")
    assert stats["years_elapsed"].value == "1"
    assert stats["positive_changes"].value == "1"
    assert stats["negative_changes"].value == "0"
    assert stats["recent_direction"].value == "up"
    assert stats["minimum"].period == "FY2020"
    assert stats["maximum"].period == "FY2021"


def synthetic_series(values: list[str], *, metric: str = "REVENUE", unit: str = "USD", frequency: str = "annual") -> SeriesResult:
    return SeriesResult(
        id="synthetic", label=metric, entity="TEST", metric=metric, unit=unit, frequency=frequency,
        observations=[SeriesPointResult(
            date=date(2020 + index // (4 if frequency == "quarterly" else 1), (index % 4) * 3 + 1 if frequency == "quarterly" else 12, 1),
            value=Decimal(value), point_type="SOURCE", fiscal_year=2020 + index // (4 if frequency == "quarterly" else 1),
            fiscal_period=(f"Q{index % 4 + 1}" if frequency == "quarterly" else "FY"), evidence=[],
        ) for index, value in enumerate(values)],
    )


def test_statistics_handle_zero_negative_quarterly_rate_growth_and_missing_values() -> None:
    zero = {item.name: item for item in statistics_for_series(synthetic_series(["0", "5"]))}
    negative = {item.name: item for item in statistics_for_series(synthetic_series(["-5", "5"]))}
    quarterly = {item.name: item for item in statistics_for_series(synthetic_series(["100", "105", "110", "115", "121"], frequency="quarterly"))}
    margin = {item.name: item for item in statistics_for_series(synthetic_series(["0.30", "0.355"], metric="OPERATING_MARGIN", unit="ratio"))}
    growth = {item.name: item for item in statistics_for_series(synthetic_series(["0.10", "0.05", "-0.02"], metric="REVENUE_GROWTH_YOY", unit="ratio"))}
    missing = {item.name: item for item in statistics_for_series(synthetic_series([]))}
    gapped_series = synthetic_series(["100", "121"])
    gapped_series.observations[1].fiscal_year = 2022
    gapped = {item.name: item for item in statistics_for_series(gapped_series)}
    assert zero["percentage_change"].value is None and zero["cagr_percent"].value is None
    assert zero["change_multiple"].state == "NOT_MEANINGFUL" and zero["change_multiple"].display_value == "Not meaningful"
    assert negative["change_multiple"].state == "NOT_MEANINGFUL" and negative["cagr_percent"].value is None
    assert Decimal(quarterly["cagr_percent"].value).quantize(Decimal("0.1")) == Decimal("21.0")
    assert margin["percentage_point_change"].value == "5.500"
    assert "percentage_change" not in margin
    assert Decimal(growth["average_rate"].value).quantize(Decimal("0.1")) == Decimal("4.3")
    assert growth["positive_periods"].value == "2" and growth["negative_periods"].value == "1"
    assert growth["cagr_percent"].value is None
    assert missing["observation_count"].value == "0"
    assert Decimal(gapped["cagr_percent"].value).quantize(Decimal("0.1")) == Decimal("10.0")


def test_change_multiple_is_display_ready_and_margin_change_uses_percentage_points() -> None:
    multiple_series = synthetic_series(["10", "200"])
    multiple_series.observations[0].observation_id = "first"
    multiple_series.observations[1].observation_id = "latest"
    multiple = {item.name: item for item in statistics_for_series(multiple_series)}["change_multiple"]
    margin = {item.name: item for item in statistics_for_series(
        synthetic_series(["0.40", "0.45"], metric="OPERATING_MARGIN", unit="ratio")
    )}
    assert multiple.value == "20" and multiple.display_value == "20.0×"
    assert multiple.evidence_refs == ["first", "latest"] and multiple.formula == "latest / first"
    assert margin["percentage_point_change"].display_value == "+5.0 pp"
    assert "change_multiple" not in margin


def test_focused_analysis_filters_invalid_evidence_refs(phase3_store) -> None:
    query = CompanyQuery(tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual")
    reference = evidence_id(phase3_store, query)
    provider = FakeAIProvider([FocusedInterpretation(observations=[
        claim(reference), claim("fabricated", "Must be removed."),
    ])])
    response = run(AnalysisService(phase3_store, provider).focused(FocusedAnalysisRequest(query=query)))
    assert response.status == "success"
    assert [item.text for item in response.interpretation.observations] == ["Grounded observation."]
    assert response.cache_key.startswith("analysis:focused:v3:")
    assert provider.generate_calls == 1
    assert "precomputed" in provider.prompts[0]


def test_company_orchestration_keeps_fundamentals_when_valuation_is_unavailable(phase3_store) -> None:
    query = CompanyQuery(tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual", start_year=2019)
    reference = evidence_id(phase3_store, query)
    provider = FakeAIProvider([assessment(reference, Stance.POSITIVE)])
    response = run(AnalysisService(phase3_store, provider).company(CompanyAnalysisRequest(ticker="aapl")))
    assert response.status == "success"
    assert response.ticker == "AAPL"
    assert response.fundamentals.stance == "POSITIVE"
    assert response.valuation is None and response.overview is None
    assert response.failures == ["valuation"]
    assert provider.generate_calls == 1


def test_base_failure_is_bounded_and_skips_downstream_review(phase3_store) -> None:
    reference = evidence_id(phase3_store, CompanyQuery(tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual"))
    provider = FakeAIProvider(["invalid"])
    response = run(AnalysisService(phase3_store, provider).company(CompanyAnalysisRequest(ticker="AAPL")))
    assert response.status == "error"
    assert provider.generate_calls == 1


def test_company_role_gets_one_bounded_grounding_repair(phase3_store) -> None:
    reference = evidence_id(phase3_store, CompanyQuery(tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual"))
    invalid = assessment("fabricated", Stance.POSITIVE)
    provider = FakeAIProvider([
        invalid, assessment(reference, Stance.POSITIVE),
    ])
    response = run(AnalysisService(phase3_store, provider).company(CompanyAnalysisRequest(ticker="AAPL")))
    assert response.status == "success"
    assert response.failures == ["valuation"]
    assert response.fundamentals is not None and response.overview is None
    assert provider.generate_calls == 2
    assert "grounding-repair" in provider.prompts[1]


def test_comparison_packet_is_metric_limited_and_has_quarterly_caveat(phase3_store) -> None:
    query = CompanyQuery(tickers=["AAPL", "MSFT"], metric=MetricCode.REVENUE, frequency="quarterly", start_year=2021)
    reference = evidence_id(phase3_store, query)
    provider = FakeAIProvider([FocusedInterpretation(observations=[claim(reference)])])
    response = run(AnalysisService(phase3_store, provider).comparison(ComparisonAnalysisRequest(queries=[query])))
    assert response.status == "success"
    assert {item.metric for item in response.evidence_context} == {"REVENUE"}
    assert "issuer-specific" in provider.prompts[0]


def test_analysis_api_reconstructs_values_and_handles_disconnect(phase3_store) -> None:
    query = CompanyQuery(tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual")
    reference = evidence_id(phase3_store, query)
    with TestClient(create_app(store=phase3_store, provider=FakeAIProvider([
        FocusedInterpretation(observations=[claim(reference)]),
    ]))) as client:
        response = client.post("/api/v1/analyze/result", json={"type": "focused", "query": query.model_dump(mode="json")})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["evidence_context"][0]["observations"][0]["value"] == "100"

    with TestClient(create_app(store=phase3_store, provider=FakeAIProvider(connected=False))) as client:
        response = client.post("/api/v1/analyze/result", json={"type": "focused", "query": query.model_dump(mode="json")})
        assert response.status_code == 200
        assert response.json()["error_code"] == "AI_DISCONNECTED"


def test_analysis_model_call_timeout_is_bounded(phase3_store) -> None:
    class SlowProvider(FakeAIProvider):
        async def generate_structured(self, prompt, output_schema, *, model=None):
            await asyncio.sleep(0.05)
            return await super().generate_structured(prompt, output_schema, model=model)

    query = CompanyQuery(tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual")
    response = run(AnalysisService(phase3_store, SlowProvider([]), timeout=0.001).focused(FocusedAnalysisRequest(query=query)))
    assert response.status == "error"
    assert response.error_code == "AI_TIMEOUT"


def test_metric_classes_period_changes_and_display_values(phase3_store) -> None:
    revenue = QueryEngine(phase3_store).execute(CompanyQuery(tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual")).series[0]
    margin = synthetic_series(["0.320", "0.355"], metric="OPERATING_MARGIN", unit="ratio")
    ratio = synthetic_series(["1.54", "0.89"], metric="CURRENT_RATIO", unit="ratio")
    growth = synthetic_series(["0.10", "0.05"], metric="REVENUE_GROWTH_YOY", unit="ratio")
    assert metric_class_for(revenue) is MetricClass.MONETARY_LEVEL
    assert metric_class_for(margin) is MetricClass.RATE_OR_MARGIN
    assert metric_class_for(ratio) is MetricClass.RATIO
    assert metric_class_for(growth) is MetricClass.GROWTH_RATE
    revenue_change = period_changes_for_series(revenue)[0]
    assert revenue_change.absolute_change == "20"
    assert Decimal(revenue_change.percentage_change).quantize(Decimal("0.1")) == Decimal("20.0")
    assert len(revenue_change.evidence_refs) == 2
    assert revenue_change.display_value == "+$20 (+20.0%)"
    margin_change = period_changes_for_series(margin)[0]
    assert margin_change.percentage_point_change == "3.500"
    assert margin_change.display_value == "+3.5 pp"
    ratio_stats = {item.name: item for item in statistics_for_series(ratio)}
    growth_stats = {item.name: item for item in statistics_for_series(growth)}
    assert "cagr_percent" not in ratio_stats
    assert ratio_stats["absolute_change"].display_value == "-0.65x"
    assert growth_stats["cagr_percent"].value is None
    assert "percentage_change" not in growth_stats
    assert {item.name for item in statistics_for_series(margin)}.isdisjoint({"percentage_change", "cagr_percent"})


def test_recent_direction_uses_both_boundaries_and_prompts_use_display_values(phase3_store) -> None:
    query = CompanyQuery(tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual")
    series = QueryEngine(phase3_store).execute(query).series[0]
    stats = {item.name: item for item in statistics_for_series(series)}
    assert len(stats["recent_direction"].evidence_refs) == 2
    assert stats["percentage_change"].display_value == "+20.0%"
    reference = series.observations[0].evidence[0].id
    provider = FakeAIProvider([FocusedInterpretation(observations=[claim(reference)])])
    run(AnalysisService(phase3_store, provider).focused(FocusedAnalysisRequest(query=query)))
    assert "display_value" in provider.prompts[0]
    assert "Do not print raw decimal values" not in provider.prompts[0]  # concise contract wording is still present below
    assert "never print raw decimal values" in provider.prompts[0]


def test_evidence_ids_in_prose_are_rejected(phase3_store) -> None:
    query = CompanyQuery(tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual")
    series = QueryEngine(phase3_store).execute(query).series[0]
    reference = series.observations[0].evidence[0].id
    packet = AnalysisService(phase3_store, FakeAIProvider())._fundamentals_packet(CompanyAnalysisRequest(ticker="AAPL"))[0]
    clean = FocusedInterpretation(observations=[claim(reference, "Revenue improved across the supplied period.")])
    leaked = FocusedInterpretation(observations=[claim(reference, f"Revenue improved [{reference}].")])
    assert validate_claims(clean, packet) is not None
    assert validate_claims(leaked, packet) is None


def test_scalar_is_suppressed_and_multi_company_search_remains_focused(phase3_store) -> None:
    scalar_query = CompanyQuery(tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual", start_year=2021, end_year=2021)
    scalar_provider = FakeAIProvider()
    scalar = run(AnalysisService(phase3_store, scalar_provider).focused(FocusedAnalysisRequest(query=scalar_query)))
    assert scalar.status == "error" and scalar.error_code == "DATA_UNAVAILABLE"
    assert scalar_provider.generate_calls == 0
    comparison_query = CompanyQuery(tickers=["AAPL", "MSFT"], metric=MetricCode.REVENUE, frequency="quarterly", start_year=2021, end_year=2021)
    reference = evidence_id(phase3_store, comparison_query)
    comparison_provider = FakeAIProvider([FocusedInterpretation(observations=[claim(reference)])])
    comparison = run(AnalysisService(phase3_store, comparison_provider).focused(FocusedAnalysisRequest(query=comparison_query)))
    assert comparison.status == "success" and comparison.mode == "focused"
    assert comparison_provider.generate_calls == 1


@pytest.mark.parametrize("failure,code,message", [
    (AIUsageLimitError("limit"), "AI_USAGE_LIMIT", "Your current Codex usage limit has been reached."),
    (AIUnavailableError("down"), "AI_UNAVAILABLE", "Analysis is temporarily unavailable."),
])
def test_specific_analysis_errors_are_preserved(phase3_store, failure, code, message) -> None:
    query = CompanyQuery(tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual")
    response = run(AnalysisService(phase3_store, FakeAIProvider(failure=failure)).focused(FocusedAnalysisRequest(query=query)))
    assert response.error_code == code
    assert message in response.message


def test_overview_challenge_is_optional_and_default_app_provider_is_lazy(phase3_store) -> None:
    reference = evidence_id(phase3_store, CompanyQuery(tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual"))
    output = OverviewOutput(
        stance=Stance.NEUTRAL, confidence=Confidence.MODERATE,
        conclusion=claim(reference, "The overall evidence is mixed."), summary=claim(reference),
        strongest_evidence=[claim(reference)], skeptic_challenge=None,
    )
    assert output.key_risks == [] and "stance" in OverviewOutput.model_fields
    app = create_app(store=phase3_store)
    assert isinstance(app.state.ai_provider, LazyCodexProvider)
    assert app.state.ai_provider._delegate is None
