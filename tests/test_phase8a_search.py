from __future__ import annotations

import json
import asyncio
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from finance_terminal.ai import AskService, ClarificationIntent, CompanyIntent, compact_result
from finance_terminal.ai_provider import FakeAIProvider
from finance_terminal.metrics import DerivedMetricCode, MetricCode
from finance_terminal.query import QueryOperation, RankingOperation
from finance_terminal.analysis import AnalysisService, AnalystClaim, ComparisonAnalysis, ComparisonAnalysisRequest, FocusedAnalysisRequest, FocusedInterpretation
from finance_terminal.query import CompanyQuery
from finance_terminal.models import DerivationKind, DurationScope, FinancialObservation, FiscalPeriod, PeriodKind
from finance_terminal.storage import Company, SQLiteStore


@pytest.fixture
def phase3_store(tmp_path: Path):
    store = SQLiteStore(tmp_path / "ranking.db")
    values = {"AAPL": ("120", 2021), "MSFT": ("80", 2021), "NVDA": ("100", 2021), "WMT": ("70", 2021), "LLY": ("60", 2021), "CAT": ("50", 2021), "META": ("40", 2021), "GOOGL": ("200", 2020), "CVX": ("90", 2020)}
    for index, (ticker, (value, year)) in enumerate(values.items(), 1):
        store.upsert_company(Company(ticker, str(index), ticker))
        store.upsert_financial_observation(FinancialObservation(
            ticker=ticker, cik=str(index), metric=MetricCode.REVENUE, value=Decimal(value), unit="USD", currency="USD",
            period_kind=PeriodKind.DURATION, period_start=date(year, 1, 1), period_end=date(year, 12, 31),
            fiscal_year=year, fiscal_period=FiscalPeriod.FY, duration_scope=DurationScope.ANNUAL,
            derivation=DerivationKind.DIRECT, source_concept="us-gaap:Revenue", accession_number=f"{ticker}-{year}",
            filing_form="10-K", filing_date=date(year + 1, 2, 1),
        ))
    yield store
    store.close()


def test_search_regression_corpus_is_practical_and_structured() -> None:
    corpus = json.loads((Path(__file__).parent / "fixtures" / "parser" / "golden.json").read_text())
    assert 150 <= len(corpus) <= 180
    assert all(isinstance(item, list) and len(item) == 3 and item[1] in {"success", "unsupported", "clarification"} for item in corpus)
    assert any(item[2].get("ranking") == "HIGHEST" and item[2].get("tickers") == [] for item in corpus)
    assert any(item[1] == "unsupported" for item in corpus)
    assert any(item[1] == "clarification" for item in corpus)


@pytest.mark.parametrize("phrase", ["last year", "previous year", "prior year", "last yr"])
def test_relative_previous_year_never_expands_to_history(phase3_store, phrase) -> None:
    service = AskService(phase3_store, FakeAIProvider(), summaries=False)
    query = service.validate_intent(
        f"which company earned the most revenue {phrase}?",
        CompanyIntent(tickers=[], metric=MetricCode.REVENUE, ranking=RankingOperation.HIGHEST),
    )
    assert query.start_year == query.end_year == date.today().year - 1
    assert query.operation is QueryOperation.LEVEL
    assert query.universe_ranking is True


def test_explicit_single_year_and_level_ranking_override_broad_model_plan(phase3_store) -> None:
    service = AskService(phase3_store, FakeAIProvider(), summaries=False)
    query = service.validate_intent(
        "which company had the highest revenue in 2025?",
        CompanyIntent(
            tickers=[], metric=MetricCode.REVENUE, start_year=2019,
            operation=QueryOperation.CAGR, ranking=RankingOperation.HIGHEST,
        ),
    )
    assert query.start_year == query.end_year == 2025
    assert query.operation is QueryOperation.LEVEL


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("which company grew revenue the most in 2025?", QueryOperation.YOY_GROWTH),
        ("which company had the fastest revenue growth since 2021?", QueryOperation.CAGR),
        ("which company had the largest increase in revenue from 2021 through 2025?", QueryOperation.CHANGE),
        ("which company had the strongest margin expansion from 2021 through 2025?", QueryOperation.CHANGE),
    ],
)
def test_growth_and_change_language_cannot_rank_raw_levels(phase3_store, question, expected) -> None:
    service = AskService(phase3_store, FakeAIProvider(), summaries=False)
    query = service.validate_intent(
        question,
        CompanyIntent(
            tickers=[], metric=DerivedMetricCode.REVENUE_GROWTH_YOY,
            start_year=2021, end_year=2025, ranking=RankingOperation.HIGHEST,
        ),
    )
    assert query.operation is expected
    if "revenue" in question:
        assert query.metric is MetricCode.REVENUE


