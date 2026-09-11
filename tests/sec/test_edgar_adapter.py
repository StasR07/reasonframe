from datetime import date
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace

import pytest

from finance_terminal.metrics import METRICS, MetricCode
from finance_terminal.models import DerivationKind, DurationScope, FiscalPeriod, PeriodKind
from finance_terminal.sec.edgar_adapter import EdgarAdapter, ObservationUnavailable


class Quality(Enum):
    HIGH = "high"
    MEDIUM = "medium"


def annual_revenue_fact(**changes: object) -> SimpleNamespace:
    values = {
        "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "value": 416_161_000_000,
        "unit": "USD",
        "period_start": date(2024, 9, 29),
        "period_end": date(2025, 9, 27),
        "period_type": "duration",
        "fiscal_year": 2025,
        "fiscal_period": "FY",
        "filing_date": date(2025, 10, 31),
        "form_type": "10-K",
        "accession": "0000320193-25-000079",
        "data_quality": Quality.HIGH,
        "is_restated": False,
        "dimensions": None,
        "calculation_context": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


class FakeFacts:
    def __init__(
        self,
        fact: SimpleNamespace | None,
        instant_facts: list[SimpleNamespace] | None = None,
        prepared_facts: list[SimpleNamespace] | None = None,
    ) -> None:
        self.fact = fact
        self.instant_facts = instant_facts or []
        self.prepared_facts = prepared_facts or []
        self.queries: list[tuple[str, int]] = []

    def get_annual_fact(self, concept: str, fiscal_year: int) -> SimpleNamespace | None:
        self.queries.append((concept, fiscal_year))
        return self.fact if self.fact and concept == self.fact.concept else None

    def query(self) -> "FakeQuery":
        return FakeQuery(self.instant_facts)

    @property
    def _ttm_ready_facts(self) -> "FakePreparedFacts":
        return FakePreparedFacts(self.prepared_facts)


class FakePreparedFacts:
    def __init__(self, facts: list[SimpleNamespace]) -> None:
        self.facts = facts

    def query(self) -> "FakeQuery":
        return FakeQuery(self.facts)


class FakeQuery:
    def __init__(self, facts: list[SimpleNamespace]) -> None:
        self.facts = facts

    def by_concept(self, _concept: str, exact: bool = False) -> "FakeQuery":
        assert exact
        self.facts = [fact for fact in self.facts if fact.concept == _concept]
        return self

    def by_fiscal_year(self, _year: int) -> "FakeQuery":
        self.facts = [fact for fact in self.facts if fact.fiscal_year == _year]
        return self

    def by_fiscal_period(self, _period: str) -> "FakeQuery":
        self.facts = [fact for fact in self.facts if fact.fiscal_period == _period]
        return self

    def by_form_type(self, _form: str) -> "FakeQuery":
        self.facts = [fact for fact in self.facts if fact.form_type == _form]
        return self

    def execute(self) -> list[SimpleNamespace]:
        return self.facts


def adapter_for(
    fact: SimpleNamespace | None,
    instant_facts: list[SimpleNamespace] | None = None,
    prepared_facts: list[SimpleNamespace] | None = None,
) -> tuple[EdgarAdapter, FakeFacts]:
    facts = FakeFacts(fact, instant_facts, prepared_facts)
    company = SimpleNamespace(cik=320193, get_facts=lambda: facts)
    return EdgarAdapter(company_factory=lambda _ticker: company), facts


def test_converts_aapl_fy2025_revenue_with_full_provenance() -> None:
    adapter, facts = adapter_for(annual_revenue_fact())

    observation = adapter.annual_observation("aapl", MetricCode.REVENUE, 2025)

    assert observation.ticker == "AAPL"
    assert observation.cik == "0000320193"
    assert observation.value == Decimal("416161000000")
    assert observation.currency == "USD"
    assert observation.fiscal_period is FiscalPeriod.FY
    assert observation.duration_scope is DurationScope.ANNUAL
    assert observation.derivation is DerivationKind.DIRECT
    assert observation.source_concept.endswith("ExcludingAssessedTax")
    assert observation.accession_number == "0000320193-25-000079"
    assert observation.filing_form == "10-K"
    assert observation.filing_date == date(2025, 10, 31)
    assert facts.queries[0][1] == 2025


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"fiscal_period": "Q4"}, "non-FY"),
        ({"fiscal_year": 2024}, "wrong fiscal year"),
        ({"accession": ""}, "incomplete provenance"),
        ({"value": None}, "no numeric value"),
    ],
)
def test_rejects_semantically_or_provenance_invalid_fact(
    change: dict[str, object], message: str
) -> None:
    adapter, _ = adapter_for(annual_revenue_fact(**change))

    with pytest.raises(ObservationUnavailable, match=message):
        adapter.annual_observation("AAPL", MetricCode.REVENUE, 2025)


