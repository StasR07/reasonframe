"""Provider-independent financial observation models."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .metrics import MetricCode


class PeriodKind(StrEnum):
    INSTANT = "INSTANT"
    DURATION = "DURATION"


class FiscalPeriod(StrEnum):
    FY = "FY"
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"


class DurationScope(StrEnum):
    ANNUAL = "ANNUAL"
    STANDALONE_QUARTER = "STANDALONE_QUARTER"


class DerivationKind(StrEnum):
    DIRECT = "DIRECT"
    PERIOD_DERIVED = "PERIOD_DERIVED"
    CALCULATION_DERIVED = "CALCULATION_DERIVED"


@dataclass(frozen=True, slots=True)
class SourceFactIdentity:
    role: str
    source_concept: str
    value: Decimal
    unit: str
    period_start: date | None
    period_end: date
    accession_number: str
    filing_form: str
    filing_date: date | None


@dataclass(frozen=True, slots=True)
class FinancialObservation:
    ticker: str
    cik: str
    metric: "MetricCode"
    value: Decimal
    unit: str
    currency: str | None
    period_kind: PeriodKind
    period_start: date | None
    period_end: date
    fiscal_year: int
    fiscal_period: FiscalPeriod
    duration_scope: DurationScope | None
    derivation: DerivationKind
    source_concept: str
    accession_number: str
    filing_form: str
    filing_date: date | None
    quality_flags: tuple[str, ...] = ()
    derivation_sources: tuple[SourceFactIdentity, ...] = ()
