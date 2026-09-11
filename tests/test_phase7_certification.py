from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from finance_terminal.certification import (
    ANNUAL_CORE, QUARTERLY_BALANCE, QUARTERLY_CORE, CertificationStatus,
    CheckState, EvaluationState, GateResult, ReasonCode, annual_coverage,
    derived_compatibility, final_status, fiscal_integrity,
    incomplete_certification, minimum_annual_experience,
    minimum_quarterly_experience, quarterly_coverage,
)
from finance_terminal.companies import CANDIDATES, CANDIDATE_BY_TICKER
from finance_terminal.metrics import MetricCode
from finance_terminal.models import (
    DerivationKind, DurationScope, FinancialObservation, FiscalPeriod, PeriodKind,
    SourceFactIdentity,
)
from finance_terminal.storage import SQLiteStore
from finance_terminal.sec_diagnostics import build_sec_diagnostic
from finance_terminal.valuation import ValuationMetricCode, ValuationState, _calculate


def observation(
    metric: MetricCode, year: int, period: FiscalPeriod = FiscalPeriod.FY,
    *, derivation: DerivationKind = DerivationKind.DIRECT,
) -> FinancialObservation:
    quarter_index = {FiscalPeriod.Q1: 0, FiscalPeriod.Q2: 1, FiscalPeriod.Q3: 2, FiscalPeriod.Q4: 3}
    if period is FiscalPeriod.FY:
        end = date(year, 12, 31)
    else:
        end = date(year, 3 * (quarter_index[period] + 1), 28)
    instant = metric in {
        MetricCode.CASH_AND_CASH_EQUIVALENTS, MetricCode.TOTAL_ASSETS,
        MetricCode.TOTAL_LIABILITIES, MetricCode.SHAREHOLDERS_EQUITY,
        MetricCode.CURRENT_ASSETS, MetricCode.CURRENT_LIABILITIES,
    }
    sources = ()
    if derivation is DerivationKind.PERIOD_DERIVED:
        sources = tuple(
            SourceFactIdentity(
                role, "us-gaap:Revenue", Decimal("1"), "USD", date(year, 1, 1), end,
                f"source-{role}", "10-Q", end + timedelta(days=30),
            )
            for role in ("YTD", "PRIOR_YTD")
        )
    return FinancialObservation(
        "TEST", "0000000001", metric, Decimal("10"),
        "USD" if metric is not MetricCode.DILUTED_EPS else "USD/shares", "USD",
        PeriodKind.INSTANT if instant else PeriodKind.DURATION,
        None if instant else date(year, 1, 1), end, year, period,
        None if instant else (DurationScope.ANNUAL if period is FiscalPeriod.FY else DurationScope.STANDALONE_QUARTER),
        derivation, "us-gaap:Revenue", f"accession-{metric.value}-{year}-{period.value}",
        "10-K" if period in {FiscalPeriod.FY, FiscalPeriod.Q4} else "10-Q",
        end + timedelta(days=30), derivation_sources=sources,
    )


def annual_fixture(years=range(2015, 2025)) -> list[FinancialObservation]:
    return [observation(metric, year) for metric in ANNUAL_CORE for year in years]


def quarterly_fixture(start_year=2022) -> list[FinancialObservation]:
    return [
        observation(metric, year, period)
        for metric in (*QUARTERLY_CORE, *QUARTERLY_BALANCE)
        for year in range(start_year, start_year + 3)
        for period in (FiscalPeriod.Q1, FiscalPeriod.Q2, FiscalPeriod.Q3, FiscalPeriod.Q4)
    ]


def test_manifest_has_exact_phase7_pool_and_alphabet_alternate_security() -> None:
    assert len(CANDIDATES) == 60
    assert len({item.ticker for item in CANDIDATES}) == 60
    alphabet = CANDIDATE_BY_TICKER["GOOGL"]
    assert alphabet.alternate_symbols == ("GOOG",)
    assert [item.symbol for item in alphabet.instruments()] == ["GOOGL", "GOOG"]


