"""Small provider-independent vocabulary for SEC financial metrics."""

from dataclasses import dataclass
from enum import StrEnum

from .models import PeriodKind


class MetricCode(StrEnum):
    REVENUE = "REVENUE"
    COST_OF_REVENUE = "COST_OF_REVENUE"
    GROSS_PROFIT = "GROSS_PROFIT"
    OPERATING_INCOME = "OPERATING_INCOME"
    NET_INCOME = "NET_INCOME"
    BASIC_EPS = "BASIC_EPS"
    DILUTED_EPS = "DILUTED_EPS"
    DILUTED_WEIGHTED_AVERAGE_SHARES = "DILUTED_WEIGHTED_AVERAGE_SHARES"
    OPERATING_CASH_FLOW = "OPERATING_CASH_FLOW"
    CAPITAL_EXPENDITURE = "CAPITAL_EXPENDITURE"
    SHARE_REPURCHASES = "SHARE_REPURCHASES"
    DIVIDENDS_PAID = "DIVIDENDS_PAID"
    CASH_AND_CASH_EQUIVALENTS = "CASH_AND_CASH_EQUIVALENTS"
    SHORT_TERM_INVESTMENTS = "SHORT_TERM_INVESTMENTS"
    CURRENT_ASSETS = "CURRENT_ASSETS"
    CURRENT_LIABILITIES = "CURRENT_LIABILITIES"
    TOTAL_ASSETS = "TOTAL_ASSETS"
    TOTAL_LIABILITIES = "TOTAL_LIABILITIES"
    SHAREHOLDERS_EQUITY = "SHAREHOLDERS_EQUITY"
    CURRENT_PORTION_LONG_TERM_DEBT = "CURRENT_PORTION_LONG_TERM_DEBT"
    LONG_TERM_DEBT_NONCURRENT = "LONG_TERM_DEBT_NONCURRENT"


class DerivedMetricCode(StrEnum):
    REVENUE_GROWTH_YOY = "REVENUE_GROWTH_YOY"
    GROSS_MARGIN = "GROSS_MARGIN"
    OPERATING_MARGIN = "OPERATING_MARGIN"
    NET_MARGIN = "NET_MARGIN"
    FREE_CASH_FLOW = "FREE_CASH_FLOW"
    FCF_MARGIN = "FCF_MARGIN"
    CURRENT_RATIO = "CURRENT_RATIO"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    code: MetricCode
    label: str
    category: str
    period_kind: PeriodKind
    unit_kind: str
    derivation_policy: str | None = None


def _metric(
    code: MetricCode,
    label: str,
    category: str,
    period_kind: PeriodKind,
    unit_kind: str = "currency",
    derivation_policy: str | None = None,
) -> MetricDefinition:
    return MetricDefinition(code, label, category, period_kind, unit_kind, derivation_policy)


