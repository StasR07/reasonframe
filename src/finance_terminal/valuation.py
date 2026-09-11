"""Conservative filing-aware valuation over canonical SEC and market facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from .market import CorporateAction, CorporateActionType, MarketDailyObservation
from .metrics import MetricCode
from .models import FinancialObservation
from .series import observation_id


class ValuationMetricCode(StrEnum):
    PE_RATIO = "PE_RATIO"
    PS_RATIO = "PS_RATIO"
    P_FCF_RATIO = "P_FCF_RATIO"
    FCF_YIELD = "FCF_YIELD"


class ValuationState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_MEANINGFUL = "NOT_MEANINGFUL"


VALUATION_LABELS = {
    ValuationMetricCode.PE_RATIO: "P/E",
    ValuationMetricCode.PS_RATIO: "P/S",
    ValuationMetricCode.P_FCF_RATIO: "P/FCF",
    ValuationMetricCode.FCF_YIELD: "FCF Yield",
}

VALUATION_INPUTS: dict[ValuationMetricCode, tuple[MetricCode, ...]] = {
    ValuationMetricCode.PE_RATIO: (MetricCode.DILUTED_EPS,),
    ValuationMetricCode.PS_RATIO: (MetricCode.REVENUE, MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES),
    ValuationMetricCode.P_FCF_RATIO: (MetricCode.OPERATING_CASH_FLOW, MetricCode.CAPITAL_EXPENDITURE, MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES),
    ValuationMetricCode.FCF_YIELD: (MetricCode.OPERATING_CASH_FLOW, MetricCode.CAPITAL_EXPENDITURE, MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES),
}

VALUATION_FORMULAS = {
    ValuationMetricCode.PE_RATIO: "RAW_CLOSE / SPLIT_ALIGNED_DILUTED_EPS",
    ValuationMetricCode.PS_RATIO: "RAW_CLOSE / (REVENUE / SPLIT_ALIGNED_DILUTED_SHARES)",
    ValuationMetricCode.P_FCF_RATIO: "RAW_CLOSE / ((OPERATING_CASH_FLOW - CAPITAL_EXPENDITURE) / SPLIT_ALIGNED_DILUTED_SHARES)",
    ValuationMetricCode.FCF_YIELD: "((OPERATING_CASH_FLOW - CAPITAL_EXPENDITURE) / SPLIT_ALIGNED_DILUTED_SHARES) / RAW_CLOSE",
}


def not_meaningful_reason(metric: ValuationMetricCode, snapshot: ValuationSnapshot | None) -> str | None:
    """User-ready deterministic explanation; never expose calculation errors."""
    if snapshot is None or snapshot.state is not ValuationState.NOT_MEANINGFUL:
        return None
    if metric is ValuationMetricCode.PE_RATIO:
        return "Diluted EPS is non-positive, so P/E is not meaningful"
    if metric is ValuationMetricCode.PS_RATIO:
        return "Sales per share is non-positive, so P/S is not meaningful"
    if metric in {ValuationMetricCode.P_FCF_RATIO, ValuationMetricCode.FCF_YIELD}:
        return "Free cash flow is non-positive, so this valuation measure is not meaningful"
    return None


@dataclass(frozen=True, slots=True)
class ValuationSnapshot:
    metric: ValuationMetricCode
    state: ValuationState
    fiscal_year: int
    availability_date: date
    price_date: date
    price: Decimal
    value: Decimal | None
    formula: str
    sec_inputs: tuple[FinancialObservation, ...]
    split_actions: tuple[CorporateAction, ...]


@dataclass(frozen=True, slots=True)
class ValuationContext:
    current: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None
    median: Decimal | None
    relative_to_median: Decimal | None
    observation_count: int
    interpretation_available: bool


def split_factor(actions: list[CorporateAction], availability: date, price_date: date) -> tuple[Decimal, tuple[CorporateAction, ...]]:
    applied = tuple(
        item for item in actions
        if item.action_type is CorporateActionType.SPLIT and item.split_ratio is not None
        and availability < item.event_date <= price_date
    )
    factor = Decimal(1)
    for item in applied:
        factor *= item.split_ratio  # type: ignore[operator]
    return factor, applied


def _calculate(
    metric: ValuationMetricCode, price: Decimal, values: dict[MetricCode, Decimal], factor: Decimal,
) -> tuple[ValuationState, Decimal | None]:
    if price <= 0:
        return ValuationState.UNAVAILABLE, None
    if metric is ValuationMetricCode.PE_RATIO:
        eps = values[MetricCode.DILUTED_EPS] / factor
        return (ValuationState.NOT_MEANINGFUL, None) if eps <= 0 else (ValuationState.AVAILABLE, price / eps)
    shares = values[MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES] * factor
    if shares <= 0:
        return ValuationState.UNAVAILABLE, None
    if metric is ValuationMetricCode.PS_RATIO:
        sales_per_share = values[MetricCode.REVENUE] / shares
        return (ValuationState.NOT_MEANINGFUL, None) if sales_per_share <= 0 else (ValuationState.AVAILABLE, price / sales_per_share)
    fcf = values[MetricCode.OPERATING_CASH_FLOW] - values[MetricCode.CAPITAL_EXPENDITURE]
    fcf_per_share = fcf / shares
    if metric is ValuationMetricCode.P_FCF_RATIO:
        if fcf <= 0:
            return ValuationState.NOT_MEANINGFUL, None
        return ValuationState.AVAILABLE, price / fcf_per_share
    return ValuationState.AVAILABLE, fcf_per_share / price


def annual_snapshots(
    metric: ValuationMetricCode, financials: list[FinancialObservation],
    prices: list[MarketDailyObservation], actions: list[CorporateAction],
) -> list[ValuationSnapshot]:
    required = VALUATION_INPUTS[metric]
    by_year: dict[int, dict[MetricCode, FinancialObservation]] = {}
    for item in financials:
        if item.metric in required:
            by_year.setdefault(item.fiscal_year, {})[item.metric] = item
    ordered_prices = sorted(prices, key=lambda item: item.trading_date)
    result: list[ValuationSnapshot] = []
    for year, indexed in sorted(by_year.items()):
        if any(code not in indexed for code in required):
            continue
        inputs = tuple(indexed[code] for code in required)
        if any(item.filing_date is None for item in inputs):
            continue
        availability = max(item.filing_date for item in inputs if item.filing_date is not None)
        price_item = next((item for item in ordered_prices if item.trading_date > availability), None)
        # A limited provider plan may begin months or years after a filing. In
        # that case the first *stored* row is not certifiably the first market
        # session after availability, so omit the historical point.
        if price_item is None or (price_item.trading_date - availability).days > 7:
            continue
        factor, applied = split_factor(actions, availability, price_item.trading_date)
        state, value = _calculate(metric, price_item.close, {item.metric: item.value for item in inputs}, factor)
        result.append(ValuationSnapshot(
            metric, state, year, availability, price_item.trading_date, price_item.close,
            value, VALUATION_FORMULAS[metric], inputs, applied,
        ))
    return result


def current_snapshot(
    metric: ValuationMetricCode, financials: list[FinancialObservation],
    prices: list[MarketDailyObservation], actions: list[CorporateAction],
) -> ValuationSnapshot | None:
    if not prices:
        return None
    latest_price = max(prices, key=lambda item: item.trading_date)
    required = VALUATION_INPUTS[metric]
    by_year: dict[int, dict[MetricCode, FinancialObservation]] = {}
    for item in financials:
        if item.metric in required:
            by_year.setdefault(item.fiscal_year, {})[item.metric] = item
    eligible: list[tuple[int, date, tuple[FinancialObservation, ...]]] = []
    for year, indexed in by_year.items():
        if any(code not in indexed for code in required):
            continue
        inputs = tuple(indexed[code] for code in required)
        if any(item.filing_date is None for item in inputs):
            continue
        availability = max(item.filing_date for item in inputs if item.filing_date is not None)
        if availability < latest_price.trading_date:
            eligible.append((year, availability, inputs))
    if not eligible:
        return None
    year, availability, inputs = max(eligible, key=lambda item: (item[0], item[1]))
    factor, applied = split_factor(actions, availability, latest_price.trading_date)
    state, value = _calculate(metric, latest_price.close, {item.metric: item.value for item in inputs}, factor)
    return ValuationSnapshot(
        metric, state, year, availability, latest_price.trading_date,
        latest_price.close, value, VALUATION_FORMULAS[metric], inputs, applied,
    )


def valuation_context(current: ValuationSnapshot | None, history: list[ValuationSnapshot]) -> ValuationContext:
    usable = [item for item in history if item.state is ValuationState.AVAILABLE and item.value is not None]
    values = sorted(item.value for item in usable[-5:] if item.value is not None)
    if not values:
        return ValuationContext(current.value if current else None, None, None, None, None, 0, False)
    count = len(values)
    midpoint = count // 2
    median = values[midpoint] if count % 2 else (values[midpoint - 1] + values[midpoint]) / Decimal(2)
    current_value = current.value if current and current.state is ValuationState.AVAILABLE else None
    relative = current_value / median - Decimal(1) if current_value is not None and median != 0 else None
    return ValuationContext(current_value, values[0], values[-1], median, relative, count, count >= 3)


def valuation_input_ids(snapshot: ValuationSnapshot) -> tuple[str, ...]:
    return tuple(observation_id(item) for item in snapshot.sec_inputs)
