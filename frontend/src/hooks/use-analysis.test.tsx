import { act, renderHook } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import type { CompanyAnalysisEvent } from "@/api/types"
import { useCompanyAnalysis } from "./use-analysis"

describe("company analysis request isolation", () => {
  it("ignores a late event from the previously selected company", async () => {
    let emit: ((event: CompanyAnalysisEvent) => void) | undefined
    const run = (onEvent: (event: CompanyAnalysisEvent) => void, signal: AbortSignal) => {
      emit = onEvent
      return new Promise<void>((resolve) => signal.addEventListener("abort", () => resolve(), { once: true }))
    }
    const hook = renderHook(({ requestKey }) => useCompanyAnalysis(requestKey, run), {
      initialProps: { requestKey: "company:AAPL" },
    })
    act(() => hook.result.current.analyze())
    expect(hook.result.current.loading).toBe(true)
    await act(async () => hook.rerender({ requestKey: "company:MSFT" }))
    act(() => emit?.({
      event: "stage_completed", stage: "fundamentals", message: null,
      result: { stance: "POSITIVE", confidence: "HIGH", thesis: { text: "Apple only", evidence_refs: ["sec:aapl"] }, supporting_claims: [], uncertainties: [] },
      evidence_context: [],
    }))
    expect(hook.result.current.key).toBe("company:MSFT")
    expect(hook.result.current.result).toBeNull()
  })
})
