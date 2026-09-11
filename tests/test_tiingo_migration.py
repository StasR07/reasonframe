from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from finance_terminal.ai_provider import FakeAIProvider
from finance_terminal.api import create_app
from finance_terminal.market import (
    CorporateAction,
    CorporateActionDateType,
    CorporateActionType,
    FetchedDailyBar,
    FetchedMarketData,
    MarketProviderError,
    MarketProviderErrorCode,
    ProviderMarketInstrument,
)
from finance_terminal.query import QueryEngine
from finance_terminal.refresh import refresh_market
from finance_terminal.storage import Company, SQLiteStore
from finance_terminal.tiingo import TiingoProvider, _default_transport


def instrument(ticker: str = "AAPL") -> ProviderMarketInstrument:
    return ProviderMarketInstrument(f"us-xnas-{ticker.lower()}", "TIINGO", ticker, "XNAS")


def row(day: str, close: object = "101.25", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "date": f"{day}T00:00:00.000Z",
        "open": "100.1",
        "high": "102",
        "low": "99",
        "close": close,
        "volume": 1000,
        "adjOpen": "25.025",
        "adjHigh": "25.5",
        "adjLow": "24.75",
        "adjClose": "25.3125",
        "adjVolume": 4000,
        "divCash": 0,
        "splitFactor": 1,
    }
    payload.update(overrides)
    return payload


def test_tiingo_maps_raw_adjusted_fields_and_uses_one_eod_request() -> None:
    calls: list[tuple[str, str]] = []

    def transport(url: str, token: str, _: float) -> object:
        calls.append((url, token))
        return [row("2024-06-03"), row("2024-06-04", "102", adjClose=None)]

    result = TiingoProvider("secret", transport=transport).fetch_eod(
        instrument(), date(2024, 6, 1), date(2024, 6, 5),
    )
    assert len(calls) == 1 and calls[0][1] == "secret"
    query = parse_qs(urlparse(calls[0][0]).query)
    assert query["startDate"] == ["2024-06-01"] and query["endDate"] == ["2024-06-05"]
    assert result.bars[0] == FetchedDailyBar(
        trading_date=date(2024, 6, 3), close=Decimal("101.25"),
        open=Decimal("100.1"), high=Decimal("102"), low=Decimal("99"), volume=Decimal("1000"),
        adjusted_open=Decimal("25.025"), adjusted_high=Decimal("25.5"),
        adjusted_low=Decimal("24.75"), adjusted_close=Decimal("25.3125"),
        adjusted_volume=Decimal("4000"),
    )
    assert result.bars[1].adjusted_close is None
    assert result.corporate_actions == ()


def test_tiingo_derives_known_splits_and_dividend_from_eod() -> None:
    payload = [
        row("2020-08-31", splitFactor=4),
        row("2024-05-10", divCash="0.25"),
        row("2024-06-10", splitFactor=10),
    ]
    provider = TiingoProvider("secret", transport=lambda *_: payload)
    apple = provider.fetch_eod(instrument(), date(2020, 8, 31), date(2024, 6, 10))
    assert apple.corporate_actions[0].action_type is CorporateActionType.SPLIT
    assert apple.corporate_actions[0].event_date == date(2020, 8, 31)
    assert apple.corporate_actions[0].split_ratio == Decimal("4")
    assert apple.corporate_actions[0].date_type is CorporateActionDateType.EX_DATE
    dividend = apple.corporate_actions[1]
    assert dividend.action_type is CorporateActionType.CASH_DIVIDEND
    assert dividend.event_date == date(2024, 5, 10)
    assert dividend.cash_amount_per_share == Decimal("0.25") and dividend.currency == "USD"
    nvda_split = apple.corporate_actions[2]
    assert nvda_split.event_date == date(2024, 6, 10) and nvda_split.split_ratio == Decimal("10")


@pytest.mark.parametrize("payload", [
    {"data": []},
    ["bad-row"],
    [row("2024-06-03", close=None)],
    [row("bad-date")],
    [row("2024-06-03", splitFactor=0)],
    [row("2024-06-03", divCash=-1)],
])
def test_tiingo_rejects_malformed_payloads(payload: object) -> None:
    provider = TiingoProvider("secret", transport=lambda *_: payload)
    with pytest.raises(MarketProviderError) as error:
        provider.fetch_eod(instrument(), date(2024, 6, 1), date(2024, 6, 5))
    assert error.value.code is MarketProviderErrorCode.MALFORMED_RESPONSE


