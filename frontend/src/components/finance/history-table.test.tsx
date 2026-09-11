import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"
import { sourceResponse } from "@/test/fixtures"
import { HistoryTable } from "./history-table"

function history(count: number) {
  return [{ ...sourceResponse.series[0], observations: Array.from({ length: count }, (_, index) => ({
    ...sourceResponse.series[0].observations[0], date: `${2025 - index}-09-27`, fiscal_year: 2025 - index,
  })) }]
}

it("leaves short history open and collapses long history until requested", async () => {
  const user = userEvent.setup()
  const short = render(<HistoryTable series={history(2)} onEvidence={vi.fn()}/>)
  expect(screen.getByText("Data & evidence · 2 observations").closest("details")).toHaveAttribute("open")
  short.unmount()

  const onEvidence = vi.fn()
  render(<HistoryTable series={history(11)} onEvidence={onEvidence}/>)
  const disclosure = screen.getByText("Data & evidence · 11 observations").closest("details")!
  expect(disclosure).not.toHaveAttribute("open")
  await user.click(screen.getByText("Data & evidence · 11 observations"))
  expect(disclosure).toHaveAttribute("open")
  expect(screen.getAllByRole("row")).toHaveLength(12)
  await user.click(screen.getByRole("button", { name: "Inspect evidence for FY2025" }))
  expect(onEvidence).toHaveBeenCalledOnce()
})
