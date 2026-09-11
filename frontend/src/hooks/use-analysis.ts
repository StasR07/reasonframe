import { useEffect, useRef, useState } from "react"
import type { AnalysisError, AnalysisResponse, AnalystAssessment, CompanyAnalysisEvent, CompanyAnalysisSuccess, CompanyStage, CompanyStageState, OverviewOutput, SkepticReview } from "@/api/types"
import { cacheAnalysis, cachedAnalysis } from "@/lib/analysis-cache"

interface AnalysisState {
  key: string
  open: boolean
  result: AnalysisResponse | null
  loading: boolean
}

export function useAnalysis(requestKey: string, run: (signal: AbortSignal) => Promise<AnalysisResponse>) {
  const running = useRef(false)
  const controller = useRef<AbortController | null>(null)
  const initialOpen = () => typeof window !== "undefined" && window.innerWidth >= 1280 && sessionStorage.getItem("analysis-rail-collapsed") !== "true"
  const [stored, setStored] = useState<AnalysisState>(() => ({ key: requestKey, open: initialOpen(), result: cachedAnalysis(requestKey), loading: false }))
  const state: AnalysisState = stored.key === requestKey
    ? stored
    : { key: requestKey, open: initialOpen(), result: cachedAnalysis(requestKey), loading: false }
  const setOpen = (open: boolean) => {
    if (typeof sessionStorage !== "undefined") sessionStorage.setItem("analysis-rail-collapsed", String(!open))
    setStored({ ...state, open })
  }
  const analyze = () => {
    if (running.current || state.loading) return
    const cached = cachedAnalysis(requestKey)
    if (cached) { setStored({ key: requestKey, open: true, result: cached, loading: false }); return }
    running.current = true
    const abort = new AbortController()
    controller.current = abort
    setStored({ key: requestKey, open: true, result: null, loading: true })
    void run(abort.signal).then((next) => {
      if (abort.signal.aborted) return
      cacheAnalysis(requestKey, next)
      setStored({ key: requestKey, open: true, result: next, loading: false })
    }).catch((error) => { if (!abort.signal.aborted) setStored({
      key: requestKey, open: true, loading: false,
      result: { status: "error", error_code: "AI_UNAVAILABLE", message: error instanceof Error ? error.message : "Analysis is temporarily unavailable. Financial data remains available." },
    }) }).finally(() => { if (controller.current === abort) controller.current = null; running.current = false })
  }
  useEffect(() => () => controller.current?.abort(), [])
  useEffect(() => { controller.current?.abort(); running.current = false }, [requestKey])
  return { ...state, setOpen, analyze }
}

const initialStages = (): Record<CompanyStage, CompanyStageState> => ({ fundamentals: "Waiting", valuation: "Waiting", skeptic: "Waiting", overview: "Waiting" })

export function useCompanyAnalysis(requestKey: string, run: (onEvent: (event: CompanyAnalysisEvent) => void, signal: AbortSignal) => Promise<void>) {
  const controller = useRef<AbortController | null>(null)
  const running = useRef(false)
  const initialOpen = () => typeof window !== "undefined" && window.innerWidth >= 1280 && sessionStorage.getItem("analysis-rail-collapsed") !== "true"
  const cached = cachedAnalysis(requestKey)
  const [state, setState] = useState<{ key: string; open: boolean; result: AnalysisResponse | null; loading: boolean; stages: Record<CompanyStage, CompanyStageState> }>(() => ({
    key: requestKey, open: initialOpen(), result: cached, loading: false,
    stages: cached?.status === "success" && cached.mode === "company" ? { fundamentals: "Complete", valuation: "Complete", skeptic: "Complete", overview: "Complete" } : initialStages(),
  }))

  useEffect(() => () => controller.current?.abort(), [])
  useEffect(() => {
    if (state.key === requestKey) return
    controller.current?.abort(); running.current = false
    const next = cachedAnalysis(requestKey)
    setState({ key: requestKey, open: initialOpen(), result: next, loading: false, stages: next?.status === "success" && next.mode === "company" ? { fundamentals: "Complete", valuation: "Complete", skeptic: "Complete", overview: "Complete" } : initialStages() })
  }, [requestKey, state.key])

  const setOpen = (open: boolean) => {
    sessionStorage.setItem("analysis-rail-collapsed", String(!open))
    setState((current) => ({ ...current, open }))
  }
  const analyze = () => {
    if (running.current || state.loading) return
    const stored = cachedAnalysis(requestKey)
    if (stored) { setState((current) => ({ ...current, open: true, result: stored })); return }
    running.current = true
    const abort = new AbortController(); controller.current = abort
    let partial: Partial<CompanyAnalysisSuccess> = { status: "success", mode: "company", packet_version: 3, cache_key: "", ticker: "", fundamentals: null, valuation: null, skeptic: null, overview: null, failures: [], failure_details: {}, evidence_context: [] }
    setState((current) => ({ ...current, open: true, result: null, loading: true, stages: initialStages() }))
    const onEvent = (event: CompanyAnalysisEvent) => {
      if (abort.signal.aborted) return
      setState((current) => {
      if (current.key !== requestKey) return current
      const stages = { ...current.stages }
      if (event.stage && event.event === "stage_started") stages[event.stage] = "Running"
      if (event.stage && event.event === "stage_completed") {
        stages[event.stage] = "Complete"
        const evidence = [...(partial.evidence_context ?? []), ...(event.evidence_context ?? [])]
        const uniqueEvidence = [...new Map(evidence.map((item) => [item.id, item])).values()]
        partial = { ...partial, [event.stage]: event.result as AnalystAssessment | SkepticReview | OverviewOutput, evidence_context: uniqueEvidence }
      }
      if (event.stage && event.event === "stage_failed") stages[event.stage] = "Failed"
      if (event.stage && event.event === "stage_skipped") stages[event.stage] = event.stage === "fundamentals" || event.stage === "valuation" ? "Unavailable" : "Skipped"
      if (event.event === "analysis_completed") {
        const final = event.result as CompanyAnalysisSuccess
        cacheAnalysis(requestKey, final)
        return { ...current, result: final, loading: false, stages }
      }
      if (event.event === "analysis_failed") return { ...current, result: event.result as AnalysisError, loading: false, stages }
      return { ...current, result: partial.fundamentals || partial.valuation || partial.skeptic || partial.overview ? partial as CompanyAnalysisSuccess : current.result, stages }
      })
    }
    void run(onEvent, abort.signal).catch((error) => {
      if (abort.signal.aborted) return
      setState((current) => ({ ...current, loading: false, result: { status: "error", error_code: "AI_UNAVAILABLE", message: error instanceof Error ? error.message : "Analysis stream failed." } }))
    }).finally(() => { if (controller.current === abort) controller.current = null; running.current = false })
  }
  return { ...state, setOpen, analyze }
}
