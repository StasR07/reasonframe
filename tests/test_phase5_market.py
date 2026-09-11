from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from finance_terminal.market import (
    CorporateAction, CorporateActionDateType, CorporateActionType, FetchedCorporateAction,
    FetchedDailyBar, FetchedMarketData, MarketDailyObservation, ProviderMarketInstrument,
    indexed_to_100, maximum_drawdown, trailing_return,
)
from finance_terminal.marketstack import MarketstackError, MarketstackProvider
from finance_terminal.api import create_app
from finance_terminal.ai import AskService, MarketIntent, ParsedIntentEnvelope, ValuationIntent
from finance_terminal.ai_provider import FakeAIProvider
from finance_terminal.metrics import MetricCode
from finance_terminal.models import DerivationKind, DurationScope, FinancialObservation, FiscalPeriod, PeriodKind
from finance_terminal.query import CompanyQuery, MarketOperation, MarketPriceSeries, MarketQuery, QueryEngine, ValuationQuery
from finance_terminal.refresh import refresh_market
from finance_terminal.storage import Company, SQLiteStore
from finance_terminal.valuation import ValuationMetricCode, ValuationState, annual_snapshots, current_snapshot, valuation_context


def financial(metric: MetricCode, value: str, year: int, filing_date: date) -> FinancialObservation:
    unit = "shares" if metric is MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES else "USD"
    return FinancialObservation(
        ticker="AAPL", cik="1", metric=metric, value=Decimal(value), unit=unit, currency="USD",
        period_kind=PeriodKind.DURATION, period_start=date(year - 1, 1, 1), period_end=date(year, 12, 31),
        fiscal_year=year, fiscal_period=FiscalPeriod.FY, duration_scope=DurationScope.ANNUAL,
        derivation=DerivationKind.DIRECT, source_concept=f"us-gaap:{metric.value}",
        accession_number=f"aapl-{year}-{metric.value}", filing_form="10-K", filing_date=filing_date,
    )


def bar(day: date, raw: str, adjusted: str | None = None, instrument: str = "us-xnas-aapl") -> MarketDailyObservation:
    return MarketDailyObservation(
        trading_date=day, close=Decimal(raw), adjusted_close=Decimal(adjusted) if adjusted is not None else None,
        instrument_id=instrument, currency="USD", source_provider="MARKETSTACK", source_symbol="AAPL",
        retrieved_at="2026-01-01T00:00:00+00:00",
    )


@pytest.fixture
def market_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "phase5.db")
    for ticker, name in (("AAPL", "Apple Inc."), ("MSFT", "Microsoft Corporation"), ("NVDA", "NVIDIA Corporation"), ("GOOGL", "Alphabet Inc.")):
        store.upsert_company(Company(ticker, ticker, name))
    store.seed_market_instruments()
    yield store
    store.close()


def test_marketstack_normalizes_paginates_and_validates_identity() -> None:
    calls: list[dict[str, list[str]]] = []
    def transport(url: str, _: float):
        query = parse_qs(urlparse(url).query); calls.append(query)
        offset = int(query["offset"][0])
        rows = [{
            "symbol": "AAPL", "exchange": "XNAS", "date": "2024-06-03T00:00:00+00:00",
            "open": "100.1", "high": "102", "low": "99", "close": "101.25", "volume": 10,
            "adj_close": "50.625",
        }] if offset == 0 else [{
            "symbol": "AAPL", "exchange": "XNAS", "date": "2024-06-04T00:00:00Z", "close": "102",
            "adj_close": None,
        }]
        return {"pagination": {"limit": 1, "offset": offset, "count": 1, "total": 2}, "data": rows}
    provider = MarketstackProvider("secret", page_size=1, transport=transport)
    instrument = ProviderMarketInstrument("i", "MARKETSTACK", "AAPL", "XNAS")
    rows = provider.fetch_daily_bars(instrument, date(2024, 6, 1), date(2024, 6, 5))
    assert [item.close for item in rows] == [Decimal("101.25"), Decimal("102")]
    assert rows[0].adjusted_close == Decimal("50.625") and rows[1].adjusted_close is None
    assert [item["offset"][0] for item in calls] == ["0", "1"]
    assert all(item["access_key"] == ["secret"] for item in calls)

    def mismatch(_: str, __: float):
        return {"data": [{"symbol": "MSFT", "exchange": "XNAS", "date": "2024-06-03", "close": "1"}]}
    with pytest.raises(MarketstackError, match="symbol mismatch"):
        MarketstackProvider("secret", transport=mismatch).fetch_daily_bars(instrument, date(2024, 6, 1), date(2024, 6, 5))


