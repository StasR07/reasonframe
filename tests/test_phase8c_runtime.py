from __future__ import annotations

import asyncio
import json
import stat
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier, BrokenBarrierError, Event

from fastapi.testclient import TestClient

from finance_terminal.ai_provider import ClaudeAgentSDKProvider, FakeAIProvider, PARSER_DEVELOPER_INSTRUCTIONS
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


def test_bootstrap_runs_independent_sources_in_parallel(tmp_path, monkeypatch) -> None:
    paths = RuntimePaths.resolve(tmp_path / "app-data")
    config = RuntimeConfig(paths)
    store = SQLiteStore(paths.database)
    manager = DataSyncManager(store, config)
    rendezvous = Barrier(3)
    simultaneous: list[str] = []

    def run_together(name: str) -> None:
        try:
            rendezvous.wait(timeout=2)
        except BrokenBarrierError:
            return
        simultaneous.append(name)

    monkeypatch.setattr(manager, "_run_sec", lambda *_args, **_kwargs: run_together("sec"))
    monkeypatch.setattr(manager, "_run_market", lambda *_args, **_kwargs: run_together("market"))
    monkeypatch.setattr(manager, "_run_macro", lambda *_args, **_kwargs: run_together("macro"))
    try:
        manager.run_bootstrap()
        assert set(simultaneous) == {"sec", "market", "macro"}
    finally:
        store.close()


def test_bootstrap_endpoint_reports_busy_before_background_thread_starts(tmp_path, monkeypatch) -> None:
    paths = RuntimePaths.resolve(tmp_path / "app-data")
    config = RuntimeConfig(paths)
    store = SQLiteStore(paths.database)
    manager = DataSyncManager(store, config)
    release = Event()

    def blocked_bootstrap(*, _lock_reserved: bool = False, **_kwargs):
        try:
            release.wait(timeout=2)
            return manager.state()
        finally:
            if _lock_reserved:
                manager._run_lock.release()

    monkeypatch.setattr(manager, "run_bootstrap", blocked_bootstrap)
    try:
        with TestClient(create_app(
            store, provider=FakeAIProvider(), runtime_config=config, sync_manager=manager,
        )) as client:
            response = client.post("/api/v1/data/bootstrap")
            assert response.status_code == 200
            assert response.json()["busy"] is True
            assert response.json()["operation"] == "BOOTSTRAP"
            release.set()
    finally:
        release.set()
        store.close()


def test_reserved_refresh_returns_to_idle_when_no_sources_are_stale(tmp_path, monkeypatch) -> None:
    paths = RuntimePaths.resolve(tmp_path / "app-data")
    config = RuntimeConfig(paths)
    store = SQLiteStore(paths.database)
    manager = DataSyncManager(store, config)
    monkeypatch.setattr(manager, "stale_sources", lambda: ())
    try:
        assert manager.reserve("REFRESH") is True
        manager.run_refresh(_lock_reserved=True)
        state = manager.state()
        assert state["busy"] is False
        assert state["operation"] == "IDLE"
    finally:
        store.close()


def test_claude_agent_sdk_invocation_is_bounded_and_uses_only_app_prompt(tmp_path, monkeypatch) -> None:
    import claude_agent_sdk
    from claude_agent_sdk import ResultMessage

    class Credentials:
        def get(self, _name: str) -> str:
            return "sk-ant-oat01-test-subscription-token"

        def set(self, _name: str, _value: str) -> None:
            pass

        def delete(self, _name: str) -> None:
            pass

    provider = ClaudeAgentSDKProvider(tmp_path, credential_store=Credentials())
    captured: dict[str, object] = {}

    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        yield ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="test", structured_output={"answer": "ok"},
        )

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    result = asyncio.run(provider.generate_structured("TASK: parser", {"type": "object"}))
    assert json.loads(result) == {"answer": "ok"}
    options = captured["options"]
    assert captured["prompt"] == "TASK: parser"
    assert options.system_prompt == PARSER_DEVELOPER_INSTRUCTIONS
    assert options.tools == []
    assert options.allowed_tools == []
    assert options.mcp_servers == {}
    assert options.strict_mcp_config is True
    assert options.setting_sources == []
    assert options.skills == []
    assert options.plugins == []
    assert options.extra_args == {"no-session-persistence": None}
    assert options.cli_path.name == "claude"
    assert options.cli_path.parent.name == "_bundled"
    assert options.output_format == {"type": "json_schema", "schema": {"type": "object"}}
    assert options.env["CLAUDE_CODE_OAUTH_TOKEN"].startswith("sk-ant-oat")
    assert options.env["CLAUDE_CONFIG_DIR"].endswith("claude-runtime")
    assert options.env["ANTHROPIC_API_KEY"] == ""