def test_this_year_annual_query_fails_closed(phase3_store) -> None:
    response = AskService(phase3_store, FakeAIProvider(), summaries=False).validate_intent(
        "which company has the highest revenue this year?",
        CompanyIntent(tickers=[], metric=MetricCode.REVENUE, ranking=RankingOperation.HIGHEST),
    )
    assert response.status == "unsupported"
    assert "incomplete" in response.message


def test_two_year_range_operations_are_calculated_from_endpoints(phase3_store) -> None:
    service = AskService(phase3_store, FakeAIProvider(), summaries=False)
    current = phase3_store.financial_observations(["AAPL"], [MetricCode.REVENUE], "annual")[0]
    phase3_store.upsert_financial_observation(replace(
        current, value=Decimal("100"), period_start=date(2020, 1, 1), period_end=date(2020, 12, 31),
        fiscal_year=2020, accession_number="AAPL-2020", filing_date=date(2021, 2, 1),
    ))
    change = service.engine.execute(CompanyQuery(
        tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual",
        start_year=2020, end_year=2021, operation=QueryOperation.CHANGE,
    ))
    assert change.series[0].observations[0].value == Decimal("20")
    assert change.series[0].observations[0].formula == "CHANGE: END - START"
    cagr = service.engine.execute(CompanyQuery(
        tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual",
        start_year=2020, end_year=2021, operation=QueryOperation.CAGR,
    ))
    assert cagr.series[0].observations[0].value == Decimal("0.2")
    assert cagr.series[0].observations[0].formula.startswith("CAGR:")
    assert cagr.series[0].metric == "REVENUE_GROWTH_CAGR"
    assert compact_result(cagr)["series"][0]["observations"][0]["display_value"] == "20.0%"


def test_universe_ranking_expands_to_supported_companies_and_reports_coverage(phase3_store) -> None:
    service = AskService(phase3_store, FakeAIProvider(), summaries=False)
    query = service.validate_intent("highest revenue", CompanyIntent(
        tickers=[], metric=MetricCode.REVENUE, end_year=2021, ranking=RankingOperation.HIGHEST,
    ))
    assert query.ranking is RankingOperation.HIGHEST
    assert len(query.tickers) == 9
    result = service.engine.execute(query)
    assert [item.entity for item in result.series] == ["AAPL", "NVDA", "MSFT", "WMT", "LLY", "CAT", "META"]
    assert result.series[0].context["coverage_count"] == 7


def test_explicit_subset_ranking_and_duplicate_metric_choice(phase3_store) -> None:
    service = AskService(phase3_store, FakeAIProvider(), summaries=False)
    query = service.validate_intent("subset", CompanyIntent(
        tickers=["AAPL", "MSFT"], metric=MetricCode.REVENUE, end_year=2021, ranking=RankingOperation.HIGHEST, ranking_limit=3,
    ))
    assert query.tickers == ["AAPL", "MSFT"]
    assert query.ranking_limit == 3 and query.universe_ranking is False
    clarified = service.validate_intent("Show Nvidia results using Revenue", ClarificationIntent(
        message="Which metric?", tickers=["NVDA"], choices=[MetricCode.REVENUE],
    ))
    assert clarified.choices[0].question.count("using Revenue") == 1


