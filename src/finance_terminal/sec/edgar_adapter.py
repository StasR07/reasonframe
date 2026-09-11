"""Thin conversion boundary from EdgarTools facts to project observations."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from finance_terminal.metrics import METRICS, MetricCode
from finance_terminal.models import (
    DerivationKind,
    DurationScope,
    FinancialObservation,
    FiscalPeriod,
    PeriodKind,
    SourceFactIdentity,
)


class ObservationUnavailable(LookupError):
    """The requested SEC observation could not be established safely."""

    def __init__(self, message: str, reason_code: str = "UNAVAILABLE") -> None:
        super().__init__(message)
        self.reason_code = reason_code


# Ordered, bounded mappings only for metrics whose validation has begun. Do not
# turn this into a general XBRL registry; add a concept only for a proven case.
_ANNUAL_CONCEPTS: dict[MetricCode, tuple[str, ...]] = {
    MetricCode.REVENUE: (
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:Revenues",
        "us-gaap:SalesRevenueNet",
    ),
    MetricCode.OPERATING_INCOME: ("us-gaap:OperatingIncomeLoss",),
    MetricCode.NET_INCOME: ("us-gaap:NetIncomeLoss",),
    MetricCode.COST_OF_REVENUE: ("us-gaap:CostOfRevenue",),
    MetricCode.GROSS_PROFIT: ("us-gaap:GrossProfit",),
    MetricCode.BASIC_EPS: ("us-gaap:EarningsPerShareBasic",),
    MetricCode.DILUTED_EPS: ("us-gaap:EarningsPerShareDiluted",),
    MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES: (
        "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
    ),
    MetricCode.DIVIDENDS_PAID: (
        "us-gaap:PaymentsOfDividendsCommonStock",
        "us-gaap:PaymentsOfDividends",
    ),
    MetricCode.CAPITAL_EXPENDITURE: (
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        "us-gaap:PaymentsToAcquireProductiveAssets",
    ),
    MetricCode.OPERATING_CASH_FLOW: (
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
    ),
    MetricCode.SHARE_REPURCHASES: (
        "us-gaap:PaymentsForRepurchaseOfCommonStock",
    ),
}

_INSTANT_CONCEPTS: dict[MetricCode, tuple[str, ...]] = {
    MetricCode.CASH_AND_CASH_EQUIVALENTS: (
        "us-gaap:CashAndCashEquivalentsAtCarryingValue",
    ),
    MetricCode.SHORT_TERM_INVESTMENTS: (
        "us-gaap:MarketableSecuritiesCurrent",
        "us-gaap:ShortTermInvestments",
    ),
    MetricCode.CURRENT_ASSETS: ("us-gaap:AssetsCurrent",),
    MetricCode.CURRENT_LIABILITIES: ("us-gaap:LiabilitiesCurrent",),
    MetricCode.TOTAL_ASSETS: ("us-gaap:Assets",),
    MetricCode.TOTAL_LIABILITIES: ("us-gaap:Liabilities",),
    MetricCode.SHAREHOLDERS_EQUITY: ("us-gaap:StockholdersEquity",),
    MetricCode.CURRENT_PORTION_LONG_TERM_DEBT: (
        "us-gaap:LongTermDebtCurrent",
    ),
    MetricCode.LONG_TERM_DEBT_NONCURRENT: (
        "us-gaap:LongTermDebtNoncurrent",
    ),
}

_SEMANTIC_EXCLUSIONS: dict[tuple[str, MetricCode], str] = {
    ("BRK.B", MetricCode.REVENUE): "NOT_MEANINGFUL",
    ("BRK.B", MetricCode.OPERATING_INCOME): "NOT_MEANINGFUL",
    ("BRK.B", MetricCode.CURRENT_ASSETS): "NOT_MEANINGFUL",
}


def _default_company_factory(ticker: str) -> Any:
    # Import lazily so deterministic model/conversion tests do not need to load
    # EdgarTools or make network access part of the project domain boundary.
    from edgar import Company

    return Company(ticker)


def _as_date(value: Any, field: str, *, optional: bool = False) -> date | None:
    if value is None and optional:
        return None
    if isinstance(value, date):
        return value
    raise ObservationUnavailable(f"upstream fact has no valid {field}")


def _as_decimal(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ObservationUnavailable("upstream fact has no numeric value")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ObservationUnavailable("upstream fact has no numeric value") from exc


class EdgarAdapter:
    """Fetch the bounded canonical facts admitted by Phase 1 validation."""

    def __init__(self, company_factory: Callable[[str], Any] | None = None) -> None:
        self._company_factory = company_factory or _default_company_factory
        self._companies: dict[str, Any] = {}

    def _company(self, ticker: str) -> Any:
        if ticker not in self._companies:
            self._companies[ticker] = self._company_factory(ticker)
        return self._companies[ticker]

    @staticmethod
    def _check_semantic_exclusion(ticker: str, metric: MetricCode) -> None:
        reason = _SEMANTIC_EXCLUSIONS.get((ticker, metric))
        if reason:
            raise ObservationUnavailable(
                f"{metric} is not meaningful for {ticker}", reason_code=reason
            )

    def annual_observation(
        self,
        ticker: str,
        metric: MetricCode,
        fiscal_year: int,
    ) -> FinancialObservation:
        """Return one direct annual observation with auditable provenance."""
        definition = METRICS[metric]
        if definition.period_kind is not PeriodKind.DURATION:
            raise ObservationUnavailable(
                f"{metric} is an instant metric; annual duration lookup is invalid"
            )

        normalized_ticker = ticker.strip().upper()
        self._check_semantic_exclusion(normalized_ticker, metric)
        concepts = _ANNUAL_CONCEPTS.get(metric)
        if not concepts:
            raise ObservationUnavailable(f"{metric} has not entered Phase 1 support")

        company = self._company(normalized_ticker)
        entity_facts = company.get_facts()
        if entity_facts is None:
            raise ObservationUnavailable(f"no EdgarTools company facts for {normalized_ticker}")

        fact = None
        for concept in concepts:
            # A bounded metric may intentionally try a retired issuer concept
            # before its current one. EdgarTools warns on that expected miss;
            # the adapter reports unavailable only after all admitted concepts.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                candidate = entity_facts.get_annual_fact(
                    concept, fiscal_year=fiscal_year
                )
            if candidate is not None:
                fact = candidate
                break
        if fact is None:
            raise ObservationUnavailable(
                f"no direct annual {metric} fact for {normalized_ticker} FY{fiscal_year}; "
                "the metric has not entered a validated issuer capability for this period"
            )

        return self._convert_direct_annual(
            fact=fact,
            ticker=normalized_ticker,
            cik=getattr(company, "cik", None),
            metric=metric,
            fiscal_year=fiscal_year,
        )

    def instant_observation(
        self,
        ticker: str,
        metric: MetricCode,
        fiscal_year: int,
    ) -> FinancialObservation:
        """Return a directly reported fiscal-year-end instant observation."""
        definition = METRICS[metric]
        if definition.period_kind is not PeriodKind.INSTANT:
            raise ObservationUnavailable(
                f"{metric} is a duration metric; instant lookup is invalid"
            )

        normalized_ticker = ticker.strip().upper()
        self._check_semantic_exclusion(normalized_ticker, metric)
        concepts = _INSTANT_CONCEPTS.get(metric)
        if not concepts:
            raise ObservationUnavailable(f"{metric} has not entered Phase 1 support")

        company = self._company(normalized_ticker)
        entity_facts = company.get_facts()
        if entity_facts is None:
            raise ObservationUnavailable(f"no EdgarTools company facts for {normalized_ticker}")

        candidates: list[Any] = []
        for concept in concepts:
            candidates.extend(
                entity_facts.query()
                .by_concept(concept, exact=True)
                .by_fiscal_year(fiscal_year)
                .by_fiscal_period("FY")
                .by_form_type("10-K")
                .execute()
            )
        candidates = [
            fact
            for fact in candidates
            if getattr(fact, "period_type", None) == "instant"
            and not getattr(fact, "dimensions", None)
            and isinstance(getattr(fact, "period_end", None), date)
        ]
        if not candidates and metric is MetricCode.TOTAL_LIABILITIES:
            return self._derived_total_liabilities(
                entity_facts, normalized_ticker, getattr(company, "cik", None),
                fiscal_year, FiscalPeriod.FY, FiscalPeriod.FY,
            )
        if not candidates:
            raise ObservationUnavailable(
                f"no direct fiscal-year-end {metric} fact for {normalized_ticker} FY{fiscal_year}"
            )

        # An annual filing commonly repeats the prior year-end instant. The
        # greatest period end is the requested fiscal-year closing balance.
        fact = max(
            candidates,
            key=lambda candidate: (
                candidate.period_end,
                getattr(candidate, "filing_date", None) or date.min,
            ),
        )
        return self._convert_direct_instant(
            fact=fact,
            ticker=normalized_ticker,
            cik=getattr(company, "cik", None),
            metric=metric,
            fiscal_year=fiscal_year,
        )

    def quarterly_observation(
        self,
        ticker: str,
        metric: MetricCode,
        fiscal_year: int,
        fiscal_period: FiscalPeriod,
    ) -> FinancialObservation:
        """Return EdgarTools' direct or safely quarterized duration fact."""
        if fiscal_period is FiscalPeriod.FY:
            raise ObservationUnavailable("quarterly lookup requires Q1, Q2, Q3, or Q4")
        definition = METRICS[metric]
        if definition.period_kind is not PeriodKind.DURATION:
            raise ObservationUnavailable(f"{metric} is not a duration metric")
        normalized_ticker = ticker.strip().upper()
        self._check_semantic_exclusion(normalized_ticker, metric)
        concepts = _ANNUAL_CONCEPTS.get(metric)
        if not concepts:
            raise ObservationUnavailable(f"{metric} has not entered Phase 1 support")

        company = self._company(normalized_ticker)
        entity_facts = company.get_facts()
        if entity_facts is None:
            raise ObservationUnavailable(f"no EdgarTools company facts for {normalized_ticker}")

        # EdgarTools 5.53.0's public quarterly statement APIs reproduce these
        # values but expose statement rows, not the FinancialFact provenance
        # (accession, calculation_context, and source periods) required here.
        # Public TTM APIs return aggregates rather than one requested quarter.
        # The version-pinned private fact view is therefore intentionally used
        # until EdgarTools offers an equivalent provenance-preserving API.
        prepared_facts = entity_facts._ttm_ready_facts
        candidates: list[Any] = []
        for concept in concepts:
            candidates.extend(
                prepared_facts.query()
                .by_concept(concept, exact=True)
                .by_fiscal_year(fiscal_year)
                .by_fiscal_period(fiscal_period.value)
                .execute()
            )
        candidates = [
            fact
            for fact in candidates
            if getattr(fact, "period_type", None) == "duration"
            and not getattr(fact, "dimensions", None)
            and getattr(fact, "period_start", None)
            and getattr(fact, "period_end", None)
            and 70 <= (fact.period_end - fact.period_start).days <= 120
        ]
        latest_prepared_end = max(
            (fact.period_end for fact in candidates), default=date.min
        )
        if fiscal_period in {FiscalPeriod.Q2, FiscalPeriod.Q3}:
            derived = self._derive_latest_ytd_quarter(
                entity_facts, normalized_ticker, getattr(company, "cik", None),
                metric, fiscal_year, fiscal_period, concepts,
            )
            if derived is not None and derived.period_end > latest_prepared_end:
                return derived
        if fiscal_period is FiscalPeriod.Q4:
            derived = (
                self._derive_latest_stable_eps_q4(
                    entity_facts, normalized_ticker, getattr(company, "cik", None),
                    fiscal_year,
                )
                if metric is MetricCode.DILUTED_EPS
                else self._derive_latest_q4(
                    entity_facts, normalized_ticker, getattr(company, "cik", None),
                    metric, fiscal_year, concepts,
                )
            )
            if derived is not None and derived.period_end > latest_prepared_end:
                return derived
        if not candidates:
            raise ObservationUnavailable(
                f"no standalone {metric} fact for {normalized_ticker} "
                f"{fiscal_period} FY{fiscal_year}"
            )
        fact = max(
            candidates,
            key=lambda candidate: (
                getattr(candidate, "period_end", None) or date.min,
                getattr(candidate, "filing_date", None) or date.min,
            ),
        )

        calculation = getattr(fact, "calculation_context", None)
        if calculation == "derived_eps_stable_shares":
            sources = self._eps_stable_share_sources(entity_facts, fact, fiscal_year)
            return self._convert_quarter(
                fact=fact, ticker=normalized_ticker, cik=getattr(company, "cik", None),
                metric=metric, fiscal_year=fiscal_year, fiscal_period=fiscal_period,
                derivation=DerivationKind.PERIOD_DERIVED, sources=sources,
            )
        if calculation == "derived_q4_fy_minus_ytd9":
            sources = self._q4_sources(entity_facts, fact, fiscal_year, concepts)
            return self._convert_quarter(
                fact=fact,
                ticker=normalized_ticker,
                cik=getattr(company, "cik", None),
                metric=metric,
                fiscal_year=fiscal_year,
                fiscal_period=fiscal_period,
                derivation=DerivationKind.PERIOD_DERIVED,
                sources=sources,
            )
        if calculation == "derived_q2_ytd6_minus_q1":
            sources = self._q2_sources(entity_facts, fact, fiscal_year)
            return self._convert_quarter(
                fact=fact,
                ticker=normalized_ticker,
                cik=getattr(company, "cik", None),
                metric=metric,
                fiscal_year=fiscal_year,
                fiscal_period=fiscal_period,
                derivation=DerivationKind.PERIOD_DERIVED,
                sources=sources,
            )
        if calculation == "derived_q3_ytd9_minus_ytd6":
            sources = self._q3_sources(entity_facts, fact, fiscal_year)
            return self._convert_quarter(
                fact=fact,
                ticker=normalized_ticker,
                cik=getattr(company, "cik", None),
                metric=metric,
                fiscal_year=fiscal_year,
                fiscal_period=fiscal_period,
                derivation=DerivationKind.PERIOD_DERIVED,
                sources=sources,
            )
        if calculation:
            raise ObservationUnavailable(f"unsupported EdgarTools derivation: {calculation}")
        return self._convert_quarter(
            fact=fact,
            ticker=normalized_ticker,
            cik=getattr(company, "cik", None),
            metric=metric,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            derivation=DerivationKind.DIRECT,
            sources=(),
        )

    @staticmethod
    def _derive_latest_ytd_quarter(
        entity_facts: Any, ticker: str, cik: Any, metric: MetricCode,
        fiscal_year: int, fiscal_period: FiscalPeriod,
        concepts: tuple[str, ...],
    ) -> FinancialObservation | None:
        """Quarterize the latest compatible YTD pair when prepared facts lag.

        Comparative facts in a later 10-Q can share the current fiscal-year
        label upstream. Selecting only prepared standalone facts can therefore
        retain the older comparative quarter. The latest same-start YTD pair
        provides an unambiguous, filing-backed correction.
        """
        raw: list[Any] = []
        for concept in concepts:
            raw.extend(
                entity_facts.query().by_concept(concept, exact=True)
                .by_fiscal_year(fiscal_year).execute()
            )
        compatible = [
            fact for fact in raw
            if getattr(fact, "period_type", None) == "duration"
            and not getattr(fact, "dimensions", None)
            and getattr(fact, "period_start", None)
            and getattr(fact, "period_end", None)
            and getattr(fact, "form_type", None) == "10-Q"
        ]
        target_days = (150, 220) if fiscal_period is FiscalPeriod.Q2 else (230, 310)
        prior_days = (70, 120) if fiscal_period is FiscalPeriod.Q2 else (150, 220)
        targets = [
            fact for fact in compatible
            if fact.fiscal_period == fiscal_period.value
            and target_days[0] <= (fact.period_end - fact.period_start).days <= target_days[1]
        ]
        for target in sorted(
            targets,
            key=lambda fact: (fact.period_end, fact.filing_date or date.min),
            reverse=True,
        ):
            priors = [
                fact for fact in compatible
                if fact.concept == target.concept and fact.unit == target.unit
                and fact.period_start == target.period_start
                and prior_days[0] <= (fact.period_end - fact.period_start).days <= prior_days[1]
                and fact.period_end < target.period_end
            ]
            if not priors:
                continue
            prior = max(priors, key=lambda fact: (fact.period_end, fact.filing_date or date.min))
            value = _as_decimal(target.value) - _as_decimal(prior.value)
            raw_cik = str(cik or "").removeprefix("CIK")
            if not raw_cik.isdigit():
                raise ObservationUnavailable("company has no valid CIK")
            return FinancialObservation(
                ticker=ticker, cik=raw_cik.zfill(10), metric=metric, value=value,
                unit=target.unit,
                currency=target.unit.upper() if target.unit.upper() in {"USD", "EUR", "GBP", "JPY", "CAD", "CHF"} else None,
                period_kind=PeriodKind.DURATION,
                period_start=prior.period_end + timedelta(days=1), period_end=target.period_end,
                fiscal_year=fiscal_year, fiscal_period=fiscal_period,
                duration_scope=DurationScope.STANDALONE_QUARTER,
                derivation=DerivationKind.PERIOD_DERIVED,
                source_concept=target.concept, accession_number=target.accession,
                filing_form=target.form_type, filing_date=target.filing_date,
                quality_flags=("generic_latest_ytd_pair",),
                derivation_sources=(
                    EdgarAdapter._source_identity(
                        "SIX_MONTH_YTD" if fiscal_period is FiscalPeriod.Q2 else "NINE_MONTH_YTD",
                        target,
                    ),
                    EdgarAdapter._source_identity(
                        "THREE_MONTH_YTD" if fiscal_period is FiscalPeriod.Q2 else "SIX_MONTH_YTD",
                        prior,
                    ),
                ),
            )
        return None

    @staticmethod
    def _derive_latest_q4(
        entity_facts: Any, ticker: str, cik: Any, metric: MetricCode,
        fiscal_year: int, concepts: tuple[str, ...],
    ) -> FinancialObservation | None:
        raw: list[Any] = []
        for concept in concepts:
            raw.extend(
                entity_facts.query().by_concept(concept, exact=True)
                .by_fiscal_year(fiscal_year).execute()
            )
        compatible = [
            fact for fact in raw
            if getattr(fact, "period_type", None) == "duration"
            and not getattr(fact, "dimensions", None)
            and getattr(fact, "period_start", None)
            and getattr(fact, "period_end", None)
        ]
        annuals = [
            fact for fact in compatible
            if fact.fiscal_period == "FY" and fact.form_type == "10-K"
            and 300 <= (fact.period_end - fact.period_start).days <= 400
        ]
        for annual in sorted(
            annuals,
            key=lambda fact: (fact.period_end, fact.filing_date or date.min),
            reverse=True,
        ):
            ytds = [
                fact for fact in compatible
                if fact.concept in concepts and fact.unit == annual.unit
                and fact.period_start == annual.period_start
                and fact.fiscal_period == "Q3"
                and 230 <= (fact.period_end - fact.period_start).days <= 310
                and fact.period_end < annual.period_end
            ]
            if not ytds:
                continue
            ytd = max(ytds, key=lambda fact: (fact.period_end, fact.filing_date or date.min))
            raw_cik = str(cik or "").removeprefix("CIK")
            if not raw_cik.isdigit():
                raise ObservationUnavailable("company has no valid CIK")
            return FinancialObservation(
                ticker=ticker, cik=raw_cik.zfill(10), metric=metric,
                value=_as_decimal(annual.value) - _as_decimal(ytd.value), unit=annual.unit,
                currency=annual.unit.upper() if annual.unit.upper() in {"USD", "EUR", "GBP", "JPY", "CAD", "CHF"} else None,
                period_kind=PeriodKind.DURATION,
                period_start=ytd.period_end + timedelta(days=1), period_end=annual.period_end,
                fiscal_year=fiscal_year, fiscal_period=FiscalPeriod.Q4,
                duration_scope=DurationScope.STANDALONE_QUARTER,
                derivation=DerivationKind.PERIOD_DERIVED,
                source_concept=annual.concept, accession_number=annual.accession,
                filing_form=annual.form_type, filing_date=annual.filing_date,
                quality_flags=("generic_latest_fy_minus_ytd9",),
                derivation_sources=(
                    EdgarAdapter._source_identity("FISCAL_YEAR", annual),
                    EdgarAdapter._source_identity("NINE_MONTH_YTD", ytd),
                ),
            )
        return None

    @staticmethod
    def _derive_latest_stable_eps_q4(
        entity_facts: Any, ticker: str, cik: Any, fiscal_year: int,
    ) -> FinancialObservation | None:
        raw: dict[MetricCode, list[Any]] = {}
        for metric in (MetricCode.NET_INCOME, MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES):
            facts: list[Any] = []
            for concept in _ANNUAL_CONCEPTS[metric]:
                facts.extend(
                    entity_facts.query().by_concept(concept, exact=True)
                    .by_fiscal_year(fiscal_year).execute()
                )
            raw[metric] = [fact for fact in facts if not getattr(fact, "dimensions", None)]
        annual_incomes = [
            fact for fact in raw[MetricCode.NET_INCOME]
            if fact.fiscal_period == "FY" and fact.form_type == "10-K"
            and fact.period_start and 300 <= (fact.period_end - fact.period_start).days <= 400
        ]
        for annual_income in sorted(
            annual_incomes,
            key=lambda fact: (fact.period_end, fact.filing_date or date.min), reverse=True,
        ):
            ytds = [
                fact for fact in raw[MetricCode.NET_INCOME]
                if fact.fiscal_period == "Q3" and fact.period_start == annual_income.period_start
                and fact.unit == annual_income.unit and fact.period_end < annual_income.period_end
            ]
            shares = [
                fact for fact in raw[MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES]
                if fact.fiscal_period == "FY" and fact.period_start == annual_income.period_start
                and fact.period_end == annual_income.period_end
            ]
            if not ytds or not shares:
                continue
            ytd = max(ytds, key=lambda fact: (fact.period_end, fact.filing_date or date.min))
            share = max(shares, key=lambda fact: fact.filing_date or date.min)
            share_value = _as_decimal(share.value)
            if share_value <= 0:
                continue
            raw_cik = str(cik or "").removeprefix("CIK")
            if not raw_cik.isdigit():
                raise ObservationUnavailable("company has no valid CIK")
            return FinancialObservation(
                ticker=ticker, cik=raw_cik.zfill(10), metric=MetricCode.DILUTED_EPS,
                value=(_as_decimal(annual_income.value) - _as_decimal(ytd.value)) / share_value,
                unit="USD/shares" if annual_income.unit == "USD" else f"{annual_income.unit}/shares",
                currency=annual_income.unit.upper() if annual_income.unit.upper() in {"USD", "EUR", "GBP", "JPY", "CAD", "CHF"} else None,
                period_kind=PeriodKind.DURATION,
                period_start=ytd.period_end + timedelta(days=1), period_end=annual_income.period_end,
                fiscal_year=fiscal_year, fiscal_period=FiscalPeriod.Q4,
                duration_scope=DurationScope.STANDALONE_QUARTER,
                derivation=DerivationKind.PERIOD_DERIVED,
                source_concept="us-gaap:EarningsPerShareDiluted",
                accession_number=annual_income.accession, filing_form=annual_income.form_type,
                filing_date=annual_income.filing_date,
                quality_flags=("generic_latest_stable_share_eps",),
                derivation_sources=(
                    EdgarAdapter._source_identity("FISCAL_YEAR_NET_INCOME", annual_income),
                    EdgarAdapter._source_identity("NINE_MONTH_NET_INCOME", ytd),
                    EdgarAdapter._source_identity("FISCAL_YEAR_DILUTED_SHARES", share),
                ),
            )
        return None

    @staticmethod
    def _eps_stable_share_sources(
        entity_facts: Any, derived_fact: Any, fiscal_year: int,
    ) -> tuple[SourceFactIdentity, ...]:
        raw: dict[MetricCode, list[Any]] = {}
        for metric in (MetricCode.NET_INCOME, MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES):
            facts: list[Any] = []
            for concept in _ANNUAL_CONCEPTS[metric]:
                facts.extend(
                    entity_facts.query().by_concept(concept, exact=True)
                    .by_fiscal_year(fiscal_year).execute()
                )
            raw[metric] = [fact for fact in facts if not getattr(fact, "dimensions", None)]
        annual_income = [
            fact for fact in raw[MetricCode.NET_INCOME]
            if fact.fiscal_period == "FY" and fact.period_end == derived_fact.period_end
        ]
        if not annual_income:
            raise ObservationUnavailable("stable-share EPS has no auditable FY net-income source")
        annual_income_fact = max(annual_income, key=lambda fact: fact.filing_date or date.min)
        ytd_income = [
            fact for fact in raw[MetricCode.NET_INCOME]
            if fact.fiscal_period == "Q3"
            and fact.period_start == annual_income_fact.period_start
            and fact.period_end < annual_income_fact.period_end
            and fact.unit == annual_income_fact.unit
        ]
        if not ytd_income:
            raise ObservationUnavailable("stable-share EPS has no auditable 9M net-income source")
        ytd_income_fact = max(ytd_income, key=lambda fact: (fact.period_end, fact.filing_date or date.min))
        annual_shares = [
            fact for fact in raw[MetricCode.DILUTED_WEIGHTED_AVERAGE_SHARES]
            if fact.fiscal_period == "FY"
            and fact.period_start == annual_income_fact.period_start
            and fact.period_end == annual_income_fact.period_end
        ]
        if not annual_shares:
            raise ObservationUnavailable("stable-share EPS has no auditable FY diluted-share source")
        annual_share_fact = max(annual_shares, key=lambda fact: fact.filing_date or date.min)
        shares = _as_decimal(annual_share_fact.value)
        if shares <= 0:
            raise ObservationUnavailable("stable-share EPS has non-positive diluted shares")
        expected = (
            _as_decimal(annual_income_fact.value) - _as_decimal(ytd_income_fact.value)
        ) / shares
        actual = _as_decimal(derived_fact.value)
        if abs(expected - actual) > Decimal("0.00000001"):
            raise ObservationUnavailable("stable-share EPS does not reconcile to its operands")
        return (
            EdgarAdapter._source_identity("FISCAL_YEAR_NET_INCOME", annual_income_fact),
            EdgarAdapter._source_identity("NINE_MONTH_NET_INCOME", ytd_income_fact),
            EdgarAdapter._source_identity("FISCAL_YEAR_DILUTED_SHARES", annual_share_fact),
        )

    def quarterly_instant_observation(
        self, ticker: str, metric: MetricCode, fiscal_year: int,
        fiscal_period: FiscalPeriod,
    ) -> FinancialObservation:
        """Return a directly reported fiscal-quarter-end balance snapshot."""
        if fiscal_period is FiscalPeriod.FY:
            raise ObservationUnavailable("quarterly instant lookup requires Q1 through Q4")
        if METRICS[metric].period_kind is not PeriodKind.INSTANT:
            raise ObservationUnavailable(f"{metric} is not an instant metric")
        normalized_ticker = ticker.strip().upper()
        self._check_semantic_exclusion(normalized_ticker, metric)
        concepts = _INSTANT_CONCEPTS.get(metric)
        if not concepts:
            raise ObservationUnavailable(f"{metric} has not entered Phase 1 support")
        company = self._company(normalized_ticker)
        entity_facts = company.get_facts()
        if entity_facts is None:
            raise ObservationUnavailable(f"no EdgarTools company facts for {normalized_ticker}")
        candidates: list[Any] = []
        source_period = FiscalPeriod.FY if fiscal_period is FiscalPeriod.Q4 else fiscal_period
        for concept in concepts:
            candidates.extend(
                entity_facts.query().by_concept(concept, exact=True)
                .by_fiscal_year(fiscal_year).by_fiscal_period(source_period.value).execute()
            )
        candidates = [
            fact for fact in candidates
            if getattr(fact, "period_type", None) == "instant"
            and not getattr(fact, "dimensions", None)
            and getattr(fact, "form_type", None) in {"10-Q", "10-K"}
            and isinstance(getattr(fact, "period_end", None), date)
        ]
        if not candidates and metric is MetricCode.TOTAL_LIABILITIES:
            return self._derived_total_liabilities(
                entity_facts, normalized_ticker, getattr(company, "cik", None),
                fiscal_year, fiscal_period, source_period,
            )
        if not candidates:
            raise ObservationUnavailable(
                f"no direct {metric} snapshot for {normalized_ticker} "
                f"{fiscal_period.value} FY{fiscal_year}"
            )
        fact = max(
            candidates,
            key=lambda item: (item.period_end, getattr(item, "filing_date", None) or date.min),
        )
        return self._convert_direct_instant(
            fact=fact, ticker=normalized_ticker, cik=getattr(company, "cik", None),
            metric=metric, fiscal_year=fiscal_year, fiscal_period=fiscal_period,
            source_fiscal_period=source_period,
        )

    @staticmethod
    def _derived_total_liabilities(
        entity_facts: Any, ticker: str, cik: Any, fiscal_year: int,
        fiscal_period: FiscalPeriod, source_period: FiscalPeriod,
    ) -> FinancialObservation:
        """Derive consolidated liabilities as assets minus consolidated equity.

        This is used only when the direct standard Liabilities concept is
        absent. Both operands must be dimensionless, same-period, same-unit
        standard US-GAAP instant facts, preserving an auditable identity for
        each source.
        """
        def facts(concept: str) -> list[Any]:
            return [
                fact for fact in entity_facts.query().by_concept(concept, exact=True)
                .by_fiscal_year(fiscal_year).by_fiscal_period(source_period.value).execute()
                if getattr(fact, "period_type", None) == "instant"
                and not getattr(fact, "dimensions", None)
                and getattr(fact, "form_type", None) in {"10-Q", "10-K"}
                and isinstance(getattr(fact, "period_end", None), date)
            ]

        assets = facts("us-gaap:Assets")
        equities = facts(
            "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
        )
        pairs = [
            (asset, equity) for asset in assets for equity in equities
            if asset.period_end == equity.period_end and asset.unit == equity.unit
        ]
        if not pairs:
            raise ObservationUnavailable(
                "total liabilities absent and assets/consolidated-equity operands unavailable"
            )
        asset, equity = max(
            pairs,
            key=lambda pair: (
                pair[0].period_end,
                pair[0].filing_date or date.min,
                pair[1].filing_date or date.min,
            ),
        )
        value = _as_decimal(asset.value) - _as_decimal(equity.value)
        if value < 0:
            raise ObservationUnavailable("assets-minus-equity produced negative liabilities")
        raw_cik = str(cik or "").removeprefix("CIK")
        if not raw_cik.isdigit():
            raise ObservationUnavailable("company has no valid CIK")
        return FinancialObservation(
            ticker=ticker, cik=raw_cik.zfill(10), metric=MetricCode.TOTAL_LIABILITIES,
            value=value, unit=asset.unit,
            currency=asset.unit.upper() if asset.unit.upper() in {"USD", "EUR", "GBP", "JPY", "CAD", "CHF"} else None,
            period_kind=PeriodKind.INSTANT, period_start=None, period_end=asset.period_end,
            fiscal_year=fiscal_year, fiscal_period=fiscal_period, duration_scope=None,
            derivation=DerivationKind.CALCULATION_DERIVED,
            source_concept="derived:AssetsMinusConsolidatedEquity",
            accession_number=asset.accession, filing_form=asset.form_type,
            filing_date=asset.filing_date,
            quality_flags=("assets_minus_consolidated_equity",),
            derivation_sources=(
                EdgarAdapter._source_identity("TOTAL_ASSETS", asset),
                EdgarAdapter._source_identity("CONSOLIDATED_EQUITY", equity),
            ),
        )

    @staticmethod
    def _q4_sources(
        entity_facts: Any,
        derived_fact: Any,
        fiscal_year: int,
        admitted_concepts: tuple[str, ...],
    ) -> tuple[SourceFactIdentity, ...]:
        # Issuers can transition between two admitted US-GAAP aliases within a
        # fiscal year. EdgarTools may safely prepare the Q4 value across that
        # transition, so locate its auditable FY and 9M inputs across the same
        # bounded metric concept set rather than only the derived fact's alias.
        raw_facts = []
        for concept in admitted_concepts:
            raw_facts.extend(
                entity_facts.query()
                .by_concept(concept, exact=True)
                .by_fiscal_year(fiscal_year)
                .execute()
            )
        compatible = [
            fact
            for fact in raw_facts
            if getattr(fact, "period_type", None) == "duration"
            and getattr(fact, "unit", None) == derived_fact.unit
            and not getattr(fact, "dimensions", None)
        ]
        annuals = [
            fact
            for fact in compatible
            if fact.fiscal_period == "FY" and fact.period_end == derived_fact.period_end
        ]
        if not annuals:
            raise ObservationUnavailable("EdgarTools Q4 derivation has no auditable FY source")
        annual = max(annuals, key=lambda fact: fact.filing_date or date.min)
        ytd_facts = [
            fact
            for fact in compatible
            if fact.fiscal_period == "Q3"
            and fact.period_start == annual.period_start
            and fact.period_end < derived_fact.period_end
        ]
        if not ytd_facts:
            raise ObservationUnavailable("EdgarTools Q4 derivation has no auditable 9M source")
        ytd = max(ytd_facts, key=lambda fact: (fact.period_end, fact.filing_date or date.min))
        if _as_decimal(annual.value) - _as_decimal(ytd.value) != _as_decimal(derived_fact.value):
            raise ObservationUnavailable("EdgarTools Q4 value does not reconcile to FY minus 9M")
        return (
            EdgarAdapter._source_identity("FISCAL_YEAR", annual),
            EdgarAdapter._source_identity("NINE_MONTH_YTD", ytd),
        )

    @staticmethod
    def _q2_sources(entity_facts: Any, derived_fact: Any, fiscal_year: int) -> tuple[SourceFactIdentity, ...]:
        raw_facts = (
            entity_facts.query()
            .by_concept(derived_fact.concept, exact=True)
            .by_fiscal_year(fiscal_year)
            .execute()
        )
        compatible = [
            fact
            for fact in raw_facts
            if getattr(fact, "period_type", None) == "duration"
            and getattr(fact, "unit", None) == derived_fact.unit
            and not getattr(fact, "dimensions", None)
        ]
        six_month_facts = [
            fact
            for fact in compatible
            if fact.fiscal_period == "Q2"
            and fact.period_end == derived_fact.period_end
            and fact.period_start < derived_fact.period_start
        ]
        if not six_month_facts:
            raise ObservationUnavailable("EdgarTools Q2 derivation has no auditable 6M source")
        six_month = max(
            six_month_facts,
            key=lambda fact: (fact.period_start, fact.filing_date or date.min),
        )
        q1_facts = [
            fact
            for fact in compatible
            if fact.fiscal_period == "Q1"
            and fact.period_start == six_month.period_start
            and fact.period_end < derived_fact.period_start
        ]
        if not q1_facts:
            raise ObservationUnavailable("EdgarTools Q2 derivation has no auditable Q1 source")
        q1 = max(q1_facts, key=lambda fact: (fact.period_end, fact.filing_date or date.min))
        if _as_decimal(six_month.value) - _as_decimal(q1.value) != _as_decimal(derived_fact.value):
            raise ObservationUnavailable("EdgarTools Q2 value does not reconcile to 6M minus Q1")
        return (
            EdgarAdapter._source_identity("SIX_MONTH_YTD", six_month),
            EdgarAdapter._source_identity("THREE_MONTH_YTD", q1),
        )

    @staticmethod
    def _q3_sources(entity_facts: Any, derived_fact: Any, fiscal_year: int) -> tuple[SourceFactIdentity, ...]:
        raw_facts = (
            entity_facts.query()
            .by_concept(derived_fact.concept, exact=True)
            .by_fiscal_year(fiscal_year)
            .execute()
        )
        compatible = [
            fact for fact in raw_facts
            if getattr(fact, "period_type", None) == "duration"
            and getattr(fact, "unit", None) == derived_fact.unit
            and not getattr(fact, "dimensions", None)
        ]
        nine_month_facts = [
            fact for fact in compatible
            if fact.fiscal_period == "Q3"
            and fact.period_end == derived_fact.period_end
            and fact.period_start < derived_fact.period_start
        ]
        if not nine_month_facts:
            raise ObservationUnavailable("EdgarTools Q3 derivation has no auditable 9M source")
        nine_month = max(
            nine_month_facts,
            key=lambda fact: (fact.period_start, fact.filing_date or date.min),
        )
        six_month_facts = [
            fact for fact in compatible
            if fact.fiscal_period == "Q2"
            and fact.period_start == nine_month.period_start
            and fact.period_end < derived_fact.period_start
        ]
        if not six_month_facts:
            raise ObservationUnavailable("EdgarTools Q3 derivation has no auditable 6M source")
        six_month = max(
            six_month_facts,
            key=lambda fact: (fact.period_end, fact.filing_date or date.min),
        )
        if _as_decimal(nine_month.value) - _as_decimal(six_month.value) != _as_decimal(derived_fact.value):
            raise ObservationUnavailable("EdgarTools Q3 value does not reconcile to 9M minus 6M")
        return (
            EdgarAdapter._source_identity("NINE_MONTH_YTD", nine_month),
            EdgarAdapter._source_identity("SIX_MONTH_YTD", six_month),
        )

    @staticmethod
    def _source_identity(role: str, fact: Any) -> SourceFactIdentity:
        return SourceFactIdentity(
            role=role,
            source_concept=fact.concept,
            value=_as_decimal(fact.value),
            unit=fact.unit,
            period_start=_as_date(fact.period_start, "period_start", optional=True),
            period_end=_as_date(fact.period_end, "period_end"),
            accession_number=fact.accession,
            filing_form=fact.form_type,
            filing_date=_as_date(fact.filing_date, "filing_date", optional=True),
        )

    @staticmethod
    def _convert_quarter(
        *,
        fact: Any,
        ticker: str,
        cik: Any,
        metric: MetricCode,
        fiscal_year: int,
        fiscal_period: FiscalPeriod,
        derivation: DerivationKind,
        sources: tuple[SourceFactIdentity, ...],
    ) -> FinancialObservation:
        if getattr(fact, "fiscal_period", None) != fiscal_period.value:
            raise ObservationUnavailable("quarter resolved to the wrong fiscal period")
        raw_cik = str(cik or "").removeprefix("CIK")
        if not raw_cik.isdigit():
            raise ObservationUnavailable("company has no valid CIK")
        unit = getattr(fact, "unit", "")
        if not all((fact.concept, fact.accession, fact.form_type, unit)):
            raise ObservationUnavailable("upstream quarter has incomplete provenance")
        currency = unit.upper() if unit.upper() in {"USD", "EUR", "GBP", "JPY", "CAD", "CHF"} else None
        flags = tuple(
            flag
            for flag in (
                getattr(fact, "calculation_context", None),
                f"upstream_quality:{fact.data_quality.value}"
                if getattr(getattr(fact, "data_quality", None), "value", "high") != "high"
                else None,
            )
            if flag
        )
        return FinancialObservation(
            ticker=ticker,
            cik=raw_cik.zfill(10),
            metric=metric,
            value=_as_decimal(fact.value),
            unit=unit,
            currency=currency,
            period_kind=PeriodKind.DURATION,
            period_start=_as_date(fact.period_start, "period_start"),
            period_end=_as_date(fact.period_end, "period_end"),
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            duration_scope=DurationScope.STANDALONE_QUARTER,
            derivation=derivation,
            source_concept=fact.concept,
            accession_number=fact.accession,
            filing_form=fact.form_type,
            filing_date=_as_date(fact.filing_date, "filing_date", optional=True),
            quality_flags=flags,
            derivation_sources=sources,
        )

    @staticmethod
    def _convert_direct_annual(
        *,
        fact: Any,
        ticker: str,
        cik: Any,
        metric: MetricCode,
        fiscal_year: int,
    ) -> FinancialObservation:
        """Convert a selected EdgarTools fact, rejecting ambiguous provenance."""
        if getattr(fact, "period_type", None) != "duration":
            raise ObservationUnavailable("annual duration metric resolved to an instant fact")
        if getattr(fact, "fiscal_period", None) != "FY":
            raise ObservationUnavailable("annual metric resolved to a non-FY fact")
        if getattr(fact, "fiscal_year", None) != fiscal_year:
            raise ObservationUnavailable("annual metric resolved to the wrong fiscal year")

        concept = getattr(fact, "concept", "")
        accession = getattr(fact, "accession", "")
        form_type = getattr(fact, "form_type", "")
        unit = getattr(fact, "unit", "")
        if not all((concept, accession, form_type, unit)):
            raise ObservationUnavailable("upstream fact has incomplete provenance")

        raw_cik = str(cik or "").removeprefix("CIK")
        if not raw_cik.isdigit():
            raise ObservationUnavailable("company has no valid CIK")

        flags: list[str] = []
        if getattr(fact, "is_restated", False):
            flags.append("restated")
        data_quality = getattr(getattr(fact, "data_quality", None), "value", None)
        if data_quality and data_quality != "high":
            flags.append(f"upstream_quality:{data_quality}")

        currency = unit.upper() if unit.upper() in {"USD", "EUR", "GBP", "JPY", "CAD", "CHF"} else None
        return FinancialObservation(
            ticker=ticker,
            cik=raw_cik.zfill(10),
            metric=metric,
            value=_as_decimal(getattr(fact, "value", None)),
            unit=unit,
            currency=currency,
            period_kind=PeriodKind.DURATION,
            period_start=_as_date(getattr(fact, "period_start", None), "period_start"),
            period_end=_as_date(getattr(fact, "period_end", None), "period_end"),
            fiscal_year=fiscal_year,
            fiscal_period=FiscalPeriod.FY,
            duration_scope=DurationScope.ANNUAL,
            derivation=DerivationKind.DIRECT,
            source_concept=concept,
            accession_number=accession,
            filing_form=form_type,
            filing_date=_as_date(getattr(fact, "filing_date", None), "filing_date", optional=True),
            quality_flags=tuple(flags),
        )

    @staticmethod
    def _convert_direct_instant(
        *,
        fact: Any,
        ticker: str,
        cik: Any,
        metric: MetricCode,
        fiscal_year: int,
        fiscal_period: FiscalPeriod = FiscalPeriod.FY,
        source_fiscal_period: FiscalPeriod | None = None,
    ) -> FinancialObservation:
        """Convert a selected year-end instant without deriving a balance."""
        if getattr(fact, "period_type", None) != "instant":
            raise ObservationUnavailable("instant metric resolved to a duration fact")
        expected_source_period = source_fiscal_period or fiscal_period
        if getattr(fact, "fiscal_period", None) != expected_source_period.value:
            raise ObservationUnavailable("instant resolved to the wrong fiscal period")
        if getattr(fact, "fiscal_year", None) != fiscal_year:
            raise ObservationUnavailable("instant metric resolved to the wrong fiscal year")

        concept = getattr(fact, "concept", "")
        accession = getattr(fact, "accession", "")
        form_type = getattr(fact, "form_type", "")
        unit = getattr(fact, "unit", "")
        if not all((concept, accession, form_type, unit)):
            raise ObservationUnavailable("upstream fact has incomplete provenance")

        raw_cik = str(cik or "").removeprefix("CIK")
        if not raw_cik.isdigit():
            raise ObservationUnavailable("company has no valid CIK")

        flags: list[str] = []
        if getattr(fact, "is_restated", False):
            flags.append("restated")
        data_quality = getattr(getattr(fact, "data_quality", None), "value", None)
        if data_quality and data_quality != "high":
            flags.append(f"upstream_quality:{data_quality}")

        currency = unit.upper() if unit.upper() in {"USD", "EUR", "GBP", "JPY", "CAD", "CHF"} else None
        return FinancialObservation(
            ticker=ticker,
            cik=raw_cik.zfill(10),
            metric=metric,
            value=_as_decimal(getattr(fact, "value", None)),
            unit=unit,
            currency=currency,
            period_kind=PeriodKind.INSTANT,
            period_start=None,
            period_end=_as_date(getattr(fact, "period_end", None), "period_end"),
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            duration_scope=None,
            derivation=DerivationKind.DIRECT,
            source_concept=concept,
            accession_number=accession,
            filing_form=form_type,
            filing_date=_as_date(getattr(fact, "filing_date", None), "filing_date", optional=True),
            quality_flags=tuple(flags),
        )