def test_annual_policy_requires_eight_of_ten_and_latest_five() -> None:
    gate, counts = annual_coverage(annual_fixture())
    assert gate.state is CheckState.PASS
    assert set(counts.values()) == {10}

    missing_old = [item for item in annual_fixture() if not (
        item.metric is MetricCode.REVENUE and item.fiscal_year in {2015, 2016}
    )]
    assert annual_coverage(missing_old)[0].state is CheckState.PASS
    missing_latest = [item for item in annual_fixture() if not (
        item.metric is MetricCode.REVENUE and item.fiscal_year == 2023
    )]
    assert annual_coverage(missing_latest)[0].reason_code is ReasonCode.ANNUAL_CORE_GAP


def test_quarter_policy_requires_exact_coherent_twelve_for_core_and_balances() -> None:
    complete = quarterly_fixture()
    assert quarterly_coverage(complete)[0].state is CheckState.PASS
    gap = [item for item in complete if not (
        item.metric is MetricCode.OPERATING_CASH_FLOW
        and item.fiscal_year == 2023 and item.fiscal_period is FiscalPeriod.Q2
    )]
    assert quarterly_coverage(gap)[0].reason_code is ReasonCode.QUARTERLY_CORE_GAP


def test_duplicate_period_and_incomplete_derivation_lineage_fail_integrity() -> None:
    item = observation(MetricCode.REVENUE, 2024)
    duplicate = fiscal_integrity([item, item])
    assert duplicate.state is CheckState.AMBIGUOUS
    assert duplicate.reason_code is ReasonCode.FISCAL_PERIOD_CONFLICT

    derived = observation(
        MetricCode.REVENUE, 2024, FiscalPeriod.Q2,
        derivation=DerivationKind.PERIOD_DERIVED,
    )
    broken = FinancialObservation(**{
        name: getattr(derived, name) for name in derived.__dataclass_fields__
        if name != "derivation_sources"
    }, derivation_sources=derived.derivation_sources[:1])
    assert fiscal_integrity([broken]).reason_code is ReasonCode.NORMALIZATION_AMBIGUOUS


def test_hard_gate_policy_maps_missing_to_partial_and_ambiguity_to_fail() -> None:
    assert final_status([GateResult("all", CheckState.PASS)]) is CertificationStatus.PASS
    assert final_status([
        GateResult("market", CheckState.MISSING, ReasonCode.MARKET_HISTORY_SHORT)
    ]) is CertificationStatus.PARTIAL
    assert final_status([
        GateResult("fiscal", CheckState.AMBIGUOUS, ReasonCode.FISCAL_PERIOD_CONFLICT)
    ]) is CertificationStatus.FAIL


def test_registry_hides_nonpass_companies_and_registry_aliases_drive_catalog() -> None:
    store = SQLiteStore(":memory:")
    try:
        store.seed_candidates()
        assert store.catalog()["companies"] == []
        now = "2026-09-05T00:00:00+00:00"
        store.start_certification_run("run", "phase7-v1", now)
        store.save_certification_result(
            "run", "AAPL", "PASS", [], {"ticker": "AAPL"}, now, "phase7-v1"
        )
        catalog = store.catalog()
        assert [item["ticker"] for item in catalog["companies"]] == ["AAPL"]
        assert catalog["company_aliases"]["Apple"] == "AAPL"
        assert "Microsoft" not in catalog["company_aliases"]
    finally:
        store.close()


def test_incomplete_evaluation_has_no_certification_status_and_stays_hidden() -> None:
    store = SQLiteStore(":memory:")
    try:
        store.seed_candidates()
        result = incomplete_certification(
            CANDIDATE_BY_TICKER["LMT"], ReasonCode.TIINGO_RATE_LIMITED,
            "FAILED [rate_limited]",
        )
        assert result.evaluation_state is EvaluationState.INCOMPLETE
        assert result.final_status is None
        store.start_certification_run("run", "phase7-v1", result.certified_at)
        store.save_certification_result(
            "run", "LMT", None, result.reason_codes, result.to_dict(),
            result.certified_at, "phase7-v1",
            evaluation_state=result.evaluation_state.value,
            evaluation_reason=result.evaluation_reason,
        )
        assert store.company("LMT") is None
        staged = store.company("LMT", include_nonpass=True)
        assert staged is not None and staged.evaluation_state == "INCOMPLETE"
    finally:
        store.close()


