import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import userEvent from "@testing-library/user-event"
import type { QueryRequest, QueryResponse } from "@/api/types"
import { catalog, sourceResponse } from "@/test/fixtures"
import { resetCatalogCacheForTests } from "@/hooks/use-api"

const { queryMock, analyzeCompanyMock, analyzeComparisonMock, catalogMock, aiStatusMock, aiConnectMock, aiDisconnectMock, aiModelsMock } = vi.hoisted(() => ({ queryMock: vi.fn(), analyzeCompanyMock: vi.fn(), analyzeComparisonMock: vi.fn(), catalogMock: vi.fn(), aiStatusMock: vi.fn(), aiConnectMock: vi.fn(), aiDisconnectMock: vi.fn(), aiModelsMock: vi.fn() }))
vi.mock("@/api/client", () => ({ api: { catalog: catalogMock, query: queryMock, analyzeCompany: analyzeCompanyMock, analyzeComparison: analyzeComparisonMock, aiStatus: aiStatusMock, aiConnect: aiConnectMock, aiDisconnect: aiDisconnectMock, aiModels: aiModelsMock } }))

import App from "@/App"

function LocationControls() {
  const location = useLocation()
  const navigate = useNavigate()
  return <><output data-testid="location">{location.pathname}{location.search}</output><button onClick={() => navigate(-1)}>Back</button><button onClick={() => navigate(1)}>Forward</button></>
}

function responseFor(request: QueryRequest): QueryResponse {
  if (request.domain === "macro") return { status: "SUCCESS", errors: [], series: [{ ...sourceResponse.series[0], id: "macro:US_INFLATION_YOY", entity: null, label: "Inflation", metric: "US_INFLATION_YOY", unit: "percent", frequency: "monthly", observations: [{ ...sourceResponse.series[0].observations[0], fiscal_year: null, fiscal_period: null, value: "2.9" }] }] }
  if (request.domain === "market") return { status: "UNAVAILABLE", errors: ["Market fixture unavailable"], series: [] }
  const series = request.tickers.map((ticker) => ({ ...sourceResponse.series[0], id: `${ticker}:${request.metric}`, entity: ticker, metric: request.metric }))
  return { status: "SUCCESS", errors: [], series }
}

beforeEach(() => {
  vi.clearAllMocks()
  resetCatalogCacheForTests()
  sessionStorage.clear()
  localStorage.setItem("finance-terminal:ai-search-model:v2", "test-default")
  catalogMock.mockResolvedValue(catalog)
  queryMock.mockImplementation(async (request: QueryRequest) => responseFor(request))
  analyzeComparisonMock.mockResolvedValue({
    status: "success", mode: "comparison", packet_version: 3, cache_key: "workspace-comparison",
    interpretation: { summary: { text: "The selected companies have different supported trade-offs.", evidence_refs: ["sec:1"] }, fundamentals_comparison: [], valuation_comparison: [], market_context: [], key_tradeoffs: [], limitations: [] },
    evidence_context: sourceResponse.series,
  })
  aiStatusMock.mockResolvedValue({ provider: "chatgpt_codex", connected: true, plan_type: "plus", model: "test-default" })
  aiDisconnectMock.mockResolvedValue({ provider: "chatgpt_codex", connected: false, plan_type: null, model: null })
  aiModelsMock.mockResolvedValue([{ id: "test-default", name: "Test default", description: "", is_default: true }])
})

