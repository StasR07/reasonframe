import json

from finance_terminal.ai import compact_catalog
from finance_terminal.metrics import MetricCode
from finance_terminal.query import CompanyQuery, QueryEngine, ResultStatus
from finance_terminal.storage import Company, SQLiteStore


def test_synthetic_50_company_catalog_and_parser_context_are_compact() -> None:
    store = SQLiteStore(":memory:")
    try:
        for index in range(50):
            store.upsert_company(Company(
                f"C{index:02d}", str(100_000 + index).zfill(10), f"Synthetic Company {index:02d}",
            ))
        catalog = store.catalog()
        parser_catalog = compact_catalog(catalog)

        assert len(catalog["companies"]) == 50
        assert len(json.dumps(catalog, separators=(",", ":")).encode()) < 15_000
        assert len(json.dumps(parser_catalog, separators=(",", ":")).encode()) < 10_000
        assert parser_catalog["aliases"] == {}
        result = QueryEngine(store).execute(CompanyQuery(
            tickers=["C49"], metric=MetricCode.REVENUE, frequency="annual",
        ))
        assert result.status is ResultStatus.UNAVAILABLE
        assert result.errors == ["Revenue is unavailable for C49 in the requested range"]
    finally:
        store.close()


def test_catalog_change_revises_once_and_hot_indexes_are_not_overlapping() -> None:
    store = SQLiteStore(":memory:")
    try:
        assert store.data_revision() == "0"
        company = Company("TEST", "0000000001", "Test Company")
        store.upsert_company(company)
        assert store.data_revision() == "1"
        store.upsert_company(company)
        assert store.data_revision() == "1"
        with store._connect() as connection:
            indexes = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )}
        assert "ix_financial_lookup_v2" in indexes
        assert "ix_market_instrument_company_primary" in indexes
        assert "ix_financial_lookup" not in indexes
        assert "ix_market_daily_date" not in indexes
    finally:
        store.close()
