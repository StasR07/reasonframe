import os
from decimal import Decimal

import pytest

from finance_terminal.metrics import MetricCode
from finance_terminal.models import FiscalPeriod
from finance_terminal.sec.edgar_adapter import EdgarAdapter
from finance_terminal.sec.validate import (
    DEFAULT_AAPL_FIXTURE,
    _aapl_fy2025_expectation,
    _aapl_fy2025_revenue_expectation,
    _fixture_expectation,
)


pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("EDGAR_IDENTITY"), reason="SEC identity not configured")
def test_aapl_fy2025_revenue_live() -> None:
    expected = _aapl_fy2025_revenue_expectation()
    observation = EdgarAdapter().annual_observation("AAPL", MetricCode.REVENUE, 2025)

    assert str(observation.value) == expected["value"]
    assert observation.accession_number
    assert observation.source_concept


@pytest.mark.skipif(not os.environ.get("EDGAR_IDENTITY"), reason="SEC identity not configured")
def test_aapl_fy2025_total_assets_live() -> None:
    expected = _aapl_fy2025_expectation(MetricCode.TOTAL_ASSETS)
    observation = EdgarAdapter().instant_observation(
        "AAPL", MetricCode.TOTAL_ASSETS, 2025
    )

    assert str(observation.value) == expected["value"]
    assert observation.period_end.isoformat() == expected["period"]["end_date"]


@pytest.mark.skipif(not os.environ.get("EDGAR_IDENTITY"), reason="SEC identity not configured")
def test_aapl_q4_fy2025_revenue_live() -> None:
    expected = _fixture_expectation(
        DEFAULT_AAPL_FIXTURE, MetricCode.REVENUE, 2025, "Q4"
    )
    observation = EdgarAdapter().quarterly_observation(
        "AAPL", MetricCode.REVENUE, 2025, FiscalPeriod.Q4
    )

    assert observation.value == Decimal(expected["value"])
    assert len(observation.derivation_sources) == 2
