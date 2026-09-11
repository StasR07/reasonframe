import { render, screen } from "@testing-library/react"
import { expect, it, vi } from "vitest"
import type { QueryRequest, QueryResponse } from "@/api/types"
import { sourceResponse } from "@/test/fixtures"

const { queryMock } = vi.hoisted(() => ({ queryMock: vi.fn() }))
vi.mock("@/api/client", () => ({ api: { query: queryMock } }))

import { useFinanceQuery } from "./use-api"

function Harness({ metric }: { metric: string }) {
  const result = useFinanceQuery({ domain: "company", tickers: ["AAPL"], metric, frequency: "annual" })
  return <div>{result.data?.series[0]?.metric ?? (result.loading ? "loading" : "empty")}</div>
}

it("does not let an older query overwrite a newer selection", async () => {
  const resolvers: Array<(value: QueryResponse) => void> = []
  const signals: AbortSignal[] = []
  queryMock.mockImplementation((_request: QueryRequest, signal: AbortSignal) => {
    signals.push(signal)
    return new Promise<QueryResponse>((resolve) => resolvers.push(resolve))
  })
  const { rerender } = render(<Harness metric="REVENUE"/>)
  rerender(<Harness metric="FREE_CASH_FLOW"/>)
  expect(signals[0]?.aborted).toBe(true)
  expect(screen.getByText("loading")).toBeInTheDocument()
  resolvers[1]({ ...sourceResponse, series: [{ ...sourceResponse.series[0], metric: "FREE_CASH_FLOW" }] })
  expect(await screen.findByText("FREE_CASH_FLOW")).toBeInTheDocument()
  resolvers[0]({ ...sourceResponse, series: [{ ...sourceResponse.series[0], metric: "REVENUE" }] })
  await Promise.resolve()
  expect(screen.getByText("FREE_CASH_FLOW")).toBeInTheDocument()
})