def test_marketstack_skips_missing_close_and_normalizes_actions() -> None:
    def transport(url: str, _: float):
        endpoint = urlparse(url).path.rsplit("/", 1)[-1]
        if endpoint == "eod":
            return {"data": [{"symbol": "AAPL", "date": "2024-01-02", "close": None}]}
        if endpoint == "splits":
            return {"data": [{"symbol": "AAPL", "date": "2024-01-03", "split_factor": "4"}]}
        return {"data": [{"symbol": "AAPL", "date": "2024-02-01", "dividend": "0.24", "currency": "USD"}]}
    provider = MarketstackProvider("secret", transport=transport, include_provider_dividends=True)
    instrument = ProviderMarketInstrument("i", "MARKETSTACK", "AAPL")
    assert provider.fetch_daily_bars(instrument, date(2024, 1, 1), date(2024, 2, 2)) == ()
    actions = provider.fetch_corporate_actions(instrument, date(2024, 1, 1), date(2024, 2, 2))
    assert actions[0].split_ratio == Decimal("4")
    assert actions[1].date_type is CorporateActionDateType.PROVIDER_REPORTED


def test_market_persistence_is_idempotent_and_correctable(market_store: SQLiteStore) -> None:
    first = bar(date(2024, 6, 3), "100", "50")
    market_store.upsert_market_observation(first)
    market_store.upsert_market_observation(bar(date(2024, 6, 3), "101", "50.5"))
    assert market_store.market_observations("us-xnas-aapl")[0].close == Decimal("101")
    action = CorporateAction(
        CorporateActionType.SPLIT, date(2024, 6, 4), CorporateActionDateType.EFFECTIVE_DATE,
        split_ratio=Decimal("4"), instrument_id="us-xnas-aapl", source_provider="MARKETSTACK",
        source_symbol="AAPL", retrieved_at="now",
    )
    market_store.upsert_corporate_action(action)
    market_store.upsert_corporate_action(action)
    assert len(market_store.corporate_actions("us-xnas-aapl")) == 1


def test_raw_adjusted_calculations_have_frozen_semantics() -> None:
    observations = [
        bar(date(2023, 6, 2), "200", "50"),
        bar(date(2023, 6, 5), "204", "51"),
        bar(date(2024, 6, 3), "120", "60"),
        bar(date(2024, 6, 4), "100", "50"),
    ]
    result = trailing_return(observations, date(2023, 6, 4))
    assert result and result[1].trading_date == date(2023, 6, 2)
    assert result[0] == Decimal("0")
    drawdown = maximum_drawdown(observations)
    assert drawdown and drawdown.value == Decimal("-0.1666666666666666666666666667")
    assert drawdown.peak_date == date(2024, 6, 3) and drawdown.trough_date == date(2024, 6, 4)
    second = [
        bar(date(2024, 6, 3), "30", "30", "us-xnas-msft"),
        bar(date(2024, 6, 4), "36", "36", "us-xnas-msft"),
    ]
    indexed = indexed_to_100({"AAPL": observations, "MSFT": second})
    assert indexed["AAPL"][0][1] == indexed["MSFT"][0][1] == Decimal("100")
    assert indexed_to_100({"AAPL": observations[:2], "MSFT": second}) == {}


