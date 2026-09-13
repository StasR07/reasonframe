import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, useLocation } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { AskResponse, CompanyQuery, MacroQuery, MarketQuery } from "@/api/types"
import { catalog, sourceResponse } from "@/test/fixtures"

const { askMock, analyzeResultMock, analyzeComparisonMock, catalogMock, queryMock, aiStatusMock, aiConnectMock, aiDisconnectMock, aiModelsMock } = vi.hoisted(() => ({ askMock: vi.fn(), analyzeResultMock: vi.fn(), analyzeComparisonMock: vi.fn(), catalogMock: vi.fn(), queryMock: vi.fn(), aiStatusMock: vi.fn(), aiConnectMock: vi.fn(), aiDisconnectMock: vi.fn(), aiModelsMock: vi.fn() }))
vi.mock("@/api/client", () => ({ api: { ask: askMock, analyzeResult: analyzeResultMock, analyzeComparison: analyzeComparisonMock, catalog: catalogMock, query: queryMock, aiStatus: aiStatusMock, aiConnect: aiConnectMock, aiDisconnect: aiDisconnectMock, aiModels: aiModelsMock } }))

import App from "@/App"
import { universeRankingPresentation } from "./search-page"

function Location() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

const companyQuery: CompanyQuery = { domain: "company", tickers: ["AAPL"], metric: "REVENUE", frequency: "annual", operation: "LEVEL" }
const success: AskResponse = {
  status: "success", question: "Show Apple revenue", validated_query: companyQuery,
  interpretation: { domain: "company", entities: ["AAPL"], metric: "Revenue", frequency: "Annual", range: "All loaded history", operation: "LEVEL" },
  results: sourceResponse,
  factual_summary: { claims: [{ text: "Apple revenue is shown for the returned period.", evidence_refs: ["sec:1"] }] },
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  localStorage.setItem("finance-terminal:ai-search-model:v2", "test-default")
  catalogMock.mockResolvedValue({ ...catalog, features: { ai_search: true } })
  queryMock.mockResolvedValue(sourceResponse)
  askMock.mockResolvedValue(success)
  analyzeResultMock.mockResolvedValue({ status: "success", mode: "focused", packet_version: 1, cache_key: "focused-evidence-key", interpretation: { observations: [{ text: "Revenue reached the supported period high.", evidence_refs: ["sec:1"] }], caveat: null }, evidence_context: sourceResponse.series })
  analyzeComparisonMock.mockResolvedValue({ status: "success", mode: "comparison", packet_version: 1, cache_key: "comparison-evidence-key", interpretation: { summary: { text: "The companies differ on supported measures.", evidence_refs: ["sec:1"] }, fundamentals_comparison: [], valuation_comparison: [], market_context: [], key_tradeoffs: [], limitations: [] }, evidence_context: sourceResponse.series })
  aiStatusMock.mockResolvedValue({ provider: "chatgpt_codex", connected: true, plan_type: "plus", model: "test-default" })
  aiConnectMock.mockResolvedValue({ provider: "chatgpt_codex", connected: false, auth_url: "https://auth.example.test" })
  aiDisconnectMock.mockResolvedValue({ provider: "chatgpt_codex", connected: false, plan_type: null, model: null })
  aiModelsMock.mockResolvedValue([{ id: "test-default", name: "Test default", description: "", is_default: true }])
})