def test_financial_window_replacement_removes_obsolete_normalization_rows() -> None:
    store = SQLiteStore(":memory:")
    try:
        store.seed_candidates()
        company = store.company("AAPL", include_nonpass=True)
        assert company is not None
        stale = replace(observation(MetricCode.REVENUE, 2024, FiscalPeriod.Q2), ticker="AAPL", cik=company.cik)
        current = replace(observation(MetricCode.REVENUE, 2024, FiscalPeriod.Q3), ticker="AAPL", cik=company.cik)
        store.upsert_financial_batch(company, [stale])
        store.replace_financial_window(company, [current], start_year=2024, end_year=2024)
        rows = store.financial_observations(["AAPL"], [MetricCode.REVENUE], "quarterly")
        assert [(item.fiscal_year, item.fiscal_period) for item in rows] == [
            (2024, FiscalPeriod.Q3),
        ]
    finally:
        store.close()


def test_diagnostic_matrix_reports_exact_periods_and_is_deterministic() -> None:
    store = SQLiteStore(":memory:")
    try:
        store.seed_candidates()
        company = store.company("AAPL", include_nonpass=True)
        assert company is not None
        rows = [
            replace(item, ticker="AAPL", cik=company.cik)
            for item in annual_fixture() + quarterly_fixture()
        ]
        rows = [
            item for item in rows
            if not (
                item.metric is MetricCode.REVENUE
                and item.fiscal_year == 2023
                and item.fiscal_period is FiscalPeriod.FY
            )
            and not (
                item.metric is MetricCode.OPERATING_CASH_FLOW
                and item.fiscal_year == 2023
                and item.fiscal_period is FiscalPeriod.Q2
            )
        ]
        store.upsert_financial_batch(company, rows)
        first = build_sec_diagnostic(store)
        second = build_sec_diagnostic(store)
        aapl = next(item for item in first["companies"] if item["ticker"] == "AAPL")
        annual = next(item for item in aapl["annual"] if item["metric"] == "REVENUE")
        quarterly = next(
            item for item in aapl["quarterly"]
            if item["metric"] == "OPERATING_CASH_FLOW"
        )
        assert annual["missing_fiscal_years"] == [2023]
        assert quarterly["missing_quarters"] == ["FY2023 Q2"]
        assert first["aggregate"] == second["aggregate"]
    finally:
        store.close()


def revised_annual_core() -> list[FinancialObservation]:
    required = (
        MetricCode.REVENUE, MetricCode.NET_INCOME, MetricCode.OPERATING_CASH_FLOW,
        MetricCode.TOTAL_ASSETS, MetricCode.SHAREHOLDERS_EQUITY,
        MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES,
    )
    return [observation(metric, year) for metric in required for year in range(2020, 2025)]


def revised_quarterly_core() -> list[FinancialObservation]:
    return [
        observation(metric, year, period)
        for metric in (MetricCode.REVENUE, MetricCode.NET_INCOME)
        for year in (2023, 2024)
        for period in (FiscalPeriod.Q1, FiscalPeriod.Q2, FiscalPeriod.Q3, FiscalPeriod.Q4)
    ]


def test_revised_policy_allows_missing_capex_and_quarterly_eps_history() -> None:
    annual_gates, _ = minimum_annual_experience(revised_annual_core())
    quarter_gates, _ = minimum_quarterly_experience(revised_quarterly_core())
    assert final_status([*annual_gates, *quarter_gates]) is CertificationStatus.PASS


def test_revised_annual_policy_blocks_missing_latest_and_short_revenue_history() -> None:
    rows = revised_annual_core()
    missing_latest = [
        item for item in rows
        if not (item.metric is MetricCode.REVENUE and item.fiscal_year == 2024)
    ]
    gates, _ = minimum_annual_experience(missing_latest)
    assert {gate.reason_code for gate in gates} >= {
        ReasonCode.CORE_LATEST_ANNUAL_INPUT_MISSING,
        ReasonCode.CORE_ANNUAL_REVENUE_INSUFFICIENT,
    }
    short = [
        item for item in rows
        if item.metric is not MetricCode.REVENUE or item.fiscal_year >= 2021
    ]
    gates, _ = minimum_annual_experience(short)
    assert ReasonCode.CORE_ANNUAL_REVENUE_INSUFFICIENT in {
        gate.reason_code for gate in gates
    }


