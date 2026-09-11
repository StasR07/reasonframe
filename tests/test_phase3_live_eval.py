"""Manual Luna-vs-Terra parser benchmark.

Run with: RUN_LIVE_CODEX=1 pytest tests/test_phase3_live_eval.py -m live_codex -s
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from pathlib import Path

import pytest

from finance_terminal.ai import AskService, IntentParser
from finance_terminal.ai_provider import CodexSubscriptionProvider
from finance_terminal.query import CompanyQuery, MacroQuery, MarketQuery, ValuationQuery
from finance_terminal.storage import SQLiteStore

pytestmark = pytest.mark.live_codex

if os.environ.get("RUN_LIVE_CODEX") != "1":
    pytest.skip("Set RUN_LIVE_CODEX=1 to spend live Codex usage", allow_module_level=True)

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "parser" / "golden.json").read_text()
)
# Forty difficult, representative cases: unsupported/typos, relative dates and
# rankings, then level-vs-growth and ambiguity distinctions. Keep live usage
# bounded; the full corpus remains the offline semantic specification.
BENCHMARK = GOLDEN[45:65] + GOLDEN[95:105] + GOLDEN[125:135]


def _assert_subset(actual: dict, expected: dict) -> None:
    for key, value in expected.items():
        assert actual.get(key) == value, f"{key}: expected {value!r}, got {actual.get(key)!r}"


def test_live_luna_vs_terra_parser_benchmark() -> None:
    async def evaluate() -> None:
        store = SQLiteStore(os.environ.get("DATABASE_PATH", "finance_terminal.db"))
        provider = CodexSubscriptionProvider()
        try:
            status = await provider.status()
            if not status.connected:
                pytest.skip("The isolated finance-terminal Codex home is not signed in")
            available = await provider.models()
            selected = {
                family: next((item.id for item in available if family in item.name.casefold()), None)
                for family in ("luna", "terra")
            }
            if not all(selected.values()):
                pytest.skip(f"Luna and Terra are not both available: {selected}")
            parser = IntentParser(provider)
            service = AskService(store, provider, summaries=False)
            catalog = store.catalog()
            large_catalog = json.loads(json.dumps(catalog))
            for index in range(1, 37):
                ticker = f"C{index:03d}"
                large_catalog["companies"].append({
                    "ticker": ticker, "cik": str(index), "name": f"Synthetic Company {index}",
                    "fiscal_year_end": None, "support_status": "SUPPORTED",
                })
                large_catalog["company_metric_support"].append({
                    "ticker": ticker, "metric": "REVENUE", "frequency": "annual",
                })
            summaries = []
            for family, model in selected.items():
                failures: list[str] = []
                latencies: list[float] = []
                counters = {"schema": 0, "semantic": 0, "clarification": 0}
                for question, expected_status, expected in BENCHMARK:
                    started = time.monotonic()
                    try:
                        intent = await parser.parse(question, catalog, model=model)
                    except Exception as exc:
                        counters["schema"] += 1
                        failures.append(f"{question}: schema failure {type(exc).__name__}")
                        continue
                    latencies.append((time.monotonic() - started) * 1000)
                    validated = service.validate_intent(question, intent)
                    if isinstance(validated, (CompanyQuery, MacroQuery, MarketQuery, ValuationQuery)):
                        actual_status = "success"
                    else:
                        actual_status = validated.status
                    actual = validated.model_dump(mode="json")
                    try:
                        assert actual_status == expected_status
                        _assert_subset(actual, expected)
                    except AssertionError as exc:
                        kind = "clarification" if expected_status == "clarification" else "semantic"
                        counters[kind] += 1
                        failures.append(f"{question}: {exc}; actual={actual}")
                scale_intent = await parser.parse(
                    "Compare Synthetic Company 7 and Synthetic Company 23 annual revenue.",
                    large_catalog, model=model,
                )
                if getattr(scale_intent, "tickers", None) != ["C007", "C023"]:
                    counters["semantic"] += 1
                    failures.append(f"synthetic catalog scale case failed: {scale_intent}")
                summary = {
                    "model": family, "total": len(BENCHMARK), "correct": len(BENCHMARK) - len(failures),
                    "schema_failures": counters["schema"], "semantic_failures": counters["semantic"],
                    "clarification_failures": counters["clarification"],
                    "median_latency_ms": round(statistics.median(latencies)) if latencies else None,
                }
                summaries.append(summary)
                print(summary)
                for failure in failures:
                    print(f"  {failure}")
            assert all(item["correct"] / item["total"] >= 0.98 for item in summaries)
            assert all(item["schema_failures"] == item["semantic_failures"] == item["clarification_failures"] == 0 for item in summaries)
        finally:
            await provider.close()
            store.close()

    asyncio.run(evaluate())
