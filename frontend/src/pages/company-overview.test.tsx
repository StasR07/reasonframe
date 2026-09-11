import { render, screen } from "@testing-library/react"
import { expect, it, vi } from "vitest"
import type { CompanyQuery } from "@/api/types"
import { sourceResponse } from "@/test/fixtures"

const { queryMock } = vi.hoisted(() => ({ queryMock: vi.fn() }))
vi.mock("@/api/client", () => ({ api: { query: queryMock } }))

import { Overview } from "./company-page"

it("keeps successful overview cards when one optional request fails", async () => {
  queryMock.mockImplementation(async (request: CompanyQuery) => {
    if (request.metric === "NET_INCOME") throw new Error("temporary failure")
    return { ...sourceResponse, series: [{ ...sourceResponse.series[0], metric: request.metric }] }
  })
  const supported = new Set(["REVENUE", "REVENUE_GROWTH_YOY", "OPERATING_MARGIN", "NET_INCOME", "FREE_CASH_FLOW"].map((metric) => `${metric}:annual`))
  render(<Overview ticker="AAPL" frequency="annual" supported={supported}/>)
  expect(await screen.findByText("Revenue growth yoy")).toBeInTheDocument()
  expect(screen.getByText("Net income")).toBeInTheDocument()
  expect(screen.getByText("Free cash flow")).toBeInTheDocument()
  expect(screen.getByText("Unavailable")).toBeInTheDocument()
  expect(screen.getAllByText("$416.2B")).toHaveLength(4)
  const revenueLabel = screen.getByTestId("kpi-label-REVENUE")
  const growthLabel = screen.getByTestId("kpi-label-REVENUE_GROWTH_YOY")
  const revenueValue = screen.getByTestId("kpi-value-REVENUE")
  const growthValue = screen.getByTestId("kpi-value-REVENUE_GROWTH_YOY")
  expect(revenueLabel).toHaveClass("min-h-8")
  expect(growthLabel).toHaveClass("min-h-8")
  expect(revenueValue.className).toBe(growthValue.className)
})
