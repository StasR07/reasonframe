import json
from decimal import Decimal
from pathlib import Path

import pytest

from finance_terminal.metrics import METRICS, MetricCode
from finance_terminal.sec.validate import _aapl_fy2025_revenue_expectation


REFERENCE_DIR = Path(__file__).parents[2] / "reference" / "sec-fixtures"
ISSUER_FIXTURES = sorted(REFERENCE_DIR.glob("*-sec-reference-v3.json"))


def test_all_five_verified_issuer_fixtures_are_preserved() -> None:
    assert [path.name for path in ISSUER_FIXTURES] == [
        "alphabet-sec-reference-v3.json",
        "apple-sec-reference-v3.json",
        "berkshire-sec-reference-v3.json",
        "microsoft-sec-reference-v3.json",
        "nvidia-sec-reference-v3.json",
    ]


@pytest.mark.parametrize("path", ISSUER_FIXTURES, ids=lambda path: path.stem)
def test_fixture_values_and_unavailable_cases_are_semantically_explicit(path: Path) -> None:
    fixture = json.loads(path.read_text())

    assert fixture["verified_real_world_data"] is True
    assert fixture["facts"]
    for fact in fixture["facts"]:
        assert MetricCode(fact["metric_code"]) in METRICS
        Decimal(fact["value"])
        assert fact["evidence"]
        assert fact["evidence"][0]["accession_number"]

    for unavailable in fixture["unavailable"]:
        assert MetricCode(unavailable["metric_code"]) in METRICS
        assert unavailable["reason_code"]
        assert "value" not in unavailable


def test_metric_reference_set_matches_canonical_vocabulary_and_period_kinds() -> None:
    definition_paths = sorted((REFERENCE_DIR / "metric-definitions").glob("*.json"))
    definitions = [json.loads(path.read_text()) for path in definition_paths]

    assert {MetricCode(item["code"]) for item in definitions} == set(MetricCode)
    for item in definitions:
        assert item["expected_period_kind"] == METRICS[MetricCode(item["code"])].period_kind


def test_aapl_gate_reads_exact_stored_precision_and_provenance() -> None:
    fact = _aapl_fy2025_revenue_expectation(
        REFERENCE_DIR / "apple-sec-reference-v3.json"
    )

    assert Decimal(fact["value"]) == Decimal("416161000000")
    assert fact["derivation_kind"] == "DIRECT"
    assert fact["period"]["start_date"] == "2024-09-29"
    assert fact["period"]["end_date"] == "2025-09-27"
    assert fact["evidence"][0] == {
        "role": "PRIMARY",
        "accession_number": "0000320193-25-000079",
        "filing_form": "10-K",
        "concept_namespace": "us-gaap",
        "concept_name": "RevenueFromContractWithCustomerExcludingAssessedTax",
        "source_value_text": "416161000000",
        "source_unit": "USD",
        "source_period_kind": "DURATION",
        "source_start_date": "2024-09-29",
        "source_end_date": "2025-09-27",
    }
