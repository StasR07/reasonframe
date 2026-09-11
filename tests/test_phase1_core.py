from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finance_terminal.api import create_app
from finance_terminal.calculations import calculate_company_metric
from finance_terminal.macro import (
    FetchedMacroSeries,
    MACRO_CATALOG,
    MacroObservation,
    MacroSeriesDefinition,
)
from finance_terminal.metrics import DerivedMetricCode, MetricCode
from finance_terminal.models import (
    DerivationKind,
    DurationScope,
    FinancialObservation,
    FiscalPeriod,
    PeriodKind,
    SourceFactIdentity,
)
from finance_terminal.query import (
    CompanyQuery,
    MacroQuery,
    QueryEngine,
    QueryOperation,
    ResultStatus,
)
from finance_terminal.refresh import refresh_macro, refresh_sec
from finance_terminal.sec.edgar_adapter import ObservationUnavailable
from finance_terminal.storage import Company, SQLiteStore


def observation(
    ticker: str,
    metric: MetricCode,
    value: str,
    year: int,
    fiscal_period: FiscalPeriod = FiscalPeriod.FY,
    *,
    end: date | None = None,
) -> FinancialObservation:
    instant = metric in {
        MetricCode.CASH_AND_CASH_EQUIVALENTS,
        MetricCode.SHORT_TERM_INVESTMENTS,
        MetricCode.CURRENT_ASSETS,
        MetricCode.CURRENT_LIABILITIES,
        MetricCode.TOTAL_ASSETS,
        MetricCode.TOTAL_LIABILITIES,
        MetricCode.SHAREHOLDERS_EQUITY,
        MetricCode.CURRENT_PORTION_LONG_TERM_DEBT,
        MetricCode.LONG_TERM_DEBT_NONCURRENT,
    }
    period_end = end or date(year, 12, 31)
    if fiscal_period is not FiscalPeriod.FY and end is None:
        month = {FiscalPeriod.Q1: 3, FiscalPeriod.Q2: 6, FiscalPeriod.Q3: 9, FiscalPeriod.Q4: 12}[fiscal_period]
        period_end = date(year, month, 28)
    return FinancialObservation(
        ticker=ticker,
        cik={"AAPL": "0000320193", "MSFT": "0000789019", "NVDA": "0001045810"}.get(ticker, "0001652044"),
        metric=metric,
        value=Decimal(value),
        unit="USD",
        currency="USD",
        period_kind=PeriodKind.INSTANT if instant else PeriodKind.DURATION,
        period_start=None if instant else date(year - 1, 1, 1) if fiscal_period is FiscalPeriod.FY else date(year, period_end.month - 2, 1),
        period_end=period_end,
        fiscal_year=year,
        fiscal_period=fiscal_period,
        duration_scope=None if instant else DurationScope.ANNUAL if fiscal_period is FiscalPeriod.FY else DurationScope.STANDALONE_QUARTER,
        derivation=DerivationKind.DIRECT,
        source_concept=f"us-gaap:{metric.value}",
        accession_number=f"{ticker}-{year}-{fiscal_period.value}",
        filing_form="10-K" if fiscal_period is FiscalPeriod.FY else "10-Q",
        filing_date=period_end,
    )


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    database = SQLiteStore(tmp_path / "phase1.db")
    yield database
    database.close()


