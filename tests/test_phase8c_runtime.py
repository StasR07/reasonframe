from __future__ import annotations

import asyncio
import json
import stat
from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from finance_terminal.ai_provider import ClaudeCodeProvider
from finance_terminal.api import create_app
from finance_terminal.data_sync import DataSyncManager, default_sync_state
from finance_terminal.market import (
    FetchedDailyBar, FetchedMarketData, MarketProviderError, MarketProviderErrorCode,
)
from finance_terminal.runtime import RuntimeConfig, RuntimePaths
from finance_terminal.storage import SQLiteStore


class RateLimitedMarket:
    name = "TIINGO"

    def __init__(self, limit: int | None = None) -> None:
        self.limit = limit
        self.calls: list[str] = []

    def fetch_eod(self, instrument, start_date, end_date):
        del start_date, end_date
        self.calls.append(instrument.provider_symbol)
        if self.limit is not None and len(self.calls) > self.limit:
            raise MarketProviderError(MarketProviderErrorCode.RATE_LIMITED, "quota")
        return FetchedMarketData((FetchedDailyBar(date.today() - timedelta(days=1), Decimal("100")),), ())


def test_secret_storage_is_owner_only_and_public_status_never_returns_value(tmp_path) -> None:
    paths = RuntimePaths.resolve(tmp_path / "app-data")
    config = RuntimeConfig(paths)
    config.set_secret("tiingo_token", "super-secret-value")
    assert config.get_secret("tiingo_token") == "super-secret-value"
    assert stat.S_IMODE(paths.secrets.stat().st_mode) == 0o600

    store = SQLiteStore(paths.database)
    try:
        with TestClient(create_app(store, runtime_config=config)) as client:
            body = client.get("/api/v1/runtime").json()
            assert body["data"]["tiingo_configured"] is True
            assert "super-secret-value" not in json.dumps(body)
            catalog = client.get("/api/v1/catalog").json()
            assert catalog["provider_connected"] is True
            assert "super-secret-value" not in json.dumps(catalog)
    finally:
        store.close()


def test_data_source_credentials_are_local_secrets_and_never_public(tmp_path, monkeypatch) -> None:
    paths = RuntimePaths.resolve(tmp_path / "app-data")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)

    config = RuntimeConfig(paths)
    config.set_secret("fred_api_key", "local-fred-key")
    config.set_secret("edgar_identity", "Desktop User contact@example.com")
    assert config.fred_api_key() == "local-fred-key"
    assert config.has_fred_api_key() is True
    assert config.edgar_identity() == "Desktop User contact@example.com"
    store = SQLiteStore(paths.database)
    try:
        with TestClient(create_app(store, runtime_config=config)) as client:
            payload = client.get("/api/v1/runtime").json()
            assert payload["data"]["fred_configured"] is True
            assert payload["data"]["sec_configured"] is True
            serialized = json.dumps(payload)
            assert "local-fred-key" not in serialized
            assert "contact@example.com" not in serialized
    finally:
        store.close()


def test_false_sec_success_checkpoint_is_repaired_when_database_has_no_fundamentals(tmp_path) -> None:
    paths = RuntimePaths.resolve(tmp_path / "app-data")
    config = RuntimeConfig(paths)
    state = default_sync_state()
    state["sources"]["sec"].update({
        "status": "UP_TO_DATE",
        "completed": 50,
        "completed_items": ["AAPL", "MSFT"],
        "last_successful_refresh": "2026-09-08T09:20:33+00:00",
    })
    config_state = config.paths.bootstrap_state
    config_state.write_text(json.dumps(state), encoding="utf-8")
    store = SQLiteStore(paths.database)
    try:
        repaired = DataSyncManager(store, config).state()["sources"]["sec"]
        assert repaired["status"] == "NOT_STARTED"
        assert repaired["completed"] == 0
        assert repaired["completed_items"] == []
        assert repaired["last_successful_refresh"] is None
    finally:
        store.close()


def test_market_bootstrap_stops_on_429_and_resumes_only_pending_items(tmp_path) -> None:
    paths = RuntimePaths.resolve(tmp_path / "app-data")
    config = RuntimeConfig(paths)
    config.set_secret("tiingo_token", "test-token")
    store = SQLiteStore(paths.database)
    manager = DataSyncManager(store, config)
    try:
        limited = RateLimitedMarket(limit=2)
        paused = manager.run_bootstrap(sources=("market",), market_provider=limited)
        assert paused["sources"]["market"]["status"] == "RATE_LIMITED"
        assert paused["sources"]["market"]["completed"] == 2
        assert len(limited.calls) == 3
        assert len(paused["market_pending"]) == 48

        resumed_provider = RateLimitedMarket()
        resumed = manager.run_bootstrap(sources=("market",), market_provider=resumed_provider)
        assert resumed["sources"]["market"]["status"] == "UP_TO_DATE"
        assert resumed["sources"]["market"]["completed"] == 50
        assert len(resumed_provider.calls) == 48
        assert resumed["market_pending"] == []

        same_day_provider = RateLimitedMarket()
        manager.market_provider_factory = lambda _token: same_day_provider
        manager.stale_sources = lambda: ("market",)  # type: ignore[method-assign]
        manager.run_refresh()
        assert same_day_provider.calls == []
    finally:
        store.close()


def test_claude_adapter_invocation_removes_tools_mcp_and_session_persistence(tmp_path) -> None:
    provider = ClaudeCodeProvider(tmp_path)
    captured: list[str] = []

    async def fake_run(*arguments: str, timeout: float):
        del timeout
        captured.extend(arguments)
        return 0, json.dumps({"structured_output": {"answer": "ok"}})

    provider._run = fake_run  # type: ignore[method-assign]
    result = asyncio.run(provider.generate_structured("TASK: parser", {"type": "object"}))
    assert json.loads(result) == {"answer": "ok"}
    assert captured[captured.index("--tools") + 1] == ""
    assert captured[captured.index("--disallowedTools") + 1] == "mcp__*"
    assert "--strict-mcp-config" in captured
    assert "--no-session-persistence" in captured
