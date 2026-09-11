"""Decimal-safe deterministic company metric calculations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal

from .metrics import DerivedMetricCode, MetricCode
from .models import FinancialObservation
from .series import CalculatedSeriesPoint, observation_id


FORMULAS: dict[DerivedMetricCode, str] = {
    DerivedMetricCode.REVENUE_GROWTH_YOY: "REVENUE / PRIOR_YEAR_REVENUE - 1",
    DerivedMetricCode.GROSS_MARGIN: "GROSS_PROFIT / REVENUE",
    DerivedMetricCode.OPERATING_MARGIN: "OPERATING_INCOME / REVENUE",
    DerivedMetricCode.NET_MARGIN: "NET_INCOME / REVENUE",
    DerivedMetricCode.FREE_CASH_FLOW: "OPERATING_CASH_FLOW - CAPITAL_EXPENDITURE",
    DerivedMetricCode.FCF_MARGIN: "(OPERATING_CASH_FLOW - CAPITAL_EXPENDITURE) / REVENUE",
    DerivedMetricCode.CURRENT_RATIO: "CURRENT_ASSETS / CURRENT_LIABILITIES",
}


def _period_key(observation: FinancialObservation) -> tuple[int, str, object, object]:
    return (
        observation.fiscal_year,
        observation.fiscal_period.value,
        observation.period_start,
        observation.period_end,
    )


def _index(
    observations: Iterable[FinancialObservation],
) -> dict[MetricCode, dict[tuple[int, str, object, object], FinancialObservation]]:
    indexed: dict[
        MetricCode, dict[tuple[int, str, object, object], FinancialObservation]
    ] = defaultdict(dict)
    for observation in observations:
        indexed[observation.metric][_period_key(observation)] = observation
    return indexed


def calculate_company_metric(
    metric: DerivedMetricCode,
    observations: Iterable[FinancialObservation],
) -> list[CalculatedSeriesPoint]:
    """Calculate aligned points; unsafe or incomplete periods are omitted."""
    items = list(observations)
    if not items:
        return []
    indexed = _index(items)

    if metric is DerivedMetricCode.REVENUE_GROWTH_YOY:
        return _growth(indexed.get(MetricCode.REVENUE, {}), metric)

    inputs_by_metric: dict[DerivedMetricCode, tuple[MetricCode, ...]] = {
        DerivedMetricCode.GROSS_MARGIN: (
            MetricCode.GROSS_PROFIT,
            MetricCode.REVENUE,
        ),
        DerivedMetricCode.OPERATING_MARGIN: (
            MetricCode.OPERATING_INCOME,
            MetricCode.REVENUE,
        ),
        DerivedMetricCode.NET_MARGIN: (MetricCode.NET_INCOME, MetricCode.REVENUE),
        DerivedMetricCode.FREE_CASH_FLOW: (
            MetricCode.OPERATING_CASH_FLOW,
            MetricCode.CAPITAL_EXPENDITURE,
        ),
        DerivedMetricCode.FCF_MARGIN: (
            MetricCode.OPERATING_CASH_FLOW,
            MetricCode.CAPITAL_EXPENDITURE,
            MetricCode.REVENUE,
        ),
        DerivedMetricCode.CURRENT_RATIO: (
            MetricCode.CURRENT_ASSETS,
            MetricCode.CURRENT_LIABILITIES,
        ),
    }
    required = inputs_by_metric[metric]
    common_keys = set(indexed.get(required[0], {}))
    for code in required[1:]:
        common_keys &= set(indexed.get(code, {}))

    points: list[CalculatedSeriesPoint] = []
    for key in sorted(common_keys):
        source = [indexed[code][key] for code in required]
        if len({item.unit for item in source}) != 1:
            continue
        denominator = source[-1].value if metric is not DerivedMetricCode.FREE_CASH_FLOW else None
        if denominator is not None and denominator <= 0:
            continue
        if metric is DerivedMetricCode.FREE_CASH_FLOW:
            value = source[0].value - source[1].value
            unit = source[0].unit
        elif metric is DerivedMetricCode.FCF_MARGIN:
            value = (source[0].value - source[1].value) / source[2].value
            unit = "ratio"
        else:
            value = source[0].value / source[1].value
            unit = "ratio"
        first = source[0]
        points.append(
            CalculatedSeriesPoint(
                date=first.period_end,
                value=value,
                unit=unit,
                formula=FORMULAS[metric],
                input_observation_ids=tuple(observation_id(item) for item in source),
                fiscal_year=first.fiscal_year,
                fiscal_period=first.fiscal_period,
            )
        )
    return points


def _growth(
    revenues: dict[tuple[int, str, object, object], FinancialObservation],
    metric: DerivedMetricCode,
) -> list[CalculatedSeriesPoint]:
    comparable: dict[tuple[int, str], FinancialObservation] = {
        (item.fiscal_year, item.fiscal_period.value): item for item in revenues.values()
    }
    points: list[CalculatedSeriesPoint] = []
    for (year, fiscal_period), current in sorted(comparable.items()):
        prior = comparable.get((year - 1, fiscal_period))
        if prior is None or prior.value <= 0:
            continue
        points.append(
            CalculatedSeriesPoint(
                date=current.period_end,
                value=current.value / prior.value - Decimal(1),
                unit="ratio",
                formula=FORMULAS[metric],
                input_observation_ids=(observation_id(current), observation_id(prior)),
                fiscal_year=current.fiscal_year,
                fiscal_period=current.fiscal_period,
            )
        )
    return points
