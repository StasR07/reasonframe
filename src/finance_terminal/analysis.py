"""Deterministic analysis packets and bounded contextual AI interpretation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from time import monotonic
from decimal import Decimal
from enum import StrEnum
from datetime import date
from typing import AsyncIterator, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .ai import ResultShape, classify_result, codex_output_schema, display_value as format_observation, metric_label, period_label
from .ai_provider import AIDisconnectedError, AIProvider, AIUsageLimitError
from .metrics import DerivedMetricCode, MetricCode
from .query import (
    CompanyQuery, MacroQuery, MarketOperation, MarketPriceSeries, MarketQuery,
    QueryEngine, QueryRequest, RankingOperation, ResultStatus, SeriesPointResult, SeriesResult,
    ValuationQuery,
)
from .storage import SQLiteStore
from .valuation import VALUATION_LABELS, ValuationMetricCode

ANALYSIS_PACKET_VERSION = 3
DEFAULT_ANALYSIS_TIMEOUT_SECONDS = 180.0
logger = logging.getLogger(__name__)


class AnalysisGroundingError(ValueError):
    pass


class AnalysisMode(StrEnum):
    FOCUSED = "focused"
    COMPANY = "company"
    COMPARISON = "comparison"


class Stance(StrEnum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"


class Confidence(StrEnum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


class AnalystClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=500)
    evidence_refs: list[str] = Field(min_length=1)


class FocusedInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observations: list[AnalystClaim] = Field(min_length=1, max_length=3)
    caveat: str | None = Field(default=None, max_length=400)


class AnalystAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stance: Stance
    confidence: Confidence
    thesis: AnalystClaim
    supporting_claims: list[AnalystClaim] = Field(min_length=1, max_length=4)
    uncertainties: list[str] = Field(default_factory=list, max_length=4)


class SkepticAssessment(StrEnum):
    NO_MATERIAL_CHALLENGE = "NO_MATERIAL_CHALLENGE"
    QUALIFIED = "QUALIFIED"
    MATERIAL_CHALLENGE = "MATERIAL_CHALLENGE"


class SkepticTarget(StrEnum):
    FUNDAMENTALS = "FUNDAMENTALS"
    VALUATION = "VALUATION"
    BOTH = "BOTH"


class SkepticIssueType(StrEnum):
    COUNTEREVIDENCE = "COUNTEREVIDENCE"
    WEAK_SUPPORT = "WEAK_SUPPORT"
    OVERSTATEMENT = "OVERSTATEMENT"
    CONTRADICTION = "CONTRADICTION"
    CROSS_ANALYST_TENSION = "CROSS_ANALYST_TENSION"


class SkepticChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: SkepticTarget
    issue_type: SkepticIssueType
    text: str = Field(min_length=1, max_length=500)
    evidence_refs: list[str] = Field(min_length=1)


class SkepticReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment: SkepticAssessment
    challenges: list[SkepticChallenge] = Field(default_factory=list, max_length=4)
    missing_evidence: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def consistent_assessment(self) -> "SkepticReview":
        if self.assessment is SkepticAssessment.NO_MATERIAL_CHALLENGE and self.challenges:
            raise ValueError("NO_MATERIAL_CHALLENGE requires an empty challenges list")
        if self.assessment is not SkepticAssessment.NO_MATERIAL_CHALLENGE and not self.challenges:
            raise ValueError("a qualified or material assessment requires a supported challenge")
        return self


class OverviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stance: Stance
    confidence: Confidence
    synthesis: AnalystClaim
    key_conclusions: list[AnalystClaim] = Field(min_length=2, max_length=4)
    key_risks: list[AnalystClaim] = Field(default_factory=list, max_length=4)
    uncertainties: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_overview(cls, value: object) -> object:
        if isinstance(value, dict) and "synthesis" not in value and value.get("conclusion"):
            conclusions = list(value.get("strongest_evidence") or [])
            if value.get("summary"):
                conclusions.insert(0, value["summary"])
            if value.get("key_tension"):
                conclusions.append(value["key_tension"])
            risks = [item for item in [value.get("skeptic_challenge")] if item]
            return {"stance": value.get("stance"), "confidence": value.get("confidence"), "synthesis": value["conclusion"], "key_conclusions": conclusions[:4], "key_risks": risks, "uncertainties": value.get("uncertainties") or []}
        return value


class DeterministicStatistic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    value: str | None = None
    display_value: str
    state: Literal["AVAILABLE", "NOT_MEANINGFUL"] = "AVAILABLE"
    formula: str | None = None
    period: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_scope: Literal["references", "full_series"] = "references"


class MetricClass(StrEnum):
    MONETARY_LEVEL = "MONETARY_LEVEL"
    COUNT_OR_LEVEL = "COUNT_OR_LEVEL"
    RATE_OR_MARGIN = "RATE_OR_MARGIN"
    RATIO = "RATIO"
    GROWTH_RATE = "GROWTH_RATE"


class PeriodChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_period: str
    to_period: str
    absolute_change: str | None = None
    percentage_change: str | None = None
    percentage_point_change: str | None = None
    display_value: str
    direction: Literal["up", "down", "flat"]
    evidence_refs: list[str]


class MetricAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str | None
    metric: str
    label: str
    unit: str
    frequency: str
    metric_class: MetricClass
    series_evidence_refs: list[str]
    observations: list[dict[str, object]]
    statistics: list[DeterministicStatistic]
    period_changes: list[PeriodChange]


class GroundedPacketBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[3] = ANALYSIS_PACKET_VERSION
    mode: AnalysisMode
    context: dict[str, object]
    caveats: list[str] = Field(default_factory=list)
    evidence_ids: list[str]
    evidence_fingerprint: str


class FundamentalsContextPacket(GroundedPacketBase):
    metrics: list[MetricAnalysis]


class FocusedContextPacket(GroundedPacketBase):
    metrics: list[MetricAnalysis]


class ComparisonContextPacket(GroundedPacketBase):
    metrics: list[MetricAnalysis]


class ComparisonAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confidence: Confidence = Confidence.MODERATE
    summary: AnalystClaim
    fundamentals_comparison: list[AnalystClaim] = Field(default_factory=list, max_length=4)
    valuation_comparison: list[AnalystClaim] = Field(default_factory=list, max_length=4)
    market_context: list[AnalystClaim] = Field(default_factory=list, max_length=3)
    key_tradeoffs: list[AnalystClaim] = Field(default_factory=list, max_length=4)
    limitations: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_observations(cls, value: object) -> object:
        if isinstance(value, dict) and "summary" not in value and value.get("observations"):
            observations = value["observations"]
            return {
                "summary": observations[0],
                "fundamentals_comparison": observations[1:],
                "limitations": [value["caveat"]] if value.get("caveat") else [],
            }
        return value


class ValuationContextPacket(GroundedPacketBase):
    latest_market_fact: dict[str, object] | None = None
    market_context: list[dict[str, object]] = Field(default_factory=list)
    current_valuation: list[dict[str, object]] = Field(default_factory=list)
    historical_valuation: list[dict[str, object]] = Field(default_factory=list)
    macro_context: list[dict[str, object]] = Field(default_factory=list)


class CompanyReviewPacket(GroundedPacketBase):
    fundamentals: dict[str, object]
    valuation: dict[str, object]


# Backwards-compatible import name for deterministic statistic helpers.
AnalysisPacket = FocusedContextPacket


class FocusedAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["focused"] = "focused"
    query: QueryRequest


class CompanyAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["company"] = "company"
    ticker: str = Field(min_length=1, max_length=12)
    start_year: int | None = Field(default=2019, ge=2019)
    end_year: int | None = Field(default=None, ge=2019)

    @model_validator(mode="after")
    def normalize(self) -> "CompanyAnalysisRequest":
        self.ticker = self.ticker.strip().upper()
        if not re.fullmatch(r"[A-Z0-9.-]+", self.ticker):
            raise ValueError("ticker contains unsupported characters")
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("start_year must not be after end_year")
        return self


class ComparisonAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["comparison"] = "comparison"
    tickers: list[str] = Field(default_factory=list, max_length=4)
    queries: list[QueryRequest] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def normalize(self) -> "ComparisonAnalysisRequest":
        if self.tickers:
            self.tickers = list(dict.fromkeys(item.strip().upper() for item in self.tickers if item.strip()))
            if not 2 <= len(self.tickers) <= 4:
                raise ValueError("comparison requires 2-4 unique tickers")
            return self
        if not self.queries:
            raise ValueError("comparison requires tickers")
        domains = {query.domain for query in self.queries}
        if len(domains) != 1:
            raise ValueError("comparison queries must use one deterministic domain")
        ticker_sets = [tuple(query.tickers) for query in self.queries if hasattr(query, "tickers")]
        if not ticker_sets or any(items != ticker_sets[0] for items in ticker_sets):
            raise ValueError("comparison queries must use the same ticker set")
        if not 2 <= len(set(ticker_sets[0])) <= 4:
            raise ValueError("comparison requires 2-4 unique tickers")
        if domains == {"company"} and len(self.queries) != 1:
            raise ValueError("fundamentals comparison uses one company query")
        if domains == {"market"} and (len(self.queries) != 1 or self.queries[0].operation is not MarketOperation.INDEXED):
            raise ValueError("performance comparison uses one indexed market query")
        if domains == {"valuation"}:
            metrics = {query.metric for query in self.queries}
            if len(metrics) != len(self.queries):
                raise ValueError("valuation comparison metrics must be unique")
        return self


class AnalysisError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["error"] = "error"
    error_code: Literal["AI_DISCONNECTED", "AI_UNAVAILABLE", "AI_USAGE_LIMIT", "AI_TIMEOUT", "MODEL_OUTPUT_INVALID", "DATA_UNAVAILABLE"]
    message: str


class FocusedAnalysisSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["success"] = "success"
    mode: Literal[AnalysisMode.FOCUSED] = AnalysisMode.FOCUSED
    packet_version: int
    cache_key: str
    interpretation: FocusedInterpretation
    evidence_context: list[SeriesResult]


class ComparisonAnalysisSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["success"] = "success"
    mode: Literal[AnalysisMode.COMPARISON] = AnalysisMode.COMPARISON
    packet_version: int
    cache_key: str
    interpretation: ComparisonAnalysis
    evidence_context: list[SeriesResult]


class CompanyAnalysisSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["success"] = "success"
    mode: Literal[AnalysisMode.COMPANY] = AnalysisMode.COMPANY
    packet_version: int
    cache_key: str
    ticker: str
    fundamentals: AnalystAssessment | None = None
    valuation: AnalystAssessment | None = None
    skeptic: SkepticReview | None = None
    overview: OverviewOutput | None = None
    failures: list[str] = Field(default_factory=list)
    failure_details: dict[str, str] = Field(default_factory=dict)
    evidence_context: list[SeriesResult]


AnalysisResponse = FocusedAnalysisSuccess | ComparisonAnalysisSuccess | CompanyAnalysisSuccess | AnalysisError


class CompanyAnalysisEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: Literal[
        "analysis_started", "stage_started", "stage_completed", "stage_failed",
        "stage_skipped", "analysis_completed", "analysis_failed",
    ]
    stage: Literal["fundamentals", "valuation", "skeptic", "overview"] | None = None
    result: AnalystAssessment | SkepticReview | OverviewOutput | CompanyAnalysisSuccess | AnalysisError | None = None
    message: str | None = None
    evidence_context: list[SeriesResult] = Field(default_factory=list)


RATE_METRICS = {
    "GROSS_MARGIN", "OPERATING_MARGIN", "NET_MARGIN", "FCF_MARGIN",
}
GROWTH_METRICS = {"REVENUE_GROWTH_YOY"}
RATIO_METRICS = {"CURRENT_RATIO"}
COUNT_METRICS = {"DILUTED_WEIGHTED_AVERAGE_SHARES"}


def _refs(point) -> list[str]:
    return list(dict.fromkeys(
        [item.id for item in point.evidence]
        + ([point.observation_id] if point.observation_id else [])
        + list(point.input_observation_ids)
    ))


def metric_class_for(series: SeriesResult) -> MetricClass:
    metric, unit = series.metric.upper(), series.unit.casefold()
    if metric in GROWTH_METRICS or "GROWTH" in metric:
        return MetricClass.GROWTH_RATE
    if metric in RATE_METRICS or "MARGIN" in metric or unit == "percent":
        return MetricClass.RATE_OR_MARGIN
    if metric in RATIO_METRICS:
        return MetricClass.RATIO
    if metric in COUNT_METRICS or unit in {"shares", "count", "units"}:
        return MetricClass.COUNT_OR_LEVEL
    return MetricClass.MONETARY_LEVEL


def _signed_display(value: Decimal, unit: str, metric: str) -> str:
    rendered = format_observation(abs(value), unit, metric)
    return ("+" if value > 0 else "-" if value < 0 else "") + rendered


def _stat_display(name: str, value: object | None, series: SeriesResult | None = None, *, not_meaningful: bool = False) -> str:
    if value is None:
        return "Not meaningful" if not_meaningful else "Unavailable"
    if name in {"observation_count", "positive_changes", "negative_changes", "unchanged_periods", "positive_periods", "negative_periods", "years_elapsed"}:
        return str(value)
    if name in {"recent_direction", "rate_momentum"}:
        return str(value)
    decimal = Decimal(str(value))
    if name in {"percentage_change", "cagr_percent"}:
        return f"{decimal:+.1f}%"
    if name == "change_multiple":
        return f"{decimal:.1f}×"
    if name == "percentage_point_change":
        return f"{decimal:+.1f} pp"
    if name in {"average_rate", "latest_rate", "peak_rate", "trough_rate"}:
        return f"{decimal:.1f}%"
    if series is None:
        return str(value)
    if name in {"absolute_change", "range", "standard_deviation_financial_metric"}:
        return _signed_display(decimal, series.unit, series.metric) if name == "absolute_change" else format_observation(decimal, series.unit, series.metric)
    return format_observation(decimal, series.unit, series.metric)


def _stat(name: str, value: object | None, refs: list[str] | None = None, period: str | None = None, series: SeriesResult | None = None, *, full_series: bool = False, not_meaningful: bool = False, formula: str | None = None) -> DeterministicStatistic:
    rendered = None if value is None else str(value)
    return DeterministicStatistic(
        name=name, value=rendered,
        display_value=_stat_display(name, value, series, not_meaningful=not_meaningful),
        state="NOT_MEANINGFUL" if not_meaningful else "AVAILABLE", formula=formula,
        period=period, evidence_refs=refs or [], evidence_scope="full_series" if full_series else "references",
    )


def period_changes_for_series(series: SeriesResult) -> list[PeriodChange]:
    points = sorted(series.observations, key=lambda item: item.date)
    kind = metric_class_for(series)
    changes: list[PeriodChange] = []
    for left, right in zip(points, points[1:]):
        change = right.value - left.value
        refs = list(dict.fromkeys(_refs(left) + _refs(right)))
        common = dict(
            from_period=period_label(left), to_period=period_label(right),
            direction="up" if change > 0 else "down" if change < 0 else "flat", evidence_refs=refs,
        )
        if kind in {MetricClass.RATE_OR_MARGIN, MetricClass.GROWTH_RATE}:
            multiplier = Decimal(100) if series.unit.casefold() == "ratio" else Decimal(1)
            points_change = change * multiplier
            changes.append(PeriodChange(percentage_point_change=str(points_change), display_value=f"{points_change:+.1f} pp", **common))
        elif kind is MetricClass.RATIO:
            changes.append(PeriodChange(absolute_change=str(change), display_value=_signed_display(change, series.unit, series.metric), **common))
        else:
            percentage = None if left.value == 0 else (right.value / left.value - 1) * 100
            display = _signed_display(change, series.unit, series.metric)
            if percentage is not None:
                display += f" ({percentage:+.1f}%)"
            changes.append(PeriodChange(absolute_change=str(change), percentage_change=None if percentage is None else str(percentage), display_value=display, **common))
    return changes


def statistics_for_series(series: SeriesResult) -> list[DeterministicStatistic]:
    """Calculate descriptors only; the model is forbidden from recalculating them."""
    points = sorted(series.observations, key=lambda item: item.date)
    if not points:
        return [_stat("observation_count", 0, series=series)]
    first, last = points[0], points[-1]
    pairs = list(zip(points, points[1:]))
    changes = [right.value - left.value for left, right in pairs]
    refs = list(dict.fromkeys(_refs(first) + _refs(last)))
    kind = metric_class_for(series)
    stats = [_stat("observation_count", len(points), series=series)]
    if kind is MetricClass.GROWTH_RATE:
        multiplier = Decimal(100) if series.unit.casefold() == "ratio" else Decimal(1)
        average = sum((point.value for point in points), Decimal(0)) / len(points)
        minimum = min(points, key=lambda item: item.value)
        maximum = max(points, key=lambda item: item.value)
        full_refs = list(dict.fromkeys(ref for point in points for ref in _refs(point)))
        stats.extend([
            _stat("average_rate", average * multiplier, series=series, full_series=True),
            _stat("latest_rate", last.value * multiplier, _refs(last), period_label(last), series),
            _stat("peak_rate", maximum.value * multiplier, _refs(maximum), period_label(maximum), series),
            _stat("trough_rate", minimum.value * multiplier, _refs(minimum), period_label(minimum), series),
            _stat("positive_periods", sum(point.value > 0 for point in points), series=series, full_series=True),
            _stat("negative_periods", sum(point.value < 0 for point in points), series=series, full_series=True),
        ])
    else:
        stats.extend([
            _stat("first_value", first.value, _refs(first), period_label(first), series),
            _stat("last_value", last.value, _refs(last), period_label(last), series),
        ])
        if kind is not MetricClass.RATE_OR_MARGIN:
            stats.append(_stat("absolute_change", last.value - first.value, refs, series=series, formula="latest - first"))
    if kind is MetricClass.RATE_OR_MARGIN:
        multiplier = Decimal(100) if series.unit.casefold() == "ratio" else Decimal(1)
        stats.append(_stat(
            "percentage_point_change", (last.value - first.value) * multiplier, refs, series=series,
            formula="(latest margin - first margin) * 100" if multiplier == 100 else "latest margin - first margin",
        ))
    elif kind in {MetricClass.MONETARY_LEVEL, MetricClass.COUNT_OR_LEVEL}:
        ratio_is_meaningful = first.value > 0 and last.value > 0
        stats.extend([
            _stat(
                "percentage_change", (last.value / first.value - 1) * 100 if ratio_is_meaningful else None,
                refs, series=series, not_meaningful=not ratio_is_meaningful,
                formula="(latest / first - 1) * 100",
            ),
            _stat(
                "change_multiple", last.value / first.value if ratio_is_meaningful else None,
                refs, series=series, not_meaningful=not ratio_is_meaningful,
                formula="latest / first",
            ),
        ])
    elapsed: Decimal | None = None
    if series.frequency == "annual" and first.fiscal_year is not None and last.fiscal_year is not None:
        elapsed = Decimal(last.fiscal_year - first.fiscal_year)
    elif series.frequency == "quarterly" and first.fiscal_year is not None and last.fiscal_year is not None:
        quarter = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
        if first.fiscal_period in quarter and last.fiscal_period in quarter:
            elapsed = Decimal((last.fiscal_year * 4 + quarter[last.fiscal_period]) - (first.fiscal_year * 4 + quarter[first.fiscal_period])) / 4
    if kind in {MetricClass.MONETARY_LEVEL, MetricClass.COUNT_OR_LEVEL} and first.value > 0 and last.value > 0 and elapsed is not None and elapsed > 0:
        cagr = (Decimal(str(math.pow(float(last.value / first.value), float(Decimal(1) / elapsed)))) - 1) * 100
        stats.extend([
            _stat("years_elapsed", elapsed, refs, series=series),
            _stat("cagr_percent", cagr, refs, series=series, formula="((latest / first)^(1 / years_elapsed) - 1) * 100"),
        ])
    elif kind in {MetricClass.MONETARY_LEVEL, MetricClass.COUNT_OR_LEVEL}:
        if elapsed is not None and elapsed > 0:
            stats.append(_stat("years_elapsed", elapsed, refs, series=series))
        stats.append(_stat(
            "cagr_percent", None, refs, series=series, not_meaningful=True,
            formula="((latest / first)^(1 / years_elapsed) - 1) * 100",
        ))
    elif kind is MetricClass.GROWTH_RATE:
        # Growth-rate series retain an explicit unavailable marker for schema
        # compatibility, never a growth-of-growth calculation.
        stats.append(_stat("cagr_percent", None, refs, series=series, not_meaningful=True))
    minimum = min(points, key=lambda item: item.value)
    maximum = max(points, key=lambda item: item.value)
    stats.extend([
        _stat("minimum", minimum.value, _refs(minimum), period_label(minimum), series),
        _stat("maximum", maximum.value, _refs(maximum), period_label(maximum), series),
        _stat("positive_changes", sum(change > 0 for change in changes), series=series, full_series=True),
        _stat("negative_changes", sum(change < 0 for change in changes), series=series, full_series=True),
        _stat("unchanged_periods", sum(change == 0 for change in changes), series=series, full_series=True),
    ])
    direction = "unavailable" if not changes else "up" if changes[-1] > 0 else "down" if changes[-1] < 0 else "flat"
    stats.append(_stat("recent_direction", direction, list(dict.fromkeys(_refs(points[-2]) + _refs(last))) if changes else _refs(last), series=series))
    if len(points) > 1:
        mean = sum((point.value for point in points), Decimal(0)) / len(points)
        variance = sum(((point.value - mean) ** 2 for point in points), Decimal(0)) / len(points)
        full_refs = list(dict.fromkeys(ref for point in points for ref in _refs(point)))
        stats.append(_stat("standard_deviation_financial_metric", Decimal(str(math.sqrt(float(variance)))), series=series, full_series=True))
        stats.append(_stat("range", maximum.value - minimum.value, list(dict.fromkeys(_refs(minimum) + _refs(maximum))), series=series))
    return stats


def _metric_analysis(series: SeriesResult) -> MetricAnalysis:
    return MetricAnalysis(
        entity=series.entity, metric=series.metric, label=series.label, unit=series.unit,
        frequency=series.frequency, metric_class=metric_class_for(series),
        series_evidence_refs=list(dict.fromkeys(ref for point in series.observations for ref in _refs(point))),
        observations=[{
            "period": period_label(point), "date": point.date.isoformat(),
            "value": str(point.value), "display_value": format_observation(point.value, series.unit, series.metric),
            "evidence_refs": _refs(point),
        } for point in series.observations],
        statistics=statistics_for_series(series), period_changes=period_changes_for_series(series),
    )


def _packet(mode: AnalysisMode, context: dict[str, object], series: list[SeriesResult], caveats: list[str] | None = None) -> FocusedContextPacket:
    evidence = sorted({ref for result in series for point in result.observations for ref in _refs(point)})
    freshness = [
        (ref, str(point.value), point.date.isoformat())
        for result in series for point in result.observations for ref in _refs(point)
    ]
    fingerprint = hashlib.sha256(json.dumps(sorted(freshness)).encode()).hexdigest()[:16]
    packet = FocusedContextPacket(
        mode=mode, context=context, metrics=[_metric_analysis(item) for item in series], caveats=caveats or [],
        evidence_ids=evidence, evidence_fingerprint=fingerprint,
    )
    return _authorize_compact_packet(packet)


ROLE_STATISTICS = {
    "fundamentals": {"first_value", "last_value", "absolute_change", "percentage_change", "change_multiple", "percentage_point_change", "cagr_percent", "years_elapsed", "minimum", "maximum", "latest_rate", "recent_direction"},
    "comparison": {"first_value", "last_value", "absolute_change", "percentage_change", "change_multiple", "percentage_point_change", "cagr_percent", "years_elapsed", "minimum", "maximum", "latest_rate", "recent_direction"},
}
ROLE_METRICS = {
    "fundamentals": {"REVENUE", "REVENUE_GROWTH_YOY", "GROSS_MARGIN", "OPERATING_MARGIN", "NET_MARGIN", "NET_INCOME", "OPERATING_CASH_FLOW", "FREE_CASH_FLOW", "FCF_MARGIN", "CASH_AND_CASH_EQUIVALENTS", "LONG_TERM_DEBT_NONCURRENT", "CURRENT_RATIO", "SHAREHOLDERS_EQUITY"},
    "comparison": None,
}


def role_packet(packet: FundamentalsContextPacket | FocusedContextPacket, role: str) -> FundamentalsContextPacket | FocusedContextPacket:
    """Compact, role-focused view without weakening statistic-to-evidence links."""
    selected = ROLE_STATISTICS[role]
    selected_metrics = ROLE_METRICS[role]
    metrics: list[MetricAnalysis] = []
    for metric in packet.metrics:
        if selected_metrics is not None and metric.metric not in selected_metrics:
            continue
        observations = metric.observations
        if len(observations) > 2:
            observations = [observations[0], observations[-1]]
        period_changes = metric.period_changes
        period_changes = period_changes[-2:]
        statistics = [item for item in metric.statistics if item.name in selected]
        metrics.append(metric.model_copy(update={
            "observations": observations,
            "statistics": statistics,
            "period_changes": period_changes,
            "series_evidence_refs": metric.series_evidence_refs if any(item.evidence_scope == "full_series" for item in statistics) else [],
        }))
    return _authorize_compact_packet(packet.model_copy(update={"metrics": metrics}))


EVIDENCE_KEYS = {"evidence_ref", "evidence_refs", "series_evidence_refs"}


def _evidence_ids_in_payload(value: object) -> set[str]:
    """Return only references that remain in the final model-visible payload."""
    found: set[str] = set()
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python", exclude={"evidence_ids", "evidence_fingerprint"})
    if isinstance(value, dict):
        for key, child in value.items():
            if key in EVIDENCE_KEYS:
                if isinstance(child, str):
                    found.add(child)
                elif isinstance(child, list):
                    found.update(item for item in child if isinstance(item, str))
            else:
                found.update(_evidence_ids_in_payload(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_evidence_ids_in_payload(child))
    return found


def _authorize_compact_packet[T: GroundedPacketBase](packet: T) -> T:
    evidence = sorted(_evidence_ids_in_payload(packet))
    compact_payload = packet.model_dump(mode="json", exclude={"evidence_ids", "evidence_fingerprint"})
    fingerprint = hashlib.sha256(json.dumps(compact_payload, sort_keys=True).encode()).hexdigest()[:16]
    return packet.model_copy(update={"evidence_ids": evidence, "evidence_fingerprint": fingerprint})


def validate_claims[T: BaseModel](output: T, packet: GroundedPacketBase) -> T | None:
    valid_ids = set(packet.evidence_ids)
    data = output.model_dump(mode="python")
    internal_language = re.compile(
        r"(?i)\b(?:the\s+)?(?:packet|schema|context object|evidence_ids|valuation_context|macro_context|interpretation_available)\b"
    )

    def clean(node: object) -> object:
        if isinstance(node, dict):
            if "text" in node and "evidence_refs" in node:
                refs = node.get("evidence_refs")
                text = node.get("text")
                leaks_id = isinstance(text, str) and (
                    any(reference in text for reference in valid_ids)
                    or bool(re.search(r"(?i)(?:\bsec:|\bfred:|\baccession(?:\s+(?:id|number))?\s*[:#])", text))
                    or bool(internal_language.search(text))
                )
                return node if isinstance(refs, list) and refs and all(ref in valid_ids for ref in refs) and not leaks_id else None
            return {key: cleaned for key, value in node.items() if (cleaned := clean(value)) is not None}
        if isinstance(node, list):
            return [cleaned for value in node if (cleaned := clean(value)) is not None]
        if isinstance(node, str) and internal_language.search(node):
            return None
        return node

    try:
        return type(output).model_validate(clean(data))
    except ValidationError:
        return None


OUTPUT_CONTRACT = """Use supplied display_value fields whenever mentioning numbers; never print raw decimal values. Never include evidence IDs, sec: identifiers, fred: identifiers, accession IDs, citation syntax, or bracketed source references in prose. Put evidence identifiers only in evidence_refs. Every substantive claim must cite canonical deterministic evidence. Never mention packets, schemas, field names, internal state flags, variable names, evidence architecture, implementation details, or model instructions. Translate limitations into normal research language."""
FOCUSED_PROMPT = """TASK: contextual-financial-analysis focused
Answer: What does this deterministic result imply or reveal? Return 1-3 concise analytical observations, about 80-150 words maximum in total, using only the supplied compact deterministic result and its precomputed statistics. Lead with the pattern, relative distinction, or material implication instead of paraphrasing visible values. Do not calculate, add external facts, causality, forecasts, recommendations, or fair value. Interpret valuation only when it is explicitly supplied.""" + "\n" + OUTPUT_CONTRACT
COMPARISON_PROMPT = """TASK: contextual-financial-analysis comparison
Answer: What is the most important interpretation of this deterministic comparison? Make summary the central relative conclusion, then identify dimensions where a company clearly leads, the main trade-offs, and material comparability limits. Do not narrate every visible value or force a winner. Companies may differ sharply by sector or business model; omit unavailable measures and synthesize the evidence that is actually comparable. One missing measure must not invalidate otherwise meaningful comparison evidence. Do not calculate, add external facts, assert stock-move causality, forecast, recommend, or estimate fair value. Unsupported measures are limitations, not negatives.""" + "\n" + OUTPUT_CONTRACT
FUNDAMENTALS_PROMPT = """TASK: contextual-financial-analysis fundamentals
Answer: What does the supplied operating and financial evidence say about the company's fundamentals? Interpret growth, profitability, cash generation, and financial position only. The stance is scoped to fundamental evidence, not expected stock performance. Do not discuss price, valuation, macro, forecasts, or recommendations.""" + "\n" + OUTPUT_CONTRACT
VALUATION_PROMPT = """TASK: contextual-financial-analysis valuation
Answer what the supplied valuation evidence says about the current observations relative to usable company history. A current multiple alone never supports favorable or demanding language. When no supported measure has enough historical comparison evidence, return NEUTRAL stance and LOW confidence and explain the limitation in ordinary research language. Mention the 10Y Treasury evidence only when it materially improves a historical valuation comparison, and explicitly explain that relevance. Do not list macro facts merely because they are available. Do not calculate ratios, estimate fair value, forecast, recommend, infer cheapness from price performance, import external benchmarks, or assert causal macro effects.""" + "\n" + OUTPUT_CONTRACT
SKEPTIC_PROMPT = """TASK: contextual-financial-analysis skeptic
Adversarially review the two structured analyst outputs against the underlying deterministic evidence. Earlier AI text is context and a challenge target, never evidence. Identify real weak support, counterevidence, overstatement, contradiction, or cross-analyst tension. Mention macro evidence only when it materially improves a historical valuation comparison and explain why. Do not manufacture disagreement; NO_MATERIAL_CHALLENGE with zero challenges is valid.""" + "\n" + OUTPUT_CONTRACT
OVERVIEW_PROMPT = """TASK: contextual-financial-analysis overview
Give an overall evidence interpretation with stance POSITIVE, NEUTRAL, or NEGATIVE, separate from confidence. This is the balance of supplied fundamental evidence, valuation evidence and its historical sample quality, material Skeptic challenges, and unresolved uncertainty; it is not an investment recommendation. Do not mechanically average the Fundamentals and Valuation stances.

Use POSITIVE when supported evidence is materially favorable despite non-dominant qualifications; NEGATIVE when it is materially unfavorable despite non-dominant positives; and NEUTRAL when favorable and unfavorable evidence are genuinely balanced, evidence is too limited, or major uncertainty dominates. Confidence reflects evidence coverage, consistency, Skeptic challenge strength, and relevant historical sample size, not the tone of the prose.

Write synthesis as a prominent, concise 1-3 sentence answer to what the company evidence adds up to. Provide 2-4 key_conclusions that combine evidence into interpretations across magnitude, direction, persistence, and trade-offs rather than restating a table. Put only material counterevidence or limitations in key_risks. Do not describe analyst agreement, disagreement, workflow, or a Skeptic challenge as an internal process. Preserve material fundamental-versus-valuation tension in user-facing research language.

Never calculate from raw values. Use only supplied precomputed display_value statistics for precise changes, multiples, CAGR, margin changes, and relative valuation positions. Do not use Buy, Hold, Sell, bullish, bearish, price target, fair value, expected return, attractive entry point, upside, downside target, worth owning, overweight, or underweight. Do not forecast, recommend, or add external facts.""" + "\n" + OUTPUT_CONTRACT


