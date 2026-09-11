import type { AskSuccessResponse } from "@/api/types"

const KEY = "finance-terminal:recent-searches:v2"
const LIMIT = 5
const BYTE_LIMIT = 1_500_000

export interface CachedSearch {
  question: string
  parser_model: string
  data_revision: string
  ask_response: AskSuccessResponse
  timestamp: number
}

export function recentSearches(): CachedSearch[] {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(KEY) ?? "[]") as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item): item is CachedSearch => Boolean(
      item && typeof item === "object" && typeof item.question === "string"
      && typeof item.parser_model === "string" && typeof item.data_revision === "string"
      && typeof item.timestamp === "number" && item.ask_response?.status === "success",
    )).slice(0, LIMIT)
  } catch { return [] }
}

const normalized = (question: string) => question.trim().replace(/\s+/g, " ").toLocaleLowerCase()

export function cachedSearch(question: string, parserModel = "default", dataRevision = "0") {
  return recentSearches().find((item) => normalized(item.question) === normalized(question)
    && item.parser_model === parserModel && item.data_revision === dataRevision)?.ask_response ?? null
}

export function cacheSearch(question: string, response: AskSuccessResponse, parserModel = "default", dataRevision = "0") {
  const entries = recentSearches().filter((item) => !(normalized(item.question) === normalized(question)
    && item.parser_model === parserModel && item.data_revision === dataRevision))
  entries.unshift({ question, parser_model: parserModel, data_revision: dataRevision, ask_response: response, timestamp: Date.now() })
  const retained: CachedSearch[] = []
  for (const entry of entries.slice(0, LIMIT)) {
    const candidate = [...retained, entry]
    if (JSON.stringify(candidate).length > BYTE_LIMIT) break
    retained.push(entry)
  }
  try { sessionStorage.setItem(KEY, JSON.stringify(retained)) } catch { /* cache is optional */ }
}
