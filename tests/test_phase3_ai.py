from __future__ import annotations

import asyncio
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from finance_terminal.ai import (
    AskService,
    ClarificationIntent,
    codex_output_schema,
    CompanyIntent,
    FactualClaim,
    FactualSummary,
    MacroIntent,
    ParsedIntentEnvelope,
    UnsupportedIntent,
    UnsupportedReason,
    ResultShape,
    classify_result,
    validate_summary,
)
from finance_terminal.ai_provider import (
    AIDisconnectedError,
    AIUnavailableError,
    AIUsageLimitError,
    CodexSubscriptionProvider,
    FakeAIProvider,
    AIModel,
    select_preferred_model,
)
from finance_terminal.api import create_app
from finance_terminal.macro import MACRO_CATALOG, MacroObservation
from finance_terminal.metrics import DerivedMetricCode, MetricCode
from finance_terminal.models import DerivationKind, DurationScope, FinancialObservation, FiscalPeriod, PeriodKind
from finance_terminal.query import CompanyQuery, QueryOperation
from finance_terminal.storage import Company, SQLiteStore


def fact(ticker: str, metric: MetricCode, value: str, year: int, period: FiscalPeriod = FiscalPeriod.FY) -> FinancialObservation:
    end = date(year, 12, 31) if period is FiscalPeriod.FY else date(year, {FiscalPeriod.Q1: 3, FiscalPeriod.Q2: 6, FiscalPeriod.Q3: 9, FiscalPeriod.Q4: 12}[period], 28)
    return FinancialObservation(
        ticker=ticker, cik={"AAPL": "1", "MSFT": "2", "NVDA": "3", "GOOGL": "4"}[ticker],
        metric=metric, value=Decimal(value), unit="USD", currency="USD", period_kind=PeriodKind.DURATION,
        period_start=date(year - 1, 1, 1) if period is FiscalPeriod.FY else date(year, max(1, end.month - 2), 1),
        period_end=end, fiscal_year=year, fiscal_period=period,
        duration_scope=DurationScope.ANNUAL if period is FiscalPeriod.FY else DurationScope.STANDALONE_QUARTER,
        derivation=DerivationKind.DIRECT, source_concept=f"us-gaap:{metric.value}",
        accession_number=f"{ticker}-{year}-{period.value}", filing_form="10-K", filing_date=end,
    )


@pytest.fixture
def phase3_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "phase3.db")
    for ticker, cik, name in (
        ("AAPL", "1", "Apple Inc."), ("MSFT", "2", "Microsoft Corporation"),
        ("NVDA", "3", "NVIDIA Corporation"), ("GOOGL", "4", "Alphabet Inc."),
    ):
        store.upsert_company(Company(ticker, cik, name))
    for item in (
        fact("AAPL", MetricCode.REVENUE, "100", 2020), fact("AAPL", MetricCode.REVENUE, "120", 2021),
        fact("MSFT", MetricCode.REVENUE, "80", 2021), fact("NVDA", MetricCode.REVENUE, "100", 2019),
        fact("NVDA", MetricCode.OPERATING_INCOME, "20", 2019),
        fact("AAPL", MetricCode.REVENUE, "25", 2021, FiscalPeriod.Q1),
        fact("MSFT", MetricCode.REVENUE, "30", 2021, FiscalPeriod.Q1),
    ):
        store.upsert_financial_observation(item)
    store.upsert_macro_series(MACRO_CATALOG["US_HEADLINE_CPI"])
    store.upsert_macro_observation(MacroObservation("US_HEADLINE_CPI", date(2010, 1, 1), Decimal("100")))
    store.upsert_macro_observation(MacroObservation("US_HEADLINE_CPI", date(2011, 1, 1), Decimal("102")))
    yield store
    store.close()


def envelope(intent) -> ParsedIntentEnvelope:
    return ParsedIntentEnvelope(intent=intent)


def test_codex_schema_requires_every_declared_object_property() -> None:
    schema = codex_output_schema(ParsedIntentEnvelope)
    assert schema["required"] == ["intent"]
    for definition in schema["$defs"].values():
        if "properties" in definition:
            assert set(definition["required"]) == set(definition["properties"])
            assert definition["additionalProperties"] is False
    rendered = json.dumps(schema)
    assert '"default"' not in rendered
    assert '"oneOf"' not in rendered
    assert '"discriminator"' not in rendered


