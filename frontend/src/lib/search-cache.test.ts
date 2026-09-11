import { describe, expect, it } from "vitest"
import type { AskSuccessResponse } from "@/api/types"
import { sourceResponse } from "@/test/fixtures"
import { cacheSearch, cachedSearch, recentSearches } from "./search-cache"

function response(question: string): AskSuccessResponse {
  return { status: "success", question, interpretation: { domain: "company", entities: ["AAPL"], metric: "Revenue", frequency: "Annual", range: "FY2025", operation: "LEVEL" }, validated_query: { domain: "company", tickers: ["AAPL"], metric: "REVENUE", frequency: "annual" }, results: sourceResponse, factual_summary: null }
}

describe("session search cache", () => {
  it("stores successful results, restores by question, and stays bounded", () => {
    for (let index = 0; index < 7; index += 1) cacheSearch(`question ${index}`, response(`question ${index}`))
    expect(recentSearches()).toHaveLength(5)
    expect(recentSearches()[0]?.question).toBe("question 6")
    expect(cachedSearch("question 6")?.question).toBe("question 6")
    expect(cachedSearch("question 0")).toBeNull()
  })
  it("separates parser models and data revisions", () => {
    cacheSearch(" Show  Apple revenue ", response("terra"), "terra", "7")
    cacheSearch("show apple revenue", response("luna"), "luna", "7")
    expect(cachedSearch("show apple   revenue", "terra", "7")?.question).toBe("terra")
    expect(cachedSearch("show apple revenue", "luna", "7")?.question).toBe("luna")
    expect(cachedSearch("show apple revenue", "terra", "8")).toBeNull()
  })
  it("does not let several large daily histories exhaust session storage", () => {
    const large = response("large")
    large.results = { ...sourceResponse, series: [{ ...sourceResponse.series[0], observations: Array.from(
      { length: 3_000 }, (_, index) => ({ ...sourceResponse.series[0].observations[0], date: `2020-01-${String(index % 28 + 1).padStart(2, "0")}`, observation_id: `market:${index}` }),
    ) }] }
    for (let index = 0; index < 5; index += 1) cacheSearch(`large ${index}`, { ...large, question: `large ${index}` })
    expect((sessionStorage.getItem("finance-terminal:recent-searches:v2") ?? "").length).toBeLessThanOrEqual(1_500_000)
  })
})
