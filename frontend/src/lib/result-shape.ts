import type { AskSuccessResponse } from "@/api/types"

export type ResultShape = "SINGLE_SCALAR" | "AGGREGATE_SCALAR" | "SINGLE_PERIOD_COMPARISON" | "TIME_SERIES" | "MULTI_SERIES_TIME_SERIES"

export function classifyResult(response: AskSuccessResponse): ResultShape {
  const counts = response.results.series.map((series) => series.observations.length)
  const total = counts.reduce((sum, count) => sum + count, 0)
  if ("operation" in response.validated_query && response.validated_query.operation === "AVERAGE" && total === 1) return "AGGREGATE_SCALAR"
  if (counts.length === 1 && total === 1) return "SINGLE_SCALAR"
  if (counts.length > 1 && counts.every((count) => count === 1)) return "SINGLE_PERIOD_COMPARISON"
  if (counts.length === 1) return "TIME_SERIES"
  return "MULTI_SERIES_TIME_SERIES"
}