METRICS: dict[MetricCode, MetricDefinition] = {
    MetricCode.REVENUE: _metric(MetricCode.REVENUE, "Revenue", "income", PeriodKind.DURATION, derivation_policy="quarter_from_ytd"),
    MetricCode.COST_OF_REVENUE: _metric(MetricCode.COST_OF_REVENUE, "Cost of revenue", "income", PeriodKind.DURATION, derivation_policy="quarter_from_ytd"),
    MetricCode.GROSS_PROFIT: _metric(MetricCode.GROSS_PROFIT, "Gross profit", "income", PeriodKind.DURATION, derivation_policy="quarter_from_ytd"),
    MetricCode.OPERATING_INCOME: _metric(MetricCode.OPERATING_INCOME, "Operating income", "income", PeriodKind.DURATION, derivation_policy="quarter_from_ytd"),
    MetricCode.NET_INCOME: _metric(MetricCode.NET_INCOME, "Net income", "income", PeriodKind.DURATION, derivation_policy="quarter_from_ytd"),
    MetricCode.BASIC_EPS: _metric(MetricCode.BASIC_EPS, "Basic EPS", "income", PeriodKind.DURATION, "currency_per_share"),
    MetricCode.DILUTED_EPS: _metric(MetricCode.DILUTED_EPS, "Diluted EPS", "income", PeriodKind.DURATION, "currency_per_share"),
    MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES: _metric(MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES, "Diluted weighted-average shares", "income", PeriodKind.DURATION, "shares"),
    MetricCode.OPERATING_CASH_FLOW: _metric(MetricCode.OPERATING_CASH_FLOW, "Operating cash flow", "cash_flow", PeriodKind.DURATION, derivation_policy="quarter_from_ytd"),
    MetricCode.CAPITAL_EXPENDITURE: _metric(MetricCode.CAPITAL_EXPENDITURE, "Capital expenditure", "cash_flow", PeriodKind.DURATION, derivation_policy="quarter_from_ytd"),
    MetricCode.SHARE_REPURCHASES: _metric(MetricCode.SHARE_REPURCHASES, "Share repurchases", "cash_flow", PeriodKind.DURATION, derivation_policy="quarter_from_ytd"),
    MetricCode.DIVIDENDS_PAID: _metric(MetricCode.DIVIDENDS_PAID, "Dividends paid", "cash_flow", PeriodKind.DURATION, derivation_policy="quarter_from_ytd"),
    MetricCode.CASH_AND_CASH_EQUIVALENTS: _metric(MetricCode.CASH_AND_CASH_EQUIVALENTS, "Cash and cash equivalents", "balance", PeriodKind.INSTANT),
    MetricCode.SHORT_TERM_INVESTMENTS: _metric(MetricCode.SHORT_TERM_INVESTMENTS, "Short-term investments", "balance", PeriodKind.INSTANT),
    MetricCode.CURRENT_ASSETS: _metric(MetricCode.CURRENT_ASSETS, "Current assets", "balance", PeriodKind.INSTANT),
    MetricCode.CURRENT_LIABILITIES: _metric(MetricCode.CURRENT_LIABILITIES, "Current liabilities", "balance", PeriodKind.INSTANT),
    MetricCode.TOTAL_ASSETS: _metric(MetricCode.TOTAL_ASSETS, "Total assets", "balance", PeriodKind.INSTANT),
    MetricCode.TOTAL_LIABILITIES: _metric(MetricCode.TOTAL_LIABILITIES, "Total liabilities", "balance", PeriodKind.INSTANT),
    MetricCode.SHAREHOLDERS_EQUITY: _metric(MetricCode.SHAREHOLDERS_EQUITY, "Shareholders' equity", "balance", PeriodKind.INSTANT),
    MetricCode.CURRENT_PORTION_LONG_TERM_DEBT: _metric(MetricCode.CURRENT_PORTION_LONG_TERM_DEBT, "Current portion of long-term debt", "balance", PeriodKind.INSTANT),
    MetricCode.LONG_TERM_DEBT_NONCURRENT: _metric(MetricCode.LONG_TERM_DEBT_NONCURRENT, "Long-term debt, noncurrent", "balance", PeriodKind.INSTANT),
}


DERIVED_METRIC_LABELS: dict[DerivedMetricCode, str] = {
    DerivedMetricCode.REVENUE_GROWTH_YOY: "Revenue growth (YoY)",
    DerivedMetricCode.GROSS_MARGIN: "Gross margin",
    DerivedMetricCode.OPERATING_MARGIN: "Operating margin",
    DerivedMetricCode.NET_MARGIN: "Net margin",
    DerivedMetricCode.FREE_CASH_FLOW: "Free cash flow",
    DerivedMetricCode.FCF_MARGIN: "Free cash flow margin",
    DerivedMetricCode.CURRENT_RATIO: "Current ratio",
}

CompanyMetric = MetricCode | DerivedMetricCode
