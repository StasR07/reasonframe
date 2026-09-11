"""Developer-facing idempotent SEC and macro refresh commands."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from .macro import MACRO_CATALOG, FredProvider, MacroObservation, MacroProvider, MacroSeriesDefinition
from .market import (
    CorporateAction, CorporateActionType, MarketDailyObservation, MarketDataProvider,
    MarketProviderError,
)
from .metrics import METRICS
from .models import FiscalPeriod, PeriodKind
from .sec.edgar_adapter import EdgarAdapter, ObservationUnavailable
from .storage import Company, SQLiteStore
from .tiingo import TiingoProvider

logger = logging.getLogger(__name__)


def _refresh_company(store: SQLiteStore, ticker: str, allow_candidates: bool) -> Company | None:
    """Resolve a registry row; explicit developer refresh may stage a manifest candidate."""
    company = store.company(ticker, include_nonpass=allow_candidates)
    if company is not None:
        return company
    from .companies import CANDIDATE_BY_TICKER
    candidate = CANDIDATE_BY_TICKER.get(ticker)
    if candidate is None:
        return None
    store.seed_candidates()
    return store.company(ticker, include_nonpass=True)


def refresh_sec(
    store: SQLiteStore,
    tickers: list[str],
    adapter: EdgarAdapter | None = None,
    start_year: int = 2019,
    end_year: int | None = None,
    *, allow_candidates: bool = False,
) -> dict[str, str]:
    adapter = adapter or EdgarAdapter()
    end_year = end_year or date.today().year + 1
    outcomes: dict[str, str] = {}
    for raw_ticker in tickers:
        ticker = raw_ticker.strip().upper()
        company = _refresh_company(store, ticker, allow_candidates)
        if company is None:
            outcomes[ticker] = "UNSUPPORTED"
            continue
        run_id = store.start_ingestion(f"SEC:{ticker}")
        observations = []
        failures = 0
        for year in range(start_year, end_year + 1):
            for metric, definition in METRICS.items():
                try:
                    if definition.period_kind is PeriodKind.INSTANT:
                        item = adapter.instant_observation(ticker, metric, year)
                    else:
                        item = adapter.annual_observation(ticker, metric, year)
                    observations.append(item)
                except ObservationUnavailable:
                    pass
                except Exception as exc:
                    failures += 1
                    logger.warning(
                        "SEC ingestion observation failed ticker=%s stage=annual metric=%s fiscal_year=%s failure_category=%s",
                        ticker, metric.value, year, type(exc).__name__,
                    )
                if definition.period_kind is PeriodKind.DURATION:
                    for quarter in (FiscalPeriod.Q1, FiscalPeriod.Q2, FiscalPeriod.Q3, FiscalPeriod.Q4):
                        try:
                            item = adapter.quarterly_observation(ticker, metric, year, quarter)
                            observations.append(item)
                        except ObservationUnavailable:
                            pass
                        except Exception as exc:
                            failures += 1
                            logger.warning(
                                "SEC ingestion observation failed ticker=%s stage=quarterly metric=%s fiscal_year=%s fiscal_period=%s failure_category=%s",
                                ticker, metric.value, year, quarter.value, type(exc).__name__,
                            )
                elif hasattr(adapter, "quarterly_instant_observation"):
                    for quarter in (FiscalPeriod.Q1, FiscalPeriod.Q2, FiscalPeriod.Q3, FiscalPeriod.Q4):
                        try:
                            observations.append(
                                adapter.quarterly_instant_observation(ticker, metric, year, quarter)
                            )
                        except ObservationUnavailable:
                            pass
                        except Exception as exc:
                            failures += 1
                            logger.warning(
                                "SEC ingestion observation failed ticker=%s stage=quarterly_balance metric=%s fiscal_year=%s fiscal_period=%s failure_category=%s",
                                ticker, metric.value, year, quarter.value, type(exc).__name__,
                            )
        try:
            if not observations:
                message = (
                    f"stored no observations; {failures} adapter errors"
                    if failures else "stored no observations; no supported SEC facts were returned"
                )
                store.finish_ingestion(run_id, "FAILED", message)
                outcomes[ticker] = f"FAILED: {message}"
                continue
            store.replace_financial_window(
                company, observations, start_year=start_year, end_year=end_year,
            )
            status = "COMPLETED" if failures == 0 else "PARTIAL"
            message = f"upserted {len(observations)} observations; {failures} adapter errors"
            store.finish_ingestion(run_id, status, message)
            outcomes[ticker] = f"{status}: {message}"
        except Exception as exc:
            store.finish_ingestion(run_id, "FAILED", str(exc))
            outcomes[ticker] = f"FAILED: {exc}"
    return outcomes


def refresh_macro(store: SQLiteStore, provider: MacroProvider | None = None) -> dict[str, str]:
    provider = provider or FredProvider()
    outcomes: dict[str, str] = {}
    for definition in MACRO_CATALOG.values():
        run_id = store.start_ingestion(f"FRED:{definition.provider_series_id}")
        try:
            fetched = provider.fetch_series(definition.provider_series_id)
            if fetched.provider_series_id != definition.provider_series_id:
                raise ValueError("provider returned metadata for the wrong series")
            if not fetched.label or not fetched.unit or not fetched.frequency:
                raise ValueError("provider returned incomplete series metadata")
            stored_definition = MacroSeriesDefinition(
                code=definition.code,
                provider=definition.provider,
                provider_series_id=fetched.provider_series_id,
                label=fetched.label,
                unit=fetched.unit,
                frequency=fetched.frequency,
                source_name=definition.source_name,
            )
            observations = [
                MacroObservation(definition.code, observation_date, value)
                for observation_date, value in fetched.observations
            ]
            store.upsert_macro_batch(stored_definition, observations)
            message = f"upserted {len(fetched.observations)} observations"
            store.finish_ingestion(run_id, "COMPLETED", message)
            outcomes[definition.code] = f"COMPLETED: {message}"
        except Exception as exc:
            store.finish_ingestion(run_id, "FAILED", str(exc))
            outcomes[definition.code] = f"FAILED: {exc}"
    return outcomes


def refresh_market(
    store: SQLiteStore, tickers: list[str], provider: MarketDataProvider | None = None,
    *, start_date: date | None = None, end_date: date | None = None, full: bool = False,
    allow_candidates: bool = False,
) -> dict[str, str]:
    end_date = end_date or date.today()
    outcomes: dict[str, str] = {}
    normalized_tickers = list(dict.fromkeys(item.strip().upper() for item in tickers))
    for ticker in normalized_tickers:
        company = _refresh_company(store, ticker, allow_candidates)
        if company is None:
            outcomes[ticker] = "UNSUPPORTED"
            continue
    if provider is None:
        try:
            provider = TiingoProvider.from_environment()
        except MarketProviderError as exc:
            for ticker in normalized_tickers:
                if _refresh_company(store, ticker, allow_candidates) is not None:
                    outcomes[ticker] = f"FAILED [{exc.code.value}]: {exc}"
            return outcomes
    store.seed_market_instruments(provider.name)
    for ticker in normalized_tickers:
        if _refresh_company(store, ticker, allow_candidates) is None:
            continue
        instrument = store.primary_market_instrument(ticker)
        source = store.provider_market_instrument(instrument.instrument_id, provider.name) if instrument else None
        if instrument is None or source is None:
            outcomes[ticker] = "UNSUPPORTED"
            continue
        run_id = store.start_ingestion(f"{provider.name}:{ticker}")
        try:
            existing = store.market_observations(instrument.instrument_id)
            requested_start = start_date
            if requested_start is None:
                try:
                    ten_year_start = end_date.replace(year=end_date.year - 10)
                except ValueError:
                    ten_year_start = end_date.replace(year=end_date.year - 10, day=28)
                requested_start = ten_year_start if full or not existing else max(existing[-1].trading_date - timedelta(days=10), date(1900, 1, 1))
            previous_action_keys = {(item.action_type, item.event_date, item.split_ratio) for item in store.corporate_actions(instrument.instrument_id)}
            fetched = provider.fetch_eod(source, requested_start, end_date)
            actions = fetched.corporate_actions
            new_split = any(
                item.action_type is CorporateActionType.SPLIT
                and (item.action_type, item.event_date, item.split_ratio) not in previous_action_keys
                for item in actions
            )
            if new_split and existing and not full:
                requested_start = existing[0].trading_date
                fetched = provider.fetch_eod(source, requested_start, end_date)
                actions = fetched.corporate_actions
            bars = fetched.bars
            retrieved_at = datetime.now(timezone.utc).isoformat()
            observations = [MarketDailyObservation(
                    trading_date=item.trading_date, close=item.close, open=item.open, high=item.high, low=item.low,
                    volume=item.volume, adjusted_open=item.adjusted_open, adjusted_high=item.adjusted_high,
                    adjusted_low=item.adjusted_low, adjusted_close=item.adjusted_close,
                    adjusted_volume=item.adjusted_volume, instrument_id=instrument.instrument_id,
                    currency=instrument.currency, source_provider=provider.name, source_symbol=source.provider_symbol,
                    retrieved_at=retrieved_at, ingestion_run_id=run_id,
                ) for item in bars]
            stored_actions = [CorporateAction(
                    action_type=item.action_type, event_date=item.event_date, date_type=item.date_type,
                    split_ratio=item.split_ratio, cash_amount_per_share=item.cash_amount_per_share,
                    currency=item.currency, instrument_id=instrument.instrument_id, source_provider=provider.name,
                    source_symbol=source.provider_symbol, retrieved_at=retrieved_at, ingestion_run_id=run_id,
                ) for item in actions]
            store.upsert_market_batch(
                observations,
                stored_actions,
                authoritative_action_range=(
                    instrument.instrument_id, requested_start, end_date, provider.name,
                ) if full else None,
            )
            message = f"upserted {len(bars)} EOD observations and {len(actions)} actions"
            if new_split and existing and not full:
                message += "; reconciled full stored history"
            store.finish_ingestion(run_id, "COMPLETED", message)
            outcomes[ticker] = f"COMPLETED: {message}"
        except MarketProviderError as exc:
            store.finish_ingestion(run_id, "FAILED", exc.code.value)
            outcomes[ticker] = f"FAILED [{exc.code.value}]: {exc}"
        except Exception as exc:
            store.finish_ingestion(run_id, "FAILED", type(exc).__name__)
            outcomes[ticker] = "FAILED: Market refresh failed due to a local processing error."
    return outcomes


def main() -> int:
    # Developer commands should honor the repository's documented .env file
    # without requiring callers to export every credential manually.
    load_dotenv()
    parser = argparse.ArgumentParser(prog="reasonframe")
    parser.add_argument("command", choices=("sec", "macro", "market"))
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--database", default=os.environ.get("DATABASE_PATH", "finance_terminal.db"))
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    store = SQLiteStore(Path(args.database))
    try:
        if args.command == "sec":
            outcomes = refresh_sec(store, args.tickers or [item.ticker for item in store.companies()])
        elif args.command == "macro":
            outcomes = refresh_macro(store)
        else:
            outcomes = refresh_market(
                store, args.tickers or [item.ticker for item in store.companies()],
                start_date=args.start_date, end_date=args.end_date, full=args.full,
            )
        for code, status in outcomes.items():
            print(f"{code}: {status}")
        return 1 if any(status.startswith("FAILED") for status in outcomes.values()) else 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
