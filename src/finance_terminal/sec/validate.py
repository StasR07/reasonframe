"""Concise live report for the Phase 0 direct-fact validation gates."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from finance_terminal.metrics import METRICS, MetricCode
from finance_terminal.models import FiscalPeriod, PeriodKind

from .edgar_adapter import EdgarAdapter, ObservationUnavailable


REFERENCE_DIR = Path(__file__).resolve().parents[3] / "reference" / "sec-fixtures"
DEFAULT_AAPL_FIXTURE = REFERENCE_DIR / "apple-sec-reference-v3.json"

# Expand only with a case whose selection behavior has been inspected.
VALIDATION_CASES = (
    ("AAPL", "apple-sec-reference-v3.json", MetricCode.REVENUE, 2025, FiscalPeriod.FY),
    ("AAPL", "apple-sec-reference-v3.json", MetricCode.TOTAL_ASSETS, 2025, FiscalPeriod.FY),
    ("MSFT", "microsoft-sec-reference-v3.json", MetricCode.REVENUE, 2026, FiscalPeriod.FY),
    ("MSFT", "microsoft-sec-reference-v3.json", MetricCode.DIVIDENDS_PAID, 2026, FiscalPeriod.FY),
    ("NVDA", "nvidia-sec-reference-v3.json", MetricCode.OPERATING_INCOME, 2026, FiscalPeriod.FY),
    ("GOOGL", "alphabet-sec-reference-v3.json", MetricCode.NET_INCOME, 2025, FiscalPeriod.FY),
    ("BRK.B", "berkshire-sec-reference-v3.json", MetricCode.NET_INCOME, 2025, FiscalPeriod.FY),
    ("AAPL", "apple-sec-reference-v3.json", MetricCode.REVENUE, 2025, FiscalPeriod.Q4),
    ("MSFT", "microsoft-sec-reference-v3.json", MetricCode.CAPITAL_EXPENDITURE, 2026, FiscalPeriod.Q2),
    ("NVDA", "nvidia-sec-reference-v3.json", MetricCode.OPERATING_CASH_FLOW, 2026, FiscalPeriod.Q2),
    ("NVDA", "nvidia-sec-reference-v3.json", MetricCode.TOTAL_ASSETS, 2026, FiscalPeriod.FY),
    ("GOOGL", "alphabet-sec-reference-v3.json", MetricCode.OPERATING_INCOME, 2025, FiscalPeriod.Q4),
    ("BRK.B", "berkshire-sec-reference-v3.json", MetricCode.TOTAL_ASSETS, 2025, FiscalPeriod.FY),
)

NEGATIVE_CASES = (
    ("BRK.B", MetricCode.REVENUE, 2025, FiscalPeriod.FY, "NOT_MEANINGFUL"),
    ("BRK.B", MetricCode.OPERATING_INCOME, 2025, FiscalPeriod.FY, "NOT_MEANINGFUL"),
    ("BRK.B", MetricCode.CURRENT_ASSETS, 2025, FiscalPeriod.FY, "NOT_MEANINGFUL"),
)

IMPROVEMENT_PROBES = (
    ("GOOGL", MetricCode.REVENUE, 2025, "us-gaap:Revenues"),
    (
        "NVDA",
        MetricCode.CAPITAL_EXPENDITURE,
        2026,
        "us-gaap:PaymentsToAcquireProductiveAssets",
    ),
)


def _fixture_expectation(
    fixture_path: Path,
    metric: MetricCode,
    fiscal_year: int,
    fiscal_period: str = "FY",
) -> dict[str, Any]:
    try:
        fixture = json.loads(fixture_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ObservationUnavailable(
            f"cannot read reference fixture {fixture_path}: {exc}"
        ) from exc

    matches = [
        fact
        for fact in fixture.get("facts", [])
        if fact.get("metric_code") == metric.value
        and fact.get("period", {}).get("fiscal_year") == fiscal_year
        and fact.get("period", {}).get("fiscal_period") == fiscal_period
    ]
    if len(matches) != 1:
        raise ObservationUnavailable(
            f"expected exactly one {metric} FY{fiscal_year} fixture fact, found {len(matches)}"
        )
    return matches[0]


def _aapl_fy2025_expectation(
    metric: MetricCode, path: Path = DEFAULT_AAPL_FIXTURE
) -> dict[str, Any]:
    return _fixture_expectation(path, metric, 2025)


def _aapl_fy2025_revenue_expectation(path: Path = DEFAULT_AAPL_FIXTURE) -> dict[str, Any]:
    return _aapl_fy2025_expectation(MetricCode.REVENUE, path)


def _differences(observation: Any, expected: dict[str, Any]) -> list[str]:
    evidence = expected["evidence"][0]
    period = expected["period"]
    expected_concept = f"{evidence['concept_namespace']}:{evidence['concept_name']}"
    comparisons = (
        ("concept", observation.source_concept, expected_concept),
        ("accession", observation.accession_number, evidence["accession_number"]),
        ("form", observation.filing_form, evidence["filing_form"]),
        ("period_end", observation.period_end.isoformat(), period["end_date"]),
        (
            "period_start",
            observation.period_start.isoformat() if observation.period_start else None,
            period.get("start_date"),
        ),
        ("derivation", observation.derivation.value, expected["derivation_kind"]),
    )
    differences = [
        f"{name}: expected {wanted}, actual {actual}"
        for name, actual, wanted in comparisons
        if actual != wanted
    ]
    if observation.value != Decimal(expected["value"]):
        differences.insert(
            0, f"value: expected {expected['value']}, actual {observation.value}"
        )
    if observation.derivation_sources:
        if len(observation.derivation_sources) != len(expected["evidence"]):
            differences.append(
                "derivation source count: expected "
                f"{len(expected['evidence'])}, actual {len(observation.derivation_sources)}"
            )
        for source, source_expected in zip(
            observation.derivation_sources, expected["evidence"], strict=False
        ):
            expected_source_concept = (
                f"{source_expected['concept_namespace']}:{source_expected['concept_name']}"
            )
            source_comparisons = (
                ("role", source.role, source_expected["role"]),
                ("concept", source.source_concept, expected_source_concept),
                ("value", source.value, Decimal(source_expected["source_value_text"])),
                ("unit", source.unit, source_expected["source_unit"]),
                ("form", source.filing_form, source_expected["filing_form"]),
                ("period_end", source.period_end.isoformat(), source_expected["source_end_date"]),
            )
            differences.extend(
                f"{source.role} source {name}: expected {wanted}, actual {actual}"
                for name, actual, wanted in source_comparisons
                if actual != wanted
            )
    return differences


def main() -> int:
    if not os.environ.get("EDGAR_IDENTITY"):
        print("SEC direct validation  FAIL  EDGAR_IDENTITY is not configured")
        print(
            f"\nSummary\n-------\nPassed: 0\nFailed: "
            f"{len(VALIDATION_CASES) + len(NEGATIVE_CASES) + len(IMPROVEMENT_PROBES)}"
            "\nAdapter exceptions: 1"
        )
        return 2

    adapter = EdgarAdapter()
    passed_count = 0
    adapter_exceptions = 0
    improved_count = 0
    for ticker, fixture_name, metric, fiscal_year, fiscal_period in VALIDATION_CASES:
        try:
            expected = _fixture_expectation(
                REFERENCE_DIR / fixture_name,
                metric,
                fiscal_year,
                fiscal_period.value,
            )
            if fiscal_period is not FiscalPeriod.FY:
                observation = adapter.quarterly_observation(
                    ticker, metric, fiscal_year, fiscal_period
                )
            elif expected["period"]["kind"] == PeriodKind.INSTANT:
                observation = adapter.instant_observation(ticker, metric, fiscal_year)
            else:
                observation = adapter.annual_observation(ticker, metric, fiscal_year)
            differences = _differences(observation, expected)
        except ObservationUnavailable as exc:
            adapter_exceptions += 1
            period_label = f"FY{fiscal_year}" if fiscal_period is FiscalPeriod.FY else f"{fiscal_period}FY{fiscal_year}"
            print(f"{ticker:<5} {metric.value:<20} {period_label:<9} FAIL  {exc}")
            continue

        passed = not differences
        passed_count += int(passed)
        status = "PASS" if passed else "FAIL"
        period_label = f"FY{fiscal_year}" if fiscal_period is FiscalPeriod.FY else f"{fiscal_period}FY{fiscal_year}"
        derivation_label = observation.derivation.value.lower().replace("_", "-")
        print(f"{ticker:<5} {metric.value:<20} {period_label:<9} {status}  {derivation_label}")
        for difference in differences:
            print(f"  {difference}")
        print(f"  concept: {observation.source_concept}")
        print(f"  source:  {observation.accession_number} {observation.filing_form}")
        if observation.period_start:
            print(f"  period:  {observation.period_start} to {observation.period_end}")
        else:
            print(f"  period:  as of {observation.period_end}")
        for source in observation.derivation_sources:
            print(
                f"  input:   {source.role} {source.value} {source.accession_number}"
            )

    for ticker, metric, fiscal_year, fiscal_period, expected_reason in NEGATIVE_CASES:
        try:
            if METRICS[metric].period_kind is PeriodKind.INSTANT:
                adapter.instant_observation(ticker, metric, fiscal_year)
            else:
                adapter.annual_observation(ticker, metric, fiscal_year)
        except ObservationUnavailable as exc:
            passed = exc.reason_code == expected_reason
            passed_count += int(passed)
            status = "PASS" if passed else "FAIL"
            print(
                f"{ticker:<5} {metric.value:<20} FY{fiscal_year:<5} {status}  "
                f"{exc.reason_code.lower().replace('_', '-')}"
            )
            if not passed:
                print(f"  expected reason: {expected_reason}")
        else:
            print(f"{ticker:<5} {metric.value:<20} FY{fiscal_year:<5} FAIL  expected unavailable")

    for ticker, metric, fiscal_year, expected_concept in IMPROVEMENT_PROBES:
        try:
            observation = adapter.annual_observation(ticker, metric, fiscal_year)
        except ObservationUnavailable as exc:
            adapter_exceptions += 1
            print(f"{ticker:<5} {metric.value:<20} FY{fiscal_year:<5} FAIL  {exc}")
            continue
        passed = (
            observation.value > 0
            and observation.source_concept == expected_concept
            and bool(observation.accession_number)
        )
        passed_count += int(passed)
        improved_count += int(passed)
        status = "PASS" if passed else "FAIL"
        print(f"{ticker:<5} {metric.value:<20} FY{fiscal_year:<5} {status}  improved-historical-gap")
        print(f"  value:   {observation.value}")
        print(f"  concept: {observation.source_concept}")
        print(f"  source:  {observation.accession_number} {observation.filing_form}")

    total_cases = len(VALIDATION_CASES) + len(NEGATIVE_CASES) + len(IMPROVEMENT_PROBES)
    failed_count = total_cases - passed_count
    print("\nSummary\n-------")
    print(f"Passed: {passed_count}")
    print(f"Failed: {failed_count}")
    print(f"Improved historical gaps: {improved_count}")
    print(f"Adapter exceptions: {adapter_exceptions}")
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
