"""Provider-independent EOD market models and Decimal-safe calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class MarketProviderErrorCode(StrEnum):
    NOT_CONNECTED = "not_connected"
    AUTHENTICATION_ERROR = "authentication_error"
    RATE_LIMITED = "rate_limited"
    INSTRUMENT_NOT_FOUND = "instrument_not_found"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MALFORMED_RESPONSE = "malformed_response"


class MarketConnectionState(StrEnum):
    CONNECTED = "CONNECTED"
    NOT_CONNECTED = "NOT_CONNECTED"
    INVALID_TOKEN = "INVALID_TOKEN"
    RATE_LIMITED = "RATE_LIMITED"


class MarketProviderError(RuntimeError):
    """Provider-neutral failure safe to translate into product state."""

    def __init__(self, code: MarketProviderErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class ValuationSupport(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"


class CorporateActionType(StrEnum):
    SPLIT = "SPLIT"
    CASH_DIVIDEND = "CASH_DIVIDEND"


class CorporateActionDateType(StrEnum):
    EFFECTIVE_DATE = "EFFECTIVE_DATE"
    EX_DATE = "EX_DATE"
    PROVIDER_REPORTED = "PROVIDER_REPORTED"


@dataclass(frozen=True, slots=True)
class MarketInstrument:
    instrument_id: str
    company_ticker: str
    symbol: str
    exchange_mic: str
    currency: str
    security_type: str = "COMMON_STOCK"
    share_class: str | None = None
    is_primary: bool = True
    valuation_status: ValuationSupport = ValuationSupport.SUPPORTED


@dataclass(frozen=True, slots=True)
class ProviderMarketInstrument:
    instrument_id: str
    provider: str
    provider_symbol: str
    provider_exchange: str | None = None
    provider_instrument_id: str | None = None


@dataclass(frozen=True, slots=True)
class FetchedDailyBar:
    trading_date: date
    close: Decimal
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    volume: Decimal | None = None
    adjusted_open: Decimal | None = None
    adjusted_high: Decimal | None = None
    adjusted_low: Decimal | None = None
    adjusted_close: Decimal | None = None
    adjusted_volume: Decimal | None = None


@dataclass(frozen=True, slots=True)
class MarketDailyObservation(FetchedDailyBar):
    instrument_id: str = ""
    currency: str = "USD"
    source_provider: str = ""
    source_symbol: str = ""
    retrieved_at: str = ""
    ingestion_run_id: int | None = None


@dataclass(frozen=True, slots=True)
class MaximumDrawdownResult:
    value: Decimal
    peak_date: date
    trough_date: date
    peak_observation_id: str
    trough_observation_id: str


@dataclass(frozen=True, slots=True)
class FetchedCorporateAction:
    action_type: CorporateActionType
    event_date: date
    date_type: CorporateActionDateType
    split_ratio: Decimal | None = None
    cash_amount_per_share: Decimal | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class FetchedMarketData:
    """One provider response normalized into bars and action signals."""

    bars: tuple[FetchedDailyBar, ...]
    corporate_actions: tuple[FetchedCorporateAction, ...]


@dataclass(frozen=True, slots=True)
class CorporateAction(FetchedCorporateAction):
    instrument_id: str = ""
    source_provider: str = ""
    source_symbol: str = ""
    retrieved_at: str = ""
    ingestion_run_id: int | None = None


class MarketDataProvider(Protocol):
    name: str

    def fetch_eod(
        self, instrument: ProviderMarketInstrument, start_date: date, end_date: date,
    ) -> FetchedMarketData: ...


def market_observation_id(instrument_id: str, trading_date: date, field: str) -> str:
    return f"market:{instrument_id}:{trading_date.isoformat()}:{field}"


def market_action_id(action: CorporateAction) -> str:
    return f"market-action:{action.instrument_id}:{action.action_type.value}:{action.event_date.isoformat()}"


def trailing_return(
    observations: list[MarketDailyObservation], target_start: date,
) -> tuple[Decimal, MarketDailyObservation, MarketDailyObservation] | None:
    usable = sorted((item for item in observations if item.adjusted_close is not None), key=lambda item: item.trading_date)
    if not usable:
        return None
    end = usable[-1]
    anchors = [item for item in usable if item.trading_date <= target_start]
    if not anchors or anchors[-1].adjusted_close == 0:
        return None
    start = anchors[-1]
    return end.adjusted_close / start.adjusted_close - Decimal(1), start, end  # type: ignore[operator]


def indexed_to_100(
    grouped: dict[str, list[MarketDailyObservation]],
) -> dict[str, list[tuple[MarketDailyObservation, Decimal]]]:
    if not grouped:
        return {}
    available_dates = [
        {item.trading_date for item in items if item.adjusted_close is not None}
        for items in grouped.values()
    ]
    common = set.intersection(*available_dates) if available_dates else set()
    if not common:
        return {}
    common_start = min(common)
    result: dict[str, list[tuple[MarketDailyObservation, Decimal]]] = {}
    for key, items in grouped.items():
        usable = sorted(
            (item for item in items if item.adjusted_close is not None and item.trading_date >= common_start),
            key=lambda item: item.trading_date,
        )
        base = next(item.adjusted_close for item in usable if item.trading_date == common_start)
        if base == 0:
            return {}
        result[key] = [(item, Decimal(100) * item.adjusted_close / base) for item in usable]  # type: ignore[operator]
    return result


def maximum_drawdown(observations: list[MarketDailyObservation]) -> MaximumDrawdownResult | None:
    usable = sorted(
        (item for item in observations if item.adjusted_close is not None),
        key=lambda item: item.trading_date,
    )
    if not usable:
        return None
    peak = usable[0]
    worst_peak = peak
    worst_trough = peak
    worst = Decimal(0)
    for item in usable:
        if item.adjusted_close > peak.adjusted_close:  # type: ignore[operator]
            peak = item
        if peak.adjusted_close > 0:  # type: ignore[operator]
            drawdown = item.adjusted_close / peak.adjusted_close - Decimal(1)  # type: ignore[operator]
            if drawdown < worst:
                worst = drawdown
                worst_peak = peak
                worst_trough = item
    return MaximumDrawdownResult(
        value=worst,
        peak_date=worst_peak.trading_date,
        trough_date=worst_trough.trading_date,
        peak_observation_id=market_observation_id(
            worst_peak.instrument_id, worst_peak.trading_date, "adjusted_close"
        ),
        trough_observation_id=market_observation_id(
            worst_trough.instrument_id, worst_trough.trading_date, "adjusted_close"
        ),
    )
