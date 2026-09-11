import { render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { calculatedResponse, sourceResponse } from "@/test/fixtures"
import { EvidenceSheet } from "./evidence-sheet"

describe("EvidenceSheet", () => {
  afterEach(() => vi.unstubAllGlobals())
  it("renders direct SEC provenance", () => {
    const series = sourceResponse.series[0]; render(<EvidenceSheet selection={{ series, point: series.observations[0] }} onClose={() => {}}/>)
    expect(screen.getByText("Reported")).toBeInTheDocument(); expect(screen.getByText("us-gaap:Revenue")).toBeInTheDocument(); expect(screen.getByText("0001-25")).toBeInTheDocument()
  })
  it("renders calculated formulas and inputs", () => {
    const series = calculatedResponse.series[0]; render(<EvidenceSheet selection={{ series, point: series.observations[0] }} onClose={() => {}}/>)
    expect(screen.getByText("Calculated")).toBeInTheDocument(); expect(screen.getByText("OPERATING_INCOME / REVENUE")).toBeInTheDocument(); expect(screen.getByText("Calculation inputs")).toBeInTheDocument()
  })
  it("renders MARKET instrument provenance", () => {
    const series = { ...sourceResponse.series[0], id: "AAPL:RAW_CLOSE", label: "Raw close", metric: "RAW_CLOSE", unit: "USD", frequency: "daily", observations: [{ ...sourceResponse.series[0].observations[0], date: "2026-08-28", value: "232.14", fiscal_year: null, fiscal_period: null, evidence: [{ ...sourceResponse.series[0].observations[0].evidence[0], id: "market:us-xnas-aapl:2026-08-28:close", label: "Raw close", metric: "RAW_CLOSE", value: "232.14", source_type: "MARKET" as const, source_concept: null, accession_number: null, filing_form: null, filing_date: null, period_start: null, period_end: "2026-08-28", derivation: null, provider: "TIINGO", symbol: "AAPL", exchange_mic: "XNAS", market_field: "close", retrieved_at: "2026-08-29T00:00:00Z" }] }] }
    render(<EvidenceSheet selection={{ series, point: series.observations[0] }} onClose={() => {}}/>)
    expect(screen.getByText("TIINGO")).toBeInTheDocument(); expect(screen.getByText("AAPL / XNAS")).toBeInTheDocument(); expect(screen.getByText("close")).toBeInTheDocument()
  })
  it("lazily resolves a thin market observation reference", async () => {
    const marketEvidence = { ...sourceResponse.series[0].observations[0].evidence[0], id: "market:us-xnas-aapl:2026-08-28:close", label: "Raw close", metric: "RAW_CLOSE", value: "232.14", source_type: "MARKET" as const, source_concept: null, accession_number: null, filing_form: null, filing_date: null, period_start: null, period_end: "2026-08-28", derivation: null, provider: "TIINGO", symbol: "AAPL", exchange_mic: "XNAS", market_field: "close", retrieved_at: "2026-08-29T00:00:00Z" }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(marketEvidence), { status: 200, headers: { "Content-Type": "application/json" } })))
    const point = { ...sourceResponse.series[0].observations[0], date: "2026-08-28", value: "232.14", fiscal_year: null, fiscal_period: null, observation_id: marketEvidence.id, evidence: [] }
    const series = { ...sourceResponse.series[0], id: "AAPL:RAW_CLOSE", label: "Raw close", metric: "RAW_CLOSE", unit: "USD", frequency: "daily", observations: [point] }
    render(<EvidenceSheet selection={{ series, point }} onClose={() => {}}/>)
    expect(await screen.findByText("TIINGO")).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith(`/api/v1/evidence/${encodeURIComponent(marketEvidence.id)}`, expect.anything())
  })
})
