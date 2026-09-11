import type { AnalysisResponse } from "@/api/types"

const PREFIX = "finance-terminal:analysis:v3:"
const ALIAS_PREFIX = `${PREFIX}alias:`
const INDEX_KEY = `${PREFIX}index`
const ENTRY_LIMIT = 8
const BYTE_LIMIT = 2_000_000

interface CacheEntry { requestKey: string; evidenceKey: string; timestamp: number; bytes: number }

function cacheIndex(): CacheEntry[] {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(INDEX_KEY) ?? "[]") as unknown
    return Array.isArray(parsed) ? parsed.filter((item): item is CacheEntry => Boolean(
      item && typeof item === "object" && typeof item.requestKey === "string"
      && typeof item.evidenceKey === "string" && typeof item.timestamp === "number" && typeof item.bytes === "number",
    )) : []
  } catch { return [] }
}

export function analysisRequestKey(context: object): string {
  return PREFIX + JSON.stringify(context)
}

export function cachedAnalysis(key: string): AnalysisResponse | null {
  try {
    const storageKey = sessionStorage.getItem(ALIAS_PREFIX + key) ?? key
    const parsed = JSON.parse(sessionStorage.getItem(storageKey) ?? "null") as AnalysisResponse | null
    if (parsed?.status !== "success") return null
    if (parsed.mode === "company" && parsed.failures.length > 0) return null
    return parsed
  } catch { return null }
}

export function cacheAnalysis(key: string, response: AnalysisResponse) {
  if (response.status !== "success") return
  if (response.mode === "company" && response.failures.length > 0) return
  const evidenceKey = PREFIX + response.cache_key
  const serialized = JSON.stringify(response)
  if (serialized.length > BYTE_LIMIT) return
  try {
    const previous = cacheIndex()
    const candidates = [
      { requestKey: key, evidenceKey, timestamp: Date.now(), bytes: serialized.length },
      ...previous.filter((item) => item.requestKey !== key),
    ]
    const retained: CacheEntry[] = []
    let bytes = 0
    for (const item of candidates) {
      if (retained.length >= ENTRY_LIMIT || bytes + item.bytes > BYTE_LIMIT) continue
      retained.push(item); bytes += item.bytes
    }
    const retainedRequests = new Set(retained.map((item) => item.requestKey))
    const retainedEvidence = new Set(retained.map((item) => item.evidenceKey))
    for (const item of previous) {
      if (!retainedRequests.has(item.requestKey)) sessionStorage.removeItem(ALIAS_PREFIX + item.requestKey)
      if (!retainedEvidence.has(item.evidenceKey)) sessionStorage.removeItem(item.evidenceKey)
    }
    if (!retainedRequests.has(key)) return
    sessionStorage.setItem(evidenceKey, serialized)
    sessionStorage.setItem(ALIAS_PREFIX + key, evidenceKey)
    sessionStorage.setItem(INDEX_KEY, JSON.stringify(retained))
  } catch { /* session caching is best effort */ }
}