def test_query_latest_exact_indexed_and_no_forward_fill(market_store: SQLiteStore) -> None:
    for item in (bar(date(2024, 6, 3), "101", "50"), bar(date(2024, 6, 4), "103", "51")):
        market_store.upsert_market_observation(item)
    for item in (
        bar(date(2024, 6, 3), "20", "20", "us-xnas-msft"),
        bar(date(2024, 6, 4), "22", "22", "us-xnas-msft"),
    ):
        market_store.upsert_market_observation(item)
    engine = QueryEngine(market_store)
    latest = engine.execute(MarketQuery(tickers=["AAPL"], series="RAW_CLOSE", view="latest"))
    assert latest.series[0].observations[0].value == Decimal("103")
    assert latest.series[0].observations[0].evidence == []
    assert latest.series[0].observations[0].observation_id == "market:us-xnas-aapl:2024-06-04:close"
    holiday = engine.execute(MarketQuery(
        tickers=["AAPL"], series="RAW_CLOSE", start_date=date(2024, 6, 2), end_date=date(2024, 6, 2),
    ))
    assert holiday.status == "UNAVAILABLE"
    indexed = engine.execute(MarketQuery(
        tickers=["AAPL", "MSFT"], series="ADJUSTED_CLOSE", operation="INDEXED",
        start_date=date(2024, 6, 1),
    ))
    assert all(series.observations[0].value == Decimal("100") for series in indexed.series)
    with pytest.raises(ValidationError):
        MarketQuery(tickers=["AAPL"], series=MarketPriceSeries.RAW_CLOSE, operation=MarketOperation.RETURN)


def test_filing_aware_valuation_uses_monday_and_aligns_split() -> None:
    friday = date(2024, 2, 2); monday = date(2024, 2, 5)
    inputs = [
        financial(MetricCode.DILUTED_EPS, "8", 2023, friday),
        financial(MetricCode.REVENUE, "1000", 2023, date(2024, 2, 1)),
        financial(MetricCode.OPERATING_CASH_FLOW, "200", 2023, date(2024, 2, 1)),
        financial(MetricCode.CAPITAL_EXPENDITURE, "40", 2023, friday),
        financial(MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES, "100", 2023, friday),
    ]
    prices = [bar(friday, "40", "10"), bar(monday, "44", "11"), bar(date(2024, 6, 4), "50", "12.5")]
    split = CorporateAction(
        CorporateActionType.SPLIT, date(2024, 4, 1), CorporateActionDateType.EFFECTIVE_DATE,
        split_ratio=Decimal("4"), instrument_id="us-xnas-aapl", source_provider="MARKETSTACK",
        source_symbol="AAPL", retrieved_at="now",
    )
    historical = annual_snapshots(ValuationMetricCode.PE_RATIO, inputs, prices, [split])[0]
    assert historical.price_date == monday
    assert historical.value == Decimal("5.5")  # split occurred later, so filing snapshot remains pre-split basis
    current = current_snapshot(ValuationMetricCode.PE_RATIO, inputs, prices, [split])
    assert current and current.price_date == date(2024, 6, 4)
    assert current.value == Decimal("25")  # EPS 8 / 4 = 2
    assert current.split_actions == (split,)


@pytest.mark.parametrize("metric,expected", [
    (ValuationMetricCode.PE_RATIO, Decimal("5.5")),
    (ValuationMetricCode.PS_RATIO, Decimal("4.4")),
    (ValuationMetricCode.P_FCF_RATIO, Decimal("27.5")),
    (ValuationMetricCode.FCF_YIELD, Decimal("0.03636363636363636363636363636")),
])
def test_valuation_formulas(metric: ValuationMetricCode, expected: Decimal) -> None:
    filed = date(2024, 2, 2)
    inputs = [
        financial(MetricCode.DILUTED_EPS, "8", 2023, filed), financial(MetricCode.REVENUE, "1000", 2023, filed),
        financial(MetricCode.OPERATING_CASH_FLOW, "200", 2023, filed), financial(MetricCode.CAPITAL_EXPENDITURE, "40", 2023, filed),
        financial(MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES, "100", 2023, filed),
    ]
    snapshot = annual_snapshots(metric, inputs, [bar(date(2024, 2, 5), "44", "44")], [])[0]
    assert snapshot.state is ValuationState.AVAILABLE
    assert snapshot.value == expected