def run(coro):
    return asyncio.run(coro)


def test_fake_provider_parses_company_and_macro_and_executes_deterministically(phase3_store: SQLiteStore) -> None:
    company_provider = FakeAIProvider([envelope(CompanyIntent(
        tickers=["NVDA"], metric=DerivedMetricCode.OPERATING_MARGIN, start_year=2019,
    ))])
    response = run(AskService(phase3_store, company_provider, summaries=False).ask("Show Nvidia operating margin since 2019"))
    assert response.status == "success"
    assert response.results.series[0].observations[0].value == Decimal("0.2")
    assert company_provider.generate_calls == 1

    macro_provider = FakeAIProvider([envelope(MacroIntent(
        series=["US_INFLATION_YOY"], start_date=date(2011, 1, 1), end_date=date(2011, 12, 31),
    ))])
    macro = run(AskService(phase3_store, macro_provider, summaries=False).ask("Show inflation in 2011"))
    assert macro.status == "success"
    assert macro.validated_query.domain == "macro"


def test_discovered_model_preference_and_safe_stale_fallback() -> None:
    luna = AIModel(id="actual-luna-id", name="GPT-5.6-Luna")
    terra = AIModel(id="actual-terra-id", name="GPT-5.6-Terra")
    account_default = AIModel(id="account-default", name="Account model", is_default=True)
    assert select_preferred_model([account_default, terra, luna]) == "actual-terra-id"
    assert select_preferred_model([account_default, terra, luna], summary=True) == "actual-luna-id"
    assert select_preferred_model([account_default, terra]) == "actual-terra-id"
    assert select_preferred_model([account_default], "stale-id") == "account-default"
    assert select_preferred_model([account_default, terra], "account-default") == "account-default"


