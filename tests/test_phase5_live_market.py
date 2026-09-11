"""Optional credential-gated Tiingo shape smoke tests (one EOD request per case)."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from finance_terminal.market import ProviderMarketInstrument
from finance_terminal.tiingo import TiingoProvider


pytestmark = pytest.mark.live_market


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TIINGO") != "1" or not os.environ.get("TIINGO_API_TOKEN"),
    reason="Set RUN_LIVE_TIINGO=1 and TIINGO_API_TOKEN for the Tiingo smoke test",
)
@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "NVDA", "GOOGL"])
def test_live_tiingo_eod_shape(ticker: str) -> None:
    end = date.today()
    provider = TiingoProvider(os.environ["TIINGO_API_TOKEN"])
    result = provider.fetch_eod(
        ProviderMarketInstrument(f"us-xnas-{ticker.lower()}", "TIINGO", ticker, "XNAS"),
        end - timedelta(days=14), end,
    )
    assert result.bars
    assert all(item.close > 0 for item in result.bars)
    assert all(result.bars[index].trading_date < result.bars[index + 1].trading_date for index in range(len(result.bars) - 1))
    assert provider.request_count == 1


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TIINGO") != "1" or not os.environ.get("TIINGO_API_TOKEN"),
    reason="Set RUN_LIVE_TIINGO=1 and TIINGO_API_TOKEN for the Tiingo smoke test",
)
def test_live_tiingo_known_nvda_split_from_eod() -> None:
    provider = TiingoProvider(os.environ["TIINGO_API_TOKEN"])
    result = provider.fetch_eod(
        ProviderMarketInstrument("us-xnas-nvda", "TIINGO", "NVDA", "XNAS"),
        date(2024, 6, 8), date(2024, 6, 12),
    )
    assert any(item.event_date == date(2024, 6, 10) and item.split_ratio == 10 for item in result.corporate_actions)
    assert provider.request_count == 1
