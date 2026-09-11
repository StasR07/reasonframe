"""Small curated macro catalog and a replaceable FRED provider boundary."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import urlopen


@dataclass(frozen=True, slots=True)
class MacroSeriesDefinition:
    code: str
    provider: str
    provider_series_id: str
    label: str
    unit: str
    frequency: str
    source_name: str = "Federal Reserve Bank of St. Louis"


@dataclass(frozen=True, slots=True)
class MacroObservation:
    series_code: str
    date: date
    value: Decimal


@dataclass(frozen=True, slots=True)
class FetchedMacroSeries:
    provider_series_id: str
    label: str
    unit: str
    frequency: str
    observations: tuple[tuple[date, Decimal], ...]


class MacroProvider(Protocol):
    def fetch_series(
        self, series_id: str, start_date: date | None = None, end_date: date | None = None
    ) -> FetchedMacroSeries: ...


MACRO_CATALOG: dict[str, MacroSeriesDefinition] = {
    item.code: item
    for item in (
        MacroSeriesDefinition("US_HEADLINE_CPI", "FRED", "CPIAUCSL", "US headline CPI", "index 1982-1984=100", "monthly"),
        MacroSeriesDefinition("US_CORE_CPI", "FRED", "CPILFESL", "US core CPI", "index 1982-1984=100", "monthly"),
        MacroSeriesDefinition("US_PCE_PRICE_INDEX", "FRED", "PCEPI", "US PCE price index", "index 2017=100", "monthly"),
        MacroSeriesDefinition("US_CORE_PCE", "FRED", "PCEPILFE", "US core PCE price index", "index 2017=100", "monthly"),
        MacroSeriesDefinition("US_UNEMPLOYMENT_RATE", "FRED", "UNRATE", "US unemployment rate", "percent", "monthly"),
        MacroSeriesDefinition("US_NONFARM_PAYROLLS", "FRED", "PAYEMS", "US nonfarm payrolls", "thousands of persons", "monthly"),
        MacroSeriesDefinition("US_FEDERAL_FUNDS_RATE", "FRED", "FEDFUNDS", "Federal funds effective rate", "percent", "monthly"),
        MacroSeriesDefinition("US_10Y_TREASURY_YIELD", "FRED", "DGS10", "10-year Treasury yield", "percent", "daily"),
        MacroSeriesDefinition("US_NOMINAL_GDP", "FRED", "GDP", "US nominal GDP", "billions of dollars SAAR", "quarterly"),
        MacroSeriesDefinition("US_REAL_GDP", "FRED", "GDPC1", "US real GDP", "billions of chained 2017 dollars SAAR", "quarterly"),
        MacroSeriesDefinition("US_INDUSTRIAL_PRODUCTION", "FRED", "INDPRO", "US industrial production", "index 2017=100", "monthly"),
        MacroSeriesDefinition("US_HOUSING_STARTS", "FRED", "HOUST", "US housing starts", "thousands of units SAAR", "monthly"),
    )
}

SEMANTIC_MACRO_SERIES: dict[str, tuple[str, str]] = {
    "US_INFLATION_YOY": ("US_HEADLINE_CPI", "12-month percentage change in headline CPI"),
    "US_CORE_INFLATION_YOY": ("US_CORE_CPI", "12-month percentage change in core CPI"),
}


class FredProvider:
    """Minimal FRED implementation; credentials are only needed when called."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("FRED_API_KEY")

    def fetch_series(
        self, series_id: str, start_date: date | None = None, end_date: date | None = None
    ) -> FetchedMacroSeries:
        if not self.api_key:
            raise RuntimeError("FRED_API_KEY is not configured")
        common = {"series_id": series_id, "api_key": self.api_key, "file_type": "json"}
        metadata = self._get("https://api.stlouisfed.org/fred/series", common)["seriess"][0]
        params = dict(common)
        if start_date:
            params["observation_start"] = start_date.isoformat()
        if end_date:
            params["observation_end"] = end_date.isoformat()
        payload = self._get("https://api.stlouisfed.org/fred/series/observations", params)
        observations: list[tuple[date, Decimal]] = []
        for item in payload["observations"]:
            try:
                value = Decimal(item["value"])
            except (InvalidOperation, KeyError):
                continue
            observations.append((date.fromisoformat(item["date"]), value))
        return FetchedMacroSeries(
            provider_series_id=series_id,
            label=metadata["title"],
            unit=metadata["units"],
            frequency=metadata["frequency"].lower(),
            observations=tuple(observations),
        )

    @staticmethod
    def _get(url: str, params: dict[str, str]) -> dict[str, object]:
        with urlopen(f"{url}?{urlencode(params)}", timeout=30) as response:  # noqa: S310
            return json.loads(response.read())


def calculate_yoy(
    observations: list[MacroObservation], target_code: str
) -> list[MacroObservation]:
    """Calculate exact 12-month percentage changes from monthly index levels."""
    indexed = {item.date: item for item in observations}
    result: list[MacroObservation] = []
    for current in sorted(observations, key=lambda item: item.date):
        try:
            prior_date = current.date.replace(year=current.date.year - 1)
        except ValueError:
            continue
        prior = indexed.get(prior_date)
        if prior is None or prior.value == 0:
            continue
        result.append(
            MacroObservation(
                series_code=target_code,
                date=current.date,
                value=(current.value / prior.value - Decimal(1)) * Decimal(100),
            )
        )
    return result

