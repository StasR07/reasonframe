import type { AIAccountStatus, AIConnectResult, AIModel, AnalysisResponse, AskResponse, Catalog, CompanyAnalysisEvent, DataSyncStatus, Evidence, QueryRequest, QueryResponse, RuntimeStatus } from "./types"

declare global { interface Window { __FINANCE_API_BASE__?: string; __FINANCE_BACKEND_ERROR__?: string | null } }
const apiBase = () => window.__FINANCE_API_BASE__ ?? import.meta.env.VITE_API_BASE_URL ?? ""

export class ApiError extends Error {
  readonly status?: number
  constructor(message: string, status?: number) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${apiBase()}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    })
  } catch {
    throw new ApiError("Could not reach the finance data service.")
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json() as { detail?: string | Array<{ msg: string }> }
      if (typeof body.detail === "string") detail = body.detail
      else if (Array.isArray(body.detail)) detail = body.detail.map((item) => item.msg).join(". ")
    } catch { /* keep safe fallback */ }
    throw new ApiError(detail, response.status)
  }
  try {
    return await response.json() as T
  } catch {
    throw new ApiError("The finance data service returned an invalid response.", response.status)
  }
}

export const api = {
  health: () => request<{ status: string }>("/health"),
  catalog: (signal?: AbortSignal) => request<Catalog>("/api/v1/catalog", { signal }),
  query: (query: QueryRequest, signal?: AbortSignal) => request<QueryResponse>("/api/v1/query", {
    method: "POST",
    body: JSON.stringify(query.domain === "valuation" ? query : { operation: "LEVEL", ...query }),
    signal,
  }),
  evidence: (id: string) => request<Evidence>(`/api/v1/evidence/${encodeURIComponent(id)}`),
  ask: (question: string, model?: string | null, signal?: AbortSignal) => request<AskResponse>("/api/v1/ask", {
    method: "POST",
    body: JSON.stringify({ question, ...(model ? { model } : {}) }),
    signal,
  }),
  aiStatus: () => request<AIAccountStatus>("/api/v1/ai/status"),
  aiProviders: () => request<AIAccountStatus[]>("/api/v1/ai/providers"),
  refreshAIProviders: () => request<AIAccountStatus[]>("/api/v1/ai/providers/refresh", { method: "POST" }),
  verifyAIProvider: () => request<{ ok: boolean }>("/api/v1/ai/verify", { method: "POST" }),
  selectAIProvider: (provider: string) => request<AIAccountStatus>("/api/v1/ai/provider", { method: "PUT", body: JSON.stringify({ provider }) }),
  aiConnect: () => request<AIConnectResult>("/api/v1/ai/connect", { method: "POST" }),
  aiDisconnect: () => request<AIAccountStatus>("/api/v1/ai/disconnect", { method: "POST" }),
  aiModels: () => request<AIModel[]>("/api/v1/ai/models"),
  runtime: () => request<RuntimeStatus>("/api/v1/runtime"),
  updateSettings: (settings: { automatic_refresh?: boolean; onboarding_completed?: boolean }) => request<{ automatic_refresh: boolean; onboarding_completed: boolean }>("/api/v1/settings", { method: "PATCH", body: JSON.stringify(settings) }),
  saveTiingo: (value: string) => request<{ configured: boolean }>("/api/v1/settings/tiingo", { method: "POST", body: JSON.stringify({ value }) }),
  removeTiingo: () => request<{ configured: boolean }>("/api/v1/settings/tiingo", { method: "DELETE" }),
  saveFred: (value: string) => request<{ configured: boolean }>("/api/v1/settings/fred", { method: "POST", body: JSON.stringify({ value }) }),
  removeFred: () => request<{ configured: boolean }>("/api/v1/settings/fred", { method: "DELETE" }),
  saveEdgarIdentity: (value: string) => request<{ configured: boolean }>("/api/v1/settings/edgar", { method: "POST", body: JSON.stringify({ value }) }),
  removeEdgarIdentity: () => request<{ configured: boolean }>("/api/v1/settings/edgar", { method: "DELETE" }),
  dataStatus: () => request<DataSyncStatus>("/api/v1/data/status"),
  bootstrapData: () => request<DataSyncStatus>("/api/v1/data/bootstrap", { method: "POST" }),
  refreshData: (automatic = false) => request<DataSyncStatus>("/api/v1/data/refresh", { method: "POST", body: JSON.stringify({ automatic }) }),
  analyzeResult: (query: QueryRequest, signal?: AbortSignal) => request<AnalysisResponse>("/api/v1/analyze/result", {
    method: "POST", body: JSON.stringify({ type: "focused", query }), signal,
  }),
  analyzeCompany: (ticker: string, startYear = 2019, endYear?: number) => request<AnalysisResponse>("/api/v1/analyze/company", {
    method: "POST", body: JSON.stringify({ type: "company", ticker, start_year: startYear, ...(endYear ? { end_year: endYear } : {}) }),
  }),
  analyzeComparison: (tickers: string[], signal?: AbortSignal) => request<AnalysisResponse>("/api/v1/analyze/comparison", {
    method: "POST", body: JSON.stringify({ type: "comparison", tickers }), signal,
  }),
  streamCompany: async (ticker: string, startYear: number, onEvent: (event: CompanyAnalysisEvent) => void, signal: AbortSignal) => {
    const response = await fetch(`${apiBase()}/api/v1/analyze/company/stream`, {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ type: "company", ticker, start_year: startYear }), signal,
    })
    if (!response.ok || !response.body) throw new ApiError(`Analysis stream failed (${response.status})`, response.status)
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n")
      let boundary = buffer.indexOf("\n\n")
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const data = frame.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n")
        if (data) onEvent(JSON.parse(data) as CompanyAnalysisEvent)
        boundary = buffer.indexOf("\n\n")
      }
      if (done) break
    }
  },
}