@pytest.mark.parametrize(
    ("metric", "inputs", "expected", "unit"),
    [
        (DerivedMetricCode.GROSS_MARGIN, [(MetricCode.GROSS_PROFIT, "40"), (MetricCode.REVENUE, "100")], Decimal("0.4"), "ratio"),
        (DerivedMetricCode.OPERATING_MARGIN, [(MetricCode.OPERATING_INCOME, "25"), (MetricCode.REVENUE, "100")], Decimal("0.25"), "ratio"),
        (DerivedMetricCode.NET_MARGIN, [(MetricCode.NET_INCOME, "20"), (MetricCode.REVENUE, "100")], Decimal("0.2"), "ratio"),
        (DerivedMetricCode.FREE_CASH_FLOW, [(MetricCode.OPERATING_CASH_FLOW, "30"), (MetricCode.CAPITAL_EXPENDITURE, "8")], Decimal("22"), "USD"),
        (DerivedMetricCode.FCF_MARGIN, [(MetricCode.OPERATING_CASH_FLOW, "30"), (MetricCode.CAPITAL_EXPENDITURE, "8"), (MetricCode.REVENUE, "100")], Decimal("0.22"), "ratio"),
        (DerivedMetricCode.CURRENT_RATIO, [(MetricCode.CURRENT_ASSETS, "150"), (MetricCode.CURRENT_LIABILITIES, "100")], Decimal("1.5"), "ratio"),
    ],
)
def test_derived_metrics_are_decimal_safe_and_keep_inputs(metric, inputs, expected, unit) -> None:
    points = calculate_company_metric(metric, [observation("AAPL", code, value, 2024) for code, value in inputs])
    assert points[0].value == expected
    assert points[0].unit == unit
    assert len(points[0].input_observation_ids) == len(inputs)
    assert points[0].formula


def test_q3_derived_fcf_and_margin_keep_ytd_provenance(store: SQLiteStore) -> None:
    def quarterized(metric: MetricCode, value: str) -> FinancialObservation:
        item = observation("AAPL", metric, value, 2025, FiscalPeriod.Q3)
        sources = (
            SourceFactIdentity("NINE_MONTH_YTD", item.source_concept, Decimal(value) + Decimal("50"), "USD", date(2025, 1, 1), item.period_end, "9m", "10-Q", item.filing_date),
            SourceFactIdentity("SIX_MONTH_YTD", item.source_concept, Decimal("50"), "USD", date(2025, 1, 1), date(2025, 6, 28), "6m", "10-Q", date(2025, 7, 30)),
        )
        return replace(item, derivation=DerivationKind.PERIOD_DERIVED, derivation_sources=sources)

    store.upsert_company(Company("AAPL", "1", "Apple Inc."))
    for item in (
        quarterized(MetricCode.OPERATING_CASH_FLOW, "30"),
        quarterized(MetricCode.CAPITAL_EXPENDITURE, "8"),
        observation("AAPL", MetricCode.REVENUE, "100", 2025, FiscalPeriod.Q3),
    ):
        store.upsert_financial_observation(item)
    engine = QueryEngine(store)
    fcf = engine.execute(CompanyQuery(tickers=["AAPL"], metric="FREE_CASH_FLOW", frequency="quarterly"))
    margin = engine.execute(CompanyQuery(tickers=["AAPL"], metric="FCF_MARGIN", frequency="quarterly"))
    assert fcf.series[0].observations[0].value == Decimal("22")
    assert margin.series[0].observations[0].value == Decimal("0.22")
    roles = {item.derivation for item in fcf.series[0].observations[0].evidence if item.derivation}
    assert any("NINE_MONTH_YTD" in role for role in roles)
    assert any("SIX_MONTH_YTD" in role for role in roles)


def test_revenue_growth_uses_prior_comparable_fiscal_period() -> None:
    inputs = [
        observation("NVDA", MetricCode.REVENUE, "100", 2023),
        observation("NVDA", MetricCode.REVENUE, "125", 2024),
    ]
    point = calculate_company_metric(DerivedMetricCode.REVENUE_GROWTH_YOY, inputs)[0]
    assert point.value == Decimal("0.25")
    assert point.fiscal_year == 2024
    assert len(point.input_observation_ids) == 2


