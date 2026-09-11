import { useEffect, useMemo, useState } from "react"
import { useNavigate, useParams, useSearchParams } from "react-router-dom"
import { CalendarDays, Info } from "lucide-react"
import { api } from "@/api/client"
import type { Catalog, Frequency, QueryResponse } from "@/api/types"
import { FinancialChart } from "@/components/charts/financial-chart"
import { ChartSkeleton, NetworkError, ResultState } from "@/components/finance/data-state"
import { EvidenceSheet, type EvidenceSelection } from "@/components/finance/evidence-sheet"
import { HistoryTable } from "@/components/finance/history-table"
import { LatestMarketPrice, MarketAndValuation } from "@/components/finance/market-valuation"
import { AnalystPanel } from "@/components/finance/analyst-panel"
import { useCompanyAnalysis } from "@/hooks/use-analysis"
import { CompanyAutocomplete, CompanySelector, FrequencyToggle, MetricSelector } from "@/components/finance/selectors"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useCatalog, useFinanceQuery } from "@/hooks/use-api"
import { normalizeCompanySelection, supportedFrequencies, supportedMetrics } from "@/lib/capabilities"
import { formatValue } from "@/lib/format"
import { analysisRequestKey } from "@/lib/analysis-cache"
import { getMetricLabel } from "@/lib/metric-label"

const overviewMetrics = ["REVENUE", "REVENUE_GROWTH_YOY", "OPERATING_MARGIN", "NET_INCOME", "FREE_CASH_FLOW"] as const

type OverviewResult = { response?: QueryResponse; failed?: boolean }

export function Overview({ ticker, frequency, supported, catalog }: { ticker: string; frequency: Frequency; supported: Set<string>; catalog?: Catalog }) {
  const [results, setResults] = useState<Map<string, OverviewResult> | null>(null)
  useEffect(() => {
    let current = true
    const controller = new AbortController()
    const metrics = overviewMetrics.filter((metric) => supported.has(`${metric}:${frequency}`))
    setResults(null)
    void Promise.allSettled(metrics.map((metric) => api.query({ domain: "company", tickers: [ticker], metric, frequency, start_year: 2019 }, controller.signal))).then((items) => {
      if (!current) return
      const settled = new Map<string, OverviewResult>()
      metrics.forEach((metric, index) => {
        const result = items[index]
        settled.set(metric, result.status === "fulfilled" ? { response: result.value } : { failed: true })
      })
      setResults(settled)
    })
    return () => { current = false; controller.abort() }
  }, [ticker, frequency, supported])
  if (!results) return <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">{Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="h-24" />)}</div>
  return <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">{overviewMetrics.map((metric) => {
    const series = results.get(metric)?.response?.series[0]
    const point = series?.observations.at(-1)
    return <Card key={metric} className="shadow-none"><CardContent className="flex h-full flex-col"><p data-testid={`kpi-label-${metric}`} className="min-h-8 text-xs font-medium leading-4 text-muted-foreground">{getMetricLabel(metric, catalog)}</p>{point && series ? <><p data-testid={`kpi-value-${metric}`} className="mt-2 whitespace-nowrap font-mono text-xl font-semibold tabular-nums">{formatValue(point.value, series.unit, series.metric)}</p><p className="mt-1 text-xs text-muted-foreground">FY{point.fiscal_year ?? "—"}</p></> : <p className="mt-2 text-sm text-muted-foreground">Unavailable</p>}</CardContent></Card>
  })}</div>
}