def test_result_shape_classifier(phase3_store: SQLiteStore) -> None:
    engine = AskService(phase3_store, FakeAIProvider(), summaries=False).engine
    time_query = CompanyQuery(tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual")
    assert classify_result(time_query, engine.execute(time_query)) is ResultShape.TIME_SERIES
    scalar_query = time_query.model_copy(update={"start_year": 2021, "end_year": 2021})
    assert classify_result(scalar_query, engine.execute(scalar_query)) is ResultShape.SINGLE_SCALAR
    average_query = time_query.model_copy(update={"operation": QueryOperation.AVERAGE})
    assert classify_result(average_query, engine.execute(average_query)) is ResultShape.AGGREGATE_SCALAR
    comparison_query = time_query.model_copy(update={"tickers": ["AAPL", "MSFT"], "start_year": 2021, "end_year": 2021})
    assert classify_result(comparison_query, engine.execute(comparison_query)) is ResultShape.SINGLE_PERIOD_COMPARISON
    multi_query = time_query.model_copy(update={"tickers": ["AAPL", "MSFT"]})
    assert classify_result(multi_query, engine.execute(multi_query)) is ResultShape.MULTI_SERIES_TIME_SERIES


def test_unsupported_and_clarification_are_first_class(phase3_store: SQLiteStore) -> None:
    provider = FakeAIProvider([
        envelope(UnsupportedIntent(reason_code=UnsupportedReason.REQUIRES_MARKET_DATA, message="Historical P/E requires market data.")),
        envelope(ClarificationIntent(message="Which margin?", tickers=["NVDA"], choices=[
            DerivedMetricCode.OPERATING_MARGIN, DerivedMetricCode.NET_MARGIN,
        ])),
    ])
    service = AskService(phase3_store, provider, summaries=False)
    unsupported = run(service.ask("Show Nvidia P/E"))
    assert unsupported.status == "unsupported"
    assert unsupported.reason_code == "REQUIRES_MARKET_DATA"
    clarified = run(service.ask("Show Nvidia margin"))
    assert clarified.status == "clarification"
    assert [choice.label for choice in clarified.choices] == ["Operating margin"]


@pytest.mark.parametrize("intent,reason", [
    (CompanyIntent(tickers=["TSLA", "AAPL"], metric=MetricCode.REVENUE), "UNSUPPORTED_ENTITY"),
    (CompanyIntent(tickers=["AAPL"], metric=DerivedMetricCode.OPERATING_MARGIN, frequency="quarterly"), "INVALID_COMBINATION"),
    (CompanyIntent(tickers=["AAPL", "MSFT", "NVDA", "GOOGL", "AAPL"], metric=MetricCode.REVENUE), "INVALID_COMBINATION"),
    (CompanyIntent(tickers=["AAPL"], metric=MetricCode.REVENUE, operation=QueryOperation.QOQ_GROWTH), "INVALID_COMBINATION"),
    (CompanyIntent(tickers=["AAPL", "MSFT"], metric=MetricCode.REVENUE, operation=QueryOperation.AVERAGE), "INVALID_COMBINATION"),
    (MacroIntent(series=["NOT_A_SERIES"]), "UNSUPPORTED_METRIC"),
])
def test_catalog_rejects_schema_valid_but_semantically_invalid_intents(phase3_store, intent, reason) -> None:
    service = AskService(phase3_store, FakeAIProvider(), summaries=False)
    response = service.validate_intent("question", intent)
    assert response.status == "unsupported"
    assert response.reason_code == reason


def test_invalid_ranges_are_rejected_semantically(phase3_store: SQLiteStore) -> None:
    company = CompanyIntent.model_construct(
        domain="company", tickers=["AAPL"], metric=MetricCode.REVENUE,
        frequency="annual", start_year=2022, end_year=2020, operation=QueryOperation.LEVEL,
    )
    macro = MacroIntent.model_construct(
        domain="macro", series=["US_INFLATION_YOY"], start_date=date(2020, 1, 1),
        end_date=date(2019, 1, 1), operation=QueryOperation.LEVEL,
    )
    service = AskService(phase3_store, FakeAIProvider(), summaries=False)
    assert service.validate_intent("q", company).reason_code == "INVALID_COMBINATION"
    assert service.validate_intent("q", macro).reason_code == "INVALID_COMBINATION"
    historical = CompanyIntent(tickers=["AAPL"], metric=MetricCode.REVENUE, start_year=2017, end_year=2017)
    assert service.validate_intent("q", historical).reason_code == "INVALID_COMBINATION"


def test_summary_grounding_and_failure_are_nonfatal(phase3_store: SQLiteStore) -> None:
    base_provider = FakeAIProvider([envelope(CompanyIntent(tickers=["AAPL"], metric=MetricCode.REVENUE))])
    base = run(AskService(phase3_store, base_provider, summaries=False).ask("Apple revenue"))
    evidence_id = base.results.series[0].observations[0].evidence[0].id
    summary = FactualSummary(claims=[
        FactualClaim(text="Grounded.", evidence_refs=[evidence_id]),
        FactualClaim(text="Invented.", evidence_refs=["missing"]),
    ])
    assert [claim.text for claim in validate_summary(summary, base.results).claims] == ["Grounded."]

    provider = FakeAIProvider([
        envelope(CompanyIntent(tickers=["AAPL"], metric=MetricCode.REVENUE)),
        "not valid summary json",
    ])
    failed = run(AskService(phase3_store, provider, summaries=True).ask("Apple revenue"))
    assert failed.status == "success"
    assert failed.factual_summary is None
    assert failed.results.series
    assert provider.generate_calls == 2

    phase3_provider = FakeAIProvider([
        envelope(CompanyIntent(tickers=["AAPL"], metric=MetricCode.REVENUE)),
        FactualSummary(claims=[FactualClaim(text="Should not run.", evidence_refs=[evidence_id])]),
    ])
    phase3 = run(AskService(phase3_store, phase3_provider).ask("Apple revenue"))
    assert phase3.factual_summary is None
    assert phase3_provider.generate_calls == 1


def test_summary_context_is_formatted_and_parser_timeout_is_specific(phase3_store: SQLiteStore) -> None:
    base = run(AskService(phase3_store, FakeAIProvider([
        envelope(CompanyIntent(tickers=["AAPL"], metric=MetricCode.REVENUE)),
    ]), summaries=False).ask("Apple revenue"))
    evidence_id = base.results.series[0].observations[0].evidence[0].id
    provider = FakeAIProvider([
        envelope(CompanyIntent(tickers=["AAPL"], metric=MetricCode.REVENUE)),
        FactualSummary(claims=[FactualClaim(text="Revenue changed.", evidence_refs=[evidence_id])]),
    ])
    response = run(AskService(phase3_store, provider, summaries=True).ask("Apple revenue"))
    assert response.factual_summary is not None
    assert '"display_value": "$100"' in provider.prompts[1]
    assert "use the supplied display_value" in provider.prompts[1]

    class SlowProvider(FakeAIProvider):
        async def generate_structured(self, *args, **kwargs):
            await asyncio.sleep(0.05)
            return await super().generate_structured(*args, **kwargs)

    timeout = run(AskService(phase3_store, SlowProvider(), parser_timeout=0.001).ask("Apple revenue"))
    assert timeout.error_code == "AI_TIMEOUT"
    assert timeout.message == "Natural-language search timed out. Try again."


def test_summary_timeout_is_nonfatal(phase3_store: SQLiteStore) -> None:
    class SlowSummaryProvider(FakeAIProvider):
        async def generate_structured(self, prompt, output_schema, *, model=None):
            if prompt.startswith("TASK: factual-summary"):
                await asyncio.sleep(0.05)
            return await super().generate_structured(prompt, output_schema, model=model)

    provider = SlowSummaryProvider([envelope(CompanyIntent(tickers=["AAPL"], metric=MetricCode.REVENUE))])
    response = run(AskService(phase3_store, provider, summaries=True, summary_timeout=0.001).ask("Apple revenue"))
    assert response.status == "success"
    assert response.results.series
    assert response.factual_summary is None


def test_oversized_result_skips_summary(phase3_store: SQLiteStore) -> None:
    for year in range(2019, 2080):
        phase3_store.upsert_financial_observation(fact("AAPL", MetricCode.REVENUE, str(year), year))
    provider = FakeAIProvider([envelope(CompanyIntent(tickers=["AAPL"], metric=MetricCode.REVENUE))])
    response = run(AskService(phase3_store, provider, summaries=True).ask("Apple revenue history"))
    assert response.status == "success"
    assert len(response.results.series[0].observations) == 61
    assert response.factual_summary is None
    assert provider.generate_calls == 1


@pytest.mark.parametrize("failure,code", [
    (AIDisconnectedError("no login"), "AI_DISCONNECTED"),
    (AIUnavailableError("down"), "AI_UNAVAILABLE"),
    (AIUsageLimitError("limit"), "AI_USAGE_LIMIT"),
])
def test_provider_failures_are_distinct(phase3_store: SQLiteStore, failure: Exception, code: str) -> None:
    response = run(AskService(phase3_store, FakeAIProvider(failure=failure)).ask("Apple revenue"))
    assert response.status == "error"
    assert response.error_code == code


def test_api_auth_and_ask_contracts_are_sanitized(phase3_store: SQLiteStore) -> None:
    provider = FakeAIProvider([
        envelope(CompanyIntent(tickers=["AAPL"], metric=MetricCode.REVENUE)),
        envelope(UnsupportedIntent(reason_code=UnsupportedReason.INVESTMENT_ADVICE, message="No recommendations.")),
        envelope(ClarificationIntent(message="Which margin?", tickers=["NVDA"], choices=[DerivedMetricCode.OPERATING_MARGIN])),
    ], connected=False)
    service = AskService(phase3_store, provider, summaries=False)
    with TestClient(create_app(phase3_store, ask_service=service)) as client:
        disconnected = client.get("/api/v1/ai/status").json()
        assert disconnected["provider"] == "chatgpt_codex"
        assert disconnected["connected"] is False
        assert disconnected["state"] == "SIGN_IN_REQUIRED"
        assert "token" not in str(disconnected).lower()
        connect = client.post("/api/v1/ai/connect").json()
        assert set(connect) == {"provider", "connected", "auth_url"}
        assert "token" not in str(connect).lower()
        run(provider.complete_login())
        connected = client.get("/api/v1/ai/status").json()
        assert connected["connected"] is True
        assert connected["state"] == "CONNECTED"
        assert set(connected) == {"provider", "connected", "plan_type", "model", "state", "installed", "supported", "message"}
        models = client.get("/api/v1/ai/models").json()
        assert models[0]["is_default"] is True
        success = client.post("/api/v1/ask", json={"question": "Show Apple revenue"}).json()
        assert success["status"] == "success"
        assert success["validated_query"]["domain"] == "company"
        assert client.post("/api/v1/ask", json={"question": "What should I buy?"}).json()["status"] == "unsupported"
        clarification = client.post("/api/v1/ask", json={"question": "Show Nvidia margin"}).json()
        assert clarification["status"] == "clarification"
        assert clarification["choices"][0]["label"] == "Operating margin"
        provider.failure = AIUsageLimitError("limit")
        assert client.post("/api/v1/ask", json={"question": "q"}).json()["error_code"] == "AI_USAGE_LIMIT"
        provider.failure = None
        assert client.post("/api/v1/ask", json={"question": "  "}).status_code == 422
        oversized = {"domain": "company", "tickers": [f"C{i}" for i in range(61)], "metric": "REVENUE", "frequency": "annual"}
        assert client.post("/api/v1/query", json=oversized).status_code == 422
        invalid_ticker = {"domain": "company", "tickers": ["AAPL');DROP"], "metric": "REVENUE", "frequency": "annual"}
        assert client.post("/api/v1/query", json=invalid_ticker).status_code == 422
        assert client.post("/api/v1/analyze/company", json={"type": "company", "ticker": "../secret"}).status_code == 422
        assert client.post("/api/v1/ai/disconnect").json()["connected"] is False
    assert provider.closed


def test_malformed_output_is_safe(phase3_store: SQLiteStore) -> None:
    response = run(AskService(phase3_store, FakeAIProvider(["not-json"])).ask("question"))
    assert response.status == "error"
    assert response.error_code == "MODEL_OUTPUT_INVALID"


def test_real_adapter_uses_isolation_catalog_default_and_restricted_ephemeral_threads(tmp_path, monkeypatch) -> None:
    import openai_codex

    captured = SimpleNamespace(config=None, thread=None, threads=[], run=None, closed=False)

    class DummyThread:
        async def run(self, prompt, **kwargs):
            captured.run = (prompt, kwargs)
            return SimpleNamespace(final_response='{"ok":true}')

    class DummyCodex:
        def __init__(self, config): captured.config = config
        async def account(self, **kwargs):
            return SimpleNamespace(account=SimpleNamespace(root=SimpleNamespace(type="chatgpt", plan_type="plus")))
        async def models(self, **kwargs):
            return SimpleNamespace(data=[
                SimpleNamespace(model="fallback", display_name="Fallback", description="", is_default=False, hidden=False),
                SimpleNamespace(model="catalog-default", display_name="Default", description="", is_default=True, hidden=False),
            ])
        async def thread_start(self, **kwargs):
            captured.thread = kwargs
            captured.threads.append(kwargs)
            return DummyThread()
        async def logout(self): pass
        async def close(self): captured.closed = True

    monkeypatch.setattr(openai_codex, "AsyncCodex", DummyCodex)
    provider = CodexSubscriptionProvider(tmp_path / "app-data")
    status = run(provider.status())
    assert status.model is None
    assert status.connected is True
    assert status.state.value == "CONNECTED"
    assert captured.config.env["HOME"] == str(Path.home())
    assert "CODEX_HOME" not in captured.config.env
    assert captured.config.codex_bin == str(provider.executable)
    assert "features.shell_tool=false" in captured.config.config_overrides
    assert "features.unified_exec=false" in captured.config.config_overrides
    assert "mcp_servers={}" in captured.config.config_overrides
    assert captured.config.cwd == str(provider.ai_sandbox)
    assert provider.ai_sandbox.is_dir()
    assert run(provider.generate_structured("TASK: test", {"type": "object"})) == '{"ok":true}'
    assert captured.thread["ephemeral"] is True
    assert captured.thread["approval_mode"].value == "deny_all"
    assert captured.thread["sandbox"].value == "read-only"
    turn_directory = Path(captured.thread["cwd"])
    assert turn_directory.parent == provider.ai_sandbox
    assert not turn_directory.exists()
    assert captured.thread["config"]["web_search"] == "disabled"
    assert captured.thread["config"]["features"]["shell_tool"] is False
    assert captured.thread["config"]["features"]["unified_exec"] is False
    assert captured.thread["config"]["apps"]["_default"]["enabled"] is False
    assert captured.run[1]["output_schema"] == {"type": "object"}
    assert captured.run[1]["effort"] is None
    assert run(provider.generate_structured(
        "TASK: contextual-financial-analysis focused", {"type": "object"},
    )) == '{"ok":true}'
    assert captured.run[1]["effort"].value == "low"
    first_directory = captured.thread["cwd"]
    assert run(provider.generate_structured("TASK: test two", {"type": "object"})) == '{"ok":true}'
    assert captured.thread["cwd"] != first_directory
    assert not Path(captured.thread["cwd"]).exists()
    run(provider.close())
    assert captured.closed
