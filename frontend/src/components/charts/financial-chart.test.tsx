import { fireEvent, render, screen } from "@testing-library/react"
import { expect, it } from "vitest"
import { sourceResponse } from "@/test/fixtures"
import { chartPointLabels, chartRowKey, chartYAxisDomain, FinancialChart, permanentDotsVisible } from "./financial-chart"

const aapl = { ...sourceResponse.series[0], id: "AAPL:REVENUE", entity: "AAPL" }
const msft = { ...sourceResponse.series[0], id: "MSFT:REVENUE", entity: "MSFT" }

it("removes permanent dots for multi-series and dense charts", () => {
  expect(permanentDotsVisible([aapl, msft])).toBe(false)
  expect(permanentDotsVisible([{ ...aapl, observations: Array.from({ length: 17 }, (_, index) => ({ ...aapl.observations[0], date: `${2000 + index}-01-01` })) }])).toBe(false)
  expect(permanentDotsVisible([aapl])).toBe(true)
})

it("lets the legend hide and restore a series without changing the series universe", () => {
  render(<FinancialChart series={[aapl, msft]}/>)
  const hide = screen.getByRole("button", { name: "Hide AAPL" })
  fireEvent.click(hide)
  expect(screen.getByRole("button", { name: "Show AAPL" })).toHaveAttribute("aria-pressed", "false")
  fireEvent.click(screen.getByRole("button", { name: "Show AAPL" }))
  expect(screen.getByRole("button", { name: "Hide AAPL" })).toHaveAttribute("aria-pressed", "true")
})

it("uses the human series label for a single-series chart", () => {
  render(<FinancialChart series={[{ ...aapl, metric: "FREE_CASH_FLOW", label: "Free cash flow" }]}/>)
  expect(screen.queryByText("FREE_CASH_FLOW")).not.toBeInTheDocument()
})

it("keeps compact daily axis labels but shows exact trading dates in tooltips", () => {
  const first = { fiscal_year: null, fiscal_period: null, date: "2025-09-02" }
  const second = { ...first, date: "2025-09-29" }
  expect(chartPointLabels(first, "daily")).toEqual({ axis: "Sep 2025", tooltip: "Sep 2, 2025" })
  expect(chartPointLabels(second, "daily")).toEqual({ axis: "Sep 2025", tooltip: "Sep 29, 2025" })
  expect(chartRowKey(first)).toBe("2025-09-02")
  expect(chartRowKey(second)).toBe("2025-09-29")
  expect(chartPointLabels({ fiscal_year: 2025, fiscal_period: "FY", date: "2025-09-27" }, "annual"))
    .toEqual({ axis: "FY2025", tooltip: "FY2025" })
})

it("uses semantic domains and shows markers for sparse quarterly observations", () => {
  const price = { ...aapl, metric: "RAW_CLOSE", frequency: "daily", observations: [
    { ...aapl.observations[0], value: "100", date: "2025-09-02" },
    { ...aapl.observations[0], value: "110", date: "2025-09-03" },
  ] }
  expect(chartYAxisDomain([price])[0]).toBeGreaterThan(0)
  expect(permanentDotsVisible([{ ...aapl, frequency: "quarterly" }, { ...msft, frequency: "quarterly" }])).toBe(true)
})