def test_calculation_omits_missing_misaligned_and_zero_denominator() -> None:
    assert calculate_company_metric(DerivedMetricCode.GROSS_MARGIN, [observation("AAPL", MetricCode.REVENUE, "100", 2024)]) == []
    assert calculate_company_metric(DerivedMetricCode.GROSS_MARGIN, [
        observation("AAPL", MetricCode.GROSS_PROFIT, "40", 2024),
        observation("AAPL", MetricCode.REVENUE, "0", 2024),
    ]) == []
    assert calculate_company_metric(DerivedMetricCode.GROSS_MARGIN, [
        observation("AAPL", MetricCode.GROSS_PROFIT, "40", 2024),
        observation("AAPL", MetricCode.REVENUE, "-100", 2024),
    ]) == []
    assert calculate_company_metric(DerivedMetricCode.REVENUE_GROWTH_YOY, [
        observation("AAPL", MetricCode.REVENUE, "-100", 2023),
        observation("AAPL", MetricCode.REVENUE, "40", 2024),
    ]) == []
    assert calculate_company_metric(DerivedMetricCode.GROSS_MARGIN, [
        observation("AAPL", MetricCode.GROSS_PROFIT, "40", 2024),
        observation("AAPL", MetricCode.REVENUE, "100", 2024, end=date(2024, 12, 30)),
    ]) == []
    wrong_unit = replace(observation("AAPL", MetricCode.REVENUE, "100", 2024), unit="EUR")
    assert calculate_company_metric(DerivedMetricCode.GROSS_MARGIN, [
        observation("AAPL", MetricCode.GROSS_PROFIT, "40", 2024), wrong_unit,
    ]) == []


def test_sqlite_round_trip_and_upsert_are_idempotent(store: SQLiteStore) -> None:
    item = observation("AAPL", MetricCode.REVENUE, "100.00", 2024)
    first_id = store.upsert_financial_observation(item)
    second_id = store.upsert_financial_observation(item)
    loaded = store.financial_observations(["AAPL"], [MetricCode.REVENUE], "annual")
    assert first_id == second_id
    assert loaded == [item]
    assert loaded[0].value == Decimal("100.00")
    count = store.table_row_count("financial_observations")
    assert count == 1


def test_sqlite_natural_key_replaces_restated_provenance(store: SQLiteStore) -> None:
    original = observation("AAPL", MetricCode.REVENUE, "100", 2024)
    restated = replace(
        original, value=Decimal("101"), accession_number="AAPL-2024-AMENDED",
        filing_form="10-K/A", quality_flags=("restated",),
    )
    assert store.upsert_financial_observation(original) == store.upsert_financial_observation(restated)
    loaded = store.financial_observations(["AAPL"], [MetricCode.REVENUE], "annual")
    assert len(loaded) == 1
    assert loaded[0].value == Decimal("101")
    assert loaded[0].accession_number == "AAPL-2024-AMENDED"


class OneFactAdapter:
    def annual_observation(self, ticker, metric, year):
        if metric is MetricCode.REVENUE:
            return observation(ticker, metric, "100", year)
        raise ObservationUnavailable("missing")

    def instant_observation(self, ticker, metric, year):
        raise ObservationUnavailable("missing")

    def quarterly_observation(self, ticker, metric, year, quarter):
        raise ObservationUnavailable("missing")


class BrokenAdapter:
    def annual_observation(self, ticker, metric, year):
        raise RuntimeError("provider unavailable")

    def instant_observation(self, ticker, metric, year):
        raise RuntimeError("provider unavailable")

    def quarterly_observation(self, ticker, metric, year, quarter):
        raise RuntimeError("provider unavailable")


def test_sec_refresh_repeats_without_duplicates_and_isolates_unsupported(store: SQLiteStore) -> None:
    initial_revision = int(store.data_revision())
    first = refresh_sec(store, ["AAPL", "XYZ"], OneFactAdapter(), 2024, 2024)
    second = refresh_sec(store, ["AAPL"], OneFactAdapter(), 2024, 2024)
    assert first["XYZ"] == "UNSUPPORTED"
    assert first["AAPL"].startswith("COMPLETED")
    assert second["AAPL"].startswith("COMPLETED")
    assert store.table_row_count("financial_observations") == 1
    assert int(store.data_revision()) == initial_revision + 2
    assert store.catalog()["data_revision"] == store.data_revision()


