import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"
import type { Catalog, MetricDefinition } from "@/api/types"
import { CompanyAutocomplete, CompanySelector, MetricSelector } from "./selectors"

const companies = Array.from({ length: 50 }, (_, index) => ({
  ticker: `C${String(index).padStart(2, "0")}`,
  cik: String(index),
  name: `Synthetic Company ${String(index).padStart(2, "0")}`,
  fiscal_year_end: null,
  support_status: "SUPPORTED",
}))

it("keeps the full selector independent while autocomplete matches names and keyboard-selects aliases", async () => {
  const user = userEvent.setup()
  const onSelect = vi.fn()
  const catalog = { companies, company_aliases: { "Forty Nine": "C49" } } as unknown as Catalog
  render(<><CompanyAutocomplete catalog={catalog} label="Find company" onSelect={onSelect}/><CompanySelector catalog={catalog} value="C00" onChange={vi.fn()}/></>)
  const search = screen.getByRole("combobox", { name: "Find company" })
  await user.type(search, "Forty Nine")
  expect(screen.getByRole("option", { name: /C49/ })).toBeInTheDocument()
  await user.keyboard("{ArrowDown}{Enter}")
  expect(onSelect).toHaveBeenCalledWith("C49")
  await user.click(screen.getByRole("combobox", { name: "Company" }))
  expect(screen.getByText("C49")).toBeInTheDocument()
  expect(screen.getByText("C01")).toBeInTheDocument()
})

it("renders every grouped metric in the shared styled selector and supports keyboard selection", async () => {
  const user = userEvent.setup()
  const onChange = vi.fn()
  const metrics = [
    { code: "REVENUE", label: "Revenue", category: "income", unit_kind: "currency", kind: "direct" },
    { code: "FREE_CASH_FLOW", label: "Free cash flow", category: "cash_flow", unit_kind: "currency", kind: "derived" },
    { code: "GROSS_MARGIN", label: "Gross margin", category: "profitability", unit_kind: "ratio", kind: "derived" },
    { code: "TOTAL_ASSETS", label: "Total assets", category: "balance", unit_kind: "currency", kind: "direct" },
  ] as MetricDefinition[]
  const { rerender } = render(<MetricSelector metrics={metrics} value="REVENUE" onChange={onChange}/>)

  const trigger = screen.getByRole("combobox", { name: "Metric" })
  expect(trigger).toHaveTextContent("Revenue")
  expect(document.querySelector("select")).not.toBeInTheDocument()
  await user.click(trigger)
  expect(screen.getByText("Income statement")).toBeInTheDocument()
  expect(screen.getByText("Cash flow")).toBeInTheDocument()
  expect(screen.getByText("Profitability")).toBeInTheDocument()
  expect(screen.getByText("Balance sheet")).toBeInTheDocument()
  expect(screen.getAllByRole("option", { hidden: true })).toHaveLength(metrics.length)
  await user.keyboard("{ArrowDown}{Enter}")
  expect(onChange).toHaveBeenCalledWith("FREE_CASH_FLOW")

  await user.click(trigger)
  await user.keyboard("{Escape}")
  expect(trigger).toHaveAttribute("aria-expanded", "false")

  rerender(<MetricSelector metrics={[]} value="REVENUE" onChange={onChange}/>)
  expect(screen.getByRole("combobox", { name: "Metric" })).toBeDisabled()
  expect(screen.getByText("Financial metrics unavailable")).toBeInTheDocument()
})