describe("workspace flows", () => {
  it("loads a company workspace from URL state", async () => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={["/company/AAPL?metric=REVENUE&frequency=annual"]}><App/></MemoryRouter>)
    expect(await screen.findByRole("heading", { name: "Apple Inc." })).toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "Natural-language financial search" })).not.toBeInTheDocument()
    expect(await screen.findByText("Financial history")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "AI Analysis" }))
    expect(screen.getByRole("button", { name: "Run company analysis" })).toBeInTheDocument()
    expect(await screen.findAllByText("$416.2B")).not.toHaveLength(0)
  })
  it("loads the two-company comparison flow", async () => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={["/compare"]}><App/></MemoryRouter>)
    expect(await screen.findByRole("heading", { name: "Company comparison" })).toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "Natural-language financial search" })).not.toBeInTheDocument()
    expect(await screen.findAllByText("MSFT")).not.toHaveLength(0)
    await user.click(screen.getByRole("button", { name: "AI Analysis" }))
    await user.click(screen.getByRole("button", { name: "Analyze comparison" }))
    await waitFor(() => expect(analyzeComparisonMock).toHaveBeenCalledWith(["AAPL", "MSFT"], expect.any(AbortSignal)))
    expect(queryMock).toHaveBeenCalledWith(expect.objectContaining({ domain: "company", tickers: ["AAPL", "MSFT"], frequency: "quarterly" }), expect.any(AbortSignal))
  })
  it("searches a 50-company comparison catalog and explains the four-company limit", async () => {
    const user = userEvent.setup()
    const companies = Array.from({ length: 50 }, (_, index) => ({
      ticker: `C${String(index).padStart(2, "0")}`, cik: String(index),
      name: `Synthetic Company ${String(index).padStart(2, "0")}`,
      fiscal_year_end: null, support_status: "SUPPORTED",
    }))
    catalogMock.mockResolvedValue({
      ...catalog, companies,
      company_metric_support: companies.flatMap(({ ticker }) => ["annual", "quarterly"].map((frequency) => ({ ticker, metric: "REVENUE", frequency }))),
    })
    render(<MemoryRouter initialEntries={["/compare?companies=C00,C01,C02,C03"]}><App/></MemoryRouter>)
    const search = await screen.findByRole("combobox", { name: "Search comparison companies" })
    await screen.findByRole("button", { name: "Remove C03" })
    expect(screen.getByRole("status")).toHaveTextContent("Maximum 4 companies")
    await user.click(screen.getByRole("button", { name: "Remove C03" }))
    await user.type(search, "C04")
    await user.click(await screen.findByRole("option", { name: /C04/ }))
    expect(screen.getByRole("status")).toHaveTextContent("Maximum 4 companies")
  })
  it("loads the macro flow with provider metadata", async () => {
    render(<MemoryRouter initialEntries={["/macro"]}><App/></MemoryRouter>)
    expect(await screen.findByRole("heading", { name: "Macroeconomic research" })).toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "Natural-language financial search" })).not.toBeInTheDocument()
    expect(await screen.findByText("FRED")).toBeInTheDocument()
    expect(screen.getByText("Long-run economic series with source and transformation details.")).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "Macro series" })).toHaveTextContent("Inflation (YoY)")
    expect(queryMock).toHaveBeenCalledWith(expect.objectContaining({ domain: "macro", series: ["US_INFLATION_YOY"] }), expect.any(AbortSignal))
  })
  it("normalizes an unsupported company metric before querying and updates the URL", async () => {
    render(<MemoryRouter initialEntries={["/company/MSFT?metric=OPERATING_MARGIN&frequency=quarterly&range=all"]}><LocationControls/><App/></MemoryRouter>)
    expect(await screen.findByRole("heading", { name: "Microsoft Corporation" })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("metric=REVENUE&frequency=quarterly"))
    expect(queryMock).not.toHaveBeenCalledWith(expect.objectContaining({ tickers: ["MSFT"], metric: "OPERATING_MARGIN" }), expect.anything())
    expect(queryMock).toHaveBeenCalledWith(expect.objectContaining({ tickers: ["MSFT"], metric: "REVENUE", frequency: "quarterly" }), expect.any(AbortSignal))
  })
  it("keeps Compare synchronized with browser Back and Forward navigation", async () => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={["/compare?companies=AAPL,MSFT&metric=REVENUE&frequency=quarterly&range=all"]}><LocationControls/><App/></MemoryRouter>)
    expect(await screen.findByRole("heading", { name: "Company comparison" })).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "5Y" }))
    expect(screen.getByTestId("location")).toHaveTextContent("range=5y")
    await user.click(screen.getByRole("button", { name: "Back" }))
    expect(screen.getByTestId("location")).toHaveTextContent("range=all")
    await user.click(screen.getByRole("button", { name: "Forward" }))
    expect(screen.getByTestId("location")).toHaveTextContent("range=5y")
  })
  it("persists the Compare mode in the URL for refreshes", async () => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={["/compare?companies=AAPL,MSFT&metric=REVENUE&frequency=quarterly&range=2021&mode=performance"]}><LocationControls/><App/></MemoryRouter>)
    expect(await screen.findByRole("heading", { name: "Indexed stock performance" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "1Y" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "5Y" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("data-slot", "button")
    expect(screen.getByTestId("location")).toHaveTextContent("mode=performance")
    await user.click(screen.getByRole("button", { name: "Valuation" }))
    expect(screen.getByTestId("location")).toHaveTextContent("mode=valuation")
  })
})