def test_failed_sec_refresh_with_zero_observations_preserves_existing_history(store: SQLiteStore) -> None:
    store.upsert_company(Company("AAPL", "0000320193", "Apple Inc."))
    store.upsert_financial_observation(observation("AAPL", MetricCode.REVENUE, "100", 2024))

    outcome = refresh_sec(store, ["AAPL"], BrokenAdapter(), 2024, 2024)

    assert outcome["AAPL"].startswith("FAILED")
    loaded = store.financial_observations(["AAPL"], [MetricCode.REVENUE], "annual")
    assert len(loaded) == 1
    assert loaded[0].value == Decimal("100")


class FakeMacroProvider:
    def fetch_series(self, series_id, start_date=None, end_date=None):
        return FetchedMacroSeries(series_id, f"Verified {series_id}", "verified units", "monthly", ((date(2024, 1, 1), Decimal("1.5")),))


def test_macro_refresh_uses_provider_metadata_and_is_idempotent(store: SQLiteStore) -> None:
    refresh_macro(store, FakeMacroProvider())
    refresh_macro(store, FakeMacroProvider())
    assert store.table_row_count("macro_series") == len(MACRO_CATALOG)
    assert store.table_row_count("macro_observations") == len(MACRO_CATALOG)
    label = store.macro_series_metadata("US_HEADLINE_CPI")["label"]
    assert label == "Verified CPIAUCSL"


def seed_acceptance_data(store: SQLiteStore) -> None:
    for ticker, cik, name in (
        ("AAPL", "0000320193", "Apple Inc."),
        ("MSFT", "0000789019", "Microsoft Corporation"),
        ("NVDA", "0001045810", "NVIDIA Corporation"),
    ):
        store.upsert_company(Company(ticker, cik, name))
    facts = [
        observation("NVDA", MetricCode.REVENUE, "100", 2019),
        observation("NVDA", MetricCode.OPERATING_INCOME, "20", 2019),
        observation("NVDA", MetricCode.REVENUE, "150", 2020),
        observation("NVDA", MetricCode.OPERATING_INCOME, "45", 2020),
        observation("MSFT", MetricCode.OPERATING_CASH_FLOW, "80", 2019),
        observation("MSFT", MetricCode.CAPITAL_EXPENDITURE, "20", 2019),
        observation("AAPL", MetricCode.CURRENT_ASSETS, "120", 2021),
        observation("AAPL", MetricCode.CURRENT_LIABILITIES, "80", 2021),
    ]
    for ticker, values in (("AAPL", ("25", "30")), ("MSFT", ("40", "50"))):
        facts.extend([
            observation(ticker, MetricCode.REVENUE, values[0], 2021, FiscalPeriod.Q1),
            observation(ticker, MetricCode.REVENUE, values[1], 2021, FiscalPeriod.Q2),
        ])
    for fact in facts:
        store.upsert_financial_observation(fact)


def test_company_acceptance_queries_and_provenance(store: SQLiteStore) -> None:
    seed_acceptance_data(store)
    engine = QueryEngine(store)
    cases = (
        CompanyQuery(tickers=["NVDA"], metric=DerivedMetricCode.OPERATING_MARGIN, frequency="annual", start_year=2019),
        CompanyQuery(tickers=["AAPL", "MSFT"], metric=MetricCode.REVENUE, frequency="quarterly", start_year=2021),
        CompanyQuery(tickers=["MSFT"], metric=DerivedMetricCode.FREE_CASH_FLOW, frequency="annual", start_year=2019),
        CompanyQuery(tickers=["AAPL"], metric=DerivedMetricCode.CURRENT_RATIO, frequency="annual", start_year=2019),
        CompanyQuery(tickers=["NVDA"], metric=DerivedMetricCode.REVENUE_GROWTH_YOY, frequency="annual", start_year=2019),
    )
    responses = [engine.execute(case) for case in cases]
    assert all(response.status is ResultStatus.SUCCESS for response in responses)
    assert [point.value for point in responses[0].series[0].observations] == [Decimal("0.2"), Decimal("0.3")]
    assert len(responses[1].series) == 2
    assert responses[2].series[0].observations[0].value == Decimal("60")
    assert responses[3].series[0].observations[0].value == Decimal("1.5")
    assert responses[4].series[0].observations[0].value == Decimal("0.5")
    for response in (responses[0], responses[2], responses[3], responses[4]):
        assert response.series[0].observations[0].point_type == "CALCULATED"
        assert response.series[0].observations[0].input_observation_ids