def test_not_meaningful_and_context_threshold() -> None:
    filed = date(2024, 2, 2)
    negative = annual_snapshots(
        ValuationMetricCode.PE_RATIO, [financial(MetricCode.DILUTED_EPS, "-1", 2023, filed)],
        [bar(date(2024, 2, 5), "44", "44")], [],
    )[0]
    assert negative.state is ValuationState.NOT_MEANINGFUL and negative.value is None
    one = negative.__class__(negative.metric, ValuationState.AVAILABLE, negative.fiscal_year, negative.availability_date, negative.price_date, negative.price, Decimal("10"), negative.formula, negative.sec_inputs, ())
    two = negative.__class__(negative.metric, ValuationState.AVAILABLE, 2024, negative.availability_date, negative.price_date, negative.price, Decimal("20"), negative.formula, negative.sec_inputs, ())
    context = valuation_context(two, [one, two])
    assert context.median == Decimal("15") and context.interpretation_available is False
    three = negative.__class__(negative.metric, ValuationState.AVAILABLE, 2025, negative.availability_date, negative.price_date, negative.price, Decimal("30"), negative.formula, negative.sec_inputs, ())
    context = valuation_context(three, [one, two, three])
    assert context.interpretation_available and context.relative_to_median == Decimal("0.5")


def test_valuation_query_emits_mixed_evidence(market_store: SQLiteStore) -> None:
    filed = date(2024, 2, 2)
    market_store.upsert_financial_observation(financial(MetricCode.DILUTED_EPS, "8", 2023, filed))
    market_store.upsert_market_observation(bar(date(2024, 2, 5), "44", "44"))
    market_store.upsert_market_observation(bar(date(2024, 2, 6), "48", "48"))
    response = QueryEngine(market_store).execute(ValuationQuery(tickers=["AAPL"], metric="PE_RATIO"))
    point = response.series[0].observations[0]
    assert point.valuation_basis == "Latest FY basis" and point.source_fiscal_year == 2023
    assert {item.source_type for item in point.evidence} == {"MARKET", "SEC"}


class FakeMarketProvider:
    name = "MARKETSTACK"
    def __init__(self): self.calls: list[tuple[str, date, date]] = []
    def fetch_eod(self, instrument, start_date, end_date):
        self.calls.append((instrument.provider_symbol, start_date, end_date))
        if instrument.provider_symbol == "MSFT": raise RuntimeError("rate limited")
        return FetchedMarketData(
            (FetchedDailyBar(date(2024, 6, 3), Decimal("100"), adjusted_close=Decimal("100")),),
            (),
        )


def test_refresh_isolates_instrument_failures_and_creates_no_synthetic_days(market_store: SQLiteStore) -> None:
    provider = FakeMarketProvider()
    result = refresh_market(market_store, ["AAPL", "MSFT"], provider, start_date=date(2024, 6, 1), end_date=date(2024, 6, 5))
    assert result["AAPL"].startswith("COMPLETED") and result["MSFT"].startswith("FAILED")
    rows = market_store.market_observations("us-xnas-aapl")
    assert [item.trading_date for item in rows] == [date(2024, 6, 3)]


def test_catalog_keeps_alphabet_one_company_with_two_instruments(market_store: SQLiteStore) -> None:
    catalog = market_store.catalog()
    alphabet = [item for item in catalog["companies"] if item["ticker"] == "GOOGL"]
    instruments = [item for item in catalog["market_instruments"] if item["company_ticker"] == "GOOGL"]
    assert len(alphabet) == 1 and {item["symbol"] for item in instruments} == {"GOOGL", "GOOG"}
    assert next(item for item in instruments if item["symbol"] == "GOOG")["valuation_status"] == "PARTIAL"