def test_heterogeneous_compare_keeps_partial_evidence_and_records_unavailable_metric(phase3_store, monkeypatch) -> None:
    service = AnalysisService(phase3_store, FakeAIProvider())
    seen = {}

    async def fake_call(prompt, schema, packet, extra=None):
        seen["caveats"] = packet.caveats
        claim = AnalystClaim(text="Microsoft has the only usable FY2021 revenue observation in this comparison.", evidence_refs=[packet.evidence_ids[0]])
        return ComparisonAnalysis(summary=claim, limitations=["Chevron FY2021 revenue is unavailable."])

    monkeypatch.setattr(service, "_call", fake_call)
    response = asyncio.run(service.comparison(ComparisonAnalysisRequest(queries=[CompanyQuery(
        tickers=["MSFT", "CVX"], metric=MetricCode.REVENUE, frequency="annual", start_year=2021, end_year=2021,
    )])))
    assert response.status == "success", response
    assert [item.entity for item in response.evidence_context] == ["MSFT"]
    assert any("CVX" in item for item in seen["caveats"])


@pytest.mark.parametrize("tickers", [
    ["MSFT", "CVX"], ["NVDA", "WMT"], ["LLY", "CAT"], ["META", "UPS"],
    ["KO", "AMD"], ["AMZN", "COP"], ["MSFT", "CVX", "WMT"],
    ["NVDA", "WMT", "LLY"], ["NVDA", "WMT", "LLY", "CAT"],
    ["META", "UPS", "KO", "AMD"],
])
def test_mixed_sector_compare_matrix_qualifies_partial_data(phase3_store, monkeypatch, tickers) -> None:
    existing = {item.ticker for item in phase3_store.companies()}
    for index, ticker in enumerate(tickers, 100):
        if ticker not in existing:
            phase3_store.upsert_company(Company(ticker, str(index), ticker))
        cik = phase3_store.company(ticker).cik
        for metric, value in ((MetricCode.OPERATING_CASH_FLOW, index), (MetricCode.CAPITAL_EXPENDITURE, 10)):
            phase3_store.upsert_financial_observation(FinancialObservation(
                ticker=ticker, cik=cik, metric=metric, value=Decimal(value), unit="USD", currency="USD",
                period_kind=PeriodKind.DURATION, period_start=date(2021, 1, 1), period_end=date(2021, 12, 31),
                fiscal_year=2021, fiscal_period=FiscalPeriod.FY, duration_scope=DurationScope.ANNUAL,
                derivation=DerivationKind.DIRECT, source_concept=f"us-gaap:{metric.value}", accession_number=f"{ticker}-{metric.value}-2021",
                filing_form="10-K", filing_date=date(2022, 2, 1),
            ))
    service = AnalysisService(phase3_store, FakeAIProvider())
    seen = {}

    async def fake_call(prompt, schema, packet, extra=None):
        seen["packet"] = packet
        return ComparisonAnalysis(
            summary=AnalystClaim(text="The available revenue evidence supports a qualified comparison.", evidence_refs=[packet.evidence_ids[0]]),
            limitations=["Metrics without overlapping evidence are omitted."],
        )

    monkeypatch.setattr(service, "_call", fake_call)
    response = asyncio.run(service.comparison(ComparisonAnalysisRequest(tickers=tickers)))
    assert response.status == "success", response
    assert {item.entity for item in response.evidence_context} >= set(tickers)
    assert seen["packet"].caveats


def test_universe_ranking_ai_packet_is_bounded_without_full_ticker_list(phase3_store, monkeypatch) -> None:
    query = AskService(phase3_store, FakeAIProvider(), summaries=False).validate_intent(
        "top revenue", CompanyIntent(tickers=[], metric=MetricCode.REVENUE, end_year=2021, ranking=RankingOperation.HIGHEST),
    )
    service = AnalysisService(phase3_store, FakeAIProvider())
    seen = {}

    async def fake_call(prompt, schema, packet, extra=None):
        seen["packet"] = packet
        return FocusedInterpretation(observations=[AnalystClaim(text="The leading companies are shown.", evidence_refs=[packet.evidence_ids[0]])])

    monkeypatch.setattr(service, "_call", fake_call)
    response = asyncio.run(service.focused(FocusedAnalysisRequest(query=query)))
    packet = seen["packet"]
    assert response.status == "success"
    assert "tickers" not in packet.context
    assert packet.context["total_eligible_count"] == 9
    assert packet.context["total_usable_count"] == 7
    assert packet.context["displayed_count"] == 5
    assert len(packet.metrics) == 5
