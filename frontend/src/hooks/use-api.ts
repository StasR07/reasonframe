import { useCallback, useEffect, useRef, useState } from "react"
import { api } from "@/api/client"
import type { Catalog, QueryRequest, QueryResponse } from "@/api/types"

let catalogCache: Catalog | null = null
let catalogRequest: Promise<Catalog> | null = null

export function resetCatalogCacheForTests() {
  catalogCache = null
  catalogRequest = null
}

export function useCatalog() {
  const [data, setData] = useState<Catalog | null>(catalogCache)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(!catalogCache)
  const load = useCallback(async () => {
    setLoading(!catalogCache); setError(null)
    try {
      catalogRequest ??= api.catalog().finally(() => { catalogRequest = null })
      catalogCache = await catalogRequest
      setData(catalogCache)
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load the catalog.") }
    finally { setLoading(false) }
  }, [])
  useEffect(() => {
    void load()
    const refreshOnFocus = () => { void load() }
    window.addEventListener("focus", refreshOnFocus)
    return () => window.removeEventListener("focus", refreshOnFocus)
  }, [load])
  return { data, error, loading, retry: load }
}

export function useFinanceQuery(query: QueryRequest | null) {
  const [data, setData] = useState<QueryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [retryToken, setRetryToken] = useState(0)
  const generation = useRef(0)
  const queryKey = JSON.stringify(query)
  useEffect(() => {
    const currentQuery = JSON.parse(queryKey) as QueryRequest | null
    if (!currentQuery) { generation.current += 1; setData(null); setError(null); setLoading(false); return }
    const requestGeneration = ++generation.current
    const controller = new AbortController()
    setData(null)
    setLoading(true); setError(null)
    void api.query(currentQuery, controller.signal).then((response) => {
      if (requestGeneration === generation.current) { setData(response); setLoading(false) }
    }).catch((reason) => {
      if (requestGeneration === generation.current) {
        setData(null)
        setError(reason instanceof Error ? reason.message : "Could not load data.")
        setLoading(false)
      }
    })
    return () => { controller.abort(); generation.current += 1 }
  }, [queryKey, retryToken])
  const retry = useCallback(() => setRetryToken((current) => current + 1), [])
  return { data, error, loading, retry }
}
