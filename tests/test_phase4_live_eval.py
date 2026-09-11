"""Manual grounded analyst evaluation. Run with RUN_LIVE_CODEX=1 pytest -m live_codex -s."""

from __future__ import annotations

import asyncio
import os
import re

import pytest

from finance_terminal.analysis import AnalysisService, CompanyAnalysisRequest, ComparisonAnalysisRequest, FocusedAnalysisRequest
from finance_terminal.ai_provider import CodexSubscriptionProvider
from finance_terminal.metrics import DerivedMetricCode, MetricCode
from finance_terminal.query import CompanyQuery
from finance_terminal.storage import SQLiteStore

pytestmark = pytest.mark.live_codex

if os.environ.get("RUN_LIVE_CODEX") != "1":
    pytest.skip("Set RUN_LIVE_CODEX=1 to spend live Codex usage", allow_module_level=True)

FORBIDDEN = ("buy", "sell", "hold", "price target", "fair value", "ai demand", "ecosystem", "subscription revenue")


def rendered(response) -> str:
    return str(response.model_dump(mode="json")).casefold()


def assert_grounded(response) -> None:
    assert response.status == "success"
    text = rendered(response)
    hits = [term for term in FORBIDDEN if re.search(rf"\b{re.escape(term)}\b", text)]
    assert not hits, f"forbidden terms {hits}: {text}"
    evidence_ids = {
        reference for series in response.evidence_context for point in series.observations
        for reference in ([item.id for item in point.evidence] + ([point.observation_id] if point.observation_id else []) + point.input_observation_ids)
    }

    def visit(node):
        if isinstance(node, dict):
            if "evidence_refs" in node:
                assert node["evidence_refs"] and set(node["evidence_refs"]) <= evidence_ids
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(response.model_dump(mode="json"))


def test_live_contextual_analysts() -> None:
    async def evaluate() -> None:
        store = SQLiteStore(os.environ.get("DATABASE_PATH", "finance_terminal.db"))
        provider = CodexSubscriptionProvider()
        selected_cases = {item.strip() for item in os.environ.get("LIVE_ANALYST_CASES", "AAPL,NVDA,MSFT,focused,comparison").split(",")}
        try:
            try:
                async with asyncio.timeout(15):
                    status = await provider.status()
            except TimeoutError:
                pytest.skip("The isolated Codex account handshake timed out")
            if not status.connected:
                pytest.skip("The isolated finance-terminal Codex home is not signed in")
            service = AnalysisService(store, provider)
            for ticker in ("AAPL", "NVDA", "MSFT"):
                if ticker not in selected_cases:
                    continue
                response = await service.company(CompanyAnalysisRequest(ticker=ticker, start_year=2019))
                assert_grounded(response)
                assert response.fundamentals is not None and response.valuation is not None
                assert response.skeptic is not None and response.overview is not None
                assert response.overview.stance in {"POSITIVE", "NEUTRAL", "NEGATIVE"}
                print({
                    "case": f"{ticker} company", "fundamentals": response.fundamentals.stance,
                    "valuation": response.valuation.stance, "skeptic": response.skeptic.assessment,
                    "overview_confidence": response.overview.confidence, "pass": True,
                })

            if "focused" in selected_cases:
                focused = await service.focused(FocusedAnalysisRequest(query=CompanyQuery(
                    tickers=["AAPL"], metric=MetricCode.REVENUE, frequency="annual", start_year=2022,
                )))
                assert_grounded(focused)
                assert 1 <= len(focused.interpretation.observations) <= 3
                print({"case": "AAPL focused revenue", "pass": True})

            if "comparison" in selected_cases:
                query = CompanyQuery(
                    tickers=["AAPL", "MSFT"], metric=DerivedMetricCode.OPERATING_MARGIN,
                    frequency="annual", start_year=2019,
                )
                comparison = await service.comparison(ComparisonAnalysisRequest(queries=[query]))
                assert_grounded(comparison)
                assert {series.metric for series in comparison.evidence_context} == {"OPERATING_MARGIN"}
                print({"case": "AAPL vs MSFT operating margin", "pass": True})
        finally:
            await provider.close()
            store.close()

    asyncio.run(evaluate())