def test_file_backed_sqlite_is_stable_under_concurrent_mixed_queries(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "concurrency.db")
    try:
        tickers = ("AAPL", "MSFT", "NVDA", "GOOGL")
        for ticker in tickers:
            store.upsert_company(Company(ticker, ticker, ticker))
        store.seed_market_instruments()
        filed = date(2024, 2, 2)
        for ticker in tickers:
            facts = [
                financial(MetricCode.DILUTED_EPS, "8", 2023, filed),
                financial(MetricCode.REVENUE, "1000", 2023, filed),
                financial(MetricCode.OPERATING_CASH_FLOW, "200", 2023, filed),
                financial(MetricCode.CAPITAL_EXPENDITURE, "40", 2023, filed),
                financial(MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES, "100", 2023, filed),
            ]
            for item in facts:
                store.upsert_financial_observation(replace(
                    item, ticker=ticker, cik=ticker,
                    accession_number=f"{ticker}-{item.accession_number}",
                ))
            instrument = store.primary_market_instrument(ticker)
            assert instrument is not None
            store.upsert_market_observation(bar(date(2024, 2, 5), "44", "44", instrument.instrument_id))
            store.upsert_market_observation(bar(date(2024, 6, 4), "48", "48", instrument.instrument_id))

        queries = []
        for index in range(200):
            ticker = tickers[index % len(tickers)]
            kind = index % 7
            if kind < 4:
                metric = ("PE_RATIO", "PS_RATIO", "P_FCF_RATIO", "FCF_YIELD")[kind]
                queries.append(ValuationQuery(tickers=[ticker], metric=metric))
            elif kind == 4:
                queries.append(ValuationQuery(tickers=[ticker], metric="PE_RATIO", view="history"))
            elif kind == 5:
                queries.append(MarketQuery(tickers=[ticker], series="RAW_CLOSE", view="latest"))
            else:
                queries.append(CompanyQuery(tickers=[ticker], metric="REVENUE", frequency="annual"))

        def execute(query):
            result = QueryEngine(store).execute(query)
            return result.model_dump_json()

        with ThreadPoolExecutor(max_workers=16) as executor:
            payloads = list(executor.map(execute, queries))
        assert len(payloads) == 200
        assert all('"status":"SUCCESS"' in payload for payload in payloads)
        repeated = [payloads[index] for index in range(0, len(payloads), 28)]
        assert len(set(repeated)) == 1
    finally:
        store.close()


def test_search_market_and_valuation_intents_execute_deterministically(market_store: SQLiteStore) -> None:
    market_store.upsert_market_observation(bar(date(2024, 6, 3), "101", "50"))
    market_provider = FakeAIProvider([ParsedIntentEnvelope(intent=MarketIntent(
        tickers=["AAPL"], series="RAW_CLOSE", view="latest",
    ))])
    market = asyncio.run(AskService(market_store, market_provider).ask("What is Apple's latest stock price?"))
    assert market.status == "success"
    assert market.validated_query.domain == "market"
    assert market.results.series[0].observations[0].value == Decimal("101")

    filed = date(2024, 2, 2)
    market_store.upsert_financial_observation(financial(MetricCode.DILUTED_EPS, "8", 2023, filed))
    valuation_provider = FakeAIProvider([ParsedIntentEnvelope(intent=ValuationIntent(
        tickers=["AAPL"], metric="PE_RATIO",
    ))])
    valuation = asyncio.run(AskService(market_store, valuation_provider).ask("What is Apple's P/E?"))
    assert valuation.status == "success"
    assert valuation.validated_query.domain == "valuation"
    assert {item.source_type for item in valuation.results.series[0].observations[0].evidence} == {"SEC", "MARKET"}


def test_market_coverage_metadata_and_fixed_period_honesty(market_store: SQLiteStore) -> None:
    for item in (
        bar(date(2024, 6, 3), "100", "100"),
        bar(date(2024, 6, 4), "90", "90"),
        bar(date(2024, 6, 5), "110", "110"),
    ):
        market_store.upsert_market_observation(item)
    support = next(item for item in market_store.catalog()["market_support"] if item["ticker"] == "AAPL")
    assert support == {
        "ticker": "AAPL", "instrument_id": "us-xnas-aapl",
        "first_date": "2024-06-03", "last_date": "2024-06-05", "observation_count": 3,
    }

    engine = QueryEngine(market_store)
    available = engine.execute(MarketQuery(
        tickers=["AAPL"], series="ADJUSTED_CLOSE", start_date=date(2024, 6, 3), operation="RETURN",
    ))
    assert available.status == "SUCCESS"
    assert available.series[0].observations[0].value == Decimal("0.1")

    for operation in ("RETURN", "MAX_DRAWDOWN"):
        unavailable = engine.execute(MarketQuery(
            tickers=["AAPL"], series="ADJUSTED_CLOSE", start_date=date(2019, 6, 3), operation=operation,
        ))
        assert unavailable.status == "UNAVAILABLE"
        assert unavailable.series[0].state == "UNAVAILABLE"
        assert unavailable.series[0].context["coverage"] == "PARTIAL"
        assert "2024-06-03 through 2024-06-05" in unavailable.errors[0]