def test_unvalidated_metric_is_explicitly_unavailable() -> None:
    adapter, _ = adapter_for(None)

    with pytest.raises(ObservationUnavailable, match="has not entered"):
        adapter.annual_observation("AAPL", MetricCode.GROSS_PROFIT, 2025)


@pytest.mark.parametrize(
    "metric",
    [MetricCode.REVENUE, MetricCode.OPERATING_INCOME, MetricCode.CURRENT_ASSETS],
)
def test_berkshire_semantic_exclusions_are_explicit(metric: MetricCode) -> None:
    adapter, _ = adapter_for(None)

    with pytest.raises(ObservationUnavailable) as error:
        if METRICS[metric].period_kind is PeriodKind.INSTANT:
            adapter.instant_observation("BRK.B", metric, 2025)
        else:
            adapter.annual_observation("BRK.B", metric, 2025)

    assert error.value.reason_code == "NOT_MEANINGFUL"


def instant_assets_fact(period_end: date, value: int) -> SimpleNamespace:
    return SimpleNamespace(
        concept="us-gaap:Assets",
        value=value,
        unit="USD",
        period_start=None,
        period_end=period_end,
        period_type="instant",
        fiscal_year=2025,
        fiscal_period="FY",
        filing_date=date(2025, 10, 31),
        form_type="10-K",
        accession="0000320193-25-000079",
        data_quality=Quality.HIGH,
        is_restated=False,
        dimensions=None,
    )


def test_selects_current_year_end_instant_not_comparative_balance() -> None:
    prior_year = instant_assets_fact(date(2024, 9, 28), 364_980_000_000)
    current_year = instant_assets_fact(date(2025, 9, 27), 359_241_000_000)
    adapter, _ = adapter_for(None, [prior_year, current_year])

    observation = adapter.instant_observation("AAPL", MetricCode.TOTAL_ASSETS, 2025)

    assert observation.value == Decimal("359241000000")
    assert observation.period_kind is PeriodKind.INSTANT
    assert observation.period_start is None
    assert observation.period_end == date(2025, 9, 27)
    assert observation.duration_scope is None
    assert observation.derivation is DerivationKind.DIRECT


def test_instant_lookup_rejects_duration_metric() -> None:
    adapter, _ = adapter_for(None)

    with pytest.raises(ObservationUnavailable, match="duration metric"):
        adapter.instant_observation("AAPL", MetricCode.REVENUE, 2025)


def test_uses_edgartools_q4_derivation_and_retains_both_source_facts() -> None:
    annual = annual_revenue_fact()
    ytd = annual_revenue_fact(
        value=313_695_000_000,
        period_end=date(2025, 6, 28),
        fiscal_period="Q3",
        filing_date=date(2025, 8, 1),
        form_type="10-Q",
        accession="0000320193-25-000073",
    )
    derived = annual_revenue_fact(
        value=102_466_000_000.0,
        period_start=date(2025, 6, 29),
        fiscal_period="Q4",
        calculation_context="derived_q4_fy_minus_ytd9",
        data_quality=Quality.MEDIUM,
    )
    adapter, _ = adapter_for(None, [annual, ytd], [derived])

    observation = adapter.quarterly_observation(
        "AAPL", MetricCode.REVENUE, 2025, FiscalPeriod.Q4
    )

    assert observation.value == Decimal("102466000000.0")
    assert observation.derivation is DerivationKind.PERIOD_DERIVED
    assert observation.duration_scope is DurationScope.STANDALONE_QUARTER
    assert [source.role for source in observation.derivation_sources] == [
        "FISCAL_YEAR",
        "NINE_MONTH_YTD",
    ]
    assert observation.derivation_sources[0].value == Decimal("416161000000")
    assert observation.derivation_sources[1].value == Decimal("313695000000")
    assert "derived_q4_fy_minus_ytd9" in observation.quality_flags