describe("natural-language search workspace", () => {
  it("keeps concise landing examples clickable with the original query behavior", async () => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={["/search"]}><Location/><App/></MemoryRouter>)
    const example = await screen.findByRole("button", { name: "Apple vs Nvidia stock performance over 5Y" })
    await user.click(example)
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("q=Apple+vs+Nvidia+stock+performance+over+5Y"))
    expect(askMock).toHaveBeenCalledWith("Apple vs Nvidia stock performance over 5Y", "test-default", expect.any(AbortSignal))
  })

  it("shows only the requested universe ranking count while retaining full coverage", () => {
    const series = Array.from({ length: 50 }, (_, index) => ({
      ...sourceResponse.series[0], id: `C${index}:NET_MARGIN`, entity: `C${index}`,
      label: "Net Margin", metric: "NET_MARGIN", unit: "ratio",
      context: { coverage_count: 50, rank: index + 1 },
      observations: [{ ...sourceResponse.series[0].observations[0], value: String((50 - index) / 100) }],
    }))
    const base: CompanyQuery = { domain: "company", tickers: series.map((item) => item.entity!), metric: "NET_MARGIN", frequency: "annual", start_year: 2025, end_year: 2025, ranking: "HIGHEST", universe_ranking: true }
    const defaultView = universeRankingPresentation(base, series)
    expect(defaultView.visible).toHaveLength(5)
    expect(defaultView.label).toBe("Top 5 of 50 companies with usable FY2025 Net Margin data")
    expect(universeRankingPresentation({ ...base, ranking: "LOWEST" }, series).visible).toHaveLength(5)
    expect(universeRankingPresentation({ ...base, ranking_limit: 3 }, series).visible).toHaveLength(3)
    expect(universeRankingPresentation({ ...base, ranking_limit: 10 }, series).visible).toHaveLength(10)
    expect(universeRankingPresentation({ ...base, universe_ranking: false, tickers: ["AAPL", "MSFT"] }, series.slice(0, 2)).visible).toHaveLength(2)
    expect(defaultView.visible[0].observations[0].value).toBe(series[0].observations[0].value)
  })

  it("shows sanitized ChatGPT connection status and supports disconnect", async () => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={["/settings"]}><App/></MemoryRouter>)
    expect(await screen.findByText("ChatGPT subscription is connected")).toBeInTheDocument()
    expect(await screen.findByRole("combobox", { name: "AI search model" })).toHaveTextContent("Test default")
    await user.click(screen.getByRole("button", { name: "Disconnect" }))
    await waitFor(() => expect(aiDisconnectMock).toHaveBeenCalledOnce())
    expect(await screen.findByText("Not connected")).toBeInTheDocument()
  })
  it("submits from Search and renders interpretation, scalar result, and evidence without a factual summary", async () => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={["/search"]}><Location/><App/></MemoryRouter>)
    const input = await screen.findByRole("textbox", { name: "Natural-language financial search" })
    await user.type(input, "Show Apple revenue")
    await user.click(screen.getByRole("button", { name: "Search" }))
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/search?q=Show%20Apple%20revenue"))
    expect(screen.getByRole("heading", { name: "Show Apple revenue" })).toBeInTheDocument()
    expect(await screen.findByText("Interpreted as")).toBeInTheDocument()
    expect(screen.getAllByText("Revenue").length).toBeGreaterThan(0)
    expect(screen.queryByText("Apple revenue is shown for the returned period.")).not.toBeInTheDocument()
    expect(screen.queryByText("Key observations")).not.toBeInTheDocument()
    expect(await screen.findByRole("button", { name: "View evidence" })).toBeInTheDocument()
    expect(screen.queryByRole("columnheader", { name: "Exact value" })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "View evidence" }))
    expect(await screen.findByText("Source evidence")).toBeInTheDocument()
    expect(askMock).toHaveBeenCalledWith("Show Apple revenue", "test-default", expect.any(AbortSignal))
  })

  it("reuses the comparison matrix and macro history presentation", async () => {
    const comparison: AskResponse = {
      ...success, question: "Compare Apple and Microsoft revenue",
      validated_query: { ...companyQuery, tickers: ["AAPL", "MSFT"] },
      interpretation: { ...success.interpretation, entities: ["AAPL", "MSFT"] },
      results: { ...sourceResponse, series: [sourceResponse.series[0], { ...sourceResponse.series[0], id: "MSFT:REVENUE", entity: "MSFT" }] },
      factual_summary: null,
    }
    askMock.mockResolvedValueOnce(comparison)
    const first = render(<MemoryRouter initialEntries={["/search?q=Compare%20Apple%20and%20Microsoft%20revenue"]}><App/></MemoryRouter>)
    expect(await screen.findByText("AAPL vs MSFT")).toBeInTheDocument()
    expect(await screen.findAllByText("$416,161,000,000")).toHaveLength(2)
    first.unmount()

    const macroQuery: MacroQuery = { domain: "macro", series: ["US_INFLATION_YOY"], operation: "LEVEL" }
    askMock.mockResolvedValueOnce({
      ...success, question: "Show inflation", validated_query: macroQuery,
      interpretation: { domain: "macro", entities: [], metric: "Inflation (YoY)", frequency: "Monthly", range: "All loaded history", operation: "LEVEL" },
      results: { ...sourceResponse, series: [{ ...sourceResponse.series[0], id: "macro:US_INFLATION_YOY", entity: null, metric: "US_INFLATION_YOY", label: "Inflation (YoY)", unit: "percent", frequency: "monthly" }] },
      factual_summary: null,
    })
    render(<MemoryRouter initialEntries={["/search?q=Show%20inflation"]}><App/></MemoryRouter>)
    expect(await screen.findAllByText("Inflation (YoY)")).not.toHaveLength(0)
    expect(await screen.findByRole("button", { name: "View evidence" })).toBeInTheDocument()
  })

  it("automatically runs a Key insight for a meaningful series and opens grounded evidence", async () => {
    const user = userEvent.setup()
    askMock.mockResolvedValueOnce({ ...success, results: { ...sourceResponse, series: [{ ...sourceResponse.series[0], observations: [sourceResponse.series[0].observations[0], { ...sourceResponse.series[0].observations[0], date: "2024-09-28", fiscal_year: 2024 }] }] } })
    render(<MemoryRouter initialEntries={["/search?q=Show%20Apple%20revenue"]}><App/></MemoryRouter>)
    expect(await screen.findByText("Revenue reached the supported period high.")).toBeInTheDocument()
    expect(analyzeResultMock).toHaveBeenCalledWith(companyQuery, expect.any(AbortSignal))
    await user.click(screen.getByRole("button", { name: "1 evidence item" }))
    expect(await screen.findByText("Source evidence")).toBeInTheDocument()
  })

  it("suppresses scalar interpretation and keeps multi-company Search to one focused call", async () => {
    const scalar = render(<MemoryRouter initialEntries={["/search?q=Show%20Apple%20revenue"]}><App/></MemoryRouter>)
    expect(await screen.findByText("$416.2B")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "AI Analysis" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Interpret result" })).not.toBeInTheDocument()
    expect(analyzeResultMock).not.toHaveBeenCalled()
    scalar.unmount()

    const comparison: AskResponse = {
      ...success, question: "Compare Apple and Microsoft revenue",
      validated_query: { ...companyQuery, tickers: ["AAPL", "MSFT"], frequency: "quarterly", start_year: 2021 },
      interpretation: { ...success.interpretation, entities: ["AAPL", "MSFT"], frequency: "Quarterly", range: "Since 2021" },
      results: { ...sourceResponse, series: [sourceResponse.series[0], { ...sourceResponse.series[0], id: "MSFT:REVENUE", entity: "MSFT" }] }, factual_summary: null,
    }
    askMock.mockResolvedValueOnce(comparison)
    render(<MemoryRouter initialEntries={["/search?q=Compare%20Apple%20and%20Microsoft%20revenue"]}><App/></MemoryRouter>)
    await waitFor(() => expect(analyzeResultMock).toHaveBeenCalledWith(comparison.validated_query, expect.any(AbortSignal)))
    expect(analyzeComparisonMock).not.toHaveBeenCalled()
  })

  it("renders unsupported and bounded clarification states", async () => {
    askMock.mockResolvedValueOnce({ status: "unsupported", question: "What stock should I buy?", reason_code: "INVESTMENT_ADVICE", message: "This search does not provide stock recommendations." })
    const first = render(<MemoryRouter initialEntries={["/search?q=What%20stock%20should%20I%20buy"]}><App/></MemoryRouter>)
    expect(await screen.findByText("Unsupported question")).toBeInTheDocument()
    expect(await screen.findByText(/does not provide stock recommendations/)).toBeInTheDocument()
    first.unmount()

    askMock.mockResolvedValueOnce({ status: "clarification", question: "Show Apple's margin", message: "Which margin do you mean?", choices: [{ label: "Operating margin", question: "Show Apple operating margin" }] })
    render(<MemoryRouter initialEntries={["/search?q=Show%20Apple%27s%20margin"]}><App/></MemoryRouter>)
    expect(await screen.findByText("Clarification needed")).toBeInTheDocument()
    expect(await screen.findByRole("button", { name: "Operating margin" })).toBeInTheDocument()
  })

  it("keeps market Search scalar and exposes no Phase 4 analyst action", async () => {
    const marketQuery: MarketQuery = { domain: "market", tickers: ["AAPL"], series: "RAW_CLOSE", view: "latest", operation: "LEVEL" }
    askMock.mockResolvedValueOnce({
      ...success, question: "What is Apple's latest stock price?", validated_query: marketQuery,
      interpretation: { domain: "market", entities: ["AAPL"], metric: "Raw close", frequency: "Daily", range: "Latest EOD", operation: "LEVEL" },
      results: { ...sourceResponse, series: [{ ...sourceResponse.series[0], id: "AAPL:RAW_CLOSE", label: "Raw close", metric: "RAW_CLOSE", unit: "USD", frequency: "daily", observations: [{ ...sourceResponse.series[0].observations[0], date: "2026-08-28", value: "232.14", fiscal_year: null, fiscal_period: null }] }] }, factual_summary: null,
    })
    render(<MemoryRouter initialEntries={["/search?q=What%20is%20Apple%27s%20latest%20stock%20price%3F"]}><App/></MemoryRouter>)
    expect(await screen.findByText("$232.14")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "AI Analysis" })).not.toBeInTheDocument()
    expect(screen.queryByRole("columnheader", { name: "Exact value" })).not.toBeInTheDocument()
  })

  it("keeps deterministic workspaces available and handles provider error and optional summary", async () => {
    askMock.mockResolvedValueOnce({ status: "error", question: "Show Apple revenue", error_code: "AI_UNAVAILABLE", message: "Natural-language search is temporarily unavailable. The manual research workspace is still available." })
    render(<MemoryRouter initialEntries={["/search?q=Show%20Apple%20revenue"]}><App/></MemoryRouter>)
    expect(await screen.findByText("Search unavailable")).toBeInTheDocument()
    expect(await screen.findByText(/manual research workspace/)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Companies" })).toBeInTheDocument()
  })

  it("restores a successful result after navigation without another ask", async () => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={["/search?q=Show%20Apple%20revenue"]}><App/></MemoryRouter>)
    expect(await screen.findByRole("button", { name: "View evidence" })).toBeInTheDocument()
    expect(askMock).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole("link", { name: "Companies" }))
    expect(await screen.findByRole("heading", { name: "Apple Inc." })).toBeInTheDocument()
    await user.click(screen.getByRole("link", { name: "Search" }))
    expect(await screen.findByRole("button", { name: "View evidence" })).toBeInTheDocument()
    expect(askMock).toHaveBeenCalledTimes(1)
  })
})
