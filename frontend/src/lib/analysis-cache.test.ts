import { beforeEach, describe, expect, it } from "vitest"
import type { AnalysisResponse } from "@/api/types"
import { analysisRequestKey, cacheAnalysis, cachedAnalysis } from "./analysis-cache"

const success: AnalysisResponse = {
  status: "success", mode: "focused", packet_version: 1, cache_key: "server-key",
  interpretation: { observations: [{ text: "Grounded", evidence_refs: ["e1"] }], caveat: null },
  evidence_context: [],
}

describe("analysis session cache", () => {
  beforeEach(() => sessionStorage.clear())
  it("reuses the same exact context and separates changed evidence context", () => {
    const first = analysisRequestKey({ mode: "focused", ticker: "AAPL", metric: "REVENUE", start: 2022, version: 1 })
    const same = analysisRequestKey({ mode: "focused", ticker: "AAPL", metric: "REVENUE", start: 2022, version: 1 })
    const changed = analysisRequestKey({ mode: "focused", ticker: "AAPL", metric: "REVENUE", start: 2023, version: 1 })
    cacheAnalysis(first, success)
    expect(cachedAnalysis(same)).toEqual(success)
    expect(cachedAnalysis(changed)).toBeNull()
  })

  it("misses company and comparison analysis after a data revision", () => {
    for (const mode of ["company", "comparison"] as const) {
      const first = analysisRequestKey({ mode, tickers: ["AAPL", "MSFT"], data_revision: "1", version: 3 })
      const changed = analysisRequestKey({ mode, tickers: ["AAPL", "MSFT"], data_revision: "2", version: 3 })
      cacheAnalysis(first, success)
      expect(cachedAnalysis(changed)).toBeNull()
    }
  })

  it("does not cache failures", () => {
    const key = analysisRequestKey({ mode: "company", ticker: "AAPL" })
    cacheAnalysis(key, { status: "error", error_code: "AI_TIMEOUT", message: "timeout" })
    expect(cachedAnalysis(key)).toBeNull()
  })

  it("does not cache incomplete company analysis", () => {
    const key = analysisRequestKey({ mode: "company", ticker: "AAPL" })
    cacheAnalysis(key, {
      status: "success", mode: "company", packet_version: 3, cache_key: "partial", ticker: "AAPL",
      fundamentals: null, valuation: null, skeptic: null, overview: null,
      failures: ["valuation", "skeptic"], failure_details: {}, evidence_context: [],
    })
    expect(cachedAnalysis(key)).toBeNull()
  })

  it("evicts old analyses after the entry bound", () => {
    for (let index = 0; index < 10; index += 1) {
      cacheAnalysis(analysisRequestKey({ index }), { ...success, cache_key: `server-${index}` })
    }
    expect(cachedAnalysis(analysisRequestKey({ index: 0 }))).toBeNull()
    expect(cachedAnalysis(analysisRequestKey({ index: 9 }))).toEqual({ ...success, cache_key: "server-9" })
  })
})