def test_generically_accepts_reconciled_q4_stable_share_eps_with_operand_lineage() -> None:
    annual_income = annual_revenue_fact(
        concept="us-gaap:NetIncomeLoss", value=Decimal("112010000000"),
    )
    nine_month_income = annual_revenue_fact(
        concept="us-gaap:NetIncomeLoss", value=Decimal("84544000000"),
        period_end=date(2025, 6, 28), fiscal_period="Q3",
        filing_date=date(2025, 8, 1), form_type="10-Q",
        accession="0000320193-25-000073",
    )
    annual_shares = annual_revenue_fact(
        concept="us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
        value=Decimal("15004697000"), unit="shares",
    )
    derived = annual_revenue_fact(
        concept="us-gaap:EarningsPerShareDiluted",
        value=Decimal("1.830493478142211"), unit="USD/share",
        period_start=date(2025, 6, 29), fiscal_period="Q4",
        calculation_context="derived_eps_stable_shares", data_quality=Quality.MEDIUM,
    )
    adapter, _ = adapter_for(
        None, [annual_income, nine_month_income, annual_shares], [derived]
    )

    result = adapter.quarterly_observation(
        "AAPL", MetricCode.DILUTED_EPS, 2025, FiscalPeriod.Q4
    )

    assert result.value == Decimal("1.830493478142211")
    assert result.derivation is DerivationKind.PERIOD_DERIVED
    assert [item.role for item in result.derivation_sources] == [
        "FISCAL_YEAR_NET_INCOME", "NINE_MONTH_NET_INCOME",
        "FISCAL_YEAR_DILUTED_SHARES",
    ]


def test_q4_derivation_accepts_a_transition_between_admitted_revenue_aliases() -> None:
    annual = annual_revenue_fact(
        concept="us-gaap:Revenues",
        value=257_637_000_000,
        period_start=date(2021, 1, 1),
        period_end=date(2021, 12, 31),
        fiscal_year=2021,
    )
    ytd = annual_revenue_fact(
        value=182_312_000_000,
        period_start=date(2021, 1, 1),
        period_end=date(2021, 9, 30),
        fiscal_year=2021,
        fiscal_period="Q3",
        form_type="10-Q",
    )
    derived = annual_revenue_fact(
        value=75_325_000_000,
        period_start=date(2021, 10, 1),
        period_end=date(2021, 12, 31),
        fiscal_year=2021,
        fiscal_period="Q4",
        calculation_context="derived_q4_fy_minus_ytd9",
    )
    adapter, _ = adapter_for(None, [annual, ytd], [derived])

    observation = adapter.quarterly_observation(
        "GOOGL", MetricCode.REVENUE, 2021, FiscalPeriod.Q4
    )

    assert observation.value == Decimal("75325000000")
    assert [source.source_concept for source in observation.derivation_sources] == [
        "us-gaap:Revenues",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    ]


def test_direct_quarter_selection_excludes_cumulative_duration() -> None:
    cumulative = annual_revenue_fact(
        concept="us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        value=49_270_000_000,
        period_start=date(2025, 7, 1),
        period_end=date(2025, 12, 31),
        fiscal_year=2026,
        fiscal_period="Q2",
        filing_date=date(2026, 1, 28),
        form_type="10-Q",
        accession="0001193125-26-027207",
    )
    standalone = annual_revenue_fact(
        concept="us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        value=29_876_000_000,
        period_start=date(2025, 10, 1),
        period_end=date(2025, 12, 31),
        fiscal_year=2026,
        fiscal_period="Q2",
        filing_date=date(2026, 1, 28),
        form_type="10-Q",
        accession="0001193125-26-027207",
    )
    adapter, _ = adapter_for(None, prepared_facts=[cumulative, standalone])

    observation = adapter.quarterly_observation(
        "MSFT", MetricCode.CAPITAL_EXPENDITURE, 2026, FiscalPeriod.Q2
    )

    assert observation.value == Decimal("29876000000")
    assert observation.derivation is DerivationKind.DIRECT


