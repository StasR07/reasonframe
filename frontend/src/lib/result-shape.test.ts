import { describe, expect, it } from "vitest"
import type { AskSuccessResponse } from "@/api/types"
import { calculatedResponse, sourceResponse } from "@/test/fixtures"
import { classifyResult } from "./result-shape"

function response(series = sourceResponse.series, operation: "LEVEL" | "AVERAGE" = "LEVEL"): AskSuccessResponse {
  return {
    status: "success", question: "q",
    interpretation: { domain: "company", entities: ["AAPL"], metric: "Revenue", frequency: "Annual", range: "FY2025", operation },
    validated_query: { domain: "company", tickers: ["AAPL"], metric: "REVENUE", frequency: "annual", operation },
    results: { status: "SUCCESS", errors: [], series }, factual_summary: null,
  }
}

describe("search result shape", () => {
  it("classifies scalar and aggregate scalar", () => {
    expect(classifyResult(response())).toBe("SINGLE_SCALAR")
    expect(classifyResult(response(sourceResponse.series, "AVERAGE"))).toBe("AGGREGATE_SCALAR")
  })

  it("classifies comparison and time-series shapes", () => {
    const second = { ...sourceResponse.series[0], id: "MSFT:REVENUE", entity: "MSFT" }
    expect(classifyResult(response([sourceResponse.series[0], second]))).toBe("SINGLE_PERIOD_COMPARISON")
    const observations = [sourceResponse.series[0].observations[0], { ...sourceResponse.series[0].observations[0], date: "2024-09-28", fiscal_year: 2024 }]
    const timeSeries = { ...sourceResponse.series[0], observations }
    expect(classifyResult(response([timeSeries]))).toBe("TIME_SERIES")
    expect(classifyResult(response([timeSeries, { ...calculatedResponse.series[0], observations: calculatedResponse.series[0].observations.concat({ ...calculatedResponse.series[0].observations[0], date: "2024-09-28", fiscal_year: 2024 }) }]))).toBe("MULTI_SERIES_TIME_SERIES")
  })
})
