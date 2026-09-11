import type { Catalog, QueryResponse } from "@/api/types"

export const catalog: Catalog = {
  data_revision: "1",
  companies: [
    { ticker: "AAPL", cik: "1", name: "Apple Inc.", fiscal_year_end: null, support_status: "SUPPORTED" },
    { ticker: "MSFT", cik: "2", name: "Microsoft Corporation", fiscal_year_end: null, support_status: "SUPPORTED" },
  ],
  company_metrics: ["REVENUE", "OPERATING_MARGIN"],
  company_metric_definitions: [
    { code: "REVENUE", label: "Revenue", category: "income", unit_kind: "currency", kind: "direct" },
    { code: "OPERATING_MARGIN", label: "Operating margin", category: "profitability", unit_kind: "ratio", kind: "derived" },
  ],
  company_metric_support: ["AAPL", "MSFT"].flatMap((ticker) => ["annual", "quarterly"].map((frequency) => ({ ticker, metric: "REVENUE", frequency: frequency as "annual" | "quarterly" }))).concat([{ ticker: "AAPL", metric: "OPERATING_MARGIN", frequency: "annual" }]),
  macro_series: [{ code: "US_INFLATION_YOY", label: "Inflation", unit: "percent", frequency: "monthly", provider_series_id: null }],
}

export const sourceResponse: QueryResponse = { status: "SUCCESS", errors: [], series: [{ id: "AAPL:REVENUE", label: "Revenue", entity: "AAPL", metric: "REVENUE", unit: "USD", frequency: "annual", observations: [{ date: "2025-09-27", value: "416161000000", point_type: "SOURCE", observation_id: "sec:1", formula: null, input_observation_ids: [], fiscal_year: 2025, fiscal_period: "FY", evidence: [{ id: "sec:1", metric: "REVENUE", label: "Revenue", value: "416161000000", unit: "USD", source_type: "SEC", source_concept: "us-gaap:Revenue", accession_number: "0001-25", filing_form: "10-K", filing_date: "2025-11-01", period_start: "2024-09-28", period_end: "2025-09-27", derivation: "DIRECT", provider_series_id: null }] }] }] }

export const calculatedResponse: QueryResponse = { status: "SUCCESS", errors: [], series: [{ ...sourceResponse.series[0], id: "AAPL:OPERATING_MARGIN", label: "Operating margin", metric: "OPERATING_MARGIN", unit: "ratio", observations: [{ ...sourceResponse.series[0].observations[0], value: "0.316", point_type: "CALCULATED", observation_id: null, formula: "OPERATING_INCOME / REVENUE", input_observation_ids: ["sec:2", "sec:1"], evidence: [sourceResponse.series[0].observations[0].evidence[0]] }] }] }
