#!/usr/bin/env python3
"""Compare local legacy Marketstack rows with fresh in-memory Tiingo normalization."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, timedelta
from decimal import Decimal

from dotenv import load_dotenv

from finance_terminal.market import ProviderMarketInstrument
from finance_terminal.storage import SQLiteStore
from finance_terminal.tiingo import TiingoProvider


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="+", help="Canonical company tickers to compare")
    parser.add_argument("--database", default=os.environ.get("DATABASE_PATH", "finance_terminal.db"))
    parser.add_argument("--start-date", type=date.fromisoformat, default=date.today() - timedelta(days=45))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()

    provider = TiingoProvider.from_environment()
    writer = csv.writer(sys.stdout)
    writer.writerow([
        "ticker", "date", "marketstack_raw_close", "tiingo_raw_close", "raw_close_diff",
        "marketstack_adjusted_close", "tiingo_adjusted_close", "adjusted_close_diff",
        "split_event_agreement", "dividend_event_agreement",
    ])
    store = SQLiteStore(args.database)
    try:
        for ticker in dict.fromkeys(item.upper() for item in args.tickers):
            canonical = store.primary_market_instrument(ticker)
            if canonical is None:
                print(f"Skipping unsupported ticker {ticker}", file=sys.stderr)
                continue
            legacy_rows = {
                item.trading_date: item for item in store.market_observations(
                    canonical.instrument_id, args.start_date, args.end_date,
                ) if item.source_provider == "MARKETSTACK"
            }
            legacy_actions = {
                (item.action_type.value, item.event_date) for item in store.corporate_actions(
                    canonical.instrument_id, args.start_date, args.end_date,
                ) if item.source_provider == "MARKETSTACK"
            }
            fresh = provider.fetch_eod(
                ProviderMarketInstrument(canonical.instrument_id, "TIINGO", canonical.symbol, canonical.exchange_mic),
                args.start_date, args.end_date,
            )
            tiingo_rows = {item.trading_date: item for item in fresh.bars}
            tiingo_actions = {(item.action_type.value, item.event_date) for item in fresh.corporate_actions}
            for trading_date in sorted(set(legacy_rows) & set(tiingo_rows)):
                old = legacy_rows[trading_date]
                new = tiingo_rows[trading_date]
                adjusted_diff: Decimal | None = None
                if old.adjusted_close is not None and new.adjusted_close is not None:
                    adjusted_diff = new.adjusted_close - old.adjusted_close
                writer.writerow([
                    ticker, trading_date, old.close, new.close, new.close - old.close,
                    old.adjusted_close, new.adjusted_close, adjusted_diff,
                    (("SPLIT", trading_date) in legacy_actions) == (("SPLIT", trading_date) in tiingo_actions),
                    (("CASH_DIVIDEND", trading_date) in legacy_actions) == (("CASH_DIVIDEND", trading_date) in tiingo_actions),
                ])
    finally:
        store.close()
    print(f"Tiingo requests made: {provider.request_count}; canonical database was not modified.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