def test_drawdown_uses_peak_trough_date_and_compact_evidence(market_store: SQLiteStore) -> None:
    for item in (
        bar(date(2024, 1, 2), "80", "80"),
        bar(date(2024, 1, 3), "120", "120"),
        bar(date(2024, 1, 4), "90", "90"),
        bar(date(2024, 1, 5), "100", "100"),
    ):
        market_store.upsert_market_observation(item)
    response = QueryEngine(market_store).execute(MarketQuery(
        tickers=["AAPL"], series="ADJUSTED_CLOSE", start_date=date(2024, 1, 2), operation="MAX_DRAWDOWN",
    ))
    point = response.series[0].observations[0]
    assert point.value == Decimal("-0.25") and point.date == date(2024, 1, 4)
    assert point.input_observation_ids == [
        "market:us-xnas-aapl:2024-01-03:adjusted_close",
        "market:us-xnas-aapl:2024-01-04:adjusted_close",
    ]
    assert point.evidence == []


def test_indexed_lineage_has_current_and_shared_base_without_forward_fill(market_store: SQLiteStore) -> None:
    for item in (
        bar(date(2024, 6, 3), "50", "50"),
        bar(date(2024, 6, 4), "60", "60"),
    ):
        market_store.upsert_market_observation(item)
    for item in (
        bar(date(2024, 6, 3), "25", "25", "us-xnas-msft"),
        bar(date(2024, 6, 5), "30", "30", "us-xnas-msft"),
    ):
        market_store.upsert_market_observation(item)
    response = QueryEngine(market_store).execute(MarketQuery(
        tickers=["AAPL", "MSFT"], series="ADJUSTED_CLOSE",
        start_date=date(2024, 6, 3), operation="INDEXED",
    ))
    aapl, msft = response.series
    assert aapl.observations[0].value == msft.observations[0].value == Decimal("100")
    assert aapl.observations[1].value == Decimal("120")
    assert [point.date for point in msft.observations] == [date(2024, 6, 3), date(2024, 6, 5)]
    assert len(aapl.observations[0].input_observation_ids) == 1
    assert aapl.context["base_observation_id"] in aapl.observations[1].input_observation_ids
    assert len(aapl.observations[1].input_observation_ids) == 2
    assert all(point.evidence == [] for series in response.series for point in series.observations)


def test_historical_valuation_respects_range_and_context_uses_latest_five_usable(market_store: SQLiteStore) -> None:
    for year in range(2019, 2026):
        filed = date(year + 1, 2, 1)
        market_store.upsert_financial_observation(financial(MetricCode.DILUTED_EPS, str(year - 2010), year, filed))
        market_store.upsert_market_observation(bar(date(year + 1, 2, 3), "100", "100"))
    response = QueryEngine(market_store).execute(ValuationQuery(
        tickers=["AAPL"], metric="PE_RATIO", view="history", start_year=2019, end_year=2025,
    ))
    assert [point.fiscal_year for point in response.series[0].observations] == list(range(2019, 2026))

    template = annual_snapshots(
        ValuationMetricCode.PE_RATIO, [financial(MetricCode.DILUTED_EPS, "1", 2020, date(2021, 2, 1))],
        [bar(date(2021, 2, 2), "10", "10")], [],
    )[0]
    history = []
    for index, value in enumerate(("10", None, "20", "30", None, "40", "50"), start=2019):
        state = ValuationState.AVAILABLE if value is not None else ValuationState.NOT_MEANINGFUL
        history.append(template.__class__(
            template.metric, state, index, template.availability_date, template.price_date,
            template.price, Decimal(value) if value is not None else None, template.formula,
            template.sec_inputs, (),
        ))
    context = valuation_context(history[-1], history)
    assert context.observation_count == 5
    assert context.minimum == Decimal("10") and context.maximum == Decimal("50") and context.median == Decimal("30")


