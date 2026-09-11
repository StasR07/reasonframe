import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"
import { sourceResponse } from "@/test/fixtures"
import { ComparisonTable, comparisonRows } from "./comparison-table"

const aapl = { ...sourceResponse.series[0], id: "AAPL:REVENUE", entity: "AAPL" }
const msft = { ...sourceResponse.series[0], id: "MSFT:REVENUE", entity: "MSFT", observations: [{ ...sourceResponse.series[0].observations[0], fiscal_year: 2024, date: "2024-06-30" }] }

it("aligns comparison rows by fiscal-period key and preserves missing cells and evidence", () => {
  const rows = comparisonRows([aapl, msft])
  expect(rows).toHaveLength(2)
  expect(rows.find((row) => row.key === "2025-FY")?.points.has(msft.id)).toBe(false)
  const onEvidence = vi.fn()
  render(<ComparisonTable series={[aapl, msft]} onEvidence={onEvidence}/>)
  expect(screen.getByRole("columnheader", { name: "AAPL" })).toBeInTheDocument()
  expect(screen.getByRole("columnheader", { name: "MSFT" })).toBeInTheDocument()
  expect(screen.getByLabelText("No data for MSFT FY2025")).toHaveTextContent("—")
  fireEvent.click(screen.getByRole("button", { name: "Inspect evidence for AAPL FY2025" }))
  expect(onEvidence).toHaveBeenCalledWith(expect.objectContaining({ series: aapl }))
})

it("collapses long comparison evidence by default and reveals every exact row", async () => {
  const user = userEvent.setup()
  const observations = Array.from({ length: 6 }, (_, index) => ({
    ...sourceResponse.series[0].observations[0],
    date: `${2025 - index}-09-27`, fiscal_year: 2025 - index,
  }))
  const longAapl = { ...aapl, observations }
  const longMsft = { ...msft, observations: observations.map((point) => ({ ...point, date: point.date.replace("09-27", "06-30") })) }
  const onEvidence = vi.fn()
  render(<ComparisonTable series={[longAapl, longMsft]} onEvidence={onEvidence}/>)
  const summary = screen.getByText("Data & evidence · 12 observations")
  const disclosure = summary.closest("details")!
  expect(disclosure).not.toHaveAttribute("open")
  await user.click(summary)
  expect(disclosure).toHaveAttribute("open")
  expect(screen.getByRole("columnheader", { name: "AAPL" })).toBeInTheDocument()
  expect(screen.getAllByRole("row")).toHaveLength(7)
  await user.click(screen.getByRole("button", { name: "Inspect evidence for AAPL FY2025" }))
  expect(onEvidence).toHaveBeenCalledWith(expect.objectContaining({ series: longAapl }))
})
