"""Tiingo EOD adapter: one request yields canonical bars and corporate actions."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .market import (
    CorporateActionDateType,
    CorporateActionType,
    FetchedCorporateAction,
    FetchedDailyBar,
    FetchedMarketData,
    MarketProviderError,
    MarketProviderErrorCode,
    ProviderMarketInstrument,
)


Transport = Callable[[str, str, float], object]


def _provider_error(status: int) -> MarketProviderError:
    if status in (401, 403):
        return MarketProviderError(
            MarketProviderErrorCode.AUTHENTICATION_ERROR,
            "Tiingo rejected the configured API token.",
        )
    if status == 404:
        return MarketProviderError(
            MarketProviderErrorCode.INSTRUMENT_NOT_FOUND,
            "Tiingo does not have this market instrument.",
        )
    if status == 429:
        return MarketProviderError(
            MarketProviderErrorCode.RATE_LIMITED,
            "Tiingo request limit reached. Try the refresh again later.",
        )
    return MarketProviderError(
        MarketProviderErrorCode.PROVIDER_UNAVAILABLE,
        "Tiingo market data is temporarily unavailable.",
    )


def _default_transport(url: str, token: str, timeout: float) -> object:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Token {token}",
            "User-Agent": "Reasonframe/0.1 (+local-BYOK)",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS base URL
            return json.load(response)
    except HTTPError as exc:
        raise _provider_error(exc.code) from exc
    except (URLError, TimeoutError) as exc:
        raise MarketProviderError(
            MarketProviderErrorCode.PROVIDER_UNAVAILABLE,
            "Tiingo market data is temporarily unavailable.",
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MarketProviderError(
            MarketProviderErrorCode.MALFORMED_RESPONSE,
            "Tiingo returned an unreadable response.",
        ) from exc


def _decimal(value: object, *, field: str, required: bool = False) -> Decimal | None:
    if value is None or value == "":
        if required:
            raise MarketProviderError(
                MarketProviderErrorCode.MALFORMED_RESPONSE,
                f"Tiingo response is missing {field}.",
            )
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarketProviderError(
            MarketProviderErrorCode.MALFORMED_RESPONSE,
            f"Tiingo response contains an invalid {field}.",
        ) from exc
    if not parsed.is_finite():
        raise MarketProviderError(
            MarketProviderErrorCode.MALFORMED_RESPONSE,
            f"Tiingo response contains an invalid {field}.",
        )
    return parsed


def _date(value: object) -> date:
    if not isinstance(value, str):
        raise MarketProviderError(
            MarketProviderErrorCode.MALFORMED_RESPONSE,
            "Tiingo response is missing the trading date.",
        )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise MarketProviderError(
            MarketProviderErrorCode.MALFORMED_RESPONSE,
            "Tiingo response contains an invalid trading date.",
        ) from exc


@dataclass(frozen=True, slots=True)
class _TiingoEodRow:
    """Provider-shaped data kept strictly inside the Tiingo boundary."""

    trading_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: Decimal | None
    adj_open: Decimal | None
    adj_high: Decimal | None
    adj_low: Decimal | None
    adj_close: Decimal | None
    adj_volume: Decimal | None
    div_cash: Decimal
    split_factor: Decimal

    @classmethod
    def parse(cls, payload: dict[str, object]) -> "_TiingoEodRow":
        div_cash = _decimal(payload.get("divCash", 0), field="divCash")
        split_factor = _decimal(payload.get("splitFactor", 1), field="splitFactor")
        return cls(
            trading_date=_date(payload.get("date")),
            open=_decimal(payload.get("open"), field="open"),
            high=_decimal(payload.get("high"), field="high"),
            low=_decimal(payload.get("low"), field="low"),
            close=_decimal(payload.get("close"), field="close", required=True),  # type: ignore[arg-type]
            volume=_decimal(payload.get("volume"), field="volume"),
            adj_open=_decimal(payload.get("adjOpen"), field="adjOpen"),
            adj_high=_decimal(payload.get("adjHigh"), field="adjHigh"),
            adj_low=_decimal(payload.get("adjLow"), field="adjLow"),
            adj_close=_decimal(payload.get("adjClose"), field="adjClose"),
            adj_volume=_decimal(payload.get("adjVolume"), field="adjVolume"),
            div_cash=div_cash if div_cash is not None else Decimal(0),
            split_factor=split_factor if split_factor is not None else Decimal(1),
        )


class TiingoProvider:
    name = "TIINGO"

    def __init__(
        self,
        api_token: str,
        *,
        base_url: str = "https://api.tiingo.com/tiingo/daily",
        timeout: float = 15.0,
        transport: Transport | None = None,
    ) -> None:
        if not api_token.strip():
            raise MarketProviderError(
                MarketProviderErrorCode.NOT_CONNECTED,
                "Tiingo market data is not connected. Set TIINGO_API_TOKEN to refresh market data.",
            )
        self._api_token = api_token.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport or _default_transport
        self.request_count = 0

    @classmethod
    def from_environment(cls) -> "TiingoProvider":
        return cls(os.environ.get("TIINGO_API_TOKEN", ""))

    def fetch_eod(
        self,
        instrument: ProviderMarketInstrument,
        start_date: date,
        end_date: date,
    ) -> FetchedMarketData:
        if start_date > end_date:
            raise ValueError("market start date must not be after end date")
        symbol = quote(instrument.provider_symbol, safe="-")
        query = urlencode({
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "format": "json",
        })
        self.request_count += 1
        payload = self.transport(f"{self.base_url}/{symbol}/prices?{query}", self._api_token, self.timeout)
        if not isinstance(payload, list):
            raise MarketProviderError(
                MarketProviderErrorCode.MALFORMED_RESPONSE,
                "Tiingo returned an unexpected EOD response.",
            )

        parsed: dict[date, _TiingoEodRow] = {}
        for raw in payload:
            if not isinstance(raw, dict):
                raise MarketProviderError(
                    MarketProviderErrorCode.MALFORMED_RESPONSE,
                    "Tiingo returned an unexpected EOD row.",
                )
            row = _TiingoEodRow.parse(raw)
            parsed[row.trading_date] = row

        bars: list[FetchedDailyBar] = []
        actions: list[FetchedCorporateAction] = []
        for trading_date in sorted(parsed):
            row = parsed[trading_date]
            bars.append(FetchedDailyBar(
                trading_date=row.trading_date,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
                adjusted_open=row.adj_open,
                adjusted_high=row.adj_high,
                adjusted_low=row.adj_low,
                adjusted_close=row.adj_close,
                adjusted_volume=row.adj_volume,
            ))
            if row.split_factor <= 0:
                raise MarketProviderError(
                    MarketProviderErrorCode.MALFORMED_RESPONSE,
                    "Tiingo response contains an invalid splitFactor.",
                )
            if row.split_factor != 1:
                # Tiingo documents splitFactor as splitTo/splitFrom, exactly the
                # canonical new_shares_per_old_share convention.
                actions.append(FetchedCorporateAction(
                    action_type=CorporateActionType.SPLIT,
                    event_date=row.trading_date,
                    date_type=CorporateActionDateType.EX_DATE,
                    split_ratio=row.split_factor,
                ))
            if row.div_cash < 0:
                raise MarketProviderError(
                    MarketProviderErrorCode.MALFORMED_RESPONSE,
                    "Tiingo response contains an invalid divCash.",
                )
            if row.div_cash != 0:
                actions.append(FetchedCorporateAction(
                    action_type=CorporateActionType.CASH_DIVIDEND,
                    event_date=row.trading_date,
                    date_type=CorporateActionDateType.EX_DATE,
                    cash_amount_per_share=row.div_cash,
                    currency="USD",
                ))
        return FetchedMarketData(tuple(bars), tuple(actions))