@pytest.mark.parametrize("fcf,ratio_state,yield_value", [
    ("100", ValuationState.AVAILABLE, Decimal("0.025")),
    ("0", ValuationState.NOT_MEANINGFUL, Decimal("0")),
    ("-100", ValuationState.NOT_MEANINGFUL, Decimal("-0.025")),
])
def test_pfcf_and_fcf_yield_diverge_for_non_positive_fcf(
    fcf: str, ratio_state: ValuationState, yield_value: Decimal,
) -> None:
    filed = date(2024, 2, 2)
    inputs = [
        financial(MetricCode.OPERATING_CASH_FLOW, fcf, 2023, filed),
        financial(MetricCode.CAPITAL_EXPENDITURE, "0", 2023, filed),
        financial(MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES, "100", 2023, filed),
    ]
    price = [bar(date(2024, 2, 5), "40", "40")]
    ratio = annual_snapshots(ValuationMetricCode.P_FCF_RATIO, inputs, price, [])[0]
    yield_snapshot = annual_snapshots(ValuationMetricCode.FCF_YIELD, inputs, price, [])[0]
    assert ratio.state is ratio_state
    assert yield_snapshot.state is ValuationState.AVAILABLE and yield_snapshot.value == yield_value


def test_lazy_market_evidence_resolves_locally_and_payload_is_thin(market_store: SQLiteStore) -> None:
    for day in range(1, 11):
        market_store.upsert_market_observation(bar(date(2024, 6, day), str(100 + day), str(100 + day)))
    engine = QueryEngine(market_store)
    response = engine.execute(MarketQuery(tickers=["AAPL"], series="ADJUSTED_CLOSE"))
    assert len(response.series[0].observations) == 10
    assert all(point.observation_id and point.evidence == [] for point in response.series[0].observations)
    reference = response.series[0].observations[0].observation_id
    resolved = engine.resolve_evidence(reference)
    assert resolved and resolved.provider == "MARKETSTACK" and resolved.market_field == "adjusted_close"
    assert resolved.period_end == date(2024, 6, 1)
    provider = FakeAIProvider()
    with TestClient(create_app(market_store, provider=provider)) as client:
        payload = client.get(f"/api/v1/evidence/{reference}").json()
    assert payload["id"] == reference and payload["source_type"] == "MARKET"
    assert provider.generate_calls == 0


def test_search_preserves_unavailable_and_not_meaningful_states(market_store: SQLiteStore) -> None:
    market_store.upsert_market_observation(bar(date(2024, 6, 3), "101", "50"))
    unavailable_provider = FakeAIProvider([ParsedIntentEnvelope(intent=MarketIntent(
        tickers=["AAPL"], series="ADJUSTED_CLOSE", start_date=date(2019, 6, 3), operation="RETURN",
    ))])
    unavailable = asyncio.run(AskService(market_store, unavailable_provider).ask("Show AAPL 5Y return"))
    assert unavailable.status == "success"
    assert unavailable.results.status == "UNAVAILABLE"

    filed = date(2024, 2, 2)
    market_store.upsert_financial_observation(financial(MetricCode.DILUTED_EPS, "-1", 2023, filed))
    not_meaningful_provider = FakeAIProvider([ParsedIntentEnvelope(intent=ValuationIntent(
        tickers=["AAPL"], metric="PE_RATIO",
    ))])
    not_meaningful = asyncio.run(AskService(market_store, not_meaningful_provider).ask("What is AAPL P/E?"))
    assert not_meaningful.status == "success"
    assert not_meaningful.results.series[0].state == "NOT_MEANINGFUL"
