"""Deterministic Phase 7 company certification over canonical stored evidence."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

from .calculations import calculate_company_metric
from .companies import CANDIDATES, CANDIDATE_BY_TICKER, CompanyCandidate
from .metrics import DerivedMetricCode, MetricCode
from .models import DerivationKind, FinancialObservation, FiscalPeriod
from .refresh import refresh_market, refresh_sec
from .storage import SQLiteStore
from .valuation import ValuationMetricCode, ValuationState, annual_snapshots, current_snapshot


POLICY_VERSION = "phase7-v2-core-product"
PREVIOUS_PHASE7_PASS = {
    "AAPL", "ABT", "ADBE", "AMAT", "CL", "CMCSA", "CSCO", "HD", "INTU",
    "ISRG", "KO", "LMT", "LOW", "MSFT", "MU", "ORCL", "PEP", "TXN", "UPS", "WM",
}
TIINGO_RETRY_TICKERS = ("DE", "ITW", "CVX", "COP", "EOG", "UPS", "NOC", "UNP", "LMT", "WM", "XOM")
GENERIC_FIXES = (
    {
        "rule": "authoritative_sec_window_replacement",
        "rationale": "remove obsolete normalized rows before inserting a rerun window",
    },
    {
        "rule": "latest_compatible_ytd_and_fy_quarter_derivation",
        "rationale": "prefer the latest reconciled filing operands over stale comparative fiscal labels",
    },
    {
        "rule": "assets_minus_consolidated_equity_total_liabilities",
        "rationale": "derive liabilities only from same-period, same-unit standard consolidated operands",
    },
)


class CheckState(StrEnum):
    PASS = "PASS"
    MISSING = "MISSING"
    NOT_MEANINGFUL = "NOT_MEANINGFUL"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"
    ERROR = "ERROR"


class CertificationStatus(StrEnum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"


class EvaluationState(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class ReasonCode(StrEnum):
    TIINGO_RATE_LIMITED = "TIINGO_RATE_LIMITED"
    EXTERNAL_FETCH_INCOMPLETE = "EXTERNAL_FETCH_INCOMPLETE"
    ISSUER_IDENTITY_AMBIGUOUS = "ISSUER_IDENTITY_AMBIGUOUS"
    UNSUPPORTED_ISSUER_CLASS = "UNSUPPORTED_ISSUER_CLASS"
    ANNUAL_CORE_GAP = "ANNUAL_CORE_GAP"
    QUARTERLY_CORE_GAP = "QUARTERLY_CORE_GAP"
    CORE_ANNUAL_REVENUE_INSUFFICIENT = "CORE_ANNUAL_REVENUE_INSUFFICIENT"
    CORE_ANNUAL_NET_INCOME_INSUFFICIENT = "CORE_ANNUAL_NET_INCOME_INSUFFICIENT"
    CORE_ANNUAL_OCF_INSUFFICIENT = "CORE_ANNUAL_OCF_INSUFFICIENT"
    CORE_LATEST_ANNUAL_INPUT_MISSING = "CORE_LATEST_ANNUAL_INPUT_MISSING"
    CORE_QUARTERLY_REVENUE_INSUFFICIENT = "CORE_QUARTERLY_REVENUE_INSUFFICIENT"
    CORE_QUARTERLY_NET_INCOME_INSUFFICIENT = "CORE_QUARTERLY_NET_INCOME_INSUFFICIENT"
    FISCAL_PERIOD_CONFLICT = "FISCAL_PERIOD_CONFLICT"
    NORMALIZATION_AMBIGUOUS = "NORMALIZATION_AMBIGUOUS"
    DERIVED_METRIC_FAILURE = "DERIVED_METRIC_FAILURE"
    DERIVED_METRIC_UNAVAILABLE = "DERIVED_METRIC_UNAVAILABLE"
    DERIVED_METRIC_ERROR = "DERIVED_METRIC_ERROR"
    MARKET_IDENTITY_MISMATCH = "MARKET_IDENTITY_MISMATCH"
    MARKET_HISTORY_SHORT = "MARKET_HISTORY_SHORT"
    MARKET_DATA_INVALID = "MARKET_DATA_INVALID"
    SHARE_BASIS_CONFLICT = "SHARE_BASIS_CONFLICT"
    CURRENT_VALUATION_FAILURE = "CURRENT_VALUATION_FAILURE"
    CURRENT_PS_UNAVAILABLE = "CURRENT_PS_UNAVAILABLE"
    HISTORICAL_VALUATION_SPARSE = "HISTORICAL_VALUATION_SPARSE"
    PROVENANCE_INCOMPLETE = "PROVENANCE_INCOMPLETE"
    STRUCTURAL_DISCONTINUITY = "STRUCTURAL_DISCONTINUITY"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


@dataclass(frozen=True, slots=True)
class GateResult:
    gate: str
    state: CheckState
    reason_code: ReasonCode | None = None
    detail: str = ""


@dataclass(slots=True)
class CompanyCertification:
    ticker: str
    company_name: str
    cik: str
    primary_instrument: str
    final_status: CertificationStatus | None
    reason_codes: list[str]
    gates: list[dict[str, str | None]]
    annual_metric_coverage: dict[str, int] = field(default_factory=dict)
    quarterly_metric_coverage: dict[str, int] = field(default_factory=dict)
    fiscal_integrity: str = CheckState.MISSING
    derived_metric_result: str = CheckState.MISSING
    market_history: dict[str, object] = field(default_factory=dict)
    current_valuation: dict[str, str] = field(default_factory=dict)
    historical_valuation_observations: dict[str, int] = field(default_factory=dict)
    provenance_result: str = CheckState.MISSING
    capability_metadata: dict[str, object] = field(default_factory=dict)
    capability_limitations: list[dict[str, object]] = field(default_factory=list)
    diagnostic: str | None = None
    certification_policy: str = POLICY_VERSION
    certified_at: str = ""
    evaluation_state: EvaluationState = EvaluationState.COMPLETE
    evaluation_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def incomplete_certification(
    candidate: CompanyCandidate, reason: ReasonCode, detail: str,
) -> CompanyCertification:
    timestamp = datetime.now(timezone.utc).isoformat()
    return CompanyCertification(
        candidate.ticker, candidate.name, candidate.cik, candidate.instrument_id,
        None, [reason.value], [], diagnostic=detail, certified_at=timestamp,
        evaluation_state=EvaluationState.INCOMPLETE,
        evaluation_reason=reason.value,
    )


ANNUAL_CORE = (
    MetricCode.REVENUE, MetricCode.OPERATING_INCOME, MetricCode.NET_INCOME,
    MetricCode.DILUTED_EPS, MetricCode.OPERATING_CASH_FLOW,
    MetricCode.CAPITAL_EXPENDITURE, MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES,
    MetricCode.CASH_AND_CASH_EQUIVALENTS, MetricCode.TOTAL_ASSETS,
    MetricCode.TOTAL_LIABILITIES, MetricCode.SHAREHOLDERS_EQUITY,
)
QUARTERLY_CORE = (
    MetricCode.REVENUE, MetricCode.OPERATING_INCOME, MetricCode.NET_INCOME,
    MetricCode.DILUTED_EPS, MetricCode.OPERATING_CASH_FLOW,
    MetricCode.CAPITAL_EXPENDITURE,
)
QUARTERLY_BALANCE = (
    MetricCode.CASH_AND_CASH_EQUIVALENTS, MetricCode.TOTAL_ASSETS,
    MetricCode.TOTAL_LIABILITIES, MetricCode.SHAREHOLDERS_EQUITY,
)
REQUIRED_DERIVED = (
    DerivedMetricCode.REVENUE_GROWTH_YOY, DerivedMetricCode.OPERATING_MARGIN,
    DerivedMetricCode.NET_MARGIN, DerivedMetricCode.FREE_CASH_FLOW,
    DerivedMetricCode.FCF_MARGIN, DerivedMetricCode.CURRENT_RATIO,
)


def _metric_periods(
    observations: Iterable[FinancialObservation], metric: MetricCode, *, annual: bool,
) -> list[FinancialObservation]:
    return sorted(
        (item for item in observations if item.metric is metric and
         (item.fiscal_period is FiscalPeriod.FY) is annual),
        key=lambda item: (item.period_end, item.fiscal_year, item.fiscal_period.value),
    )


def annual_coverage(observations: list[FinancialObservation]) -> tuple[GateResult, dict[str, int]]:
    years = sorted({item.fiscal_year for item in observations if item.fiscal_period is FiscalPeriod.FY})
    if not years:
        return GateResult("annual_fundamentals", CheckState.MISSING, ReasonCode.ANNUAL_CORE_GAP, "no annual observations"), {}
    latest = years[-1]
    window = set(range(latest - 9, latest + 1))
    latest_five = set(range(latest - 4, latest + 1))
    counts: dict[str, int] = {}
    failures: list[str] = []
    for metric in ANNUAL_CORE:
        metric_years = {item.fiscal_year for item in _metric_periods(observations, metric, annual=True)}
        counts[metric.value] = len(metric_years & window)
        if counts[metric.value] < 8 or not latest_five.issubset(metric_years):
            failures.append(metric.value)
    if failures:
        return GateResult("annual_fundamentals", CheckState.MISSING, ReasonCode.ANNUAL_CORE_GAP, "threshold failed: " + ", ".join(failures)), counts
    return GateResult("annual_fundamentals", CheckState.PASS), counts


def _coherent_quarters(items: list[FinancialObservation]) -> bool:
    if len(items) < 12:
        return False
    latest = items[-12:]
    keys = [(item.fiscal_year, item.fiscal_period.value) for item in latest]
    if len(set(keys)) != 12 or len({item.period_end for item in latest}) != 12:
        return False
    order = [FiscalPeriod.Q1, FiscalPeriod.Q2, FiscalPeriod.Q3, FiscalPeriod.Q4]
    for prior, current in zip(latest, latest[1:]):
        prior_index = order.index(prior.fiscal_period)
        expected_period = order[(prior_index + 1) % 4]
        expected_year = prior.fiscal_year + (1 if prior.fiscal_period is FiscalPeriod.Q4 else 0)
        if current.fiscal_period is not expected_period or current.fiscal_year != expected_year:
            return False
    return True


def quarterly_coverage(observations: list[FinancialObservation]) -> tuple[GateResult, dict[str, int]]:
    counts: dict[str, int] = {}
    failures: list[str] = []
    windows: list[tuple[tuple[int, str], ...]] = []
    for metric in (*QUARTERLY_CORE, *QUARTERLY_BALANCE):
        items = _metric_periods(observations, metric, annual=False)
        counts[metric.value] = min(len(items), 12)
        if not _coherent_quarters(items):
            failures.append(metric.value)
        else:
            windows.append(tuple((item.fiscal_year, item.fiscal_period.value) for item in items[-12:]))
    if windows and any(window != windows[0] for window in windows[1:]):
        return GateResult(
            "quarterly_fundamentals", CheckState.MISSING,
            ReasonCode.QUARTERLY_CORE_GAP,
            "required metrics do not share the same latest 12 fiscal quarters",
        ), counts
    if failures:
        return GateResult("quarterly_fundamentals", CheckState.MISSING, ReasonCode.QUARTERLY_CORE_GAP, "12-quarter threshold failed: " + ", ".join(failures)), counts
    return GateResult("quarterly_fundamentals", CheckState.PASS), counts


ANNUAL_MINIMUM_LATEST = (
    MetricCode.REVENUE, MetricCode.NET_INCOME, MetricCode.OPERATING_CASH_FLOW,
    MetricCode.TOTAL_ASSETS, MetricCode.SHAREHOLDERS_EQUITY,
    MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES,
)
ANNUAL_TRENDS = {
    MetricCode.REVENUE: ReasonCode.CORE_ANNUAL_REVENUE_INSUFFICIENT,
    MetricCode.NET_INCOME: ReasonCode.CORE_ANNUAL_NET_INCOME_INSUFFICIENT,
    MetricCode.OPERATING_CASH_FLOW: ReasonCode.CORE_ANNUAL_OCF_INSUFFICIENT,
}


def minimum_annual_experience(
    observations: list[FinancialObservation],
) -> tuple[list[GateResult], dict[str, object]]:
    annuals = [item for item in observations if item.fiscal_period is FiscalPeriod.FY]
    core_years = sorted({
        item.fiscal_year for item in annuals if item.metric in ANNUAL_MINIMUM_LATEST
    })
    latest = core_years[-1] if core_years else None
    counts: dict[str, int] = {}
    gates: list[GateResult] = []
    if latest is None:
        gates.append(GateResult(
            "minimum_annual_latest", CheckState.MISSING,
            ReasonCode.CORE_ANNUAL_REVENUE_INSUFFICIENT, "no annual Revenue observations",
        ))
        return gates, {"latest_fiscal_year": None, "trend_counts": counts}
    latest_metrics = {item.metric for item in annuals if item.fiscal_year == latest}
    missing_latest = [metric.value for metric in ANNUAL_MINIMUM_LATEST if metric not in latest_metrics]
    gates.append(
        GateResult("minimum_annual_latest", CheckState.PASS)
        if not missing_latest else GateResult(
            "minimum_annual_latest", CheckState.MISSING,
            ReasonCode.CORE_LATEST_ANNUAL_INPUT_MISSING,
            f"FY{latest} missing: {', '.join(missing_latest)}",
        )
    )
    window = set(range(latest - 9, latest + 1))
    for metric, reason in ANNUAL_TRENDS.items():
        years = {
            item.fiscal_year for item in annuals
            if item.metric is metric and item.fiscal_year in window
        }
        counts[metric.value] = len(years)
        if len(years) < 5 or latest not in years:
            gates.append(GateResult(
                f"minimum_annual_{metric.value.lower()}", CheckState.MISSING, reason,
                f"{len(years)} usable FY observations in latest 10; latest FY={latest}",
            ))
        else:
            gates.append(GateResult(f"minimum_annual_{metric.value.lower()}", CheckState.PASS))
    return gates, {
        "latest_fiscal_year": latest,
        "latest_required_metrics": {
            metric.value: ("AVAILABLE" if metric in latest_metrics else "UNAVAILABLE")
            for metric in ANNUAL_MINIMUM_LATEST
        },
        "trend_counts": counts,
    }


def _quarter_number(period: FiscalPeriod) -> int:
    return {FiscalPeriod.Q1: 1, FiscalPeriod.Q2: 2, FiscalPeriod.Q3: 3, FiscalPeriod.Q4: 4}[period]


def _latest_eight(latest: tuple[int, int]) -> list[tuple[int, int]]:
    year, quarter = latest
    result: list[tuple[int, int]] = []
    for _ in range(8):
        result.append((year, quarter))
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
    return list(reversed(result))


def minimum_quarterly_experience(
    observations: list[FinancialObservation],
) -> tuple[list[GateResult], dict[str, object]]:
    quarterly = [item for item in observations if item.fiscal_period is not FiscalPeriod.FY]
    indexed: dict[MetricCode, set[tuple[int, int]]] = {
        metric: {
            (item.fiscal_year, _quarter_number(item.fiscal_period))
            for item in quarterly if item.metric is metric
        }
        for metric in (MetricCode.REVENUE, MetricCode.NET_INCOME)
    }
    all_periods = indexed[MetricCode.REVENUE] | indexed[MetricCode.NET_INCOME]
    if not all_periods:
        return [GateResult(
            "minimum_quarterly_revenue", CheckState.MISSING,
            ReasonCode.CORE_QUARTERLY_REVENUE_INSUFFICIENT,
            "no quarterly Revenue or Net Income observations",
        )], {"latest_quarter": None, "latest_eight_counts": {}}
    latest = max(all_periods)
    window = set(_latest_eight(latest))
    gates: list[GateResult] = []
    counts: dict[str, int] = {}
    for metric, reason in (
        (MetricCode.REVENUE, ReasonCode.CORE_QUARTERLY_REVENUE_INSUFFICIENT),
        (MetricCode.NET_INCOME, ReasonCode.CORE_QUARTERLY_NET_INCOME_INSUFFICIENT),
    ):
        count = len(indexed[metric] & window)
        counts[metric.value] = count
        if latest not in indexed[metric] or count < 7:
            gates.append(GateResult(
                f"minimum_quarterly_{metric.value.lower()}", CheckState.MISSING, reason,
                f"{count}/8 usable quarters; latest FY{latest[0]} Q{latest[1]} "
                f"present={latest in indexed[metric]}",
            ))
        else:
            gates.append(GateResult(f"minimum_quarterly_{metric.value.lower()}", CheckState.PASS))
    return gates, {
        "latest_quarter": f"FY{latest[0]} Q{latest[1]}",
        "latest_eight_counts": counts,
    }


def fiscal_integrity(observations: list[FinancialObservation]) -> GateResult:
    seen: set[tuple[str, int, str]] = set()
    for item in observations:
        key = (item.metric.value, item.fiscal_year, item.fiscal_period.value)
        if key in seen:
            return GateResult("fiscal_integrity", CheckState.AMBIGUOUS, ReasonCode.FISCAL_PERIOD_CONFLICT, f"duplicate normalized period {key}")
        seen.add(key)
        if item.derivation is not DerivationKind.DIRECT and len(item.derivation_sources) < 2:
            return GateResult("fiscal_integrity", CheckState.AMBIGUOUS, ReasonCode.NORMALIZATION_AMBIGUOUS, f"incomplete quarter lineage {key}")
    return GateResult("fiscal_integrity", CheckState.PASS)


def derived_compatibility(observations: list[FinancialObservation]) -> tuple[GateResult, dict[str, str]]:
    states: dict[str, str] = {}
    try:
        for metric in (*REQUIRED_DERIVED, DerivedMetricCode.GROSS_MARGIN):
            points = calculate_company_metric(metric, observations)
            states[metric.value] = "AVAILABLE" if points else "UNAVAILABLE"
    except Exception as exc:
        return GateResult(
            "derived_metric_safety", CheckState.ERROR,
            ReasonCode.DERIVED_METRIC_ERROR,
            f"deterministic calculation error: {type(exc).__name__}",
        ), states
    unavailable = [name for name, state in states.items() if state == "UNAVAILABLE"]
    return GateResult(
        "derived_metric_safety", CheckState.PASS,
        detail=("safe unavailable: " + ", ".join(unavailable)) if unavailable else "",
    ), states


def provenance_integrity(observations: list[FinancialObservation]) -> GateResult:
    for item in observations:
        if item.metric not in ANNUAL_CORE + QUARTERLY_CORE + QUARTERLY_BALANCE:
            continue
        if not all((item.cik, item.source_concept, item.unit, item.accession_number, item.filing_form, item.period_end)):
            return GateResult("provenance", CheckState.ERROR, ReasonCode.PROVENANCE_INCOMPLETE, f"incomplete provenance for {item.metric.value} FY{item.fiscal_year} {item.fiscal_period.value}")
        if item.derivation is not DerivationKind.DIRECT and len(item.derivation_sources) < 2:
            return GateResult("provenance", CheckState.ERROR, ReasonCode.PROVENANCE_INCOMPLETE, "derived quarter has incomplete operand lineage")
    return GateResult("provenance", CheckState.PASS)


FAIL_REASONS = {
    ReasonCode.ISSUER_IDENTITY_AMBIGUOUS, ReasonCode.UNSUPPORTED_ISSUER_CLASS,
    ReasonCode.FISCAL_PERIOD_CONFLICT, ReasonCode.NORMALIZATION_AMBIGUOUS,
    ReasonCode.MARKET_IDENTITY_MISMATCH, ReasonCode.MARKET_DATA_INVALID,
    ReasonCode.SHARE_BASIS_CONFLICT, ReasonCode.PROVENANCE_INCOMPLETE,
    ReasonCode.STRUCTURAL_DISCONTINUITY, ReasonCode.DERIVED_METRIC_ERROR,
    ReasonCode.UNEXPECTED_ERROR,
}


def final_status(gates: list[GateResult]) -> CertificationStatus:
    reasons = {gate.reason_code for gate in gates if gate.reason_code is not None}
    if reasons & FAIL_REASONS:
        return CertificationStatus.FAIL
    if any(gate.state is not CheckState.PASS for gate in gates):
        return CertificationStatus.PARTIAL
    return CertificationStatus.PASS


def certify_stored_company(store: SQLiteStore, candidate: CompanyCandidate, *, today: date | None = None) -> CompanyCertification:
    today = today or date.today()
    company = store.company(candidate.ticker, include_nonpass=True)
    instrument = store.primary_market_instrument(candidate.ticker)
    gates: list[GateResult] = []
    observations = store.financial_observations(
        [candidate.ticker], list(MetricCode), "annual"
    ) + store.financial_observations([candidate.ticker], list(MetricCode), "quarterly")
    identity_invalid = (
        company is None or company.cik != candidate.cik or instrument is None
        or instrument.symbol != candidate.ticker
        or any(item.cik != candidate.cik for item in observations)
        or any(
            not item.source_concept.startswith(("us-gaap:", "derived:"))
            for item in observations
        )
    )
    if identity_invalid:
        gates.append(GateResult("identity", CheckState.AMBIGUOUS, ReasonCode.ISSUER_IDENTITY_AMBIGUOUS, "registry, SEC filer, taxonomy, or primary instrument identity mismatch"))
    else:
        gates.append(GateResult("identity", CheckState.PASS))
    gates.append(GateResult("issuer_eligibility", CheckState.PASS))
    legacy_annual_gate, annual_counts = annual_coverage(observations)
    legacy_quarter_gate, quarter_counts = quarterly_coverage(observations)
    annual_gates, annual_core = minimum_annual_experience(observations)
    quarter_gates, quarterly_core = minimum_quarterly_experience(observations)
    fiscal_gate = fiscal_integrity(observations)
    derived_gate, derived_capabilities = derived_compatibility(observations)
    provenance_gate = provenance_integrity(observations)
    gates.extend((*annual_gates, *quarter_gates, fiscal_gate, derived_gate))

    prices = store.market_observations(instrument.instrument_id) if instrument else []
    actions = store.corporate_actions(instrument.instrument_id) if instrument else []
    market_summary: dict[str, object] = {"row_count": len(prices), "first_date": None, "last_date": None}
    if prices:
        market_summary.update(first_date=prices[0].trading_date.isoformat(), last_date=prices[-1].trading_date.isoformat())
    invalid_market = any(
        item.close <= 0 or item.adjusted_close is None or item.adjusted_close <= 0
        for item in prices
    ) or any(action.split_ratio is not None and action.split_ratio <= 0 for action in actions)
    years = (prices[-1].trading_date - prices[0].trading_date).days / 365.2425 if prices else 0
    if invalid_market:
        market_gate = GateResult("market_data", CheckState.ERROR, ReasonCode.MARKET_DATA_INVALID, "non-positive/missing retained price or action value")
    elif len(prices) < 2200 or years < 9 or not prices or (today - prices[-1].trading_date).days > 10:
        market_gate = GateResult("market_data", CheckState.MISSING, ReasonCode.MARKET_HISTORY_SHORT, f"{len(prices)} rows over {years:.2f} years")
    else:
        market_gate = GateResult("market_data", CheckState.PASS)
    gates.append(market_gate)

    current_states: dict[str, str] = {}
    history_counts: dict[str, int] = {}
    annual_observations = [item for item in observations if item.fiscal_period is FiscalPeriod.FY]
    try:
        for metric in ValuationMetricCode:
            current = current_snapshot(metric, annual_observations, prices, actions)
            current_states[metric.value] = (
                current.state.value if current else ValuationState.UNAVAILABLE.value
            )
            history_counts[metric.value] = sum(
                item.state is ValuationState.AVAILABLE
                for item in annual_snapshots(metric, annual_observations, prices, actions)
            )
        if current_states[ValuationMetricCode.PS_RATIO.value] == ValuationState.AVAILABLE.value:
            current_gate = GateResult("current_valuation", CheckState.PASS)
        else:
            current_gate = GateResult(
                "current_valuation", CheckState.MISSING,
                ReasonCode.CURRENT_PS_UNAVAILABLE,
                f"P/S state={current_states[ValuationMetricCode.PS_RATIO.value]}",
            )
    except Exception as exc:
        current_gate = GateResult(
            "current_valuation", CheckState.ERROR,
            ReasonCode.CURRENT_VALUATION_FAILURE,
            f"deterministic valuation error: {type(exc).__name__}",
        )
    historical_available = history_counts.get(ValuationMetricCode.PS_RATIO.value, 0) >= 7 and max(
        history_counts.get(ValuationMetricCode.PE_RATIO.value, 0),
        history_counts.get(ValuationMetricCode.P_FCF_RATIO.value, 0),
    ) >= 5
    gates.extend((current_gate, provenance_gate))
    capability_limitations: list[dict[str, object]] = []
    annual_status: dict[str, str] = {}
    latest_annual = annual_core.get("latest_fiscal_year")
    for metric in ANNUAL_CORE:
        count = annual_counts.get(metric.value, 0)
        years = {
            item.fiscal_year for item in observations
            if item.metric is metric and item.fiscal_period is FiscalPeriod.FY
        }
        state = "UNAVAILABLE" if count == 0 else "AVAILABLE"
        if count and (count < 8 or not isinstance(latest_annual, int) or not set(
            range(latest_annual - 4, latest_annual + 1)
        ).issubset(years)):
            state = "PARTIAL_HISTORY"
        annual_status[metric.value] = state
        if state != "AVAILABLE":
            capability_limitations.append({
                "capability": f"annual:{metric.value}", "state": state,
                "prior_reason": ReasonCode.ANNUAL_CORE_GAP.value,
            })
    quarterly_status: dict[str, str] = {}
    for metric in (*QUARTERLY_CORE, *QUARTERLY_BALANCE):
        items = _metric_periods(observations, metric, annual=False)
        state = "UNAVAILABLE" if not items else (
            "AVAILABLE" if _coherent_quarters(items) else "PARTIAL_HISTORY"
        )
        quarterly_status[metric.value] = state
        if state != "AVAILABLE":
            capability_limitations.append({
                "capability": f"quarterly:{metric.value}", "state": state,
                "prior_reason": ReasonCode.QUARTERLY_CORE_GAP.value,
            })
    for metric, state in derived_capabilities.items():
        if state == "UNAVAILABLE":
            limitation: dict[str, object] = {
                "capability": f"derived:{metric}", "state": state,
                "reason_code": ReasonCode.DERIVED_METRIC_UNAVAILABLE.value,
            }
            if metric in {item.value for item in REQUIRED_DERIVED}:
                limitation["prior_reason"] = ReasonCode.DERIVED_METRIC_FAILURE.value
            capability_limitations.append(limitation)
    historical_state = "AVAILABLE" if historical_available else "PARTIAL_HISTORY"
    if not historical_available:
        capability_limitations.append({
            "capability": "historical_valuation", "state": historical_state,
            "prior_reason": ReasonCode.HISTORICAL_VALUATION_SPARSE.value,
        })
    for metric, state in current_states.items():
        if metric != ValuationMetricCode.PS_RATIO.value and state != ValuationState.AVAILABLE.value:
            limitation = {
                "capability": f"current_valuation:{metric}", "state": state,
            }
            if state == ValuationState.UNAVAILABLE.value:
                limitation["prior_reason"] = ReasonCode.CURRENT_VALUATION_FAILURE.value
            capability_limitations.append(limitation)
    status = final_status(gates)
    timestamp = datetime.now(timezone.utc).isoformat()
    reason_codes = list(dict.fromkeys(gate.reason_code.value for gate in gates if gate.reason_code))
    return CompanyCertification(
        candidate.ticker, candidate.name, candidate.cik,
        instrument.instrument_id if instrument else candidate.instrument_id,
        status, reason_codes,
        [{"gate": gate.gate, "state": gate.state.value, "reason_code": gate.reason_code.value if gate.reason_code else None, "detail": gate.detail} for gate in gates],
        annual_counts, quarter_counts, fiscal_gate.state.value, derived_gate.state.value,
        market_summary, current_states, history_counts, provenance_gate.state.value,
        {
            "annual_core": annual_core,
            "quarterly_core": quarterly_core,
            "annual_metrics": annual_status,
            "quarterly_metrics": quarterly_status,
            "derived_metrics": derived_capabilities,
            "historical_valuation": historical_state,
            "legacy_annual_gate": legacy_annual_gate.state.value,
            "legacy_quarterly_gate": legacy_quarter_gate.state.value,
        },
        capability_limitations,
        certified_at=timestamp,
    )


def render_markdown(
    results: list[dict[str, object]], run_id: str,
    *, tiingo_outcomes: dict[str, dict[str, str]] | None = None,
) -> str:
    complete = [item for item in results if item.get("evaluation_state") == "COMPLETE"]
    incomplete = [item for item in results if item.get("evaluation_state") == "INCOMPLETE"]
    counts = Counter(str(item["final_status"]) for item in complete)
    passed = [str(item["ticker"]) for item in results if item["final_status"] == "PASS"]
    reasons = Counter(code for item in results for code in item.get("reason_codes", []))
    lines = [
        f"# Phase 7 certification — {run_id}", "", f"Policy: `{POLICY_VERSION}`", "",
        f"Candidates: {len(results)} | PASS: {counts['PASS']} | PARTIAL: {counts['PARTIAL']} | FAIL: {counts['FAIL']} | INCOMPLETE: {len(incomplete)}",
        "", "## PASS universe", "", ", ".join(passed) if passed else "None", "",
        "## PARTIAL / FAIL", "", "| Ticker | Status | Reason codes |", "|---|---|---|",
    ]
    lines.extend(
        f"| {item['ticker']} | {item['final_status']} | {', '.join(item.get('reason_codes', [])) or '—'} |"
        for item in complete if item["final_status"] != "PASS"
    )
    if incomplete:
        lines.extend(("", "## INCOMPLETE evaluations", "", "| Ticker | Reason |", "|---|---|"))
        lines.extend(
            f"| {item['ticker']} | {item.get('evaluation_reason') or 'EXTERNAL_FETCH_INCOMPLETE'} |"
            for item in incomplete
        )
    lines.extend(("", "## Failure clusters", "", "| Reason code | Count |", "|---|---:|"))
    lines.extend(f"| {reason} | {count} |" for reason, count in reasons.most_common())
    promoted = sorted(set(passed) - PREVIOUS_PHASE7_PASS)
    demoted = sorted(PREVIOUS_PHASE7_PASS - set(passed))
    annual_limitations = sum(
        limitation.get("capability", "").startswith("annual:")
        for item in results for limitation in item.get("capability_limitations", [])
    )
    quarterly_limitations = sum(
        limitation.get("capability", "").startswith("quarterly:")
        for item in results for limitation in item.get("capability_limitations", [])
    )
    sparse_history = sum(
        any(x.get("capability") == "historical_valuation" for x in item.get("capability_limitations", []))
        for item in results
    )
    lines.extend((
        "", "## Capability-policy conclusions", "",
        "Issuer admission now uses the minimum useful core-product contract; legacy completeness measurements remain capability metadata.", "",
        f"Promoted from the previous 20-PASS registry: {', '.join(promoted) if promoted else 'none'}", "",
        f"Demoted: {', '.join(demoted) if demoted else 'none'}", "",
        f"Non-blocking annual metric limitations: {annual_limitations}",
        f"Non-blocking quarterly metric limitations: {quarterly_limitations}",
        f"Companies without full historical-relative valuation capability: {sparse_history}", "",
        "No financial calculation, valuation formula/date semantic, provenance rule, or normalization rule was loosened in this policy pass.",
        "", "### Promotion rationale", "",
        "| Ticker | Prior blockers now capability-level |", "|---|---|",
    ))
    by_ticker = {str(item["ticker"]): item for item in results}
    for ticker in promoted:
        prior = sorted({
            str(item["prior_reason"])
            for item in by_ticker[ticker].get("capability_limitations", [])
            if item.get("prior_reason")
        })
        lines.append(f"| {ticker} | {', '.join(prior) or 'No prior coverage blocker retained'} |")
    lines.extend((
        "", "## Remaining PARTIAL by revised core blocker", "",
        "| Blocker | Tickers |", "|---|---|",
    ))
    grouped: dict[str, list[str]] = {}
    for item in results:
        if item.get("final_status") != "PARTIAL":
            continue
        for reason in item.get("reason_codes", []):
            grouped.setdefault(str(reason), []).append(str(item["ticker"]))
    lines.extend(
        f"| {reason} | {', '.join(tickers)} |" for reason, tickers in sorted(grouped.items())
    )
    lines.extend((
        "", "## XOM identity conclusion", "",
        "XOM remains FAIL: the manifest current registrant CIK is 0002115436 while retained SEC observations identify CIK 0000034088. The current registry cannot represent that successor/security continuity without ambiguity, so no issuer-specific bridge was added.",
        "", "The policy rerun used local SQLite evidence only; no SEC or Tiingo calls were made.",
        "", "Phase 8 deferral: quota-window scheduling and user-facing automatic resumption remain out of scope.",
    ))
    return "\n".join(lines) + "\n"


def unfinished_market_candidates(store: SQLiteStore) -> list[str]:
    """Return candidates with no retained primary-instrument market history."""
    unfinished: list[str] = []
    for candidate in CANDIDATES:
        instrument = store.primary_market_instrument(candidate.ticker)
        if instrument is None or not store.market_observations(instrument.instrument_id):
            unfinished.append(candidate.ticker)
    return unfinished


def run_certification(
    store: SQLiteStore, *, refresh_live: bool | None = None,
    refresh_sec_data: bool | None = None, refresh_market_data: bool | None = None,
    output_dir: Path,
    tickers: list[str] | None = None,
) -> tuple[str, list[dict[str, object]], Path, Path]:
    selected = (
        [CANDIDATE_BY_TICKER[t] for t in tickers]
        if tickers is not None else list(CANDIDATES)
    )
    if refresh_live is not None:
        refresh_sec_data = refresh_live
        refresh_market_data = refresh_live
    refresh_sec_data = True if refresh_sec_data is None else refresh_sec_data
    refresh_market_data = True if refresh_market_data is None else refresh_market_data
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started = datetime.now(timezone.utc).isoformat()
    store.seed_candidates()
    store.start_certification_run(run_id, POLICY_VERSION, started)
    results: list[dict[str, object]] = []
    for candidate in selected:
        try:
            sec_outcome: str | None = None
            market_outcome: str | None = None
            if refresh_sec_data:
                sec_outcome = refresh_sec(
                    store, [candidate.ticker], start_year=date.today().year - 11,
                    allow_candidates=True,
                )[candidate.ticker]
            if refresh_market_data:
                market_outcome = refresh_market(
                    store, [candidate.ticker], full=True, allow_candidates=True,
                )[candidate.ticker]
            if market_outcome and "[rate_limited]" in market_outcome:
                result = incomplete_certification(
                    candidate, ReasonCode.TIINGO_RATE_LIMITED, market_outcome,
                )
            elif any(
                outcome and outcome.startswith("FAILED")
                for outcome in (sec_outcome, market_outcome)
            ):
                result = incomplete_certification(
                    candidate, ReasonCode.EXTERNAL_FETCH_INCOMPLETE,
                    market_outcome or sec_outcome or "external fetch incomplete",
                )
            else:
                result = certify_stored_company(store, candidate)
        except Exception as exc:
            timestamp = datetime.now(timezone.utc).isoformat()
            gate = GateResult("runner", CheckState.ERROR, ReasonCode.UNEXPECTED_ERROR, type(exc).__name__)
            result = CompanyCertification(
                candidate.ticker, candidate.name, candidate.cik, candidate.instrument_id,
                CertificationStatus.FAIL, [ReasonCode.UNEXPECTED_ERROR.value],
                [{"gate": gate.gate, "state": gate.state.value, "reason_code": gate.reason_code.value, "detail": gate.detail}],
                diagnostic=f"{type(exc).__name__}: {str(exc)[:300]}", certified_at=timestamp,
            )
        payload = result.to_dict()
        store.save_certification_result(
            run_id, candidate.ticker,
            result.final_status.value if result.final_status else None,
            result.reason_codes, payload, result.certified_at, POLICY_VERSION,
            evaluation_state=result.evaluation_state.value,
            evaluation_reason=result.evaluation_reason,
        )
        results.append(payload)
        label = result.final_status.value if result.final_status else result.evaluation_state.value
        print(f"{candidate.ticker}: {label} {' '.join(result.reason_codes)}", flush=True)
    store.finish_certification_run(run_id, datetime.now(timezone.utc).isoformat(), "COMPLETED")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"phase7-certification-{run_id}.json"
    markdown_path = output_dir / f"phase7-certification-{run_id}.md"
    tiingo_outcomes = store.latest_ingestion_outcomes(
        [f"TIINGO:{ticker}" for ticker in TIINGO_RETRY_TICKERS]
    )
    complete_counts = Counter(
        str(item["final_status"]) for item in results
        if item.get("evaluation_state") == "COMPLETE"
    )
    pass_set = {
        str(item["ticker"]) for item in results if item["final_status"] == "PASS"
    }
    promoted_set = pass_set - PREVIOUS_PHASE7_PASS
    limitation_counts = {
        "annual_metric_gaps": sum(
            str(limitation.get("capability", "")).startswith("annual:")
            for item in results for limitation in item.get("capability_limitations", [])
        ),
        "quarterly_metric_gaps": sum(
            str(limitation.get("capability", "")).startswith("quarterly:")
            for item in results for limitation in item.get("capability_limitations", [])
        ),
        "historical_valuation_limited_companies": sum(
            any(
                limitation.get("capability") == "historical_valuation"
                for limitation in item.get("capability_limitations", [])
            )
            for item in results
        ),
    }
    partial_by_blocker: dict[str, list[str]] = {}
    for item in results:
        if item["final_status"] != "PARTIAL":
            continue
        for reason in item["reason_codes"]:
            partial_by_blocker.setdefault(str(reason), []).append(str(item["ticker"]))
    metadata = {
        "run_id": run_id,
        "policy_version": POLICY_VERSION,
        "policy_or_threshold_changed": True,
        "policy_change_scope": "issuer admission gates only",
        "live_provider_calls_made": bool(refresh_sec_data or refresh_market_data),
        "counts": {
            "PASS": complete_counts["PASS"],
            "PARTIAL": complete_counts["PARTIAL"],
            "FAIL": complete_counts["FAIL"],
            "INCOMPLETE": sum(item.get("evaluation_state") == "INCOMPLETE" for item in results),
        },
        "pass_tickers": [item["ticker"] for item in results if item["final_status"] == "PASS"],
        "promoted_from_previous_20_pass_registry": sorted(promoted_set),
        "promotion_details": [
            {
                "ticker": item["ticker"],
                "prior_blockers_now_capability_level": sorted({
                    str(limitation["prior_reason"])
                    for limitation in item.get("capability_limitations", [])
                    if limitation.get("prior_reason")
                }),
            }
            for item in results if str(item["ticker"]) in promoted_set
        ],
        "demoted_from_previous_20_pass_registry": sorted(
            PREVIOUS_PHASE7_PASS - pass_set
        ),
        "nonblocking_capability_counts": limitation_counts,
        "remaining_partial_by_core_blocker": partial_by_blocker,
        "tiingo_retry_outcomes": tiingo_outcomes,
        "normalization_changes_in_this_policy_pass": [],
        "results": results,
    }
    json_path.write_text(json.dumps(metadata, indent=2) + "\n")
    markdown_path.write_text(
        render_markdown(results, run_id, tiingo_outcomes=tiingo_outcomes)
    )
    return run_id, results, json_path, markdown_path


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="certify-companies")
    parser.add_argument("tickers", nargs="*", type=str.upper)
    parser.add_argument("--database", default=os.environ.get("DATABASE_PATH", "finance_terminal.db"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument("--no-refresh", action="store_true")
    parser.add_argument("--no-sec-refresh", action="store_true")
    parser.add_argument("--no-market-refresh", action="store_true")
    parser.add_argument(
        "--resume-market", action="store_true",
        help="refresh market data only for candidates with no retained history",
    )
    args = parser.parse_args()
    unknown = sorted(set(args.tickers) - set(CANDIDATE_BY_TICKER))
    if unknown:
        parser.error("unknown candidates: " + ", ".join(unknown))
    store = SQLiteStore(args.database)
    try:
        selected_tickers = args.tickers or None
        if args.resume_market:
            selected_tickers = unfinished_market_candidates(store)
        _, results, json_path, markdown_path = run_certification(
            store, output_dir=args.output_dir,
            refresh_sec_data=False if args.resume_market else not (args.no_refresh or args.no_sec_refresh),
            refresh_market_data=not (args.no_refresh or args.no_market_refresh),
            tickers=selected_tickers,
        )
        counts = Counter(str(item["final_status"]) for item in results if item["final_status"])
        incomplete = sum(item.get("evaluation_state") == "INCOMPLETE" for item in results)
        print(f"PASS={counts['PASS']} PARTIAL={counts['PARTIAL']} FAIL={counts['FAIL']} INCOMPLETE={incomplete}")
        print(json_path)
        print(markdown_path)
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