def _years_before(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _point_payload(point: SeriesPointResult, series: SeriesResult) -> dict[str, object]:
    return {
        "date": point.date.isoformat(), "value": str(point.value),
        "display_value": format_observation(point.value, series.unit, series.metric),
        "evidence_refs": _refs(point), "state": point.state,
    }


def _fingerprint(series: list[SeriesResult]) -> tuple[list[str], str]:
    evidence = sorted({ref for item in series for point in item.observations for ref in _refs(point)})
    freshness = sorted(
        (ref, point.date.isoformat(), str(point.value), point.state)
        for item in series for point in item.observations for ref in _refs(point)
    )
    return evidence, hashlib.sha256(json.dumps(freshness).encode()).hexdigest()[:16]


def _compact_series_for_ai(query: QueryRequest, series: list[SeriesResult]) -> list[SeriesResult]:
    """Bound high-frequency focused/comparison packets without altering evidence context."""
    if isinstance(query, ValuationQuery) or isinstance(query, CompanyQuery):
        return series
    compact: list[SeriesResult] = []
    for item in series:
        points = sorted(item.observations, key=lambda point: point.date)
        if len(points) <= 6:
            compact.append(item)
            continue
        selected = [points[0], points[-1], min(points, key=lambda point: point.value), max(points, key=lambda point: point.value)]
        if isinstance(query, MarketQuery):
            selected.extend(points[-2:])
        unique = {point.date: point for point in selected}
        compact.append(item.model_copy(update={"observations": sorted(unique.values(), key=lambda point: point.date)}))
        if isinstance(query, MarketQuery) and points[0].value != 0:
            compact.append(SeriesResult(
                id=f"{item.id}:COMPACT_RETURN", label="Displayed period return", entity=item.entity,
                metric="RETURN", unit="return", frequency="period",
                observations=[SeriesPointResult(
                    date=points[-1].date, value=points[-1].value / points[0].value - Decimal(1),
                    point_type="CALCULATED", formula="DISPLAY_END / DISPLAY_START - 1",
                    input_observation_ids=list(dict.fromkeys(_refs(points[0]) + _refs(points[-1]))),
                )], context=item.context,
            ))
            peak = points[0]
            worst_peak, worst_trough, worst = peak, peak, Decimal(0)
            for point in points:
                if point.value > peak.value:
                    peak = point
                drawdown = point.value / peak.value - Decimal(1) if peak.value > 0 else Decimal(0)
                if drawdown < worst:
                    worst_peak, worst_trough, worst = peak, point, drawdown
            compact.append(SeriesResult(
                id=f"{item.id}:COMPACT_DRAWDOWN", label="Displayed maximum drawdown", entity=item.entity,
                metric="MAX_DRAWDOWN", unit="return", frequency="period",
                observations=[SeriesPointResult(
                    date=worst_trough.date, value=worst, point_type="CALCULATED",
                    formula="DISPLAY_TROUGH / DISPLAY_PEAK - 1",
                    input_observation_ids=list(dict.fromkeys(_refs(worst_peak) + _refs(worst_trough))),
                )], context=item.context,
            ))
    return compact


class AnalysisService:
    COMPANY_METRICS = (
        MetricCode.REVENUE, DerivedMetricCode.REVENUE_GROWTH_YOY,
        DerivedMetricCode.GROSS_MARGIN, DerivedMetricCode.OPERATING_MARGIN,
        DerivedMetricCode.NET_MARGIN, MetricCode.NET_INCOME,
        MetricCode.OPERATING_CASH_FLOW, DerivedMetricCode.FREE_CASH_FLOW,
        DerivedMetricCode.FCF_MARGIN, MetricCode.CASH_AND_CASH_EQUIVALENTS,
        MetricCode.LONG_TERM_DEBT_NONCURRENT, DerivedMetricCode.CURRENT_RATIO,
        MetricCode.SHAREHOLDERS_EQUITY,
    )
    VALUATION_METRICS = (
        ValuationMetricCode.PE_RATIO, ValuationMetricCode.PS_RATIO,
        ValuationMetricCode.P_FCF_RATIO, ValuationMetricCode.FCF_YIELD,
    )
    COMPARISON_METRICS = (
        DerivedMetricCode.REVENUE_GROWTH_YOY, DerivedMetricCode.GROSS_MARGIN,
        DerivedMetricCode.OPERATING_MARGIN, DerivedMetricCode.NET_MARGIN,
        DerivedMetricCode.FREE_CASH_FLOW, DerivedMetricCode.FCF_MARGIN,
        MetricCode.CASH_AND_CASH_EQUIVALENTS, MetricCode.LONG_TERM_DEBT_NONCURRENT,
        DerivedMetricCode.CURRENT_RATIO,
    )
    MACRO_SERIES = (
        "US_10Y_TREASURY_YIELD",
    )

    def __init__(
        self, store: SQLiteStore, provider: AIProvider, *,
        timeout: float = DEFAULT_ANALYSIS_TIMEOUT_SECONDS, model: str | None = None,
    ) -> None:
        self.store, self.provider, self.engine = store, provider, QueryEngine(store)
        self.timeout, self.model = timeout, model

    async def _call(self, prompt: str, schema: type[BaseModel], packet: GroundedPacketBase, extra: object | None = None):
        payload: dict[str, object] = {"analysis_packet": packet.model_dump(mode="json", exclude_none=True)}
        if extra is not None:
            payload["prior_structured_outputs"] = extra
        task = prompt.partition("\n")[0].removeprefix("TASK: ")
        serialized = json.dumps(payload, separators=(",", ":"))
        started = monotonic()
        logger.info(
            "analysis generation started task=%s payload_chars=%d timeout_seconds=%.0f",
            task, len(serialized), self.timeout,
        )
        try:
            async with asyncio.timeout(self.timeout):
                raw = await self.provider.generate_structured(
                    prompt + "\n\n" + serialized, codex_output_schema(schema), model=self.model,
                )
        except Exception:
            logger.warning(
                "analysis generation failed task=%s elapsed_seconds=%.1f",
                task, monotonic() - started, exc_info=True,
            )
            raise
        logger.info(
            "analysis generation completed task=%s elapsed_seconds=%.1f",
            task, monotonic() - started,
        )
        parsed = schema.model_validate_json(raw)
        validated = validate_claims(parsed, packet)
        if validated is not None:
            return validated
        repair_payload = {
            **payload, "invalid_output": parsed.model_dump(mode="json"),
            "repair_reason": "Remove unsupported claims and use only canonical packet evidence refs.",
        }
        repair_prompt = """TASK: contextual-financial-analysis grounding-repair
Repair this output once. Retain only packet-supported claims, keep evidence identifiers only in evidence_refs, and return exactly the requested schema. Do not add facts or calculate."""
        repair_serialized = json.dumps(repair_payload, separators=(",", ":"))
        repair_started = monotonic()
        logger.info(
            "analysis grounding repair started task=%s payload_chars=%d timeout_seconds=%.0f",
            task, len(repair_serialized), self.timeout,
        )
        try:
            async with asyncio.timeout(self.timeout):
                repaired = await self.provider.generate_structured(
                    repair_prompt + "\n\n" + repair_serialized, codex_output_schema(schema), model=self.model,
                )
        except Exception:
            logger.warning(
                "analysis grounding repair failed task=%s elapsed_seconds=%.1f",
                task, monotonic() - repair_started, exc_info=True,
            )
            raise
        logger.info(
            "analysis grounding repair completed task=%s elapsed_seconds=%.1f",
            task, monotonic() - repair_started,
        )
        return validate_claims(schema.model_validate_json(repaired), packet)

    @staticmethod
    def _error(exc: Exception) -> AnalysisError:
        if isinstance(exc, AIDisconnectedError):
            return AnalysisError(error_code="AI_DISCONNECTED", message="Connect ChatGPT to analyze this evidence. Financial data remains available.")
        if isinstance(exc, AIUsageLimitError):
            return AnalysisError(error_code="AI_USAGE_LIMIT", message="Your current Codex usage limit has been reached. Financial data remains available.")
        if isinstance(exc, TimeoutError):
            return AnalysisError(error_code="AI_TIMEOUT", message="Analysis timed out. Financial data remains available.")
        if isinstance(exc, (ValidationError, json.JSONDecodeError, AnalysisGroundingError)):
            return AnalysisError(error_code="MODEL_OUTPUT_INVALID", message="The analysis could not be grounded reliably. Financial data remains available.")
        return AnalysisError(error_code="AI_UNAVAILABLE", message="Analysis is temporarily unavailable. Financial data remains available.")

    @staticmethod
    def _cache_key(packet: GroundedPacketBase) -> str:
        context = hashlib.sha256(json.dumps(packet.context, sort_keys=True).encode()).hexdigest()[:12]
        return f"analysis:{packet.mode}:v{packet.version}:{context}:{packet.evidence_fingerprint}"

    async def focused(self, request: FocusedAnalysisRequest) -> AnalysisResponse:
        result = self.engine.execute(request.query)
        if result.status is not ResultStatus.SUCCESS:
            return AnalysisError(error_code="DATA_UNAVAILABLE", message=result.errors[0] if result.errors else "The requested evidence is unavailable.")
        shape = classify_result(request.query, result)
        useful_market_scalar = isinstance(request.query, MarketQuery) and request.query.operation in {
            MarketOperation.RETURN, MarketOperation.MAX_DRAWDOWN,
        }
        if shape is ResultShape.SINGLE_SCALAR and not useful_market_scalar:
            return AnalysisError(error_code="DATA_UNAVAILABLE", message="No interpretation is needed for a single observation. Explore a longer range to analyze context.")
        compact = _compact_series_for_ai(request.query, result.series)
        context = request.query.model_dump(mode="json")
        caveats = list(result.errors)
        if isinstance(request.query, CompanyQuery) and request.query.universe_ranking and request.query.ranking is not RankingOperation.NONE:
            display_count = min(request.query.ranking_limit or 5, len(result.series))
            compact = compact[:display_count]
            context = {
                "domain": "company", "metric": request.query.metric.value,
                "frequency": request.query.frequency, "start_year": request.query.start_year,
                "end_year": request.query.end_year, "ranking": request.query.ranking.value,
                "total_eligible_count": len(request.query.tickers),
                "total_usable_count": len(result.series), "displayed_count": display_count,
            }
        packet = _packet(AnalysisMode.FOCUSED, context, compact, caveats)
        try:
            output = await self._call(FOCUSED_PROMPT, FocusedInterpretation, packet)
            if output is None:
                raise AnalysisGroundingError("all claims had invalid evidence")
            return FocusedAnalysisSuccess(packet_version=packet.version, cache_key=self._cache_key(packet), interpretation=output, evidence_context=result.series)
        except Exception as exc:
            return self._error(exc)

    async def comparison(self, request: ComparisonAnalysisRequest) -> AnalysisResponse:
        evidence_context: list[SeriesResult] = []
        packet_series: list[SeriesResult] = []
        caveats: list[str] = []
        queries = list(request.queries)
        if request.tickers:
            queries = [
                CompanyQuery(tickers=request.tickers, metric=metric, frequency="annual", start_year=2019)
                for metric in self.COMPARISON_METRICS
            ] + [
                ValuationQuery(tickers=request.tickers, metric=metric, view="latest")
                for metric in self.VALUATION_METRICS
            ]
            latest_dates = [
                point.date for ticker in request.tickers
                for item in self.engine.execute(MarketQuery(tickers=[ticker], series=MarketPriceSeries.ADJUSTED_CLOSE, view="latest")).series
                for point in item.observations
            ]
            if latest_dates:
                end = max(latest_dates)
                for years in (1, 3, 5):
                    queries.append(MarketQuery(
                        tickers=request.tickers, series=MarketPriceSeries.ADJUSTED_CLOSE,
                        operation=MarketOperation.RETURN, start_date=_years_before(end, years), end_date=end,
                    ))
                queries.append(MarketQuery(
                    tickers=request.tickers, series=MarketPriceSeries.ADJUSTED_CLOSE,
                    operation=MarketOperation.MAX_DRAWDOWN, start_date=_years_before(end, 5), end_date=end,
                ))
        for query in queries:
            result = self.engine.execute(query)
            if result.status is not ResultStatus.SUCCESS:
                caveats.extend(result.errors or ["A requested comparison measure is unavailable."])
                continue
            evidence_context.extend(result.series)
            packet_series.extend(_compact_series_for_ai(query, result.series))
            caveats.extend(result.errors)
            if isinstance(query, CompanyQuery) and query.frequency == "quarterly":
                caveats.append("Fiscal-quarter labels are issuer-specific and may cover different calendar periods.")
        if not packet_series:
            return AnalysisError(error_code="DATA_UNAVAILABLE", message="Comparison evidence is unavailable.")
        tickers = request.tickers or list(request.queries[0].tickers)
        base_packet = _packet(AnalysisMode.COMPARISON, {"tickers": tickers}, packet_series, caveats)
        compact_packet = role_packet(base_packet, "comparison")
        packet = ComparisonContextPacket(**compact_packet.model_dump(mode="python"))
        try:
            output = await self._call(COMPARISON_PROMPT, ComparisonAnalysis, packet)
            if output is None:
                raise AnalysisGroundingError("all claims had invalid evidence")
            return ComparisonAnalysisSuccess(packet_version=packet.version, cache_key=self._cache_key(packet), interpretation=output, evidence_context=evidence_context)
        except Exception as exc:
            return self._error(exc)

    def _fundamentals_packet(self, request: CompanyAnalysisRequest) -> tuple[FundamentalsContextPacket, list[SeriesResult]] | None:
        support = {(item["ticker"], item["metric"], item["frequency"]) for item in self.store.catalog()["company_metric_support"]}
        series: list[SeriesResult] = []
        unavailable: list[str] = []
        for metric in self.COMPANY_METRICS:
            if (request.ticker, metric.value, "annual") not in support:
                unavailable.append(metric_label(metric))
                continue
            result = self.engine.execute(CompanyQuery(
                tickers=[request.ticker], metric=metric, frequency="annual",
                start_year=request.start_year, end_year=request.end_year,
            ))
            if result.status is ResultStatus.SUCCESS:
                series.extend(result.series)
            else:
                unavailable.append(metric_label(metric))
        if not series:
            return None
        evidence, fingerprint = _fingerprint(series)
        focused = FocusedContextPacket(
            mode=AnalysisMode.COMPANY,
            context={**request.model_dump(mode="json"), "unavailable_metrics": unavailable},
            metrics=[_metric_analysis(item) for item in series],
            caveats=[f"Unavailable metric: {metric}" for metric in unavailable],
            evidence_ids=evidence, evidence_fingerprint=fingerprint,
        )
        compact = role_packet(focused, "fundamentals")
        packet = FundamentalsContextPacket(**compact.model_dump(mode="python"))
        return packet, series

    def _macro_context(self) -> tuple[list[dict[str, object]], list[SeriesResult], list[str]]:
        contexts: list[dict[str, object]] = []
        evidence_series: list[SeriesResult] = []
        caveats: list[str] = []
        for code in self.MACRO_SERIES:
            result = self.engine.execute(MacroQuery(series=[code]))
            if result.status is not ResultStatus.SUCCESS or not result.series[0].observations:
                caveats.append(f"{code} is unavailable in local macro storage.")
                continue
            item = result.series[0]
            points = sorted(item.observations, key=lambda point: point.date)
            current = points[-1]
            selected: list[SeriesPointResult] = [current]
            payload: dict[str, object] = {
                "series_code": code, "series_label": item.label, "current": _point_payload(current, item),
                "one_year_anchor": None, "one_year_change": None,
                "five_year_anchor": None, "five_year_change": None,
                "five_year_minimum": None, "five_year_maximum": None,
            }
            tolerance = 10 if item.frequency == "daily" else 45
            for years, name in ((1, "one_year"), (5, "five_year")):
                target = _years_before(current.date, years)
                candidates = [point for point in points if point.date <= target]
                anchor = candidates[-1] if candidates else None
                if anchor is None or (target - anchor.date).days > tolerance:
                    caveats.append(f"{code} lacks a reliable {years}Y anchor.")
                    continue
                selected.append(anchor)
                payload[f"{name}_anchor"] = _point_payload(anchor, item)
                change = current.value - anchor.value
                payload[f"{name}_change"] = {
                    "value": str(change), "display_value": f"{change:+.2f} pp",
                    "evidence_refs": list(dict.fromkeys(_refs(anchor) + _refs(current))),
                }
                if years == 5:
                    window = [point for point in points if anchor.date <= point.date <= current.date]
                    minimum, maximum = min(window, key=lambda point: point.value), max(window, key=lambda point: point.value)
                    selected.extend((minimum, maximum))
                    payload["five_year_minimum"] = _point_payload(minimum, item)
                    payload["five_year_maximum"] = _point_payload(maximum, item)
            contexts.append(payload)
            unique = {point.date: point for point in selected}
            evidence_series.append(item.model_copy(update={"observations": sorted(unique.values(), key=lambda point: point.date)}))
        return contexts, evidence_series, caveats

    def _valuation_packet(self, request: CompanyAnalysisRequest) -> tuple[ValuationContextPacket, list[SeriesResult]] | None:
        latest_result = self.engine.execute(MarketQuery(
            tickers=[request.ticker], series=MarketPriceSeries.RAW_CLOSE, view="latest",
        ))
        if latest_result.status is not ResultStatus.SUCCESS or not latest_result.series or not latest_result.series[0].observations:
            return None
        latest_series = latest_result.series[0]
        latest = latest_series.observations[-1]
        evidence_context: list[SeriesResult] = [latest_series]
        market_context: list[dict[str, object]] = []
        caveats: list[str] = []
        for years in (1, 3, 5):
            target = _years_before(latest.date, years)
            result = self.engine.execute(MarketQuery(
                tickers=[request.ticker], series=MarketPriceSeries.ADJUSTED_CLOSE,
                operation=MarketOperation.RETURN, start_date=target, end_date=latest.date,
            ))
            usable = result.series[0] if result.series else None
            if usable is not None and usable.observations:
                evidence_context.append(usable)
                market_context.append({"period": f"{years}Y return", **_point_payload(usable.observations[0], usable)})
            else:
                caveats.append(f"{years}Y adjusted-price return is unavailable from local coverage.")
        drawdown = self.engine.execute(MarketQuery(
            tickers=[request.ticker], series=MarketPriceSeries.ADJUSTED_CLOSE,
            operation=MarketOperation.MAX_DRAWDOWN, start_date=_years_before(latest.date, 5), end_date=latest.date,
        ))
        if drawdown.series and drawdown.series[0].observations:
            item = drawdown.series[0]
            evidence_context.append(item)
            market_context.append({"period": "5Y maximum drawdown", **_point_payload(item.observations[0], item)})
        else:
            caveats.append("5Y maximum drawdown is unavailable from local coverage.")

        current_context: list[dict[str, object]] = []
        historical_context: list[dict[str, object]] = []
        supported_metrics = 0
        for metric in self.VALUATION_METRICS:
            current_result = self.engine.execute(ValuationQuery(tickers=[request.ticker], metric=metric, view="latest"))
            history_result = self.engine.execute(ValuationQuery(tickers=[request.ticker], metric=metric, view="history"))
            if not current_result.series:
                caveats.append(f"{VALUATION_LABELS[metric]} is unavailable.")
                continue
            current_series = current_result.series[0]
            history_series = history_result.series[0] if history_result.series else current_series.model_copy(update={"observations": []})
            evidence_context.extend((current_series, history_series))
            point = current_series.observations[-1] if current_series.observations else None
            if point is not None or current_series.state == "NOT_MEANINGFUL":
                supported_metrics += 1
            current_refs = _refs(point) if point else list(current_series.context.get("evidence_refs") or [])
            current_context.append({
                "metric_code": metric.value, "metric_label": VALUATION_LABELS[metric], "state": current_series.state,
                "display_value": format_observation(point.value, current_series.unit, current_series.metric) if point else None,
                "reason": current_series.context.get("reason"),
                "source_fiscal_year": current_series.context.get("source_fiscal_year"),
                "price_date": current_series.context.get("price_date"),
                "basis": current_series.context.get("basis"), "evidence_refs": current_refs,
            })
            history_points = history_series.observations[-5:]
            history_refs = list(dict.fromkeys(ref for item in history_points for ref in _refs(item)))
            unit = current_series.unit
            def summary(value: object, refs: list[str], *, relative: bool = False) -> dict[str, object]:
                rendered = None if value is None else str(value)
                display = "Unavailable" if value is None else (
                    f"{Decimal(str(value)) * 100:+.1f}%" if relative
                    else format_observation(Decimal(str(value)), unit, current_series.metric)
                )
                return {"value": rendered, "display_value": display, "evidence_refs": refs}
            historical_context.append({
                "metric_code": metric.value, "metric_label": VALUATION_LABELS[metric],
                "observations": [_point_payload(item, history_series) for item in history_points],
                "current": summary(point.value if point else None, current_refs),
                "minimum": summary(current_series.context.get("minimum"), history_refs),
                "maximum": summary(current_series.context.get("maximum"), history_refs),
                "median": summary(current_series.context.get("median"), history_refs),
                "relative_to_median": summary(current_series.context.get("relative_to_median"), list(dict.fromkeys(current_refs + history_refs)), relative=True),
                "usable_observation_count": {
                    "value": str(current_series.context.get("observation_count", 0)),
                    "display_value": f"{current_series.context.get('observation_count', 0)} annual observations",
                    "evidence_refs": history_refs,
                },
                "historical_comparison_supported": bool(current_series.context.get("interpretation_available")),
            })
        if not supported_metrics:
            return None
        macro, macro_series, macro_caveats = self._macro_context()
        evidence_context.extend(macro_series)
        caveats.extend(macro_caveats)
        evidence, fingerprint = _fingerprint(evidence_context)
        instrument = self.store.primary_market_instrument(request.ticker)
        packet = ValuationContextPacket(
            mode=AnalysisMode.COMPANY, context=request.model_dump(mode="json"), caveats=caveats,
            evidence_ids=evidence, evidence_fingerprint=fingerprint,
            latest_market_fact={
                "close": _point_payload(latest, latest_series),
                "price_date": latest.date.isoformat(),
                "instrument_id": instrument.instrument_id if instrument else None,
                "symbol": instrument.symbol if instrument else request.ticker,
            },
            market_context=market_context, current_valuation=current_context,
            historical_valuation=historical_context, macro_context=macro,
        )
        return _authorize_compact_packet(packet), evidence_context

    @staticmethod
    def _review_packet(fundamentals: FundamentalsContextPacket, valuation: ValuationContextPacket) -> CompanyReviewPacket:
        evidence = sorted(set(fundamentals.evidence_ids) | set(valuation.evidence_ids))
        fingerprint = hashlib.sha256(
            f"{fundamentals.evidence_fingerprint}:{valuation.evidence_fingerprint}".encode()
        ).hexdigest()[:16]
        packet = CompanyReviewPacket(
            mode=AnalysisMode.COMPANY, context={"workflow": "company_review"}, caveats=fundamentals.caveats + valuation.caveats,
            evidence_ids=evidence, evidence_fingerprint=fingerprint,
            fundamentals=fundamentals.model_dump(mode="json"), valuation=valuation.model_dump(mode="json"),
        )
        return _authorize_compact_packet(packet)

    async def company_events(self, request: CompanyAnalysisRequest) -> AsyncIterator[CompanyAnalysisEvent]:
        yield CompanyAnalysisEvent(event="analysis_started")
        fundamentals_built = self._fundamentals_packet(request)
        valuation_built = self._valuation_packet(request)
        packets: dict[str, GroundedPacketBase] = {}
        evidence_context: list[SeriesResult] = []
        stage_evidence: dict[str, list[SeriesResult]] = {}
        if fundamentals_built:
            packets["fundamentals"], fundamentals_series = fundamentals_built
            stage_evidence["fundamentals"] = fundamentals_series
            evidence_context.extend(fundamentals_series)
        if valuation_built:
            packets["valuation"], valuation_series = valuation_built
            stage_evidence["valuation"] = valuation_series
            evidence_context.extend(valuation_series)

        outputs: dict[str, AnalystAssessment | SkepticReview | OverviewOutput | None] = {
            "fundamentals": None, "valuation": None, "skeptic": None, "overview": None,
        }
        failures: list[str] = []
        details: dict[str, str] = {}
        failure_codes: dict[str, str] = {}
        tasks: set[asyncio.Task[tuple[str, AnalystAssessment]]] = set()

        async def run_base(stage: str, prompt: str, packet: GroundedPacketBase) -> tuple[str, AnalystAssessment]:
            output = await self._call(prompt, AnalystAssessment, packet)
            if output is None:
                raise AnalysisGroundingError(f"{stage} could not be grounded")
            if stage == "valuation" and isinstance(packet, ValuationContextPacket):
                sufficient = any(
                    bool(item.get("historical_comparison_supported"))
                    for item in packet.historical_valuation
                )
                if not sufficient:
                    reference = packet.evidence_ids[0]
                    output = output.model_copy(update={
                        "stance": Stance.NEUTRAL,
                        "confidence": Confidence.LOW,
                        "thesis": AnalystClaim(
                            text="Current valuation observations are available, but the company's usable history is too limited for a dependable favorable or demanding conclusion.",
                            evidence_refs=[reference],
                        ),
                    })
            return stage, output

        try:
            for stage, prompt in (("fundamentals", FUNDAMENTALS_PROMPT), ("valuation", VALUATION_PROMPT)):
                if stage not in packets:
                    failures.append(stage)
                    details[stage] = "Required deterministic evidence is unavailable."
                    yield CompanyAnalysisEvent(event="stage_skipped", stage=stage, message=details[stage])
                else:
                    yield CompanyAnalysisEvent(event="stage_started", stage=stage)
                    tasks.add(asyncio.create_task(run_base(stage, prompt, packets[stage]), name=stage))

            while tasks:
                done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    try:
                        stage, output = task.result()
                        outputs[stage] = output
                        yield CompanyAnalysisEvent(
                            event="stage_completed", stage=stage, result=output,
                            evidence_context=stage_evidence.get(stage, []),
                        )
                    except Exception as exc:
                        stage = task.get_name()
                        failures.append(stage)
                        classified = self._error(exc)
                        details[stage] = classified.message
                        failure_codes[stage] = classified.error_code
                        yield CompanyAnalysisEvent(event="stage_failed", stage=stage, message=details[stage])

            if outputs["fundamentals"] is not None and outputs["valuation"] is not None:
                review_packet = self._review_packet(
                    packets["fundamentals"], packets["valuation"],  # type: ignore[arg-type]
                )
                prior = {
                    "fundamentals": outputs["fundamentals"].model_dump(mode="json"),
                    "valuation": outputs["valuation"].model_dump(mode="json"),
                }
                yield CompanyAnalysisEvent(event="stage_started", stage="skeptic")
                try:
                    skeptic = await self._call(SKEPTIC_PROMPT, SkepticReview, review_packet, prior)
                    if skeptic is None:
                        raise AnalysisGroundingError("skeptic could not be grounded")
                    outputs["skeptic"] = skeptic
                    yield CompanyAnalysisEvent(event="stage_completed", stage="skeptic", result=skeptic)
                except Exception as exc:
                    failures.append("skeptic")
                    details["skeptic"] = self._error(exc).message
                    logger.warning(
                        "company skeptic stage failed",
                        extra={"error_type": type(exc).__name__}, exc_info=exc,
                    )
                    yield CompanyAnalysisEvent(event="stage_failed", stage="skeptic", message=details["skeptic"])
                if outputs["skeptic"] is not None:
                    yield CompanyAnalysisEvent(event="stage_started", stage="overview")
                    try:
                        overview = await self._call(
                            OVERVIEW_PROMPT, OverviewOutput, review_packet,
                            {**prior, "skeptic": outputs["skeptic"].model_dump(mode="json")},
                        )
                        if overview is None:
                            raise AnalysisGroundingError("overview could not be grounded")
                        outputs["overview"] = overview
                        yield CompanyAnalysisEvent(event="stage_completed", stage="overview", result=overview)
                    except Exception as exc:
                        failures.append("overview")
                        details["overview"] = self._error(exc).message
                        logger.warning(
                            "company overview stage failed",
                            extra={"error_type": type(exc).__name__}, exc_info=exc,
                        )
                        yield CompanyAnalysisEvent(event="stage_failed", stage="overview", message=details["overview"])
                else:
                    yield CompanyAnalysisEvent(event="stage_skipped", stage="overview", message="Overview requires a completed Skeptic review.")
            else:
                for stage in ("skeptic", "overview"):
                    yield CompanyAnalysisEvent(event="stage_skipped", stage=stage, message="Both base analysts must complete first.")

            if outputs["fundamentals"] is None and outputs["valuation"] is None:
                codes = [failure_codes[stage] for stage in ("fundamentals", "valuation") if stage in failure_codes]
                code = codes[0] if codes and all(item == codes[0] for item in codes) else next(
                    (item for item in ("AI_DISCONNECTED", "AI_USAGE_LIMIT", "AI_TIMEOUT", "MODEL_OUTPUT_INVALID") if item in codes),
                    "AI_UNAVAILABLE",
                )
                messages = {
                    "AI_DISCONNECTED": "Connect ChatGPT to analyze this evidence. Financial data remains available.",
                    "AI_USAGE_LIMIT": "Your current Codex usage limit has been reached. Financial data remains available.",
                    "AI_TIMEOUT": "Analysis timed out. Financial data remains available.",
                    "MODEL_OUTPUT_INVALID": "The analysis could not be grounded reliably. Financial data remains available.",
                    "AI_UNAVAILABLE": "No company analysis stage could be completed. Financial data remains available.",
                }
                error = AnalysisError(error_code=code, message=messages[code])
                yield CompanyAnalysisEvent(event="analysis_failed", result=error, message=error.message)
                return
            base_packet = packets.get("fundamentals") or packets["valuation"]
            deterministic_state = [
                packet.model_dump(mode="json") for _, packet in sorted(packets.items())
            ]
            combined = base_packet.model_copy(update={
                "evidence_fingerprint": hashlib.sha256(
                    json.dumps(deterministic_state, sort_keys=True).encode()
                ).hexdigest()[:16],
            })
            final = CompanyAnalysisSuccess(
                packet_version=ANALYSIS_PACKET_VERSION, cache_key=self._cache_key(combined), ticker=request.ticker,
                fundamentals=outputs["fundamentals"], valuation=outputs["valuation"],
                skeptic=outputs["skeptic"], overview=outputs["overview"],
                failures=failures, failure_details=details, evidence_context=evidence_context,
            )
            yield CompanyAnalysisEvent(event="analysis_completed", result=final)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def company(self, request: CompanyAnalysisRequest) -> AnalysisResponse:
        final: AnalysisResponse | None = None
        async for event in self.company_events(request):
            if event.event in {"analysis_completed", "analysis_failed"}:
                final = event.result  # type: ignore[assignment]
        return final or AnalysisError(error_code="AI_UNAVAILABLE", message="Analysis ended without a final result.")