def test_uses_edgartools_q2_derivation_and_retains_ytd_sources() -> None:
    concept = "us-gaap:NetCashProvidedByUsedInOperatingActivities"
    six_month = annual_revenue_fact(
        concept=concept,
        value=42_779_000_000,
        period_start=date(2025, 1, 27),
        period_end=date(2025, 7, 27),
        fiscal_year=2026,
        fiscal_period="Q2",
        filing_date=date(2025, 8, 27),
        form_type="10-Q",
        accession="0001045810-25-000209",
    )
    q1 = annual_revenue_fact(
        concept=concept,
        value=27_414_000_000,
        period_start=date(2025, 1, 27),
        period_end=date(2025, 4, 27),
        fiscal_year=2026,
        fiscal_period="Q1",
        filing_date=date(2025, 5, 28),
        form_type="10-Q",
        accession="0001045810-25-000115",
    )
    derived = annual_revenue_fact(
        concept=concept,
        value=15_365_000_000.0,
        period_start=date(2025, 4, 28),
        period_end=date(2025, 7, 27),
        fiscal_year=2026,
        fiscal_period="Q2",
        filing_date=date(2025, 8, 27),
        form_type="10-Q",
        accession="0001045810-25-000209",
        calculation_context="derived_q2_ytd6_minus_q1",
        data_quality=Quality.MEDIUM,
    )
    adapter, _ = adapter_for(None, [six_month, q1], [derived])

    observation = adapter.quarterly_observation(
        "NVDA", MetricCode.OPERATING_CASH_FLOW, 2026, FiscalPeriod.Q2
    )

    assert observation.value == Decimal("15365000000.0")
    assert observation.derivation is DerivationKind.PERIOD_DERIVED
    assert [source.role for source in observation.derivation_sources] == [
        "SIX_MONTH_YTD",
        "THREE_MONTH_YTD",
    ]


def q3_derivation_facts(**six_month_changes: object):
    concept = "us-gaap:NetCashProvidedByUsedInOperatingActivities"
    nine_month = annual_revenue_fact(
        concept=concept, value=80, period_start=date(2025, 1, 1), period_end=date(2025, 9, 30),
        fiscal_period="Q3", filing_date=date(2025, 10, 30), form_type="10-Q",
    )
    six_month = annual_revenue_fact(**{
        "concept": concept, "value": 50, "period_start": date(2025, 1, 1),
        "period_end": date(2025, 6, 30), "fiscal_period": "Q2",
        "filing_date": date(2025, 7, 30), "form_type": "10-Q", **six_month_changes,
    })
    derived = annual_revenue_fact(
        concept=concept, value=30, period_start=date(2025, 7, 1), period_end=date(2025, 9, 30),
        fiscal_period="Q3", filing_date=date(2025, 10, 30), form_type="10-Q",
        calculation_context="derived_q3_ytd9_minus_ytd6", data_quality=Quality.MEDIUM,
    )
    return nine_month, six_month, derived


def test_uses_q3_ytd9_minus_ytd6_and_retains_both_sources() -> None:
    nine_month, six_month, derived = q3_derivation_facts()
    adapter, _ = adapter_for(None, [nine_month, six_month], [derived])

    observation = adapter.quarterly_observation(
        "AAPL", MetricCode.OPERATING_CASH_FLOW, 2025, FiscalPeriod.Q3,
    )

    assert observation.value == Decimal("30")
    assert observation.derivation is DerivationKind.PERIOD_DERIVED
    assert [source.role for source in observation.derivation_sources] == [
        "NINE_MONTH_YTD", "SIX_MONTH_YTD",
    ]
    assert "derived_q3_ytd9_minus_ytd6" in observation.quality_flags


@pytest.mark.parametrize("changes", [
    {"unit": "EUR"},
    {"fiscal_year": 2024},
    {"period_start": date(2025, 2, 1)},
])
def test_q3_derivation_rejects_incompatible_sources(changes: dict[str, object]) -> None:
    nine_month, six_month, derived = q3_derivation_facts(**changes)
    adapter, _ = adapter_for(None, [nine_month, six_month], [derived])

    with pytest.raises(ObservationUnavailable):
        adapter.quarterly_observation(
            "AAPL", MetricCode.OPERATING_CASH_FLOW, 2025, FiscalPeriod.Q3,
        )