@pytest.mark.parametrize("status,code", [
    (401, MarketProviderErrorCode.AUTHENTICATION_ERROR),
    (403, MarketProviderErrorCode.AUTHENTICATION_ERROR),
    (404, MarketProviderErrorCode.INSTRUMENT_NOT_FOUND),
    (429, MarketProviderErrorCode.RATE_LIMITED),
    (500, MarketProviderErrorCode.PROVIDER_UNAVAILABLE),
])
def test_tiingo_normalizes_http_failures(monkeypatch: pytest.MonkeyPatch, status: int, code: MarketProviderErrorCode) -> None:
    def fail(*_: object, **__: object) -> object:
        raise HTTPError("https://api.tiingo.com", status, "provider detail", {}, None)

    monkeypatch.setattr("finance_terminal.tiingo.urlopen", fail)
    with pytest.raises(MarketProviderError) as error:
        _default_transport("https://api.tiingo.com/test", "secret", 1)
    assert error.value.code is code
    assert "provider detail" not in str(error.value)


def test_tiingo_normalizes_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("finance_terminal.tiingo.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(MarketProviderError) as error:
        _default_transport("https://api.tiingo.com/test", "secret", 1)
    assert error.value.code is MarketProviderErrorCode.PROVIDER_UNAVAILABLE


class SequenceProvider:
    name = "TIINGO"

    def __init__(self, responses: list[FetchedMarketData]) -> None:
        self.responses = responses
        self.calls: list[tuple[date, date]] = []

    def fetch_eod(self, _instrument, start_date: date, end_date: date) -> FetchedMarketData:
        self.calls.append((start_date, end_date))
        return self.responses.pop(0)


def fetched(day: date, close: str, adjusted: str, *, split: str | None = None) -> FetchedMarketData:
    actions = ()
    if split is not None:
        from finance_terminal.market import FetchedCorporateAction
        actions = (FetchedCorporateAction(
            CorporateActionType.SPLIT, day, CorporateActionDateType.EX_DATE,
            split_ratio=Decimal(split),
        ),)
    return FetchedMarketData(
        (FetchedDailyBar(day, Decimal(close), adjusted_close=Decimal(adjusted)),),
        actions,
    )


def test_refresh_full_repeat_overlap_correction_and_action_idempotency(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "tiingo.db")
    store.upsert_company(Company("AAPL", "1", "Apple Inc."))
    try:
        store.seed_market_instruments("MARKETSTACK")
        store.upsert_corporate_action(CorporateAction(
            CorporateActionType.CASH_DIVIDEND,
            date(2024, 6, 2),
            CorporateActionDateType.PROVIDER_REPORTED,
            cash_amount_per_share=Decimal("0.17"),
            currency="USD",
            instrument_id="us-xnas-aapl",
            source_provider="MARKETSTACK",
            source_symbol="AAPL",
            retrieved_at="legacy",
        ))
        first = fetched(date(2024, 6, 3), "100", "50", split="4")
        repeat = fetched(date(2024, 6, 3), "100", "50", split="4")
        corrected = fetched(date(2024, 6, 3), "101", "50.5", split="4")
        provider = SequenceProvider([first, repeat, corrected])
        assert refresh_market(store, ["AAPL"], provider, start_date=date(2024, 6, 1), end_date=date(2024, 6, 5), full=True)["AAPL"].startswith("COMPLETED")
        assert refresh_market(store, ["AAPL"], provider, start_date=date(2024, 6, 1), end_date=date(2024, 6, 5), full=True)["AAPL"].startswith("COMPLETED")
        assert refresh_market(store, ["AAPL"], provider, start_date=date(2024, 6, 1), end_date=date(2024, 6, 5), full=True)["AAPL"].startswith("COMPLETED")
        stored = store.market_observations("us-xnas-aapl")
        assert len(stored) == 1 and stored[0].close == Decimal("101")
        assert stored[0].adjusted_close == Decimal("50.5") and stored[0].source_provider == "TIINGO"
        actions = store.corporate_actions("us-xnas-aapl")
        assert len(actions) == 1 and actions[0].split_ratio == Decimal("4")
        assert actions[0].source_provider == "TIINGO"
        assert len(provider.calls) == 3
    finally:
        store.close()


def test_incremental_refresh_uses_ten_day_overlap_without_deleting_history(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "incremental.db")
    store.upsert_company(Company("AAPL", "1", "Apple Inc."))
    provider = SequenceProvider([
        fetched(date(2024, 6, 20), "100", "100"),
        fetched(date(2024, 6, 28), "105", "105"),
    ])
    try:
        refresh_market(store, ["AAPL"], provider, start_date=date(2024, 6, 1), end_date=date(2024, 6, 20), full=True)
        refresh_market(store, ["AAPL"], provider, end_date=date(2024, 6, 30))
        assert provider.calls[1] == (date(2024, 6, 10), date(2024, 6, 30))
        assert [item.trading_date for item in store.market_observations("us-xnas-aapl")] == [date(2024, 6, 20), date(2024, 6, 28)]
    finally:
        store.close()


def test_full_refresh_removes_provider_action_missing_from_authoritative_response(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "stale-action.db")
    store.upsert_company(Company("AAPL", "1", "Apple Inc."))
    provider = SequenceProvider([
        fetched(date(2024, 6, 3), "100", "50", split="4"),
        fetched(date(2024, 6, 3), "100", "100"),
    ])
    try:
        refresh_market(store, ["AAPL"], provider, start_date=date(2024, 6, 1), end_date=date(2024, 6, 5), full=True)
        assert len(store.corporate_actions("us-xnas-aapl")) == 1
        refresh_market(store, ["AAPL"], provider, start_date=date(2024, 6, 1), end_date=date(2024, 6, 5), full=True)
        assert store.corporate_actions("us-xnas-aapl") == []
    finally:
        store.close()


def test_missing_token_is_controlled_and_app_starts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TIINGO_API_TOKEN", raising=False)
    store = SQLiteStore(tmp_path / "no-token.db")
    try:
        result = refresh_market(store, ["AAPL"])
        assert result == {
            "AAPL": "FAILED [not_connected]: Tiingo market data is not connected. Set TIINGO_API_TOKEN to refresh market data."
        }
        with TestClient(create_app(store, provider=FakeAIProvider())) as client:
            catalog = client.get("/api/v1/catalog").json()
        assert catalog["market_provider"] == "TIINGO"
        assert catalog["provider_connected"] is False
        assert catalog["market_connection_state"] == "NOT_CONNECTED"
    finally:
        store.close()


def test_tiingo_evidence_resolves_locally_without_provider() -> None:
    store = SQLiteStore(":memory:")
    try:
        store.upsert_company(Company("AAPL", "1", "Apple Inc."))
        store.seed_market_instruments("TIINGO")
        from finance_terminal.market import MarketDailyObservation
        store.upsert_market_observation(MarketDailyObservation(
            trading_date=date(2024, 6, 3), close=Decimal("101"), adjusted_close=Decimal("50"),
            instrument_id="us-xnas-aapl", source_provider="TIINGO", source_symbol="AAPL",
            retrieved_at="2026-09-03T00:00:00+00:00",
        ))
        resolved = QueryEngine(store).resolve_evidence("market:us-xnas-aapl:2024-06-03:adjusted_close")
        assert resolved is not None
        assert resolved.provider == "TIINGO" and resolved.market_field == "adjusted_close"
        assert resolved.value == Decimal("50") and resolved.retrieved_at == "2026-09-03T00:00:00+00:00"
    finally:
        store.close()


def test_tiingo_maps_both_alphabet_share_classes_without_merging(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "alphabet.db")
    try:
        store.upsert_company(Company("GOOGL", "1", "Alphabet Inc."))
        store.seed_market_instruments("TIINGO")
        primary = store.provider_market_instrument("us-xnas-googl", "TIINGO")
        alternate = store.provider_market_instrument("us-xnas-goog", "TIINGO")
        assert primary is not None and primary.provider_symbol == "GOOGL"
        assert alternate is not None and alternate.provider_symbol == "GOOG"
        assert primary.instrument_id != alternate.instrument_id
    finally:
        store.close()


def test_four_company_full_refresh_budgets_one_eod_request_per_ticker(tmp_path: Path) -> None:
    provider = SequenceProvider([FetchedMarketData((), ()) for _ in range(4)])
    store = SQLiteStore(tmp_path / "request-budget.db")
    try:
        result = refresh_market(
            store, ["AAPL", "MSFT", "NVDA", "GOOGL"], provider,
            end_date=date(2026, 9, 3), full=True,
        )
        assert all(status.startswith("COMPLETED") for status in result.values())
        assert len(provider.calls) == 4
        assert {start for start, _ in provider.calls} == {date(2016, 9, 3)}
    finally:
        store.close()
