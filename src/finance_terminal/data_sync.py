"""Resumable, source-isolated local bootstrap and refresh planning."""

from __future__ import annotations

import copy
import os
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable

from .macro import FredProvider, MacroProvider
from .market import MarketDataProvider
from .refresh import refresh_macro, refresh_market, refresh_sec
from .runtime import LocalJSONStore, RuntimeConfig
from .sec.edgar_adapter import EdgarAdapter
from .storage import SQLiteStore
from .tiingo import TiingoProvider


SOURCE_NAMES = ("market", "sec", "macro")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_state() -> dict[str, Any]:
    return {
        "status": "NOT_STARTED",
        "completed": 0,
        "total": 0,
        "last_attempted_refresh": None,
        "last_successful_refresh": None,
        "message": None,
    }


def default_sync_state() -> dict[str, Any]:
    return {
        "version": 1,
        "operation": "IDLE",
        "sources": {name: _source_state() for name in SOURCE_NAMES},
        "market_pending": [],
        "onboarding_ready": False,
    }


class DataSyncManager:
    """One local coordinator; each successful unit is committed and checkpointed."""

    def __init__(
        self,
        store: SQLiteStore,
        config: RuntimeConfig,
        *,
        state_store: LocalJSONStore | None = None,
        market_provider_factory: Callable[[str], MarketDataProvider] = TiingoProvider,
        sec_adapter_factory: Callable[[], EdgarAdapter] = EdgarAdapter,
        macro_provider_factory: Callable[[str | None], MacroProvider] = FredProvider,
    ) -> None:
        self.store = store
        self.config = config
        self.state_store = state_store or LocalJSONStore(config.paths.bootstrap_state, default_sync_state())
        self.market_provider_factory = market_provider_factory
        self.sec_adapter_factory = sec_adapter_factory
        self.macro_provider_factory = macro_provider_factory
        self._run_lock = Lock()
        self.store.seed_certified_registry()
        self._reconcile_sec_checkpoint()

    def _reconcile_sec_checkpoint(self) -> None:
        """Repair persisted SEC success claims that have no matching local rows."""
        state = self.state_store.read()
        state.setdefault("sources", {})
        source = {**_source_state(), **state["sources"].get("sec", {})}
        certified = set(self._certified_tickers())
        loaded = self.store.tickers_with_financial_observations() & certified
        completed = set(source.get("completed_items", [])) & certified
        claimed_complete = source.get("status") == "UP_TO_DATE" or bool(completed)
        if not claimed_complete or (loaded == certified and completed == certified):
            return
        source["completed_items"] = sorted(loaded)
        source["completed"] = len(loaded)
        source["total"] = len(certified)
        source["status"] = "PARTIALLY_READY" if loaded else "NOT_STARTED"
        source["last_successful_refresh"] = None
        source["message"] = (
            f"{len(loaded)} of {len(certified)} companies have verified SEC data; refresh required"
        )
        state["sources"]["sec"] = source
        self.state_store.write(state)

    def state(self) -> dict[str, Any]:
        state = self.state_store.read()
        defaults = default_sync_state()
        state.setdefault("sources", {})
        for name in SOURCE_NAMES:
            state["sources"][name] = {**defaults["sources"][name], **state["sources"].get(name, {})}
        state["busy"] = self._run_lock.locked()
        return copy.deepcopy(state)

    def _save(self, state: dict[str, Any]) -> None:
        persisted = copy.deepcopy(state)
        persisted.pop("busy", None)
        self.state_store.write(persisted)

    def _start_source(self, state: dict[str, Any], name: str, total: int) -> None:
        source = state["sources"][name]
        source.update(status="UPDATING", total=total, last_attempted_refresh=_now(), message=None)
        self._save(state)

    def _finish_source(self, state: dict[str, Any], name: str, *, status: str, message: str) -> None:
        source = state["sources"][name]
        source.update(status=status, message=message)
        if status == "UP_TO_DATE":
            source["last_successful_refresh"] = _now()
        self._save(state)

    def _certified_tickers(self) -> list[str]:
        return [company.ticker for company in self.store.companies()]

    def run_bootstrap(
        self,
        *,
        sources: tuple[str, ...] = ("sec", "market", "macro"),
        sec_adapter: EdgarAdapter | None = None,
        market_provider: MarketDataProvider | None = None,
        macro_provider: MacroProvider | None = None,
    ) -> dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return self.state()
        try:
            state = self.state()
            state["operation"] = "BOOTSTRAP"
            self._save(state)
            tickers = self._certified_tickers()
            if "sec" in sources:
                self._run_safely(state, "sec", lambda: self._run_sec(state, tickers, sec_adapter))
            if "market" in sources:
                self._run_safely(state, "market", lambda: self._run_market(state, tickers, market_provider, full=True))
            if "macro" in sources:
                self._run_safely(state, "macro", lambda: self._run_macro(state, macro_provider))
            source_states = [state["sources"][name]["status"] for name in SOURCE_NAMES]
            state["onboarding_ready"] = any(
                self.store.table_row_count(table) > 0
                for table in ("financial_observations", "market_daily_observations", "macro_observations")
            )
            state["operation"] = "IDLE"
            if all(item == "UP_TO_DATE" for item in source_states):
                state["onboarding_ready"] = True
            self._save(state)
            return self.state()
        finally:
            self._run_lock.release()

    def _run_safely(self, state: dict[str, Any], source_name: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except Exception:
            labels = {"sec": "SEC", "market": "Market-data", "macro": "Macro-data"}
            self._finish_source(
                state, source_name, status="FAILED",
                message=f"{labels[source_name]} refresh could not complete. Existing local data remains available.",
            )

    def _run_sec(self, state: dict[str, Any], tickers: list[str], adapter: EdgarAdapter | None) -> None:
        source = state["sources"]["sec"]
        completed_tickers = set(source.get("completed_items", []))
        pending = [ticker for ticker in tickers if ticker not in completed_tickers]
        self._start_source(state, "sec", len(tickers))
        source["completed"] = len(completed_tickers)
        identity = self.config.edgar_identity()
        if adapter is None and not identity:
            self._finish_source(
                state, "sec", status="NOT_CONFIGURED",
                message="Add an SEC fair-access identity to continue.",
            )
            return
        if adapter is None and identity:
            os.environ["EDGAR_IDENTITY"] = identity
        adapter = adapter or self.sec_adapter_factory()
        failures = 0
        for ticker in pending:
            outcome = refresh_sec(self.store, [ticker], adapter=adapter).get(ticker, "FAILED")
            if outcome.startswith(("COMPLETED", "PARTIAL")):
                completed_tickers.add(ticker)
                source["completed_items"] = sorted(completed_tickers)
                source["completed"] = len(completed_tickers)
            else:
                failures += 1
            self._save(state)
        status = "UP_TO_DATE" if not failures and len(completed_tickers) == len(tickers) else "PARTIALLY_READY"
        self._finish_source(state, "sec", status=status, message=f"{len(completed_tickers)} of {len(tickers)} companies ready")

    def _run_market(
        self, state: dict[str, Any], tickers: list[str], provider: MarketDataProvider | None, *, full: bool
    ) -> None:
        source = state["sources"]["market"]
        completed_tickers = set(source.get("completed_items", [])) if full else set()
        pending = [ticker for ticker in tickers if ticker not in completed_tickers]
        processed_tickers: set[str] = set()
        state["market_pending"] = pending
        self._start_source(state, "market", len(tickers))
        source["completed"] = len(completed_tickers)
        token = self.config.get_secret("tiingo_token", environment_fallback="TIINGO_API_TOKEN")
        if provider is None and not token:
            self._finish_source(state, "market", status="NOT_CONFIGURED", message="Add a Tiingo token to continue.")
            return
        provider = provider or self.market_provider_factory(token or "")
        failures = 0
        for ticker in pending:
            start_date = None
            if not full:
                instrument = self.store.primary_market_instrument(ticker)
                existing = self.store.market_observations(instrument.instrument_id) if instrument else []
                start_date = existing[-1].trading_date + timedelta(days=1) if existing else None
                target = date.today() - timedelta(days=1)
                while target.weekday() >= 5:
                    target -= timedelta(days=1)
                if existing and existing[-1].trading_date >= target:
                    processed_tickers.add(ticker)
                    source["completed"] += 1
                    state["market_pending"] = [item for item in pending if item not in processed_tickers]
                    self._save(state)
                    continue
            outcome = refresh_market(
                self.store, [ticker], provider=provider, full=full, start_date=start_date
            ).get(ticker, "FAILED")
            if "[rate_limited]" in outcome.casefold():
                done = completed_tickers if full else processed_tickers
                state["market_pending"] = [item for item in pending if item not in done]
                self._finish_source(
                    state, "market", status="RATE_LIMITED",
                    message=f"Market data setup paused. {source['completed']} of {len(tickers)} companies are ready. Tiingo's hourly request limit was reached.",
                )
                return
            if outcome.startswith("COMPLETED"):
                processed_tickers.add(ticker)
                if full:
                    completed_tickers.add(ticker)
                    source["completed_items"] = sorted(completed_tickers)
                    source["completed"] = len(completed_tickers)
                else:
                    source["completed"] += 1
            else:
                failures += 1
            done = completed_tickers if full else processed_tickers
            state["market_pending"] = [item for item in pending if item not in done]
            self._save(state)
        status = "UP_TO_DATE" if not failures else "PARTIALLY_READY"
        self._finish_source(state, "market", status=status, message=f"{source['completed']} of {len(tickers)} companies ready")

    def _run_macro(self, state: dict[str, Any], provider: MacroProvider | None) -> None:
        from .macro import MACRO_CATALOG
        self._start_source(state, "macro", len(MACRO_CATALOG))
        key = self.config.fred_api_key()
        if provider is None and not key:
            self._finish_source(state, "macro", status="NOT_CONFIGURED", message="Add a FRED API key to continue.")
            return
        provider = provider or self.macro_provider_factory(key)
        outcomes = refresh_macro(self.store, provider=provider)
        successes = sum(value.startswith("COMPLETED") for value in outcomes.values())
        source = state["sources"]["macro"]
        source["completed"] = successes
        status = "UP_TO_DATE" if successes == len(outcomes) else "PARTIALLY_READY"
        self._finish_source(state, "macro", status=status, message=f"{successes} of {len(outcomes)} series ready")

    @staticmethod
    def _older_than(timestamp: str | None, hours: int) -> bool:
        if not timestamp:
            return True
        try:
            parsed = datetime.fromisoformat(timestamp)
            return datetime.now(timezone.utc) - parsed.astimezone(timezone.utc) >= timedelta(hours=hours)
        except ValueError:
            return True

    def stale_sources(self) -> tuple[str, ...]:
        state = self.state()
        stale: list[str] = []
        for name in ("sec", "macro"):
            if self._older_than(state["sources"][name]["last_successful_refresh"], 24):
                stale.append(name)
        latest_dates = []
        for company in self.store.companies():
            instrument = self.store.primary_market_instrument(company.ticker)
            if instrument:
                value = self.store.market_coverage(instrument.instrument_id)["last_date"]
                if value:
                    latest_dates.append(date.fromisoformat(str(value)))
        target = date.today() - timedelta(days=1)
        while target.weekday() >= 5:
            target -= timedelta(days=1)
        if not latest_dates or min(latest_dates) < target:
            stale.append("market")
        return tuple(stale)

    def run_refresh(self, *, automatic: bool = False) -> dict[str, Any]:
        if automatic and not bool(self.config.settings.read().get("automatic_refresh", True)):
            return self.state()
        sources = self.stale_sources()
        if not sources:
            return self.state()
        if not self._run_lock.acquire(blocking=False):
            return self.state()
        try:
            state = self.state()
            state["operation"] = "REFRESH"
            self._save(state)
            tickers = self._certified_tickers()
            if "sec" in sources:
                # Refresh checks are independent of bootstrap completion checkpoints.
                state["sources"]["sec"].pop("completed_items", None)
                self._run_safely(state, "sec", lambda: self._run_sec(state, tickers, None))
            if "market" in sources:
                self._run_safely(state, "market", lambda: self._run_market(state, tickers, None, full=False))
            if "macro" in sources:
                self._run_safely(state, "macro", lambda: self._run_macro(state, None))
            state["operation"] = "IDLE"
            self._save(state)
            return self.state()
        finally:
            self._run_lock.release()

    def public_status(self) -> dict[str, Any]:
        state = self.state()
        coverages = []
        for company in self.store.companies():
            instrument = self.store.primary_market_instrument(company.ticker)
            if instrument:
                coverages.append(self.store.market_coverage(instrument.instrument_id))
        dates = [str(item["last_date"]) for item in coverages if item["last_date"]]
        state["latest_market_date"] = max(dates) if dates else None
        state["market_ready"] = sum(bool(item["observation_count"]) for item in coverages)
        state["market_total"] = len(coverages)
        state["tiingo_configured"] = self.config.has_secret("tiingo_token", environment_fallback="TIINGO_API_TOKEN")
        state["fred_configured"] = self.config.has_fred_api_key()
        state["sec_configured"] = self.config.has_edgar_identity()
        state["automatic_refresh"] = bool(self.config.settings.read().get("automatic_refresh", True))
        return state
