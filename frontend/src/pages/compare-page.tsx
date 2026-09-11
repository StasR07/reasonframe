import { useEffect, useMemo, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { api } from "@/api/client"
import type { Frequency, MarketQuery } from "@/api/types"
import { FinancialChart } from "@/components/charts/financial-chart"
import { ComparisonTable } from "@/components/finance/comparison-table"
import { ChartSkeleton, NetworkError, ResultState } from "@/components/finance/data-state"
import { EvidenceSheet, type EvidenceSelection } from "@/components/finance/evidence-sheet"
import { AnalystPanel } from "@/components/finance/analyst-panel"
import { StockPerformanceCompare, ValuationCompare } from "@/components/finance/market-compare"
import { useAnalysis } from "@/hooks/use-analysis"
import { CompanyAutocomplete, FrequencyToggle, MetricSelector } from "@/components/finance/selectors"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useCatalog, useFinanceQuery } from "@/hooks/use-api"
import { normalizeCompanySelection, supportedFrequencies, supportedMetrics } from "@/lib/capabilities"
import { analysisRequestKey } from "@/lib/analysis-cache"
import { getMetricLabel } from "@/lib/metric-label"

export function ComparePage() {
  const catalog = useCatalog()
  const [params, setParams] = useSearchParams()
  const requestedCompanies = params.get("companies") ?? "AAPL,MSFT"
  const requestedTickers = useMemo(() => requestedCompanies.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean), [requestedCompanies])
  const requestedMetric = params.get("metric") ?? "REVENUE"
  const requestedFrequency: Frequency = params.get("frequency") === "annual" ? "annual" : "quarterly"
  const range = params.get("range") ?? "5y"
  const [evidence, setEvidence] = useState<EvidenceSelection | null>(null)
  const [limitMessage, setLimitMessage] = useState("")
  const [, setPerformanceQuery] = useState<MarketQuery | null>(null)
  const requestedMode = params.get("mode")
  const mode: "fundamentals" | "performance" | "valuation" = requestedMode === "performance" || requestedMode === "valuation" ? requestedMode : "fundamentals"
  const tickers = useMemo(() => {
    if (!catalog.data) return [...new Set(requestedTickers)].slice(0, 4)
    const known = new Set(catalog.data.companies.map((item) => item.ticker))
    const valid = [...new Set(requestedTickers)].filter((item) => known.has(item)).slice(0, 4)
    return valid.length >= 2 ? valid : catalog.data.companies.slice(0, 2).map((item) => item.ticker)
  }, [catalog.data, requestedTickers])
  const tickerKey = tickers.join(",")
  useEffect(() => setEvidence(null), [tickerKey, mode])
  const normalized = useMemo(() => catalog.data ? normalizeCompanySelection(catalog.data, tickers, requestedMetric, requestedFrequency) : { metric: requestedMetric, frequency: requestedFrequency }, [catalog.data, tickers, requestedMetric, requestedFrequency])
  const { metric, frequency } = normalized
  const metrics = useMemo(() => catalog.data ? supportedMetrics(catalog.data, tickers) : [], [catalog.data, tickers])
  const frequencies = useMemo(() => catalog.data ? supportedFrequencies(catalog.data, tickers, metric) : [], [catalog.data, tickers, metric])

  useEffect(() => {
    if (!catalog.data) return
    const canonical = { companies: tickers.join(","), metric, frequency, range, mode }
    if (params.get("companies") !== canonical.companies || params.get("metric") !== metric || params.get("frequency") !== frequency || params.get("range") !== range || params.get("mode") !== mode) setParams(canonical, { replace: true })
  }, [catalog.data, tickers, tickerKey, metric, frequency, range, mode, params, setParams])

  const startYear = range === "5y" ? new Date().getFullYear() - 5 : undefined
  const analysis = useAnalysis(analysisRequestKey({ mode: "comparison", tickers: [...tickers].sort(), data_revision: catalog.data?.data_revision ?? "loading", version: 3 }), (signal) => api.analyzeComparison(tickers, signal))
  const query = useFinanceQuery(catalog.data && tickers.length >= 2 && frequencies.includes(frequency) ? { domain: "company", tickers, metric, frequency, ...(startYear ? { start_year: startYear } : {}) } : null)
  if (catalog.loading) return <ChartSkeleton/>
  if (catalog.error || !catalog.data) return <NetworkError message={catalog.error ?? "Catalog unavailable."} retry={catalog.retry}/>
  const metricLabel = getMetricLabel(metric, catalog.data)

  const updateSelection = (nextTickers: string[], nextMetric = metric, nextFrequency = frequency, nextRange = range) => {
    const selection = normalizeCompanySelection(catalog.data!, nextTickers, nextMetric, nextFrequency)
    setParams({ companies: nextTickers.join(","), ...selection, range: nextRange, mode })
  }
  const addTicker = (ticker: string) => {
    if (tickers.length >= 4) {
      setLimitMessage("Compare supports up to 4 companies. Remove one before adding another.")
      return
    }
    setLimitMessage(""); updateSelection([...tickers, ticker])
  }
  const removeTicker = (ticker: string) => {
    if (tickers.length <= 2) { setLimitMessage("Select at least 2 companies to compare."); return }
    setLimitMessage(""); updateSelection(tickers.filter((item) => item !== ticker))
  }
  return <div className="flex items-start gap-6"><div className="min-w-0 flex-1 space-y-7"><header><Badge variant="secondary" className="mb-3">2–4 companies</Badge><h1 className="text-3xl font-semibold tracking-tight">Company comparison</h1><p className="mt-1 text-sm text-muted-foreground">Compare fundamentals, indexed stock performance, or valuation.</p></header>
    <section className="rounded-xl border bg-background p-4 sm:p-6"><div className="space-y-4"><CompanyAutocomplete catalog={catalog.data} excluded={tickers} onSelect={addTicker} label="Search comparison companies"/><div className="flex flex-wrap gap-2" aria-label="Selected companies">{tickers.map((ticker) => { const selected = catalog.data!.companies.find((item) => item.ticker === ticker); return <span key={ticker} className="inline-flex min-w-0 items-center gap-2 rounded-md border bg-muted/60 px-2.5 py-1.5 text-sm shadow-xs"><strong className="tracking-wide">{ticker}</strong><span className="max-w-48 truncate text-muted-foreground">{selected?.name}</span><button type="button" aria-label={`Remove ${ticker}`} onClick={() => removeTicker(ticker)} className="ml-0.5 rounded-sm px-0.5 text-base leading-none text-muted-foreground hover:bg-muted hover:text-foreground">×</button></span> })}</div>{tickers.length >= 4 && <p role="status" className="text-sm text-muted-foreground">Maximum 4 companies selected. Remove one to add another.</p>}{limitMessage && <p role="status" className="text-sm text-muted-foreground">{limitMessage}</p>}</div>
      <div className="-mx-4 mt-5 mb-6 flex flex-wrap gap-1 border-y bg-muted/20 px-4 py-2 sm:-mx-6 sm:px-6" aria-label="Comparison mode">{[["fundamentals", "Fundamentals"], ["performance", "Stock performance"], ["valuation", "Valuation"]].map(([value, label]) => <Button key={value} size="sm" variant={mode === value ? "secondary" : "ghost"} className="px-3" onClick={() => setParams({ companies: tickers.join(","), metric, frequency, range, mode: value })}>{label}</Button>)}</div>
      {mode === "fundamentals" && <><div className="mb-6 flex flex-col justify-between gap-3 lg:flex-row lg:items-center"><div className="flex flex-col gap-2 sm:flex-row"><MetricSelector metrics={metrics} value={metric} onChange={(next) => updateSelection(tickers, next)}/><FrequencyToggle value={frequency} supported={frequencies} onChange={(next) => updateSelection(tickers, metric, next)}/></div><div className="flex gap-1" aria-label="Date range">{[["5y", "5Y"], ["all", "All"]].map(([value, label]) => <Button key={value} size="sm" variant={range === value ? "secondary" : "ghost"} onClick={() => updateSelection(tickers, metric, frequency, value)}>{label}</Button>)}</div></div>
      {query.loading ? <ChartSkeleton/> : query.error ? <NetworkError message={query.error} retry={query.retry}/> : query.data ? <><ResultState response={query.data}/>{query.data.status === "SUCCESS" && <div className="space-y-6"><h2 className="text-base font-semibold">{metricLabel}</h2><FinancialChart series={query.data.series} onPointClick={(series, index) => setEvidence({ series, point: series.observations[index] })}/><p className="text-xs text-muted-foreground">Fiscal quarters are issuer-specific and may not represent identical calendar periods. Missing or unsupported quarters are intentionally left blank.</p><ComparisonTable series={query.data.series} onEvidence={setEvidence}/></div>}</> : null}</>}
      {mode === "performance" && <StockPerformanceCompare tickers={tickers} marketSupport={catalog.data.market_support ?? []} onEvidence={setEvidence} onQueryChange={setPerformanceQuery}/>} 
      {mode === "valuation" && <ValuationCompare tickers={tickers} onEvidence={setEvidence}/>} 
    </section><EvidenceSheet selection={evidence} onClose={() => setEvidence(null)}/></div><AnalystPanel title={tickers.join(" vs ")} subtitle="Cross-company fundamentals, valuation, and market context" open={analysis.open} onOpenChange={analysis.setOpen} loading={analysis.loading} result={analysis.result} actionLabel="Analyze comparison" onRun={analysis.analyze} idleMessage="Compare the selected companies across the most useful supported dimensions."/></div>
}