def test_q3_derivation_rejects_missing_or_ambiguous_reconciliation() -> None:
    nine_month, six_month, derived = q3_derivation_facts()
    adapter, _ = adapter_for(None, [nine_month], [derived])
    with pytest.raises(ObservationUnavailable, match="6M source"):
        adapter.quarterly_observation("AAPL", MetricCode.OPERATING_CASH_FLOW, 2025, FiscalPeriod.Q3)

    ambiguous = annual_revenue_fact(**{**six_month.__dict__, "value": 49, "filing_date": date(2025, 7, 31)})
    adapter, _ = adapter_for(None, [nine_month, six_month, ambiguous], [derived])
    with pytest.raises(ObservationUnavailable, match="does not reconcile"):
        adapter.quarterly_observation("AAPL", MetricCode.OPERATING_CASH_FLOW, 2025, FiscalPeriod.Q3)


def test_nvidia_productive_assets_concept_is_a_bounded_capex_improvement() -> None:
    fact = annual_revenue_fact(
        concept="us-gaap:PaymentsToAcquireProductiveAssets",
        value=6_042_000_000,
        period_start=date(2025, 1, 27),
        period_end=date(2026, 1, 25),
        fiscal_year=2026,
        filing_date=date(2026, 2, 25),
        accession="0001045810-26-000021",
    )
    adapter, _ = adapter_for(fact)

    observation = adapter.annual_observation(
        "NVDA", MetricCode.CAPITAL_EXPENDITURE, 2026
    )

    assert observation.value == Decimal("6042000000")
    assert observation.source_concept == "us-gaap:PaymentsToAcquireProductiveAssets"


@pytest.mark.parametrize(
    ("metric", "concept", "unit"),
    [
        (MetricCode.COST_OF_REVENUE, "us-gaap:CostOfRevenue", "USD"),
        (MetricCode.GROSS_PROFIT, "us-gaap:GrossProfit", "USD"),
        (MetricCode.BASIC_EPS, "us-gaap:EarningsPerShareBasic", "USD/shares"),
        (MetricCode.DILUTED_EPS, "us-gaap:EarningsPerShareDiluted", "USD/shares"),
        (MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES, "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding", "shares"),
        (MetricCode.SHARE_REPURCHASES, "us-gaap:PaymentsForRepurchaseOfCommonStock", "USD"),
    ],
)
def test_phase1_annual_metric_expansion_is_direct_and_provenanced(
    metric: MetricCode, concept: str, unit: str
) -> None:
    fact = annual_revenue_fact(concept=concept, value=123, unit=unit)
    adapter, _ = adapter_for(fact)

    item = adapter.annual_observation("AAPL", metric, 2025)

    assert item.value == Decimal("123")
    assert item.derivation is DerivationKind.DIRECT
    assert item.period_kind is PeriodKind.DURATION
    assert item.source_concept == concept
    assert item.accession_number


@pytest.mark.parametrize(
    ("metric", "concept"),
    [
        (MetricCode.CASH_AND_CASH_EQUIVALENTS, "us-gaap:CashAndCashEquivalentsAtCarryingValue"),
        (MetricCode.SHORT_TERM_INVESTMENTS, "us-gaap:ShortTermInvestments"),
        (MetricCode.CURRENT_ASSETS, "us-gaap:AssetsCurrent"),
        (MetricCode.CURRENT_LIABILITIES, "us-gaap:LiabilitiesCurrent"),
        (MetricCode.TOTAL_LIABILITIES, "us-gaap:Liabilities"),
        (MetricCode.SHAREHOLDERS_EQUITY, "us-gaap:StockholdersEquity"),
        (MetricCode.CURRENT_PORTION_LONG_TERM_DEBT, "us-gaap:LongTermDebtCurrent"),
        (MetricCode.LONG_TERM_DEBT_NONCURRENT, "us-gaap:LongTermDebtNoncurrent"),
    ],
)
def test_phase1_instant_metric_expansion_never_uses_subtraction(
    metric: MetricCode, concept: str
) -> None:
    fact = instant_assets_fact(date(2025, 9, 27), 123)
    fact.concept = concept
    adapter, _ = adapter_for(None, [fact])

    item = adapter.instant_observation("AAPL", metric, 2025)

    assert item.value == Decimal("123")
    assert item.derivation is DerivationKind.DIRECT
    assert item.period_start is None
    assert item.source_concept == concept


