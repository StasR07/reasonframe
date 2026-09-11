"""Clear SQLite persistence for canonical SEC and macro observations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from .macro import MacroObservation, MacroSeriesDefinition
from .market import (
    CorporateAction, CorporateActionDateType, CorporateActionType,
    MarketDailyObservation, MarketInstrument,
    ProviderMarketInstrument, ValuationSupport,
)
from .metrics import MetricCode
from .models import (
    DerivationKind,
    DurationScope,
    FinancialObservation,
    FiscalPeriod,
    PeriodKind,
    SourceFactIdentity,
)
from .series import observation_id


@dataclass(frozen=True, slots=True)
class Company:
    ticker: str
    cik: str
    name: str
    fiscal_year_end: str | None = None
    support_status: str = "SUPPORTED"
    legal_name: str | None = None
    certification_status: str = "PASS"
    certification_policy: str | None = None
    certified_at: str | None = None
    evaluation_state: str = "COMPLETE"
    evaluation_reason: str | None = None


class SQLiteStore:
    def __init__(self, path: str | Path = "finance_terminal.db") -> None:
        raw_path = str(path)
        self._anchor: sqlite3.Connection | None = None
        if raw_path == ":memory:":
            self.path = f"file:finance_terminal_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._uri = True
            self._anchor = self._new_connection()
        else:
            self.path = raw_path
            self._uri = raw_path.startswith("file:")
        self.initialize()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, uri=self._uri, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        if not self._uri or "mode=memory" not in self.path:
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _connect(self):
        connection = self._new_connection()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS companies (
                ticker TEXT PRIMARY KEY,
                cik TEXT NOT NULL,
                name TEXT NOT NULL,
                fiscal_year_end TEXT,
                support_status TEXT NOT NULL,
                legal_name TEXT,
                certification_status TEXT NOT NULL DEFAULT 'PASS',
                certification_policy TEXT,
                certified_at TEXT,
                evaluation_state TEXT NOT NULL DEFAULT 'COMPLETE',
                evaluation_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS company_aliases (
                company_ticker TEXT NOT NULL,
                alias TEXT NOT NULL COLLATE NOCASE,
                PRIMARY KEY(company_ticker, alias),
                FOREIGN KEY(company_ticker) REFERENCES companies(ticker)
            );
            CREATE TABLE IF NOT EXISTS financial_observations (
                observation_id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                cik TEXT NOT NULL,
                metric TEXT NOT NULL,
                value_text TEXT NOT NULL,
                unit TEXT NOT NULL,
                currency TEXT,
                period_kind TEXT NOT NULL,
                period_start TEXT,
                period_end TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                fiscal_period TEXT NOT NULL,
                duration_scope TEXT,
                derivation TEXT NOT NULL,
                source_concept TEXT NOT NULL,
                accession_number TEXT NOT NULL,
                filing_form TEXT NOT NULL,
                filing_date TEXT,
                quality_flags_json TEXT NOT NULL,
                derivation_sources_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_financial_lookup_v2
                ON financial_observations(ticker, metric, fiscal_period, fiscal_year);
            CREATE TABLE IF NOT EXISTS macro_series (
                code TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                provider_series_id TEXT NOT NULL,
                label TEXT NOT NULL,
                unit TEXT NOT NULL,
                frequency TEXT NOT NULL,
                source_name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS macro_observations (
                series_code TEXT NOT NULL,
                observation_date TEXT NOT NULL,
                value_text TEXT NOT NULL,
                PRIMARY KEY(series_code, observation_date),
                FOREIGN KEY(series_code) REFERENCES macro_series(code)
            );
            CREATE TABLE IF NOT EXISTS ingestion_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                message TEXT
            );
            CREATE TABLE IF NOT EXISTS application_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS certification_runs (
                run_id TEXT PRIMARY KEY,
                policy_version TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS certification_results (
                run_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                final_status TEXT NOT NULL,
                reason_codes_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                certified_at TEXT NOT NULL,
                evaluation_state TEXT NOT NULL DEFAULT 'COMPLETE',
                evaluation_reason TEXT,
                PRIMARY KEY(run_id, ticker),
                FOREIGN KEY(run_id) REFERENCES certification_runs(run_id),
                FOREIGN KEY(ticker) REFERENCES companies(ticker)
            );
            CREATE TABLE IF NOT EXISTS market_instruments (
                instrument_id TEXT PRIMARY KEY,
                company_ticker TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange_mic TEXT NOT NULL,
                currency TEXT NOT NULL,
                security_type TEXT NOT NULL,
                share_class TEXT,
                is_primary INTEGER NOT NULL,
                valuation_status TEXT NOT NULL,
                UNIQUE(symbol, exchange_mic),
                FOREIGN KEY(company_ticker) REFERENCES companies(ticker)
            );
            CREATE INDEX IF NOT EXISTS ix_market_instrument_company_primary
                ON market_instruments(company_ticker, is_primary);
            CREATE TABLE IF NOT EXISTS market_instrument_sources (
                instrument_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_symbol TEXT NOT NULL,
                provider_exchange TEXT,
                provider_instrument_id TEXT,
                PRIMARY KEY(instrument_id, provider),
                FOREIGN KEY(instrument_id) REFERENCES market_instruments(instrument_id)
            );
            CREATE TABLE IF NOT EXISTS market_daily_observations (
                instrument_id TEXT NOT NULL,
                trading_date TEXT NOT NULL,
                open_text TEXT, high_text TEXT, low_text TEXT, close_text TEXT NOT NULL,
                volume_text TEXT,
                adjusted_open_text TEXT, adjusted_high_text TEXT, adjusted_low_text TEXT,
                adjusted_close_text TEXT, adjusted_volume_text TEXT,
                currency TEXT NOT NULL,
                source_provider TEXT NOT NULL,
                source_symbol TEXT NOT NULL,
                retrieved_at TEXT NOT NULL,
                ingestion_run_id INTEGER,
                PRIMARY KEY(instrument_id, trading_date),
                FOREIGN KEY(instrument_id) REFERENCES market_instruments(instrument_id)
            );
            CREATE TABLE IF NOT EXISTS market_corporate_actions (
                instrument_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                event_date TEXT NOT NULL,
                date_type TEXT NOT NULL,
                split_ratio_text TEXT,
                cash_amount_per_share_text TEXT,
                currency TEXT,
                source_provider TEXT NOT NULL,
                source_symbol TEXT NOT NULL,
                retrieved_at TEXT NOT NULL,
                ingestion_run_id INTEGER,
                PRIMARY KEY(instrument_id, action_type, event_date),
                FOREIGN KEY(instrument_id) REFERENCES market_instruments(instrument_id)
            );
            """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(companies)")}
            for name, declaration in (
                ("legal_name", "TEXT"),
                ("certification_status", "TEXT NOT NULL DEFAULT 'PASS'"),
                ("certification_policy", "TEXT"),
                ("certified_at", "TEXT"),
                ("evaluation_state", "TEXT NOT NULL DEFAULT 'COMPLETE'"),
                ("evaluation_reason", "TEXT"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE companies ADD COLUMN {name} {declaration}")
            result_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(certification_results)")
            }
            for name, declaration in (
                ("evaluation_state", "TEXT NOT NULL DEFAULT 'COMPLETE'"),
                ("evaluation_reason", "TEXT"),
            ):
                if name not in result_columns:
                    connection.execute(
                        f"ALTER TABLE certification_results ADD COLUMN {name} {declaration}"
                    )
            # These older indexes are fully covered by the replacement lookup
            # index and the market table's composite primary key respectively.
            connection.execute("DROP INDEX IF EXISTS ix_financial_lookup")
            connection.execute("DROP INDEX IF EXISTS ix_market_daily_date")
            connection.execute(
                "INSERT OR IGNORE INTO application_metadata(key, value) VALUES ('data_revision', '0')"
            )
            connection.commit()

    def close(self) -> None:
        if self._anchor is not None:
            self._anchor.close()
            self._anchor = None

    def data_revision(self) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM application_metadata WHERE key='data_revision'"
            ).fetchone()
        return str(row["value"] if row else "0")

    @staticmethod
    def _increment_revision(connection: sqlite3.Connection) -> None:
        connection.execute(
            """INSERT INTO application_metadata(key, value) VALUES ('data_revision', '1')
            ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1"""
        )

    def table_row_count(self, table: str) -> int:
        allowed = {
            "companies", "financial_observations", "macro_series", "macro_observations",
            "market_instruments", "market_daily_observations", "market_corporate_actions",
            "company_aliases", "certification_runs", "certification_results",
        }
        if table not in allowed:
            raise ValueError("unsupported table")
        with self._connect() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def tickers_with_financial_observations(self) -> set[str]:
        """Return issuers that actually have locally stored SEC observations."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT ticker FROM financial_observations ORDER BY ticker"
            ).fetchall()
        return {str(row["ticker"]) for row in rows}

    def upsert_company(self, company: Company) -> None:
        with self._connect() as connection, connection:
            if self._upsert_company(connection, company):
                self._increment_revision(connection)

    @staticmethod
    def _upsert_company(connection: sqlite3.Connection, company: Company) -> bool:
        previous = connection.execute(
            """SELECT cik, name, fiscal_year_end, support_status, legal_name,
            certification_status, certification_policy, certified_at,
            evaluation_state, evaluation_reason
            FROM companies WHERE ticker=?""",
            (company.ticker,),
        ).fetchone()
        connection.execute(
            """INSERT INTO companies(
            ticker, cik, name, fiscal_year_end, support_status, legal_name,
            certification_status, certification_policy, certified_at,
            evaluation_state, evaluation_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET cik=excluded.cik, name=excluded.name,
            fiscal_year_end=excluded.fiscal_year_end, support_status=excluded.support_status,
            legal_name=excluded.legal_name, certification_status=excluded.certification_status,
            certification_policy=excluded.certification_policy, certified_at=excluded.certified_at,
            evaluation_state=excluded.evaluation_state,
            evaluation_reason=excluded.evaluation_reason""",
            (company.ticker, company.cik, company.name, company.fiscal_year_end,
             company.support_status, company.legal_name, company.certification_status,
             company.certification_policy, company.certified_at,
             company.evaluation_state, company.evaluation_reason),
        )
        current = (company.cik, company.name, company.fiscal_year_end, company.support_status,
                   company.legal_name, company.certification_status,
                   company.certification_policy, company.certified_at,
                   company.evaluation_state, company.evaluation_reason)
        return previous is None or tuple(previous) != current

    def known_company_tickers(self, tickers: list[str], *, include_nonpass: bool = False) -> set[str]:
        """Return catalog-backed ticker matches without loading the full catalog."""
        normalized = list(dict.fromkeys(ticker.strip().upper() for ticker in tickers))
        if not normalized:
            return set()
        marks = ",".join("?" for _ in normalized)
        with self._connect() as connection:
            status_clause = "" if include_nonpass else " AND certification_status='PASS'"
            rows = connection.execute(
                f"SELECT ticker FROM companies WHERE ticker IN ({marks}){status_clause}", normalized
            ).fetchall()
        return {str(row["ticker"]) for row in rows}

    def company(self, ticker: str, *, include_nonpass: bool = False) -> Company | None:
        clause = "" if include_nonpass else " AND certification_status='PASS'"
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM companies WHERE ticker=?" + clause, (ticker.strip().upper(),)
            ).fetchone()
        return Company(**dict(row)) if row else None

    def companies(self, *, include_nonpass: bool = False) -> list[Company]:
        clause = "" if include_nonpass else " WHERE certification_status='PASS'"
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM companies" + clause + " ORDER BY ticker").fetchall()
        return [Company(**dict(row)) for row in rows]

    def replace_company_aliases(self, ticker: str, aliases: tuple[str, ...]) -> None:
        unique = {alias.strip().casefold(): alias.strip() for alias in aliases if alias.strip()}
        with self._connect() as connection, connection:
            connection.execute("DELETE FROM company_aliases WHERE company_ticker=?", (ticker,))
            connection.executemany(
                "INSERT INTO company_aliases(company_ticker, alias) VALUES (?, ?)",
                ((ticker, alias) for alias in unique.values()),
            )

    def company_aliases(self, *, pass_only: bool = True) -> dict[str, str]:
        where = "WHERE company.certification_status='PASS'" if pass_only else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT alias.alias, alias.company_ticker FROM company_aliases alias
                JOIN companies company ON company.ticker=alias.company_ticker {where}
                ORDER BY alias.alias"""
            ).fetchall()
        return {str(row["alias"]): str(row["company_ticker"]) for row in rows}

    def upsert_financial_observation(self, item: FinancialObservation) -> str:
        with self._connect() as connection, connection:
            return self._upsert_financial_observation(connection, item)

    @staticmethod
    def _upsert_financial_observation(
        connection: sqlite3.Connection, item: FinancialObservation,
    ) -> str:
        item_id = observation_id(item)
        sources = []
        for source in item.derivation_sources:
            data = asdict(source)
            data["value"] = str(source.value)
            data["period_start"] = source.period_start.isoformat() if source.period_start else None
            data["period_end"] = source.period_end.isoformat()
            data["filing_date"] = source.filing_date.isoformat() if source.filing_date else None
            sources.append(data)
        connection.execute(
            """INSERT INTO financial_observations VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(observation_id) DO UPDATE SET
            value_text=excluded.value_text, unit=excluded.unit, currency=excluded.currency,
            derivation=excluded.derivation, source_concept=excluded.source_concept,
            accession_number=excluded.accession_number, filing_form=excluded.filing_form,
            filing_date=excluded.filing_date,
            quality_flags_json=excluded.quality_flags_json,
            derivation_sources_json=excluded.derivation_sources_json""",
            (
                item_id, item.ticker, item.cik, item.metric.value, str(item.value), item.unit,
                item.currency, item.period_kind.value,
                item.period_start.isoformat() if item.period_start else None,
                item.period_end.isoformat(), item.fiscal_year, item.fiscal_period.value,
                item.duration_scope.value if item.duration_scope else None,
                item.derivation.value, item.source_concept, item.accession_number,
                item.filing_form, item.filing_date.isoformat() if item.filing_date else None,
                json.dumps(item.quality_flags), json.dumps(sources),
            ),
        )
        return item_id

    def upsert_financial_batch(
        self, company: Company, items: list[FinancialObservation], *, revise: bool = True,
    ) -> list[str]:
        with self._connect() as connection, connection:
            self._upsert_company(connection, company)
            ids = [self._upsert_financial_observation(connection, item) for item in items]
            if revise and items:
                self._increment_revision(connection)
        return ids

    def replace_financial_window(
        self,
        company: Company,
        items: list[FinancialObservation],
        *,
        start_year: int,
        end_year: int,
    ) -> list[str]:
        """Atomically replace one issuer's normalized fiscal-year window.

        A normalization rerun is an authoritative snapshot for the requested
        window. Deleting that window first prevents facts selected by an older
        adapter version from surviving alongside the new canonical result.
        """
        with self._connect() as connection, connection:
            self._upsert_company(connection, company)
            connection.execute(
                """DELETE FROM financial_observations
                WHERE ticker=? AND fiscal_year BETWEEN ? AND ?""",
                (company.ticker, start_year, end_year),
            )
            ids = [self._upsert_financial_observation(connection, item) for item in items]
            self._increment_revision(connection)
        return ids

    def financial_observations(
        self,
        tickers: list[str],
        metrics: list[MetricCode],
        frequency: str,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> list[FinancialObservation]:
        if not tickers or not metrics:
            return []
        ticker_marks = ",".join("?" for _ in tickers)
        metric_marks = ",".join("?" for _ in metrics)
        clauses = [f"ticker IN ({ticker_marks})", f"metric IN ({metric_marks})"]
        params: list[object] = [*tickers, *(metric.value for metric in metrics)]
        clauses.append("fiscal_period = 'FY'" if frequency == "annual" else "fiscal_period != 'FY'")
        if start_year is not None:
            clauses.append("fiscal_year >= ?")
            params.append(start_year)
        if end_year is not None:
            clauses.append("fiscal_year <= ?")
            params.append(end_year)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM financial_observations WHERE " + " AND ".join(clauses)
                + " ORDER BY ticker, period_end, fiscal_period",
                params,
            ).fetchall()
        return [self._financial_from_row(row) for row in rows]

    @staticmethod
    def _financial_from_row(row: sqlite3.Row) -> FinancialObservation:
        sources = tuple(
            SourceFactIdentity(
                role=item["role"], source_concept=item["source_concept"],
                value=Decimal(item["value"]), unit=item["unit"],
                period_start=date.fromisoformat(item["period_start"]) if item["period_start"] else None,
                period_end=date.fromisoformat(item["period_end"]),
                accession_number=item["accession_number"], filing_form=item["filing_form"],
                filing_date=date.fromisoformat(item["filing_date"]) if item["filing_date"] else None,
            )
            for item in json.loads(row["derivation_sources_json"])
        )
        return FinancialObservation(
            ticker=row["ticker"], cik=row["cik"], metric=MetricCode(row["metric"]),
            value=Decimal(row["value_text"]), unit=row["unit"], currency=row["currency"],
            period_kind=PeriodKind(row["period_kind"]),
            period_start=date.fromisoformat(row["period_start"]) if row["period_start"] else None,
            period_end=date.fromisoformat(row["period_end"]), fiscal_year=row["fiscal_year"],
            fiscal_period=FiscalPeriod(row["fiscal_period"]),
            duration_scope=DurationScope(row["duration_scope"]) if row["duration_scope"] else None,
            derivation=DerivationKind(row["derivation"]), source_concept=row["source_concept"],
            accession_number=row["accession_number"], filing_form=row["filing_form"],
            filing_date=date.fromisoformat(row["filing_date"]) if row["filing_date"] else None,
            quality_flags=tuple(json.loads(row["quality_flags_json"])), derivation_sources=sources,
        )

    def upsert_macro_series(self, item: MacroSeriesDefinition) -> None:
        with self._connect() as connection, connection:
            self._upsert_macro_series(connection, item)

    @staticmethod
    def _upsert_macro_series(connection: sqlite3.Connection, item: MacroSeriesDefinition) -> None:
        connection.execute(
            """INSERT INTO macro_series VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET label=excluded.label, unit=excluded.unit,
            frequency=excluded.frequency, source_name=excluded.source_name""",
            (item.code, item.provider, item.provider_series_id, item.label, item.unit, item.frequency, item.source_name),
        )

    def upsert_macro_observation(self, item: MacroObservation) -> None:
        with self._connect() as connection, connection:
            self._upsert_macro_observation(connection, item)

    @staticmethod
    def _upsert_macro_observation(connection: sqlite3.Connection, item: MacroObservation) -> None:
        connection.execute(
            """INSERT INTO macro_observations VALUES (?, ?, ?)
            ON CONFLICT(series_code, observation_date) DO UPDATE SET value_text=excluded.value_text""",
            (item.series_code, item.date.isoformat(), str(item.value)),
        )

    def upsert_macro_batch(
        self, definition: MacroSeriesDefinition, items: list[MacroObservation], *, revise: bool = True,
    ) -> None:
        with self._connect() as connection, connection:
            self._upsert_macro_series(connection, definition)
            for item in items:
                self._upsert_macro_observation(connection, item)
            if revise and items:
                self._increment_revision(connection)

    def macro_observations(
        self, code: str, start_date: date | None = None, end_date: date | None = None
    ) -> list[MacroObservation]:
        clauses = ["series_code = ?"]
        params: list[object] = [code]
        if start_date:
            clauses.append("observation_date >= ?")
            params.append(start_date.isoformat())
        if end_date:
            clauses.append("observation_date <= ?")
            params.append(end_date.isoformat())
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM macro_observations WHERE " + " AND ".join(clauses)
                + " ORDER BY observation_date", params
            ).fetchall()
        return [MacroObservation(row["series_code"], date.fromisoformat(row["observation_date"]), Decimal(row["value_text"])) for row in rows]

    def macro_series_metadata(self, code: str) -> dict[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT label, unit, frequency FROM macro_series WHERE code = ?", (code,)
            ).fetchone()
        return dict(row) if row else None

    def start_ingestion(self, provider: str) -> int:
        with self._connect() as connection, connection:
            cursor = connection.execute(
                "INSERT INTO ingestion_runs(provider, started_at, status) VALUES (?, ?, 'RUNNING')",
                (provider, datetime.now(timezone.utc).isoformat()),
            )
        return int(cursor.lastrowid)

    def finish_ingestion(self, run_id: int, status: str, message: str = "") -> None:
        with self._connect() as connection, connection:
            connection.execute(
                "UPDATE ingestion_runs SET completed_at=?, status=?, message=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), status, message, run_id),
            )

    def latest_ingestion_outcomes(self, providers: list[str]) -> dict[str, dict[str, str]]:
        if not providers:
            return {}
        marks = ",".join("?" for _ in providers)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT run.provider, run.status, COALESCE(run.message, '') AS message
                FROM ingestion_runs run
                JOIN (
                    SELECT provider, MAX(id) AS id FROM ingestion_runs
                    WHERE provider IN ({marks}) GROUP BY provider
                ) latest ON latest.id=run.id
                ORDER BY run.provider""",
                providers,
            ).fetchall()
        return {
            str(row["provider"]): {
                "status": str(row["status"]), "message": str(row["message"]),
            }
            for row in rows
        }

    def upsert_market_instrument(self, item: MarketInstrument) -> None:
        with self._connect() as connection, connection:
            self._upsert_market_instrument(connection, item)

    @staticmethod
    def _upsert_market_instrument(connection: sqlite3.Connection, item: MarketInstrument) -> None:
        connection.execute(
            """INSERT INTO market_instruments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_id) DO UPDATE SET company_ticker=excluded.company_ticker,
            symbol=excluded.symbol, exchange_mic=excluded.exchange_mic, currency=excluded.currency,
            security_type=excluded.security_type, share_class=excluded.share_class,
            is_primary=excluded.is_primary, valuation_status=excluded.valuation_status""",
            (item.instrument_id, item.company_ticker, item.symbol, item.exchange_mic, item.currency,
             item.security_type, item.share_class, int(item.is_primary), item.valuation_status.value),
        )

    def upsert_market_instrument_source(self, item: ProviderMarketInstrument) -> None:
        with self._connect() as connection, connection:
            self._upsert_market_instrument_source(connection, item)

    @staticmethod
    def _upsert_market_instrument_source(
        connection: sqlite3.Connection, item: ProviderMarketInstrument,
    ) -> None:
        connection.execute(
            """INSERT INTO market_instrument_sources VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(instrument_id, provider) DO UPDATE SET
            provider_symbol=excluded.provider_symbol, provider_exchange=excluded.provider_exchange,
            provider_instrument_id=excluded.provider_instrument_id""",
            (item.instrument_id, item.provider, item.provider_symbol, item.provider_exchange, item.provider_instrument_id),
        )

    def seed_market_instruments(self, provider: str = "TIINGO") -> None:
        """Seed instruments from the candidate registry (legacy method name retained)."""
        from .companies import CANDIDATE_BY_TICKER, provider_instrument

        with self._connect() as connection, connection:
            existing_companies = {row["ticker"] for row in connection.execute("SELECT ticker FROM companies")}
            for ticker in sorted(existing_companies):
                candidate = CANDIDATE_BY_TICKER.get(ticker)
                if candidate is None:
                    continue
                for item in candidate.instruments():
                    self._upsert_market_instrument(connection, item)
                    self._upsert_market_instrument_source(
                        connection, provider_instrument(item, provider)
                    )

    def seed_candidates(self) -> None:
        """Idempotently stage manifest identities without exposing them as supported."""
        from .companies import CANDIDATES, provider_instrument

        with self._connect() as connection, connection:
            for candidate in CANDIDATES:
                previous = connection.execute(
                    "SELECT certification_status FROM companies WHERE ticker=?", (candidate.ticker,)
                ).fetchone()
                status = str(previous[0]) if previous else "CANDIDATE"
                company = Company(
                    candidate.ticker, candidate.cik, candidate.name,
                    support_status="SUPPORTED" if status == "PASS" else status,
                    legal_name=candidate.legal_name,
                    certification_status=status,
                )
                self._upsert_company(connection, company)
                connection.execute("DELETE FROM company_aliases WHERE company_ticker=?", (candidate.ticker,))
                aliases = {
                    alias.strip().casefold(): alias.strip()
                    for alias in (candidate.name, candidate.legal_name, *candidate.aliases)
                    if alias.strip()
                }
                connection.executemany(
                    "INSERT INTO company_aliases(company_ticker, alias) VALUES (?, ?)",
                    ((candidate.ticker, alias) for alias in aliases.values()),
                )
                for instrument in candidate.instruments():
                    self._upsert_market_instrument(connection, instrument)
                    self._upsert_market_instrument_source(
                        connection, provider_instrument(instrument, "TIINGO")
                    )

    def seed_certified_registry(self) -> None:
        """Install the frozen release registry into a clean local database."""
        from .companies import CANDIDATE_BY_TICKER, provider_instrument
        from .registry import CERTIFICATION_POLICY, CERTIFIED_AT, CERTIFIED_TICKERS

        with self._connect() as connection, connection:
            for ticker in CERTIFIED_TICKERS:
                candidate = CANDIDATE_BY_TICKER[ticker]
                company = Company(
                    ticker=candidate.ticker,
                    cik=candidate.cik,
                    name=candidate.name,
                    support_status="SUPPORTED",
                    legal_name=candidate.legal_name,
                    certification_status="PASS",
                    certification_policy=CERTIFICATION_POLICY,
                    certified_at=CERTIFIED_AT,
                )
                self._upsert_company(connection, company)
                aliases = {
                    alias.strip().casefold(): alias.strip()
                    for alias in (candidate.name, candidate.legal_name, *candidate.aliases)
                    if alias.strip()
                }
                connection.execute("DELETE FROM company_aliases WHERE company_ticker=?", (ticker,))
                connection.executemany(
                    "INSERT INTO company_aliases(company_ticker, alias) VALUES (?, ?)",
                    ((ticker, alias) for alias in aliases.values()),
                )
                primary = candidate.instruments()[0]
                self._upsert_market_instrument(connection, primary)
                self._upsert_market_instrument_source(
                    connection, provider_instrument(primary, "TIINGO")
                )

    def start_certification_run(self, run_id: str, policy_version: str, started_at: str) -> None:
        with self._connect() as connection, connection:
            connection.execute(
                """INSERT INTO certification_runs VALUES (?, ?, ?, NULL, 'RUNNING')
                ON CONFLICT(run_id) DO UPDATE SET policy_version=excluded.policy_version,
                started_at=excluded.started_at, completed_at=NULL, status='RUNNING'""",
                (run_id, policy_version, started_at),
            )

    def save_certification_result(
        self, run_id: str, ticker: str, final_status: str | None,
        reason_codes: list[str], result: dict[str, object], certified_at: str,
        policy_version: str, *, evaluation_state: str = "COMPLETE",
        evaluation_reason: str | None = None,
    ) -> None:
        stored_status = final_status or "CANDIDATE"
        with self._connect() as connection, connection:
            previous = connection.execute(
                "SELECT certification_status FROM companies WHERE ticker=?", (ticker,)
            ).fetchone()
            connection.execute(
                """INSERT INTO certification_results(
                run_id, ticker, final_status, reason_codes_json, result_json, certified_at,
                evaluation_state, evaluation_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, ticker) DO UPDATE SET final_status=excluded.final_status,
                reason_codes_json=excluded.reason_codes_json, result_json=excluded.result_json,
                certified_at=excluded.certified_at,
                evaluation_state=excluded.evaluation_state,
                evaluation_reason=excluded.evaluation_reason""",
                (run_id, ticker, stored_status, json.dumps(reason_codes),
                 json.dumps(result, sort_keys=True), certified_at,
                 evaluation_state, evaluation_reason),
            )
            connection.execute(
                """UPDATE companies SET certification_status=?, support_status=?,
                certification_policy=?, certified_at=?, evaluation_state=?,
                evaluation_reason=? WHERE ticker=?""",
                (stored_status, "SUPPORTED" if final_status == "PASS" else stored_status,
                 policy_version, certified_at, evaluation_state, evaluation_reason, ticker),
            )
            if previous is not None and str(previous[0]) != stored_status:
                self._increment_revision(connection)

    def finish_certification_run(self, run_id: str, completed_at: str, status: str) -> None:
        with self._connect() as connection, connection:
            connection.execute(
                "UPDATE certification_runs SET completed_at=?, status=? WHERE run_id=?",
                (completed_at, status, run_id),
            )

    def certification_results(self, run_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT result_json FROM certification_results WHERE run_id=? ORDER BY ticker",
                (run_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def primary_market_instrument(self, ticker: str) -> MarketInstrument | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM market_instruments WHERE company_ticker=? AND is_primary=1", (ticker.upper(),)
            ).fetchone()
        return self._market_instrument_from_row(row) if row else None

    def market_instrument(self, instrument_id: str) -> MarketInstrument | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM market_instruments WHERE instrument_id=?", (instrument_id,)
            ).fetchone()
        return self._market_instrument_from_row(row) if row else None

    def provider_market_instrument(self, instrument_id: str, provider: str) -> ProviderMarketInstrument | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM market_instrument_sources WHERE instrument_id=? AND provider=?",
                (instrument_id, provider),
            ).fetchone()
        return ProviderMarketInstrument(**dict(row)) if row else None

    @staticmethod
    def _market_instrument_from_row(row: sqlite3.Row) -> MarketInstrument:
        return MarketInstrument(
            instrument_id=row["instrument_id"], company_ticker=row["company_ticker"], symbol=row["symbol"],
            exchange_mic=row["exchange_mic"], currency=row["currency"], security_type=row["security_type"],
            share_class=row["share_class"], is_primary=bool(row["is_primary"]),
            valuation_status=ValuationSupport(row["valuation_status"]),
        )

    def upsert_market_observation(self, item: MarketDailyObservation) -> None:
        with self._connect() as connection, connection:
            self._upsert_market_observation(connection, item)

    @staticmethod
    def _upsert_market_observation(
        connection: sqlite3.Connection, item: MarketDailyObservation,
    ) -> None:
        def value(value: Decimal | None) -> str | None:
            return str(value) if value is not None else None
        connection.execute(
            """INSERT INTO market_daily_observations VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_id, trading_date) DO UPDATE SET
            open_text=excluded.open_text, high_text=excluded.high_text, low_text=excluded.low_text,
            close_text=excluded.close_text, volume_text=excluded.volume_text,
            adjusted_open_text=excluded.adjusted_open_text, adjusted_high_text=excluded.adjusted_high_text,
            adjusted_low_text=excluded.adjusted_low_text, adjusted_close_text=excluded.adjusted_close_text,
            adjusted_volume_text=excluded.adjusted_volume_text, currency=excluded.currency,
            source_provider=excluded.source_provider, source_symbol=excluded.source_symbol,
            retrieved_at=excluded.retrieved_at, ingestion_run_id=excluded.ingestion_run_id""",
            (item.instrument_id, item.trading_date.isoformat(), value(item.open), value(item.high),
             value(item.low), value(item.close), value(item.volume), value(item.adjusted_open),
             value(item.adjusted_high), value(item.adjusted_low), value(item.adjusted_close),
             value(item.adjusted_volume), item.currency, item.source_provider, item.source_symbol,
             item.retrieved_at, item.ingestion_run_id),
        )

    def market_observations(
        self, instrument_id: str, start_date: date | None = None, end_date: date | None = None,
    ) -> list[MarketDailyObservation]:
        clauses = ["instrument_id=?"]
        params: list[object] = [instrument_id]
        if start_date is not None:
            clauses.append("trading_date>=?"); params.append(start_date.isoformat())
        if end_date is not None:
            clauses.append("trading_date<=?"); params.append(end_date.isoformat())
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM market_daily_observations WHERE " + " AND ".join(clauses) + " ORDER BY trading_date", params
            ).fetchall()
        def decimal_value(row: sqlite3.Row, key: str) -> Decimal | None:
            return Decimal(row[key]) if row[key] is not None else None
        return [MarketDailyObservation(
            trading_date=date.fromisoformat(row["trading_date"]), close=Decimal(row["close_text"]),
            open=decimal_value(row, "open_text"), high=decimal_value(row, "high_text"),
            low=decimal_value(row, "low_text"), volume=decimal_value(row, "volume_text"),
            adjusted_open=decimal_value(row, "adjusted_open_text"), adjusted_high=decimal_value(row, "adjusted_high_text"),
            adjusted_low=decimal_value(row, "adjusted_low_text"), adjusted_close=decimal_value(row, "adjusted_close_text"),
            adjusted_volume=decimal_value(row, "adjusted_volume_text"), instrument_id=row["instrument_id"],
            currency=row["currency"], source_provider=row["source_provider"], source_symbol=row["source_symbol"],
            retrieved_at=row["retrieved_at"], ingestion_run_id=row["ingestion_run_id"],
        ) for row in rows]

    def market_coverage(self, instrument_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT MIN(trading_date) AS first_date, MAX(trading_date) AS last_date,
                COUNT(*) AS observation_count, MIN(source_provider) AS provider
                FROM market_daily_observations WHERE instrument_id=?""",
                (instrument_id,),
            ).fetchone()
        return {
            "first_date": row["first_date"],
            "last_date": row["last_date"],
            "observation_count": row["observation_count"],
            "provider": row["provider"],
        }

    def upsert_corporate_action(self, item: CorporateAction) -> None:
        with self._connect() as connection, connection:
            self._upsert_corporate_action(connection, item)

    @staticmethod
    def _upsert_corporate_action(connection: sqlite3.Connection, item: CorporateAction) -> None:
        connection.execute(
            """INSERT INTO market_corporate_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_id, action_type, event_date) DO UPDATE SET
            date_type=excluded.date_type, split_ratio_text=excluded.split_ratio_text,
            cash_amount_per_share_text=excluded.cash_amount_per_share_text, currency=excluded.currency,
            source_provider=excluded.source_provider, source_symbol=excluded.source_symbol,
            retrieved_at=excluded.retrieved_at, ingestion_run_id=excluded.ingestion_run_id""",
            (item.instrument_id, item.action_type.value, item.event_date.isoformat(), item.date_type.value,
             str(item.split_ratio) if item.split_ratio is not None else None,
             str(item.cash_amount_per_share) if item.cash_amount_per_share is not None else None,
             item.currency, item.source_provider, item.source_symbol, item.retrieved_at, item.ingestion_run_id),
        )

    def upsert_market_batch(
        self, observations: list[MarketDailyObservation], actions: list[CorporateAction],
        *, revise: bool = True,
        authoritative_action_range: tuple[str, date, date, str] | None = None,
    ) -> None:
        with self._connect() as connection, connection:
            removed_actions = 0
            if authoritative_action_range is not None:
                instrument_id, start_date, end_date, _source_provider = authoritative_action_range
                cursor = connection.execute(
                    """DELETE FROM market_corporate_actions
                    WHERE instrument_id=? AND event_date>=? AND event_date<=?""",
                    (instrument_id, start_date.isoformat(), end_date.isoformat()),
                )
                removed_actions = cursor.rowcount
            for item in observations:
                self._upsert_market_observation(connection, item)
            for item in actions:
                self._upsert_corporate_action(connection, item)
            if revise and (observations or actions or removed_actions):
                self._increment_revision(connection)

    def corporate_actions(
        self, instrument_id: str, start_date: date | None = None, end_date: date | None = None,
    ) -> list[CorporateAction]:
        clauses = ["instrument_id=?"]
        params: list[object] = [instrument_id]
        if start_date is not None:
            clauses.append("event_date>=?"); params.append(start_date.isoformat())
        if end_date is not None:
            clauses.append("event_date<=?"); params.append(end_date.isoformat())
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM market_corporate_actions WHERE " + " AND ".join(clauses) + " ORDER BY event_date", params
            ).fetchall()
        return [CorporateAction(
            action_type=CorporateActionType(row["action_type"]), event_date=date.fromisoformat(row["event_date"]),
            date_type=CorporateActionDateType(row["date_type"]),
            split_ratio=Decimal(row["split_ratio_text"]) if row["split_ratio_text"] else None,
            cash_amount_per_share=Decimal(row["cash_amount_per_share_text"]) if row["cash_amount_per_share_text"] else None,
            currency=row["currency"], instrument_id=row["instrument_id"], source_provider=row["source_provider"],
            source_symbol=row["source_symbol"], retrieved_at=row["retrieved_at"], ingestion_run_id=row["ingestion_run_id"],
        ) for row in rows]

    def catalog(self) -> dict[str, object]:
        from .macro import SEMANTIC_MACRO_SERIES
        from .metrics import DERIVED_METRIC_LABELS, METRICS
        from .query import DERIVED_INPUTS

        with self._connect() as connection:
            companies = [dict(row) for row in connection.execute(
                """SELECT ticker, cik, name, fiscal_year_end, support_status
                FROM companies WHERE certification_status='PASS' ORDER BY ticker"""
            )]
            support = [dict(row) for row in connection.execute(
                """SELECT ticker, metric, CASE WHEN fiscal_period = 'FY' THEN 'annual' ELSE 'quarterly' END AS frequency
                FROM financial_observations observation JOIN companies company USING(ticker)
                WHERE company.certification_status='PASS'
                GROUP BY ticker, metric, frequency ORDER BY ticker, metric, frequency"""
            )]
            macro_series = [dict(row) for row in connection.execute(
                "SELECT code, label, unit, frequency, provider_series_id FROM macro_series ORDER BY code"
            )]
            market_instruments = [dict(row) for row in connection.execute(
                """SELECT instrument_id, company_ticker, symbol, exchange_mic, currency,
                security_type, share_class, is_primary, valuation_status
                FROM market_instruments instrument JOIN companies company
                ON company.ticker=instrument.company_ticker
                WHERE company.certification_status='PASS'
                ORDER BY company_ticker, is_primary DESC"""
            )]
            market_support = [dict(row) for row in connection.execute(
                """SELECT instrument.company_ticker AS ticker, instrument.instrument_id,
                MIN(observation.trading_date) AS first_date,
                MAX(observation.trading_date) AS last_date,
                COUNT(observation.trading_date) AS observation_count
                FROM market_instruments AS instrument
                LEFT JOIN market_daily_observations AS observation USING(instrument_id)
                JOIN companies company ON company.ticker=instrument.company_ticker
                WHERE instrument.is_primary=1 AND company.certification_status='PASS'
                GROUP BY instrument.company_ticker, instrument.instrument_id
                ORDER BY instrument.company_ticker"""
            )]
            valuation_support = [dict(row) for row in connection.execute(
                """SELECT company_ticker AS ticker, instrument_id, valuation_status AS status
                FROM market_instruments instrument JOIN companies company
                ON company.ticker=instrument.company_ticker
                WHERE is_primary=1 AND company.certification_status='PASS'
                ORDER BY company_ticker"""
            )]
            aliases = {str(row["alias"]): str(row["company_ticker"]) for row in connection.execute(
                """SELECT alias, company_ticker FROM company_aliases alias
                JOIN companies company ON company.ticker=alias.company_ticker
                WHERE company.certification_status='PASS' ORDER BY alias"""
            )}
        direct_support = {
            (item["ticker"], item["metric"], item["frequency"])
            for item in support
        }
        derived_support = []
        tickers = {item["ticker"] for item in support}
        for ticker in tickers:
            for metric, inputs in DERIVED_INPUTS.items():
                for frequency in ("annual", "quarterly"):
                    if metric.value == "CURRENT_RATIO" and frequency == "quarterly":
                        continue
                    if all((ticker, item.value, frequency) in direct_support for item in inputs):
                        derived_support.append({
                            "ticker": ticker,
                            "metric": metric.value,
                            "frequency": frequency,
                        })
        support.extend(derived_support)
        support.sort(key=lambda item: (item["ticker"], item["metric"], item["frequency"]))
        company_metrics = sorted({item["metric"] for item in support})
        metric_definitions = [
            {
                "code": definition.code.value,
                "label": definition.label,
                "category": definition.category,
                "unit_kind": definition.unit_kind,
                "kind": "direct",
            }
            for definition in METRICS.values()
        ]
        derived_categories = {
            "REVENUE_GROWTH_YOY": "profitability",
            "GROSS_MARGIN": "profitability",
            "OPERATING_MARGIN": "profitability",
            "NET_MARGIN": "profitability",
            "FREE_CASH_FLOW": "cash_flow",
            "FCF_MARGIN": "profitability",
            "CURRENT_RATIO": "balance",
        }
        metric_definitions.extend({
            "code": code.value,
            "label": label,
            "category": derived_categories[code.value],
            "unit_kind": "currency" if code.value == "FREE_CASH_FLOW" else "ratio",
            "kind": "derived",
        } for code, label in DERIVED_METRIC_LABELS.items())
        loaded_codes = {item["code"] for item in macro_series}
        for code, (base_code, label) in SEMANTIC_MACRO_SERIES.items():
            if base_code in loaded_codes:
                macro_series.append({
                    "code": code, "label": label, "unit": "percent",
                    "frequency": "monthly", "provider_series_id": None,
                })
        return {
            "companies": companies,
            "company_aliases": aliases,
            "company_metrics": company_metrics,
            "company_metric_definitions": metric_definitions,
            "company_metric_support": support,
            "macro_series": macro_series,
            "market_instruments": market_instruments,
            "market_support": market_support,
            "market_metric_definitions": [
                {"code": "RAW_CLOSE", "label": "Raw close", "unit_kind": "currency"},
                {"code": "ADJUSTED_CLOSE", "label": "Provider-adjusted close", "unit_kind": "currency"},
            ],
            "valuation_metric_definitions": [
                {"code": "PE_RATIO", "label": "P/E", "unit_kind": "multiple"},
                {"code": "PS_RATIO", "label": "P/S", "unit_kind": "multiple"},
                {"code": "P_FCF_RATIO", "label": "P/FCF", "unit_kind": "multiple"},
                {"code": "FCF_YIELD", "label": "FCF Yield", "unit_kind": "yield"},
            ],
            "valuation_support": valuation_support,
            "data_revision": self.data_revision(),
        }
