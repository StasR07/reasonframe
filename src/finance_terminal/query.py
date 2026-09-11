"""Typed deterministic query contract and execution engine."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .calculations import calculate_company_metric
from .macro import MACRO_CATALOG, SEMANTIC_MACRO_SERIES, calculate_yoy
from .market import (
    MarketDailyObservation, indexed_to_100, market_action_id, market_observation_id,
    maximum_drawdown, trailing_return,
)
from .metrics import DERIVED_METRIC_LABELS, METRICS, DerivedMetricCode, MetricCode
from .models import FinancialObservation, PeriodKind
from .series import CalculatedSeriesPoint, SeriesPoint, SourceSeriesPoint, observation_id
from .storage import SQLiteStore
from .valuation import (
    VALUATION_INPUTS, VALUATION_LABELS, ValuationMetricCode, ValuationState,
    annual_snapshots, current_snapshot, not_meaningful_reason, valuation_context,
)


class QueryOperation(StrEnum):
    LEVEL = "LEVEL"
    YOY_GROWTH = "YOY_GROWTH"
    QOQ_GROWTH = "QOQ_GROWTH"
    AVERAGE = "AVERAGE"
    CHANGE = "CHANGE"
    CAGR = "CAGR"


class RankingOperation(StrEnum):
    NONE = "NONE"
    HIGHEST = "HIGHEST"
    LOWEST = "LOWEST"


class ResultStatus(StrEnum):
    SUCCESS = "SUCCESS"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID = "INVALID"


Ticker = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12, pattern=r"^[A-Za-z0-9.-]+$")]
SeriesCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")]


class CompanyQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: Literal["company"] = "company"
    tickers: list[Ticker] = Field(min_length=1, max_length=60)
    metric: MetricCode | DerivedMetricCode
    frequency: Literal["annual", "quarterly"]
    start_year: int | None = Field(default=None, ge=2019)
    end_year: int | None = Field(default=None, ge=2019)
    operation: QueryOperation = QueryOperation.LEVEL
    ranking: RankingOperation = RankingOperation.NONE
    ranking_limit: int | None = Field(default=None, ge=1, le=20)
    universe_ranking: bool = False

    @model_validator(mode="after")
    def validate_range(self) -> "CompanyQuery":
        self.tickers = [ticker.strip().upper() for ticker in self.tickers]
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("start_year must not be after end_year")
        return self


class MacroQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: Literal["macro"] = "macro"
    series: list[SeriesCode] = Field(min_length=1, max_length=20)
    start_date: date | None = None
    end_date: date | None = None
    operation: Literal[QueryOperation.LEVEL, QueryOperation.AVERAGE] = QueryOperation.LEVEL

    @model_validator(mode="after")
    def validate_range(self) -> "MacroQuery":
        self.series = [code.strip().upper() for code in self.series]
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class MarketPriceSeries(StrEnum):
    RAW_CLOSE = "RAW_CLOSE"
    ADJUSTED_CLOSE = "ADJUSTED_CLOSE"


class MarketOperation(StrEnum):
    LEVEL = "LEVEL"
    RETURN = "RETURN"
    INDEXED = "INDEXED"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"


class MarketQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: Literal["market"] = "market"
    tickers: list[Ticker] = Field(min_length=1, max_length=4)
    series: MarketPriceSeries
    view: Literal["latest", "history"] = "history"
    start_date: date | None = None
    end_date: date | None = None
    operation: MarketOperation = MarketOperation.LEVEL

    @model_validator(mode="after")
    def validate_market(self) -> "MarketQuery":
        self.tickers = [ticker.strip().upper() for ticker in self.tickers]
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if self.view == "latest" and self.operation is not MarketOperation.LEVEL:
            raise ValueError("latest market view only supports LEVEL")
        if self.operation in {MarketOperation.RETURN, MarketOperation.INDEXED, MarketOperation.MAX_DRAWDOWN} and self.series is not MarketPriceSeries.ADJUSTED_CLOSE:
            raise ValueError("RETURN, INDEXED, and MAX_DRAWDOWN require ADJUSTED_CLOSE")
        if self.operation is MarketOperation.INDEXED and len(self.tickers) > 4:
            raise ValueError("INDEXED supports at most four companies")
        return self


class ValuationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: Literal["valuation"] = "valuation"
    tickers: list[Ticker] = Field(min_length=1, max_length=4)
    metric: ValuationMetricCode
    view: Literal["latest", "history"] = "latest"
    start_year: int | None = Field(default=None, ge=1900)
    end_year: int | None = Field(default=None, ge=1900)

    @model_validator(mode="after")
    def validate_valuation(self) -> "ValuationQuery":
        self.tickers = [ticker.strip().upper() for ticker in self.tickers]
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("start_year must not be after end_year")
        return self


QueryRequest = Annotated[CompanyQuery | MacroQuery | MarketQuery | ValuationQuery, Field(discriminator="domain")]


class SeriesPointResult(BaseModel):
    date: date
    value: Decimal
    point_type: Literal["SOURCE", "CALCULATED"]
    observation_id: str | None = None
    formula: str | None = None
    input_observation_ids: list[str] = Field(default_factory=list)
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    evidence: list["EvidenceResult"] = Field(default_factory=list)
    state: Literal["AVAILABLE", "UNAVAILABLE", "NOT_MEANINGFUL"] = "AVAILABLE"
    valuation_basis: str | None = None
    source_fiscal_year: int | None = None
    availability_date: date | None = None


class EvidenceResult(BaseModel):
    id: str
    metric: str
    label: str
    value: Decimal
    unit: str
    source_type: Literal["SEC", "FRED", "MARKET"]
    source_concept: str | None = None
    accession_number: str | None = None
    filing_form: str | None = None
    filing_date: date | None = None
    period_start: date | None = None
    period_end: date
    derivation: str | None = None
    provider_series_id: str | None = None
    provider: str | None = None
    instrument_id: str | None = None
    symbol: str | None = None
    exchange_mic: str | None = None
    market_field: str | None = None
    retrieved_at: str | None = None
    action_type: str | None = None
    date_type: str | None = None


class SeriesResult(BaseModel):
    id: str
    label: str
    entity: str | None
    metric: str
    unit: str
    frequency: str
    observations: list[SeriesPointResult]
    state: Literal["AVAILABLE", "UNAVAILABLE", "NOT_MEANINGFUL"] = "AVAILABLE"
    context: dict[str, object] = Field(default_factory=dict)


class QueryResponse(BaseModel):
    status: ResultStatus
    series: list[SeriesResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


EDGE_CASE_COMPANIES = {"BRK.B": "Berkshire Hathaway Inc."}


DERIVED_INPUTS: dict[DerivedMetricCode, list[MetricCode]] = {
    DerivedMetricCode.REVENUE_GROWTH_YOY: [MetricCode.REVENUE],
    DerivedMetricCode.GROSS_MARGIN: [MetricCode.GROSS_PROFIT, MetricCode.REVENUE],
    DerivedMetricCode.OPERATING_MARGIN: [MetricCode.OPERATING_INCOME, MetricCode.REVENUE],
    DerivedMetricCode.NET_MARGIN: [MetricCode.NET_INCOME, MetricCode.REVENUE],
    DerivedMetricCode.FREE_CASH_FLOW: [MetricCode.OPERATING_CASH_FLOW, MetricCode.CAPITAL_EXPENDITURE],
    DerivedMetricCode.FCF_MARGIN: [MetricCode.OPERATING_CASH_FLOW, MetricCode.CAPITAL_EXPENDITURE, MetricCode.REVENUE],
    DerivedMetricCode.CURRENT_RATIO: [MetricCode.CURRENT_ASSETS, MetricCode.CURRENT_LIABILITIES],
}


class QueryEngine:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def execute(self, query: CompanyQuery | MacroQuery | MarketQuery | ValuationQuery) -> QueryResponse:
        if isinstance(query, CompanyQuery):
            return self._company(query)
        if isinstance(query, MacroQuery):
            return self._macro(query)
        if isinstance(query, MarketQuery):
            return self._market(query)
        return self._valuation(query)

    def resolve_evidence(self, evidence_id: str) -> EvidenceResult | None:
        """Resolve a canonical evidence reference from local SQLite only."""
        if not evidence_id.startswith("market:"):
            return None
        parts = evidence_id.split(":")
        if len(parts) != 4:
            return None
        _, instrument_id, raw_date, field = parts
        if field not in {"close", "adjusted_close"}:
            return None
        try:
            trading_date = date.fromisoformat(raw_date)
        except ValueError:
            return None
        rows = self.store.market_observations(instrument_id, trading_date, trading_date)
        if not rows:
            return None
        item = rows[0]
        if getattr(item, field) is None:
            return None
        return self._market_evidence(item, field)

    def _company(self, query: CompanyQuery) -> QueryResponse:
        display_label = METRICS[query.metric].label if isinstance(query.metric, MetricCode) else DERIVED_METRIC_LABELS[query.metric]
        known = self.store.known_company_tickers(query.tickers)
        # Explicit semantic edge cases remain queryable even when they are not
        # part of the certified selector catalog.
        known.update(EDGE_CASE_COMPANIES)
        unknown = [ticker for ticker in query.tickers if ticker not in known]
        if unknown:
            return QueryResponse(status=ResultStatus.UNSUPPORTED, errors=[f"unsupported company: {ticker}" for ticker in unknown])
        if "BRK.B" in query.tickers and query.metric in {
            MetricCode.REVENUE,
            MetricCode.OPERATING_INCOME,
            MetricCode.CURRENT_ASSETS,
            DerivedMetricCode.GROSS_MARGIN,
            DerivedMetricCode.OPERATING_MARGIN,
            DerivedMetricCode.REVENUE_GROWTH_YOY,
            DerivedMetricCode.CURRENT_RATIO,
        }:
            return QueryResponse(status=ResultStatus.UNSUPPORTED, errors=[f"{display_label} is not meaningful for BRK.B"])
        if query.frequency == "quarterly" and (
            query.metric is DerivedMetricCode.CURRENT_RATIO
            or isinstance(query.metric, MetricCode) and METRICS[query.metric].period_kind is PeriodKind.INSTANT
        ):
            return QueryResponse(status=ResultStatus.INVALID, errors=["instant metrics are only supported at annual fiscal year-end frequency"])
        if query.operation is QueryOperation.QOQ_GROWTH and query.frequency != "quarterly":
            return QueryResponse(status=ResultStatus.INVALID, errors=["QOQ_GROWTH requires quarterly frequency"])

        required = [query.metric] if isinstance(query.metric, MetricCode) else DERIVED_INPUTS[query.metric]
        # YoY derived metrics need the preceding year even when the output is filtered.
        fetch_start = query.start_year
        if fetch_start is not None and (
            query.metric is DerivedMetricCode.REVENUE_GROWTH_YOY
            or query.operation is QueryOperation.YOY_GROWTH
        ):
            fetch_start -= 1
        observations = self.store.financial_observations(
            query.tickers, required, query.frequency, fetch_start, query.end_year
        )
        grouped: dict[str, list[FinancialObservation]] = defaultdict(list)
        for observation in observations:
            grouped[observation.ticker].append(observation)

        results: list[SeriesResult] = []
        errors: list[str] = []
        for ticker in query.tickers:
            source = grouped.get(ticker, [])
            if isinstance(query.metric, MetricCode):
                points: list[SeriesPoint] = [
                    SourceSeriesPoint(
                        date=item.period_end, value=item.value, unit=item.unit,
                        observation_id=observation_id(item), fiscal_year=item.fiscal_year,
                        fiscal_period=item.fiscal_period,
                    )
                    for item in source if item.metric is query.metric
                ]
                label = METRICS[query.metric].label
            else:
                points = list(calculate_company_metric(query.metric, source))
                label = DERIVED_METRIC_LABELS[query.metric]
            if query.operation in {QueryOperation.YOY_GROWTH, QueryOperation.QOQ_GROWTH}:
                points = self._apply_company_operation(points, query.operation, query.frequency)
                points = self._filter_company_points(points, query.start_year, query.end_year)
            else:
                points = self._filter_company_points(points, query.start_year, query.end_year)
                points = self._apply_company_operation(points, query.operation, query.frequency)
            if not points:
                errors.append(f"{display_label} is unavailable for {ticker} in the requested range")
                continue
            evidence_index = {observation_id(item): item for item in source}
            result_metric = query.metric.value
            if query.operation in {QueryOperation.YOY_GROWTH, QueryOperation.QOQ_GROWTH}:
                result_metric = f"{query.metric.value}_{query.operation.value}"
                label = f"{label} {'YoY' if query.operation is QueryOperation.YOY_GROWTH else 'QoQ'} growth"
            elif query.operation is QueryOperation.CAGR:
                result_metric = f"{query.metric.value}_GROWTH_CAGR"
                label = f"{label} CAGR"
            elif query.operation is QueryOperation.CHANGE:
                result_metric = f"{query.metric.value}_CHANGE"
                label = f"{label} change"
            results.append(self._series_result(
                ticker, result_metric, label, query.frequency, points,
                evidence_index=evidence_index,
            ))
        if not results:
            return QueryResponse(status=ResultStatus.UNAVAILABLE, errors=errors)
        if query.ranking is not RankingOperation.NONE:
            results.sort(
                key=lambda item: item.observations[-1].value,
                reverse=query.ranking is RankingOperation.HIGHEST,
            )
            coverage = len(results)
            for rank, item in enumerate(results, 1):
                item.context.update({"ranking": query.ranking.value, "rank": rank, "coverage_count": coverage})
        return QueryResponse(status=ResultStatus.SUCCESS, series=results, errors=errors)

    @staticmethod
    def _filter_company_points(points: list[SeriesPoint], start: int | None, end: int | None) -> list[SeriesPoint]:
        return [point for point in points if (start is None or (point.fiscal_year or 0) >= start) and (end is None or (point.fiscal_year or 0) <= end)]

    def _apply_company_operation(self, points: list[SeriesPoint], operation: QueryOperation, frequency: str) -> list[SeriesPoint]:
        if operation is QueryOperation.LEVEL:
            return points
        if operation is QueryOperation.AVERAGE:
            return self._average(points)
        if operation in {QueryOperation.CHANGE, QueryOperation.CAGR}:
            ordered = sorted(points, key=lambda item: item.date)
            if len(ordered) < 2:
                return []
            first, last = ordered[0], ordered[-1]
            if operation is QueryOperation.CHANGE:
                value = last.value - first.value
                unit = last.unit
                formula = "CHANGE: END - START"
            else:
                years = (last.fiscal_year or last.date.year) - (first.fiscal_year or first.date.year)
                if years <= 0 or first.value <= 0 or last.value < 0:
                    return []
                value = (last.value / first.value) ** (Decimal(1) / Decimal(years)) - Decimal(1)
                unit = "ratio"
                formula = f"CAGR: (END / START) ^ (1 / {years}) - 1"
            return [CalculatedSeriesPoint(
                date=last.date, value=value, unit=unit, formula=formula,
                input_observation_ids=self._point_inputs(first) + self._point_inputs(last),
                fiscal_year=last.fiscal_year, fiscal_period=last.fiscal_period,
            )]
        ordered = sorted(points, key=lambda item: item.date)
        comparable = {
            (point.fiscal_year, point.fiscal_period.value if point.fiscal_period else None): point
            for point in ordered
        }
        result: list[SeriesPoint] = []
        prior_quarter = {"Q1": (-1, "Q4"), "Q2": (0, "Q1"), "Q3": (0, "Q2"), "Q4": (0, "Q3")}
        for current in ordered:
            if current.fiscal_year is None or current.fiscal_period is None:
                continue
            if operation is QueryOperation.YOY_GROWTH:
                prior_key = (current.fiscal_year - 1, current.fiscal_period.value)
            else:
                year_delta, period = prior_quarter[current.fiscal_period.value]
                prior_key = (current.fiscal_year + year_delta, period)
            prior = comparable.get(prior_key)
            if prior is None:
                continue
            if prior.value <= 0:
                continue
            result.append(CalculatedSeriesPoint(
                date=current.date, value=current.value / prior.value - Decimal(1), unit="ratio",
                formula=f"{operation.value}: CURRENT / PRIOR - 1",
                input_observation_ids=self._point_inputs(current) + self._point_inputs(prior),
                fiscal_year=current.fiscal_year, fiscal_period=current.fiscal_period,
            ))
        return result

    def _macro(self, query: MacroQuery) -> QueryResponse:
        known = set(MACRO_CATALOG) | set(SEMANTIC_MACRO_SERIES)
        unknown = [code for code in query.series if code not in known]
        if unknown:
            return QueryResponse(status=ResultStatus.UNSUPPORTED, errors=[f"unsupported macro series: {code}" for code in unknown])
        results: list[SeriesResult] = []
        errors: list[str] = []
        for code in query.series:
            if code in SEMANTIC_MACRO_SERIES:
                base_code, label = SEMANTIC_MACRO_SERIES[code]
                raw = self.store.macro_observations(base_code)
                calculated = calculate_yoy(raw, code)
                points: list[SeriesPoint] = [CalculatedSeriesPoint(
                    date=item.date, value=item.value, unit="percent",
                    formula=f"100 * ({base_code}(t) / {base_code}(t-12m) - 1)",
                    input_observation_ids=(f"macro:{base_code}:{item.date.isoformat()}", f"macro:{base_code}:{item.date.year - 1:04d}-{item.date.month:02d}-{item.date.day:02d}"),
                ) for item in calculated]
                frequency = "monthly"
                metric_label = label
            else:
                definition = MACRO_CATALOG[code]
                metadata = self.store.macro_series_metadata(code) or {
                    "label": definition.label,
                    "unit": definition.unit,
                    "frequency": definition.frequency,
                }
                raw = self.store.macro_observations(code)
                points = [SourceSeriesPoint(item.date, item.value, metadata["unit"], f"macro:{code}:{item.date.isoformat()}") for item in raw]
                frequency = metadata["frequency"]
                metric_label = metadata["label"]
            points = [point for point in points if (query.start_date is None or point.date >= query.start_date) and (query.end_date is None or point.date <= query.end_date)]
            if query.operation is QueryOperation.AVERAGE:
                points = self._average(points)
            if not points:
                errors.append(f"{code} is unavailable in the requested range")
                continue
            base_code = SEMANTIC_MACRO_SERIES[code][0] if code in SEMANTIC_MACRO_SERIES else code
            definition = MACRO_CATALOG[base_code]
            raw_by_id = {
                f"macro:{base_code}:{item.date.isoformat()}": item
                for item in self.store.macro_observations(base_code)
            }
            macro_evidence = {
                item_id: EvidenceResult(
                    id=item_id, metric=base_code, label=definition.label,
                    value=item.value, unit=definition.unit, source_type="FRED",
                    period_end=item.date, provider_series_id=definition.provider_series_id,
                )
                for item_id, item in raw_by_id.items()
            }
            results.append(self._series_result(
                None, code, metric_label, frequency, points,
                macro_evidence=macro_evidence,
            ))
        if not results:
            return QueryResponse(status=ResultStatus.UNAVAILABLE, errors=errors)
        return QueryResponse(status=ResultStatus.SUCCESS, series=results, errors=errors)

    def _market_evidence(self, item: MarketDailyObservation, field: str) -> EvidenceResult:
        instrument = self.store.market_instrument(item.instrument_id)
        value = item.close if field == "close" else item.adjusted_close
        assert value is not None
        return EvidenceResult(
            id=market_observation_id(item.instrument_id, item.trading_date, field),
            metric="RAW_CLOSE" if field == "close" else "ADJUSTED_CLOSE",
            label="Raw close" if field == "close" else "Provider-adjusted close",
            value=value, unit=item.currency, source_type="MARKET", period_end=item.trading_date,
            provider=item.source_provider, instrument_id=item.instrument_id, symbol=item.source_symbol,
            exchange_mic=instrument.exchange_mic if instrument else None, market_field=field,
            retrieved_at=item.retrieved_at,
        )

    def _market(self, query: MarketQuery) -> QueryResponse:
        unknown = [ticker for ticker in query.tickers if self.store.primary_market_instrument(ticker) is None]
        if unknown:
            return QueryResponse(status=ResultStatus.UNSUPPORTED, errors=[f"market data is unsupported for {ticker}" for ticker in unknown])
        grouped: dict[str, list[MarketDailyObservation]] = {}
        instruments = {}
        for ticker in query.tickers:
            instrument = self.store.primary_market_instrument(ticker)
            assert instrument is not None
            instruments[ticker] = instrument
            # Load local history through the requested end so coverage can be
            # checked before applying a requested start boundary.
            grouped[ticker] = self.store.market_observations(instrument.instrument_id, None, query.end_date)

        def series_context(ticker: str, observations: list[MarketDailyObservation]) -> dict[str, str | int | bool | None]:
            instrument = instruments[ticker]
            coverage = self.store.market_coverage(instrument.instrument_id)
            first = coverage["first_date"]
            requested = query.start_date.isoformat() if query.start_date else None
            return {
                "instrument_id": instrument.instrument_id,
                "symbol": instrument.symbol,
                "exchange_mic": instrument.exchange_mic,
                "provider": coverage["provider"],
                "field": query.series.value,
                "first_date": first,
                "last_date": coverage["last_date"],
                "observation_count": coverage["observation_count"],
                "requested_start_date": requested,
                "coverage": "FULL" if query.start_date is None or (first is not None and str(first) <= requested) else "PARTIAL",
            }

        def unavailable_series(ticker: str, metric: str, label: str, reason: str) -> SeriesResult:
            context = series_context(ticker, grouped[ticker])
            context["reason"] = reason
            return SeriesResult(
                id=f"{ticker}:{metric}", label=label, entity=ticker, metric=metric,
                unit="return" if metric in {"RETURN", "MAX_DRAWDOWN"} else instruments[ticker].currency,
                frequency="period" if metric in {"RETURN", "MAX_DRAWDOWN"} else "daily",
                observations=[], state="UNAVAILABLE", context=context,
            )

        if query.operation is MarketOperation.INDEXED:
            ranged = {
                ticker: [
                    item for item in observations
                    if (query.start_date is None or item.trading_date >= query.start_date)
                    and (query.end_date is None or item.trading_date <= query.end_date)
                ]
                for ticker, observations in grouped.items()
            }
            indexed = indexed_to_100(ranged)
            if not indexed:
                return QueryResponse(status=ResultStatus.UNAVAILABLE, errors=["no common adjusted-close base date is available"])
            results = []
            for ticker in query.tickers:
                base_item = indexed[ticker][0][0]
                base_id = market_observation_id(base_item.instrument_id, base_item.trading_date, "adjusted_close")
                points = [SeriesPointResult(
                    date=item.trading_date, value=value, point_type="CALCULATED",
                    formula="100 * ADJUSTED_CLOSE / ADJUSTED_CLOSE_COMMON_START",
                    input_observation_ids=list(dict.fromkeys([
                        market_observation_id(item.instrument_id, item.trading_date, "adjusted_close"),
                        base_id,
                    ])),
                ) for item, value in indexed[ticker]]
                context = series_context(ticker, grouped[ticker])
                context["base_observation_id"] = base_id
                context["common_start_date"] = base_item.trading_date.isoformat()
                results.append(SeriesResult(
                    id=f"{ticker}:INDEXED", label="Indexed stock performance", entity=ticker,
                    metric="INDEXED", unit="index", frequency="daily", observations=points,
                    context=context,
                ))
            return QueryResponse(status=ResultStatus.SUCCESS, series=results)

        results: list[SeriesResult] = []
        errors: list[str] = []
        for ticker in query.tickers:
            observations = grouped[ticker]
            field = "close" if query.series is MarketPriceSeries.RAW_CLOSE else "adjusted_close"
            usable = [item for item in observations if getattr(item, field) is not None]
            if query.operation is MarketOperation.MAX_DRAWDOWN:
                first = usable[0].trading_date if usable else None
                if query.start_date is not None and (first is None or first > query.start_date):
                    reason = self._coverage_reason(ticker, first, usable[-1].trading_date if usable else None, query.start_date)
                    errors.append(reason)
                    results.append(unavailable_series(ticker, "MAX_DRAWDOWN", "Maximum drawdown", reason))
                    continue
                period = [item for item in usable if query.start_date is None or item.trading_date >= query.start_date]
                calculated = maximum_drawdown(period)
                if calculated is None:
                    reason = f"maximum drawdown is unavailable for {ticker}"
                    errors.append(reason)
                    results.append(unavailable_series(ticker, "MAX_DRAWDOWN", "Maximum drawdown", reason))
                    continue
                results.append(SeriesResult(
                    id=f"{ticker}:MAX_DRAWDOWN", label="Maximum drawdown", entity=ticker,
                    metric="MAX_DRAWDOWN", unit="return", frequency="period",
                    observations=[SeriesPointResult(
                        date=calculated.trough_date, value=calculated.value, point_type="CALCULATED",
                        formula="ADJUSTED_CLOSE_TROUGH / ADJUSTED_CLOSE_PEAK - 1",
                        input_observation_ids=list(dict.fromkeys([
                            calculated.peak_observation_id, calculated.trough_observation_id,
                        ])),
                    )],
                    context=series_context(ticker, observations),
                ))
                continue
            if query.operation is MarketOperation.RETURN:
                if query.start_date is None:
                    reason = f"a target start date is required for {ticker} return"
                    errors.append(reason)
                    results.append(unavailable_series(ticker, "RETURN", "Trailing return", reason))
                    continue
                calculated = trailing_return(observations, query.start_date)
                if calculated is None:
                    first = usable[0].trading_date if usable else None
                    reason = self._coverage_reason(ticker, first, usable[-1].trading_date if usable else None, query.start_date)
                    errors.append(reason)
                    results.append(unavailable_series(ticker, "RETURN", "Trailing return", reason))
                    continue
                value, start, end = calculated
                points = [SeriesPointResult(
                    date=end.trading_date, value=value, point_type="CALCULATED",
                    formula="ADJUSTED_CLOSE_END / ADJUSTED_CLOSE_START_ANCHOR - 1",
                    input_observation_ids=[
                        market_observation_id(start.instrument_id, start.trading_date, "adjusted_close"),
                        market_observation_id(end.instrument_id, end.trading_date, "adjusted_close"),
                    ],
                )]
                results.append(SeriesResult(
                    id=f"{ticker}:RETURN", label="Trailing return", entity=ticker,
                    metric="RETURN", unit="return", frequency="period", observations=points,
                    context=series_context(ticker, observations),
                ))
                continue
            usable = [
                item for item in usable
                if (query.start_date is None or item.trading_date >= query.start_date)
                and (query.end_date is None or item.trading_date <= query.end_date)
            ]
            if query.view == "latest" and usable:
                usable = [usable[-1]]
            if not usable:
                reason = f"{query.series.value} is unavailable for {ticker} in the requested range"
                errors.append(reason)
                results.append(unavailable_series(ticker, query.series.value, "Raw close" if field == "close" else "Provider-adjusted close", reason))
                continue
            points = [SeriesPointResult(
                date=item.trading_date, value=getattr(item, field), point_type="SOURCE",
                observation_id=market_observation_id(item.instrument_id, item.trading_date, field),
            ) for item in usable]
            results.append(SeriesResult(
                id=f"{ticker}:{query.series.value}",
                label="Raw close" if field == "close" else "Provider-adjusted close",
                entity=ticker, metric=query.series.value, unit=instruments[ticker].currency,
                frequency="daily", observations=points,
                context=series_context(ticker, observations),
            ))
        return QueryResponse(
            status=ResultStatus.SUCCESS if any(item.observations for item in results) else ResultStatus.UNAVAILABLE,
            series=results, errors=errors,
        )

    @staticmethod
    def _coverage_reason(ticker: str, first: date | None, last: date | None, requested: date) -> str:
        if first is None:
            return f"adjusted market history is unavailable for {ticker}"
        return (
            f"requested period beginning {requested.isoformat()} is unavailable for {ticker}; "
            f"local adjusted history covers {first.isoformat()} through {last.isoformat() if last else first.isoformat()}"
        )

    def _valuation_evidence(self, ticker: str, snapshot) -> list[EvidenceResult] | None:
        instrument = self.store.primary_market_instrument(ticker)
        assert instrument is not None
        price = next((
            item for item in self.store.market_observations(instrument.instrument_id, snapshot.price_date, snapshot.price_date)
            if item.trading_date == snapshot.price_date
        ), None)
        if price is None:
            return None
        evidence = [self._market_evidence(price, "close")]
        for source in snapshot.sec_inputs:
            item_id = observation_id(source)
            evidence.append(EvidenceResult(
                id=item_id, metric=source.metric.value, label=METRICS[source.metric].label,
                value=source.value, unit=source.unit, source_type="SEC",
                source_concept=source.source_concept, accession_number=source.accession_number,
                filing_form=source.filing_form, filing_date=source.filing_date,
                period_start=source.period_start, period_end=source.period_end,
                derivation=source.derivation.value,
            ))
        for action in snapshot.split_actions:
            evidence.append(EvidenceResult(
                id=market_action_id(action), metric="SPLIT", label="Stock split factor",
                value=action.split_ratio, unit="ratio", source_type="MARKET",  # type: ignore[arg-type]
                period_end=action.event_date, provider=action.source_provider,
                instrument_id=action.instrument_id, symbol=action.source_symbol,
                exchange_mic=instrument.exchange_mic, retrieved_at=action.retrieved_at,
                action_type=action.action_type.value, date_type=action.date_type.value,
            ))
        return evidence

    def _valuation(self, query: ValuationQuery) -> QueryResponse:
        results: list[SeriesResult] = []
        errors: list[str] = []
        for ticker in query.tickers:
            instrument = self.store.primary_market_instrument(ticker)
            if instrument is None:
                errors.append(f"valuation is unsupported for {ticker}")
                continue
            if instrument.valuation_status.value != "SUPPORTED":
                errors.append(f"valuation share basis is not certified for {ticker}")
                continue
            financials = self.store.financial_observations(
                [ticker], list(VALUATION_INPUTS[query.metric]), "annual", query.start_year, query.end_year,
            )
            prices = self.store.market_observations(instrument.instrument_id)
            actions = self.store.corporate_actions(instrument.instrument_id)
            history = annual_snapshots(query.metric, financials, prices, actions)
            history = [item for item in history if (query.start_year is None or item.fiscal_year >= query.start_year) and (query.end_year is None or item.fiscal_year <= query.end_year)]
            current = current_snapshot(query.metric, financials, prices, actions)
            selected = ([current] if current else []) if query.view == "latest" else history
            context = valuation_context(current, history)
            state = ValuationState.UNAVAILABLE
            if selected:
                state = selected[-1].state
            points = []
            for item in selected:
                if item.value is None:
                    continue
                evidence = self._valuation_evidence(ticker, item)
                if evidence is None:
                    errors.append(f"valuation source observation is unavailable for {ticker} on {item.price_date}")
                    continue
                points.append(SeriesPointResult(
                    date=item.price_date, value=item.value, point_type="CALCULATED", formula=item.formula,
                    input_observation_ids=[source.id for source in evidence],
                    fiscal_year=item.fiscal_year, fiscal_period="FY", evidence=evidence,
                    state=item.state.value, valuation_basis="Latest FY basis" if query.view == "latest" else "Annual post-filing snapshot",
                    source_fiscal_year=item.fiscal_year, availability_date=item.availability_date,
                ))
            if selected and not points:
                errors.append(f"{VALUATION_LABELS[query.metric]} is {selected[-1].state.value.replace('_', ' ').lower()} for {ticker}")
            if not selected:
                errors.append(f"{VALUATION_LABELS[query.metric]} is unavailable for {ticker}")
            results.append(SeriesResult(
                id=f"{ticker}:{query.metric.value}", label=VALUATION_LABELS[query.metric], entity=ticker,
                metric=query.metric.value, unit="yield" if query.metric is ValuationMetricCode.FCF_YIELD else "multiple",
                frequency="annual", observations=points, state=state.value,
                context={
                    "basis": "Latest FY basis" if query.view == "latest" else "Annual post-filing snapshots",
                    "source_fiscal_year": selected[-1].fiscal_year if selected else None,
                    "price_date": selected[-1].price_date.isoformat() if selected else None,
                    "minimum": str(context.minimum) if context.minimum is not None else None,
                    "maximum": str(context.maximum) if context.maximum is not None else None,
                    "median": str(context.median) if context.median is not None else None,
                    "relative_to_median": str(context.relative_to_median) if context.relative_to_median is not None else None,
                    "observation_count": context.observation_count,
                    "interpretation_available": context.interpretation_available,
                    "reason": not_meaningful_reason(query.metric, selected[-1] if selected else None),
                    "evidence_refs": [evidence.id for evidence in points[-1].evidence] if points else [],
                },
            ))
        if not results:
            return QueryResponse(status=ResultStatus.UNSUPPORTED if errors else ResultStatus.UNAVAILABLE, errors=errors)
        return QueryResponse(status=ResultStatus.SUCCESS, series=results, errors=errors)

    @staticmethod
    def _point_inputs(point: SeriesPoint) -> tuple[str, ...]:
        if isinstance(point, SourceSeriesPoint):
            return (point.observation_id,)
        return point.input_observation_ids

    def _average(self, points: list[SeriesPoint]) -> list[SeriesPoint]:
        if not points:
            return []
        return [CalculatedSeriesPoint(
            date=max(point.date for point in points),
            value=sum((point.value for point in points), Decimal(0)) / Decimal(len(points)),
            unit=points[0].unit, formula="ARITHMETIC_MEAN",
            input_observation_ids=tuple(input_id for point in points for input_id in self._point_inputs(point)),
        )]

    @staticmethod
    def _series_result(
        entity: str | None,
        metric: str,
        label: str,
        frequency: str,
        points: list[SeriesPoint],
        evidence_index: dict[str, FinancialObservation] | None = None,
        macro_evidence: dict[str, EvidenceResult] | None = None,
    ) -> SeriesResult:
        rendered = []
        evidence_index = evidence_index or {}
        macro_evidence = macro_evidence or {}
        for point in points:
            evidence_ids = (
                [point.observation_id]
                if isinstance(point, SourceSeriesPoint)
                else list(point.input_observation_ids)
            )
            evidence: list[EvidenceResult] = []
            for item_id in evidence_ids:
                source = evidence_index.get(item_id)
                if source is not None:
                    evidence.append(EvidenceResult(
                        id=item_id, metric=source.metric.value,
                        label=METRICS[source.metric].label, value=source.value,
                        unit=source.unit, source_type="SEC",
                        source_concept=source.source_concept,
                        accession_number=source.accession_number,
                        filing_form=source.filing_form,
                        filing_date=source.filing_date,
                        period_start=source.period_start,
                        period_end=source.period_end,
                        derivation=source.derivation.value,
                    ))
                    evidence.extend(EvidenceResult(
                        id=f"{item_id}:derivation:{index}", metric=source.metric.value,
                        label=f"{METRICS[source.metric].label} · {derived.role.replace('_', ' ').title()}",
                        value=derived.value, unit=derived.unit, source_type="SEC",
                        source_concept=derived.source_concept,
                        accession_number=derived.accession_number,
                        filing_form=derived.filing_form, filing_date=derived.filing_date,
                        period_start=derived.period_start, period_end=derived.period_end,
                        derivation=f"{source.derivation.value}: {derived.role}",
                    ) for index, derived in enumerate(source.derivation_sources))
                elif item_id in macro_evidence:
                    evidence.append(macro_evidence[item_id])
            rendered.append(SeriesPointResult(
                date=point.date, value=point.value, point_type=point.point_type,
                observation_id=point.observation_id if isinstance(point, SourceSeriesPoint) else None,
                formula=point.formula if isinstance(point, CalculatedSeriesPoint) else None,
                input_observation_ids=list(point.input_observation_ids) if isinstance(point, CalculatedSeriesPoint) else [],
                fiscal_year=point.fiscal_year,
                fiscal_period=point.fiscal_period.value if point.fiscal_period else None,
                evidence=evidence,
            ))
        return SeriesResult(
            id=f"{entity or 'macro'}:{metric}", label=label, entity=entity, metric=metric,
            unit=points[0].unit, frequency=frequency, observations=rendered,
        )