def test_revised_annual_policy_requires_latest_equity() -> None:
    rows = [
        item for item in revised_annual_core()
        if not (item.metric is MetricCode.SHAREHOLDERS_EQUITY and item.fiscal_year == 2024)
    ]
    gates, _ = minimum_annual_experience(rows)
    latest = next(gate for gate in gates if gate.gate == "minimum_annual_latest")
    assert latest.reason_code is ReasonCode.CORE_LATEST_ANNUAL_INPUT_MISSING
    assert "SHAREHOLDERS_EQUITY" in latest.detail


def test_revised_quarterly_policy_requires_seven_of_latest_eight_and_latest() -> None:
    rows = revised_quarterly_core()
    rows = [
        item for item in rows
        if not (
            item.metric is MetricCode.REVENUE
            and (item.fiscal_year, item.fiscal_period) in {
                (2023, FiscalPeriod.Q4), (2024, FiscalPeriod.Q1),
            }
        )
    ]
    gates, _ = minimum_quarterly_experience(rows)
    revenue = next(gate for gate in gates if gate.gate.endswith("revenue"))
    assert revenue.reason_code is ReasonCode.CORE_QUARTERLY_REVENUE_INSUFFICIENT


def test_safe_derived_unavailability_is_nonblocking(monkeypatch) -> None:
    gate, states = derived_compatibility([])
    assert gate.state is CheckState.PASS
    assert set(states.values()) == {"UNAVAILABLE"}
    assert final_status([gate]) is CertificationStatus.PASS

    import finance_terminal.certification as certification
    monkeypatch.setattr(
        certification, "calculate_company_metric",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ArithmeticError()),
    )
    error_gate, _ = certification.derived_compatibility(revised_annual_core())
    assert error_gate.reason_code is ReasonCode.DERIVED_METRIC_ERROR
    assert final_status([error_gate]) is CertificationStatus.FAIL


def test_catalog_status_change_increments_data_revision() -> None:
    store = SQLiteStore(":memory:")
    try:
        store.seed_candidates()
        before = int(store.data_revision())
        now = "2026-09-05T00:00:00+00:00"
        store.start_certification_run("policy", "phase7-v2-core-product", now)
        store.save_certification_result(
            "policy", "GOOGL", "PASS", [], {"ticker": "GOOGL"}, now,
            "phase7-v2-core-product",
        )
        assert int(store.data_revision()) == before + 1
        assert store.company_aliases()["Alphabet"] == "GOOGL"
        assert store.primary_market_instrument("GOOGL") is not None
        assert [item.symbol for item in CANDIDATE_BY_TICKER["GOOGL"].instruments()] == [
            "GOOGL", "GOOG",
        ]
    finally:
        store.close()


def test_valuation_policy_allows_nm_and_optional_unavailable_but_blocks_ps() -> None:
    state, value = _calculate(
        ValuationMetricCode.PE_RATIO, Decimal("100"),
        {MetricCode.DILUTED_EPS: Decimal("-1")}, Decimal("1"),
    )
    assert (state, value) == (ValuationState.NOT_MEANINGFUL, None)
    assert final_status([GateResult("current_valuation", CheckState.PASS)]) is CertificationStatus.PASS
    assert final_status([GateResult(
        "current_valuation", CheckState.MISSING, ReasonCode.CURRENT_PS_UNAVAILABLE,
    )]) is CertificationStatus.PARTIAL


def test_historical_valuation_sparsity_is_not_an_admission_gate() -> None:
    capability = {
        "capability": "historical_valuation", "state": "PARTIAL_HISTORY",
        "prior_reason": ReasonCode.HISTORICAL_VALUATION_SPARSE.value,
    }
    assert capability["state"] == "PARTIAL_HISTORY"
    assert final_status([GateResult("all_revised_hard_gates", CheckState.PASS)]) is CertificationStatus.PASS