def test_query_operations_validation_and_unavailable_states(store: SQLiteStore) -> None:
    seed_acceptance_data(store)
    engine = QueryEngine(store)
    growth = engine.execute(CompanyQuery(
        tickers=["NVDA"], metric=MetricCode.REVENUE, frequency="annual",
        start_year=2020, operation=QueryOperation.YOY_GROWTH,
    ))
    assert growth.series[0].observations[0].value == Decimal("0.5")
    assert engine.execute(CompanyQuery(tickers=["XYZ"], metric=MetricCode.REVENUE, frequency="annual")).status is ResultStatus.UNSUPPORTED
    assert engine.execute(CompanyQuery(tickers=["AAPL"], metric=MetricCode.TOTAL_ASSETS, frequency="quarterly")).status is ResultStatus.INVALID
    unavailable = engine.execute(CompanyQuery(tickers=["AAPL"], metric=MetricCode.NET_INCOME, frequency="annual"))
    assert unavailable.status is ResultStatus.UNAVAILABLE
    assert unavailable.series == []
    berkshire = engine.execute(CompanyQuery(tickers=["BRK.B"], metric=DerivedMetricCode.GROSS_MARGIN, frequency="annual", start_year=2019))
    assert berkshire.status is ResultStatus.UNSUPPORTED
    assert berkshire.errors[0] == "Gross margin is not meaningful for BRK.B"


def test_macro_inflation_average_acceptance_query(store: SQLiteStore) -> None:
    definition = MACRO_CATALOG["US_HEADLINE_CPI"]
    store.upsert_macro_series(definition)
    for year in range(2009, 2016):
        value = Decimal("100") * (Decimal("1.02") ** (year - 2009))
        for month in range(1, 13):
            store.upsert_macro_observation(MacroObservation("US_HEADLINE_CPI", date(year, month, 1), value))
    response = QueryEngine(store).execute(MacroQuery(
        series=["US_INFLATION_YOY"], start_date=date(2010, 1, 1),
        end_date=date(2015, 12, 31), operation=QueryOperation.AVERAGE,
    ))
    assert response.status is ResultStatus.SUCCESS
    assert response.series[0].observations[0].value == Decimal("2.00")
    assert len(response.series[0].observations[0].input_observation_ids) == 144


def test_api_boundary_has_only_generic_contract_endpoints(store: SQLiteStore) -> None:
    seed_acceptance_data(store)
    client = TestClient(create_app(store))
    assert client.get("/health").json() == {"status": "ok"}
    catalog = client.get("/api/v1/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["company_metric_support"]
    assert "OPERATING_MARGIN" in catalog.json()["company_metrics"]
    assert any(
        item["metric"] == "OPERATING_MARGIN" and item["frequency"] == "annual"
        for item in catalog.json()["company_metric_support"]
    )
    response = client.post("/api/v1/query", json={
        "domain": "company", "tickers": ["NVDA"], "metric": "OPERATING_MARGIN",
        "frequency": "annual", "start_year": 2019, "operation": "LEVEL",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"
    point = response.json()["series"][0]["observations"][0]
    assert len(point["evidence"]) == 2
    assert point["evidence"][0]["accession_number"]
    assert client.post("/api/v1/query", json={"domain": "wrong"}).status_code == 422
