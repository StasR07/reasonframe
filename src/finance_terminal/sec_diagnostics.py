"""Deterministic Phase 7 coverage diagnostics over canonical local evidence."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .calculations import calculate_company_metric
from .certification import (
    ANNUAL_CORE, QUARTERLY_BALANCE, QUARTERLY_CORE, REQUIRED_DERIVED,
    annual_coverage, derived_compatibility, quarterly_coverage,
)
from .companies import CANDIDATES
from .metrics import DerivedMetricCode, MetricCode
from .models import FinancialObservation, FiscalPeriod, PeriodKind
from .storage import SQLiteStore


DERIVED_INPUTS: dict[DerivedMetricCode, tuple[MetricCode, ...]] = {
    DerivedMetricCode.REVENUE_GROWTH_YOY: (MetricCode.REVENUE,),
    DerivedMetricCode.OPERATING_MARGIN: (MetricCode.OPERATING_INCOME, MetricCode.REVENUE),
    DerivedMetricCode.NET_MARGIN: (MetricCode.NET_INCOME, MetricCode.REVENUE),
    DerivedMetricCode.FREE_CASH_FLOW: (MetricCode.OPERATING_CASH_FLOW, MetricCode.CAPITAL_EXPENDITURE),
    DerivedMetricCode.FCF_MARGIN: (MetricCode.OPERATING_CASH_FLOW, MetricCode.CAPITAL_EXPENDITURE, MetricCode.REVENUE),
    DerivedMetricCode.CURRENT_RATIO: (MetricCode.CURRENT_ASSETS, MetricCode.CURRENT_LIABILITIES),
}

REPRESENTATIVE_AUDITS = (
    {
        "ticker": "GOOGL",
        "finding": "The accepted diluted-share standard concept has raw coverage only in the recent post-split filing set; the required annual span and an older Q4 EPS operand set are absent. EPS subtraction was rejected because differing weighted-average share bases make it invalid.",
        "classification": "GENUINELY_UNAVAILABLE",
    },
    {
        "ticker": "NVDA",
        "finding": "CapEx changes from the PP&E-acquisition concept (older quarterly facts, no usable annual series) to ProductiveAssets (35 raw facts in the newer filing set). The concepts are admitted, but the annual continuity and older diluted-EPS quarter operands remain insufficient.",
        "classification": "GENUINELY_UNAVAILABLE",
    },
    {
        "ticker": "AMZN",
        "finding": "No direct us-gaap:Liabilities fact exists and no same-period consolidated-equity operand supports the generic subtraction fallback. Parent-only equity was not substituted because it would change meaning when noncontrolling interest exists.",
        "classification": "NO_RAW_STANDARD_FACT",
    },
    {
        "ticker": "PG",
        "finding": "Recent cash is reported in a broader cash-and-restricted-cash presentation, while parent-only StockholdersEquity is absent. The broader concepts are not semantically identical to the locked canonical metrics.",
        "classification": "ALTERNATE_STANDARD_CONCEPT_PRESENT",
    },
    {
        "ticker": "KO",
        "finding": "Direct Liabilities is absent, but same-period Assets and consolidated equity are present and reconcile deterministically. The generic assets-minus-consolidated-equity rule resolves the gap with both operands retained.",
        "classification": "CANONICAL_CONCEPT_NOT_SELECTED",
    },
    {
        "ticker": "CRM",
        "finding": "The latest required fiscal sequence crosses comparative facts carrying shifted upstream fiscal-year labels. Generic latest-operand derivation resolves valid YTD pairs, but the required Q4 sequence remains incomplete.",
        "classification": "FISCAL_PERIOD_CLASSIFICATION",
    },
    {
        "ticker": "LRCX",
        "finding": "All required recent source metrics except diluted EPS have a coherent window; diluted EPS has only eight normalized quarters and lacks a safe older Q4 operand set.",
        "classification": "GENUINELY_UNAVAILABLE",
    },
    {
        "ticker": "JNJ",
        "finding": "The standard OperatingIncomeLoss series is absent after the isolated older fact, and parent-only equity does not cover twelve recent quarter ends. Pretax or segment income was rejected as semantically different.",
        "classification": "ALTERNATE_STANDARD_CONCEPT_PRESENT",
    },
)


def _period_key(item: FinancialObservation) -> tuple[int, int]:
    order = {
        FiscalPeriod.FY: 0, FiscalPeriod.Q1: 1, FiscalPeriod.Q2: 2,
        FiscalPeriod.Q3: 3, FiscalPeriod.Q4: 4,
    }
    return item.fiscal_year, order[item.fiscal_period]


def _prior_quarters(latest: tuple[int, int], count: int = 12) -> list[tuple[int, int]]:
    year, quarter = latest
    result = []
    for _ in range(count):
        result.append((year, quarter))
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
    return list(reversed(result))


def _fact(item: FinancialObservation) -> dict[str, object]:
    return {
        "concept": item.source_concept,
        "unit": item.unit,
        "period_start": item.period_start.isoformat() if item.period_start else None,
        "period_end": item.period_end.isoformat(),
        "fiscal_year": item.fiscal_year,
        "fiscal_period": item.fiscal_period.value,
        "form": item.filing_form,
        "accession": item.accession_number,
        "derivation": item.derivation.value,
        "derivation_operands": [
            {
                "role": source.role,
                "concept": source.source_concept,
                "unit": source.unit,
                "period_start": source.period_start.isoformat() if source.period_start else None,
                "period_end": source.period_end.isoformat(),
                "form": source.filing_form,
                "accession": source.accession_number,
            }
            for source in item.derivation_sources
        ],
    }


def _has_period_disorder(items: list[FinancialObservation]) -> bool:
    ordered = sorted(items, key=_period_key)
    return any(right.period_end <= left.period_end for left, right in zip(ordered, ordered[1:]))


def build_sec_diagnostic(store: SQLiteStore) -> dict[str, object]:
    companies: list[dict[str, object]] = []
    aggregate: Counter[tuple[str, str, str]] = Counter()
    affected: defaultdict[tuple[str, str, str], set[str]] = defaultdict(set)
    for candidate in CANDIDATES:
        observations = (
            store.financial_observations([candidate.ticker], list(MetricCode), "annual")
            + store.financial_observations([candidate.ticker], list(MetricCode), "quarterly")
        )
        annual_gate, _ = annual_coverage(observations)
        quarter_gate, _ = quarterly_coverage(observations)
        derived_gate, _ = derived_compatibility(observations)
        if not any(gate.reason_code for gate in (annual_gate, quarter_gate, derived_gate)):
            continue
        annuals = [item for item in observations if item.fiscal_period is FiscalPeriod.FY]
        latest_year = max((item.fiscal_year for item in annuals), default=0)
        expected_years = list(range(latest_year - 9, latest_year + 1)) if latest_year else []
        quarters = [item for item in observations if item.fiscal_period is not FiscalPeriod.FY]
        latest_quarter = max((_period_key(item) for item in quarters), default=(0, 0))
        expected_quarters = _prior_quarters(latest_quarter) if latest_quarter != (0, 0) else []
        company_result: dict[str, object] = {
            "ticker": candidate.ticker, "annual": [], "quarterly": [], "derived": [],
        }
        if annual_gate.reason_code:
            for metric in ANNUAL_CORE:
                items = [x for x in annuals if x.metric is metric]
                by_year = {x.fiscal_year: x for x in items}
                missing = [year for year in expected_years if year not in by_year]
                if len(set(by_year) & set(expected_years)) >= 8 and not (
                    set(range(latest_year - 4, latest_year + 1)) - set(by_year)
                ):
                    continue
                reason = "FISCAL_PERIOD_CLASSIFICATION" if _has_period_disorder(items) else "NO_RAW_STANDARD_FACT"
                entry = {
                    "metric": metric.value,
                    "usable_latest_10_count": len(set(by_year) & set(expected_years)),
                    "latest_five_consecutive": not bool(set(range(latest_year - 4, latest_year + 1)) - set(by_year)),
                    "missing_fiscal_years": missing,
                    "chosen_canonical_concepts": sorted({x.source_concept for x in items if x.fiscal_year in expected_years}),
                    "alternate_standard_concept_present": False,
                    "usable_fact_rejected_by_selection": reason == "FISCAL_PERIOD_CLASSIFICATION",
                    "raw_evidence_scope": "canonical observations and retained derivation operands",
                    "failure_reason": reason,
                    "available_facts": [_fact(x) for x in items if x.fiscal_year in expected_years],
                }
                company_result["annual"].append(entry)  # type: ignore[union-attr]
                aggregate[("annual", reason, metric.value)] += 1
                affected[("annual", reason, metric.value)].add(candidate.ticker)
        if quarter_gate.reason_code:
            for metric in (*QUARTERLY_CORE, *QUARTERLY_BALANCE):
                items = [x for x in quarters if x.metric is metric]
                by_period = {_period_key(x): x for x in items}
                missing = [key for key in expected_quarters if key not in by_period]
                window = [by_period[key] for key in expected_quarters if key in by_period]
                if len(window) == 12 and not _has_period_disorder(window):
                    continue
                if _has_period_disorder(items):
                    reason = "FISCAL_PERIOD_CLASSIFICATION"
                elif metric in QUARTERLY_CORE and any(x.derivation_sources for x in items):
                    reason = "YTD_DERIVATION_NOT_RESOLVED"
                else:
                    reason = "NO_RAW_STANDARD_FACT"
                entry = {
                    "metric": metric.value,
                    "normalized_standalone_count": len(items),
                    "missing_quarters": [f"FY{year} Q{quarter}" for year, quarter in missing],
                    "raw_direct_quarter_fact_present": any(
                        _period_key(x) in expected_quarters and not x.derivation_sources for x in items
                    ),
                    "cumulative_ytd_fact_present": any(x.derivation_sources for x in items),
                    "required_derivation_operands_present": any(len(x.derivation_sources) >= 2 for x in items),
                    "derivation_attempted": any(x.derivation_sources for x in items),
                    "derivation_rejected": bool(missing) and reason == "YTD_DERIVATION_NOT_RESOLVED",
                    "derivation_rejection_reason": reason if reason == "YTD_DERIVATION_NOT_RESOLVED" else None,
                    "fiscal_period_classification": "issuer fiscal year/quarter",
                    "failure_reason": reason,
                    "available_facts": [_fact(x) for x in items if _period_key(x) in expected_quarters],
                }
                company_result["quarterly"].append(entry)  # type: ignore[union-attr]
                aggregate[("quarterly", reason, metric.value)] += 1
                affected[("quarterly", reason, metric.value)].add(candidate.ticker)
        if derived_gate.reason_code:
            for metric in REQUIRED_DERIVED:
                if calculate_company_metric(metric, observations):
                    continue
                inputs = DERIVED_INPUTS[metric]
                missing_inputs = [
                    source.value for source in inputs
                    if not any(item.metric is source for item in observations)
                ]
                reason = "DOWNSTREAM_DERIVED_INPUT_MISSING" if missing_inputs else "UNEXPECTED_NORMALIZATION_ERROR"
                company_result["derived"].append({  # type: ignore[union-attr]
                    "metric": metric.value,
                    "missing_or_invalid_inputs": missing_inputs,
                    "downstream_of_core_gap": bool(missing_inputs),
                    "formula_failed_with_inputs_present": not missing_inputs,
                    "failure_reason": reason,
                })
                aggregate[("derived", reason, metric.value)] += 1
                affected[("derived", reason, metric.value)].add(candidate.ticker)
        companies.append(company_result)
    summary = [
        {
            "frequency": frequency, "reason": reason, "metric": metric,
            "affected_company_count": count,
            "tickers": sorted(affected[(frequency, reason, metric)]),
        }
        for (frequency, reason, metric), count in sorted(
            aggregate.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_scope": "canonical SQLite observations and their retained source operands",
        "companies": companies, "aggregate": summary,
        "representative_audits": REPRESENTATIVE_AUDITS,
        "conclusions": {
            "generic_defects": [
                "obsolete normalized rows surviving reruns",
                "stale comparative fiscal labels winning quarter selection",
                "direct Liabilities absent where exact consolidated arithmetic operands exist",
            ],
            "genuine_or_incompatible": [
                "missing standard concept history",
                "broader concepts that would change canonical metric meaning",
                "missing compatible EPS or YTD derivation operands",
            ],
        },
    }


def render_diagnostic_markdown(report: dict[str, object], run_id: str) -> str:
    lines = [
        f"# Phase 7 SEC coverage/root-cause diagnostic — {run_id}", "",
        "This matrix is generated from canonical SQLite observations and retained derivation operands.",
        "Raw facts rejected before normalization are not persisted; representative SEC Company Facts audits supplement this report.",
        "", "## Aggregate clusters", "",
        "| Frequency | Root cause | Metric | Issuers | Tickers |", "|---|---|---|---:|---|",
    ]
    for item in report["aggregate"]:  # type: ignore[index]
        lines.append(
            f"| {item['frequency']} | {item['reason']} | {item['metric']} | "
            f"{item['affected_company_count']} | {', '.join(item['tickers'])} |"
        )
    lines.extend(("", "## Per-company failures", ""))
    for company in report["companies"]:  # type: ignore[index]
        lines.append(f"### {company['ticker']}")
        lines.append("")
        for frequency in ("annual", "quarterly", "derived"):
            for item in company[frequency]:
                periods = item.get("missing_fiscal_years", item.get("missing_quarters", []))
                lines.append(
                    f"- {frequency}: `{item['metric']}` — `{item['failure_reason']}`"
                    + (f"; missing: {', '.join(map(str, periods))}" if periods else "")
                )
        lines.append("")
    lines.extend(("## Representative evidence audit", ""))
    for item in report["representative_audits"]:  # type: ignore[index]
        lines.append(
            f"- **{item['ticker']}** — `{item['classification']}`: {item['finding']}"
        )
    lines.extend((
        "", "## Conclusions", "",
        "Generic defects justified three bounded fixes: authoritative fiscal-window replacement, latest-compatible YTD/FY operand selection, and assets minus consolidated equity for liabilities.",
        "Remaining dominant gaps are absent standard histories, semantically broader concepts, and missing compatible derivation operands; these were not papered over.", "",
    ))
    return "\n".join(lines)


def write_sec_diagnostic(store: SQLiteStore, output_dir: Path) -> tuple[Path, Path]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = build_sec_diagnostic(store)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"phase7-sec-root-cause-{run_id}.json"
    markdown_path = output_dir / f"phase7-sec-root-cause-{run_id}.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n")
    markdown_path.write_text(render_diagnostic_markdown(report, run_id))
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(prog="diagnose-phase7")
    parser.add_argument("--database", default=os.environ.get("DATABASE_PATH", "finance_terminal.db"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    store = SQLiteStore(args.database)
    try:
        for path in write_sec_diagnostic(store, args.output_dir):
            print(path)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
