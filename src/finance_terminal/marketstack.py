"""Small Marketstack v2 adapter for EOD bars and corporate actions."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .market import (
    CorporateActionDateType,
    CorporateActionType,
    FetchedCorporateAction,
    FetchedDailyBar,
    FetchedMarketData,
    ProviderMarketInstrument,
)


class MarketstackError(RuntimeError):
    pass


Transport = Callable[[str, float], dict[str, object]]


def _default_transport(url: str, timeout: float) -> dict[str, object]:
    try:
        request = Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Reasonframe/0.1 (+local-development)",
        })
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS base URL
            payload = json.load(response)
    except HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("type") or "")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        suffix = f": {detail}" if detail else ""
        raise MarketstackError(f"Marketstack HTTP {exc.code}{suffix}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise MarketstackError("Marketstack request failed") from exc
    if not isinstance(payload, dict):
        raise MarketstackError("Marketstack returned an invalid payload")
    return payload


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarketstackError(f"invalid decimal value: {value!r}") from exc
    return parsed if parsed.is_finite() else None


def _date(value: object) -> date:
    if not isinstance(value, str):
        raise MarketstackError("missing market date")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise MarketstackError(f"invalid market date: {value}") from exc


class MarketstackProvider:
    """Deprecated migration-audit adapter; Tiingo is the active runtime provider."""
    name = "MARKETSTACK"

    def __init__(
        self, api_key: str, *, base_url: str = "https://api.marketstack.com/v2",
        timeout: float = 15.0, page_size: int = 1000, transport: Transport | None = None,
        include_provider_dividends: bool = False,
    ) -> None:
        if not api_key:
            raise ValueError("MARKETSTACK_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.page_size = page_size
        self.transport = transport or _default_transport
        # Dedicated cash-dividend events are not certified for Phase 5 core
        # calculations. Keep support available for explicit provider checks,
        # but quarantine them from normal refreshes by default.
        self.include_provider_dividends = include_provider_dividends

    def _pages(self, endpoint: str, params: dict[str, object]):
        offset = 0
        while True:
            query = urlencode({"access_key": self.api_key, "limit": self.page_size, "offset": offset, **params})
            payload = self.transport(f"{self.base_url}/{endpoint}?{query}", self.timeout)
            error = payload.get("error")
            if error:
                message = error.get("message") if isinstance(error, dict) else str(error)
                raise MarketstackError(f"Marketstack error: {message}")
            data = payload.get("data")
            if not isinstance(data, list):
                raise MarketstackError("Marketstack payload is missing data")
            yield data
            pagination = payload.get("pagination")
            if not isinstance(pagination, dict):
                break
            count = int(pagination.get("count", len(data)))
            total = int(pagination.get("total", count))
            offset = int(pagination.get("offset", offset)) + count
            if count == 0 or offset >= total:
                break

    @staticmethod
    def _validate_identity(row: dict[str, object], instrument: ProviderMarketInstrument) -> None:
        symbol = row.get("symbol") or row.get("ticker")
        if symbol and str(symbol).upper() != instrument.provider_symbol.upper():
            raise MarketstackError(f"symbol mismatch: expected {instrument.provider_symbol}, got {symbol}")
        expected_exchange = instrument.provider_exchange
        actual_exchange = row.get("exchange") or row.get("exchange_code") or row.get("mic")
        if expected_exchange and actual_exchange and str(actual_exchange).upper() != expected_exchange.upper():
            raise MarketstackError(f"exchange mismatch: expected {expected_exchange}, got {actual_exchange}")

    def fetch_daily_bars(
        self, instrument: ProviderMarketInstrument, start_date: date, end_date: date,
    ) -> tuple[FetchedDailyBar, ...]:
        result: dict[date, FetchedDailyBar] = {}
        params = {
            "symbols": instrument.provider_symbol,
            "date_from": start_date.isoformat(), "date_to": end_date.isoformat(),
            "sort": "ASC",
        }
        for page in self._pages("eod", params):
            for raw in page:
                if not isinstance(raw, dict):
                    continue
                self._validate_identity(raw, instrument)
                close = _decimal(raw.get("close"))
                if close is None:
                    continue
                item_date = _date(raw.get("date"))
                result[item_date] = FetchedDailyBar(
                    trading_date=item_date, close=close,
                    open=_decimal(raw.get("open")), high=_decimal(raw.get("high")), low=_decimal(raw.get("low")),
                    volume=_decimal(raw.get("volume")), adjusted_open=_decimal(raw.get("adj_open") or raw.get("adjusted_open")),
                    adjusted_high=_decimal(raw.get("adj_high") or raw.get("adjusted_high")),
                    adjusted_low=_decimal(raw.get("adj_low") or raw.get("adjusted_low")),
                    adjusted_close=_decimal(raw.get("adj_close") or raw.get("adjusted_close")),
                    adjusted_volume=_decimal(raw.get("adj_volume") or raw.get("adjusted_volume")),
                )
        return tuple(result[key] for key in sorted(result))

    def fetch_corporate_actions(
        self, instrument: ProviderMarketInstrument, start_date: date, end_date: date,
    ) -> tuple[FetchedCorporateAction, ...]:
        common = {
            "symbols": instrument.provider_symbol,
            "date_from": start_date.isoformat(), "date_to": end_date.isoformat(),
            "sort": "ASC",
        }
        result: list[FetchedCorporateAction] = []
        for page in self._pages("splits", common):
            for raw in page:
                if not isinstance(raw, dict):
                    continue
                self._validate_identity(raw, instrument)
                ratio = _decimal(raw.get("split_factor") or raw.get("ratio") or raw.get("split_ratio"))
                if ratio is None or ratio <= 0:
                    continue
                result.append(FetchedCorporateAction(
                    CorporateActionType.SPLIT, _date(raw.get("date")),
                    CorporateActionDateType.EFFECTIVE_DATE, split_ratio=ratio,
                ))
        if self.include_provider_dividends:
            for page in self._pages("dividends", common):
                for raw in page:
                    if not isinstance(raw, dict):
                        continue
                    self._validate_identity(raw, instrument)
                    amount = _decimal(raw.get("dividend") or raw.get("amount"))
                    if amount is None:
                        continue
                    result.append(FetchedCorporateAction(
                        CorporateActionType.CASH_DIVIDEND, _date(raw.get("date")),
                        CorporateActionDateType.PROVIDER_REPORTED,
                        cash_amount_per_share=amount, currency=str(raw.get("currency") or "USD"),
                    ))
        return tuple(sorted(result, key=lambda item: (item.event_date, item.action_type.value)))

    def fetch_eod(
        self, instrument: ProviderMarketInstrument, start_date: date, end_date: date,
    ) -> FetchedMarketData:
        return FetchedMarketData(
            self.fetch_daily_bars(instrument, start_date, end_date),
            self.fetch_corporate_actions(instrument, start_date, end_date),
        )