def test_total_liabilities_generic_assets_minus_consolidated_equity_fallback() -> None:
    assets = instant_assets_fact(date(2025, 9, 27), 500)
    equity = instant_assets_fact(date(2025, 9, 27), 175)
    equity.concept = "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
    adapter, _ = adapter_for(None, [assets, equity])

    item = adapter.instant_observation("TEST", MetricCode.TOTAL_LIABILITIES, 2025)

    assert item.value == Decimal("325")
    assert item.derivation is DerivationKind.CALCULATION_DERIVED
    assert [source.role for source in item.derivation_sources] == [
        "TOTAL_ASSETS", "CONSOLIDATED_EQUITY",
    ]


def test_total_liabilities_fallback_rejects_period_or_unit_mismatch() -> None:
    assets = instant_assets_fact(date(2025, 9, 27), 500)
    equity = instant_assets_fact(date(2024, 9, 28), 175)
    equity.concept = "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
    adapter, _ = adapter_for(None, [assets, equity])

    with pytest.raises(ObservationUnavailable, match="operands unavailable"):
        adapter.instant_observation("TEST", MetricCode.TOTAL_LIABILITIES, 2025)


def test_latest_ytd_pair_supersedes_stale_prepared_comparative_quarter() -> None:
    concept = "us-gaap:NetCashProvidedByUsedInOperatingActivities"
    stale = annual_revenue_fact(
        concept=concept, value=10, period_start=date(2021, 10, 1),
        period_end=date(2021, 12, 31), fiscal_year=2023, fiscal_period="Q2",
        form_type="10-Q", calculation_context="derived_q2_ytd6_minus_q1",
    )
    six_month = annual_revenue_fact(
        concept=concept, value=90, period_start=date(2022, 7, 1),
        period_end=date(2022, 12, 31), fiscal_year=2023, fiscal_period="Q2",
        form_type="10-Q",
    )
    q1 = annual_revenue_fact(
        concept=concept, value=40, period_start=date(2022, 7, 1),
        period_end=date(2022, 9, 30), fiscal_year=2023, fiscal_period="Q1",
        form_type="10-Q",
    )
    adapter, _ = adapter_for(None, [six_month, q1], [stale])

    item = adapter.quarterly_observation(
        "TEST", MetricCode.OPERATING_CASH_FLOW, 2023, FiscalPeriod.Q2,
    )

    assert item.value == Decimal("50")
    assert item.period_start == date(2022, 10, 1)
    assert item.period_end == date(2022, 12, 31)
    assert item.quality_flags == ("generic_latest_ytd_pair",)


def test_latest_fy_ytd_pair_supersedes_stale_prepared_q4() -> None:
    stale = annual_revenue_fact(
        value=10, period_start=date(2021, 10, 1), period_end=date(2021, 12, 31),
        fiscal_year=2023, fiscal_period="Q4", calculation_context="derived_q4_fy_minus_ytd9",
    )
    annual = annual_revenue_fact(
        value=400, period_start=date(2022, 1, 1), period_end=date(2022, 12, 31),
        fiscal_year=2023,
    )
    nine_month = annual_revenue_fact(
        value=285, period_start=date(2022, 1, 1), period_end=date(2022, 9, 30),
        fiscal_year=2023, fiscal_period="Q3", form_type="10-Q",
    )
    adapter, _ = adapter_for(None, [annual, nine_month], [stale])

    item = adapter.quarterly_observation(
        "TEST", MetricCode.REVENUE, 2023, FiscalPeriod.Q4,
    )

    assert item.value == Decimal("115")
    assert item.period_end == date(2022, 12, 31)
    assert item.quality_flags == ("generic_latest_fy_minus_ytd9",)
