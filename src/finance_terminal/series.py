"""Provenance-aware series points shared by calculations and queries."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .metrics import DerivedMetricCode, MetricCode
from .models import FinancialObservation, FiscalPeriod


def observation_id(observation: FinancialObservation) -> str:
    """Return a stable identity for one canonical observation."""
    fields = (
        observation.ticker,
        observation.cik,
        observation.metric.value,
        observation.period_start.isoformat() if observation.period_start else "",
        observation.period_end.isoformat(),
        str(observation.fiscal_year),
        observation.fiscal_period.value,
    )
    digest = hashlib.sha256("\x1f".join(fields).encode()).hexdigest()[:24]
    return f"sec:{digest}"


@dataclass(frozen=True, slots=True)
class SourceSeriesPoint:
    date: date
    value: Decimal
    unit: str
    observation_id: str
    fiscal_year: int | None = None
    fiscal_period: FiscalPeriod | None = None
    point_type: str = "SOURCE"


@dataclass(frozen=True, slots=True)
class CalculatedSeriesPoint:
    date: date
    value: Decimal
    unit: str
    formula: str
    input_observation_ids: tuple[str, ...]
    fiscal_year: int | None = None
    fiscal_period: FiscalPeriod | None = None
    point_type: str = "CALCULATED"


SeriesPoint = SourceSeriesPoint | CalculatedSeriesPoint
CompanyMetricCode = MetricCode | DerivedMetricCode
