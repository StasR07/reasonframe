import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { MarketSupport } from "@/api/types"
import { marketRangeCovered } from "@/lib/market-coverage"
import { retryFailedValuations, shouldRenderValuationChart } from "./market-valuation"

describe("market range coverage", () => {
  beforeEach(() => vi.setSystemTime(new Date("2026-08-31T12:00:00Z")))
  afterEach(() => vi.useRealTimers())

  const support = (first_date: string): MarketSupport => ({
    ticker: "AAPL", instrument_id: "us-xnas-aapl", first_date,
    last_date: "2026-08-28", observation_count: 250,
  })

  it("enables only ranges whose start boundary is locally covered", () => {
    expect(marketRangeCovered(support("2025-08-29"), 1)).toBe(true)
    expect(marketRangeCovered(support("2025-08-29"), 3)).toBe(false)
    expect(marketRangeCovered(support("2021-08-30"), 5)).toBe(true)
  })
})

it("retries every failed valuation request in one action", () => {
  const failedOne = vi.fn(); const failedTwo = vi.fn(); const successful = vi.fn()
  retryFailedValuations([
    { error: "failed", retry: failedOne }, { error: null, retry: successful }, { error: "failed", retry: failedTwo },
  ])
  expect(failedOne).toHaveBeenCalledOnce()
  expect(failedTwo).toHaveBeenCalledOnce()
  expect(successful).not.toHaveBeenCalled()
})

it("requires two observations for a historical valuation trend chart", () => {
  expect(shouldRenderValuationChart(0)).toBe(false)
  expect(shouldRenderValuationChart(1)).toBe(false)
  expect(shouldRenderValuationChart(2)).toBe(true)
})