export function CompanyPage() {
  const { ticker: routeTicker = "AAPL" } = useParams()
  const ticker = routeTicker.toUpperCase()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const catalog = useCatalog()
  const requestedMetric = params.get("metric") ?? "REVENUE"
  const requestedFrequency: Frequency = params.get("frequency") === "quarterly" ? "quarterly" : "annual"
  const range = params.get("range") ?? "all"
  const [evidence, setEvidence] = useState<EvidenceSelection | null>(null)
  useEffect(() => setEvidence(null), [ticker])
  const supports = useMemo(() => new Set(catalog.data?.company_metric_support.filter((item) => item.ticker === ticker).map((item) => `${item.metric}:${item.frequency}`) ?? []), [catalog.data, ticker])
  const normalized = useMemo(() => catalog.data ? normalizeCompanySelection(catalog.data, [ticker], requestedMetric, requestedFrequency) : { metric: requestedMetric, frequency: requestedFrequency }, [catalog.data, ticker, requestedMetric, requestedFrequency])
  const { metric, frequency } = normalized
  const metrics = useMemo(() => catalog.data ? supportedMetrics(catalog.data, [ticker]) : [], [catalog.data, ticker])
  const frequencies = useMemo(() => catalog.data ? supportedFrequencies(catalog.data, [ticker], metric) : [], [catalog.data, ticker, metric])
  useEffect(() => {
    if (catalog.data && (metric !== requestedMetric || frequency !== requestedFrequency)) setParams({ metric, frequency, range }, { replace: true })
  }, [catalog.data, metric, frequency, requestedMetric, requestedFrequency, range, setParams])
  const startYear = range === "5y" ? new Date().getFullYear() - 5 : undefined
  const analysisStart = startYear ?? 2019
  const analysis = useCompanyAnalysis(
    analysisRequestKey({ mode: "company", ticker, start_year: analysisStart, data_revision: catalog.data?.data_revision ?? "loading", version: 3 }),
    (onEvent, signal) => api.streamCompany(ticker, analysisStart, onEvent, signal),
  )
  const query = useFinanceQuery(catalog.data && frequencies.includes(frequency) ? { domain: "company", tickers: [ticker], metric, frequency, ...(startYear ? { start_year: startYear } : {}) } : null)
  const company = catalog.data?.companies.find((item) => item.ticker === ticker)
  if (catalog.loading) return <div className="space-y-6"><Skeleton className="h-12 w-72"/><ChartSkeleton/></div>
  if (catalog.error || !catalog.data) return <NetworkError message={catalog.error ?? "Catalog unavailable."} retry={catalog.retry}/>
  if (!company) return <Alert><Info/><AlertDescription>This company is not in the supported research catalog.</AlertDescription></Alert>
  const marketSupport = catalog.data.market_support?.find((item) => item.ticker === ticker)
  const marketLoaded = Boolean(marketSupport && marketSupport.observation_count > 0)
  const metricLabel = getMetricLabel(metric, catalog.data)
  const selectCompany = (next: string) => { const selection = normalizeCompanySelection(catalog.data!, [next], metric, frequency); navigate(`/company/${next}?${new URLSearchParams({ ...selection, range })}`) }
  return <div className="flex items-start gap-6"><div className="min-w-0 flex-1 space-y-7">
    <header className="space-y-5"><div className="w-full min-w-0"><div className="mb-2 flex items-center gap-2"><Badge variant="secondary">{company.ticker}</Badge><Badge variant="outline">{company.support_status === "SUPPORTED" ? "Supported" : company.support_status}</Badge></div><div className="flex flex-wrap items-start justify-between gap-x-8 gap-y-2"><h1 className="text-3xl font-semibold tracking-tight">{company.name}</h1><LatestMarketPrice ticker={ticker}/></div><p className="mt-2 flex items-center gap-1.5 text-sm text-muted-foreground"><CalendarDays className="size-4"/>Fiscal periods use issuer-reported labels.</p></div><div className="flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center"><CompanyAutocomplete catalog={catalog.data} onSelect={selectCompany} label="Find company" placeholder="Search ticker, company, or alias..."/><CompanySelector catalog={catalog.data} value={ticker} onChange={selectCompany}/></div></header>
    <Overview ticker={ticker} frequency="annual" supported={supports} catalog={catalog.data}/>
    <MarketAndValuation ticker={ticker} marketLoaded={marketLoaded} marketSupport={marketSupport} providerConnected={catalog.data.provider_connected} onEvidence={setEvidence}/>
    <section className="rounded-xl border bg-background p-4 sm:p-6"><div className="mb-6 flex flex-col justify-between gap-3 lg:flex-row lg:items-center"><h2 className="text-lg font-semibold">Financial history</h2><div className="flex flex-col gap-2 sm:flex-row"><MetricSelector metrics={metrics} value={metric} onChange={(next) => setParams({ ...normalizeCompanySelection(catalog.data!, [ticker], next, frequency), range })}/><FrequencyToggle value={frequency} supported={frequencies} onChange={(next) => setParams({ metric, frequency: next, range })}/></div></div>
      <div className="mb-4 flex gap-1" aria-label="Date range">{[["5y", "5Y"], ["all", "All"]].map(([value, label]) => <Button key={value} size="sm" variant={range === value ? "secondary" : "ghost"} onClick={() => setParams({ metric, frequency, range: value })}>{label}</Button>)}</div>
      {query.loading ? <ChartSkeleton/> : query.error ? <NetworkError message={query.error} retry={query.retry}/> : query.data ? <><ResultState response={query.data}/>{query.data.status === "SUCCESS" && <div className="space-y-6"><h3 className="text-base font-semibold">{metricLabel}</h3><FinancialChart series={query.data.series} onPointClick={(series, index) => setEvidence({ series, point: series.observations[index] })}/><HistoryTable series={query.data.series} onEvidence={setEvidence}/></div>}</> : null}
    </section><EvidenceSheet selection={evidence} onClose={() => setEvidence(null)}/></div><AnalystPanel title={company.name} subtitle={`FY${analysisStart}–latest available`} open={analysis.open} onOpenChange={analysis.setOpen} loading={analysis.loading} result={analysis.result} stages={analysis.stages} actionLabel="Run company analysis" onRun={analysis.analyze} idleMessage="Run independent fundamentals and valuation assessments, followed by adversarial review and an overview."/>
  </div>
}
