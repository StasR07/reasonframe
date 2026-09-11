from finance_terminal.metrics import METRICS, MetricCode
from finance_terminal.models import PeriodKind


def test_registry_contains_exact_canonical_vocabulary() -> None:
    assert set(METRICS) == set(MetricCode)


def test_revenue_is_a_duration_metric() -> None:
    definition = METRICS[MetricCode.REVENUE]
    assert definition.period_kind is PeriodKind.DURATION
    assert definition.unit_kind == "currency"

