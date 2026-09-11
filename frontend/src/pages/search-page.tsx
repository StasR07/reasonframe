import { useCallback, useEffect, useRef, useState } from "react"
import { AlertCircle, HelpCircle, Search } from "lucide-react"
import { useSearchParams } from "react-router-dom"
import { api } from "@/api/client"
import type { AskResponse, AskSuccessResponse, Catalog, CompanyQuery, SeriesPoint, SeriesResult } from "@/api/types"
import { FinancialChart } from "@/components/charts/financial-chart"
import { ChartSkeleton, NetworkError, ResultState } from "@/components/finance/data-state"
import { ComparisonTable } from "@/components/finance/comparison-table"
import { EvidenceSheet, type EvidenceSelection } from "@/components/finance/evidence-sheet"
import { HistoryTable } from "@/components/finance/history-table"
import { AnalystPanel } from "@/components/finance/analyst-panel"
import { useAnalysis } from "@/hooks/use-analysis"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { persistedAIModel } from "@/lib/ai-model"
import { fiscalLabel, formatValue } from "@/lib/format"
import { classifyResult } from "@/lib/result-shape"
import { cacheSearch, cachedSearch, recentSearches } from "@/lib/search-cache"
import { analysisRequestKey } from "@/lib/analysis-cache"
import { useCatalog } from "@/hooks/use-api"
import { getMetricLabel } from "@/lib/metric-label"

const examples = ["Apple latest stock price", "Apple vs Nvidia stock performance over 5Y", "Nvidia P/FCF", "Nvidia operating margin since 2019"]

function Interpretation({ response, catalog }: { response: AskSuccessResponse; catalog: Catalog }) {
  const item = response.interpretation
  const entities = item.entities.length ? item.entities.join(" vs ") : "Macro"
  return <Card className="shadow-none"><CardContent><p className="mb-3 text-xs font-semibold uppercase tracking-[0.13em] text-muted-foreground">Interpreted as</p><div className="flex flex-wrap gap-2"><Badge variant="secondary">{entities}</Badge><Badge variant="outline">{getMetricLabel(item.metric, catalog)}</Badge>{item.frequency && <Badge variant="outline">{item.frequency}</Badge>}<Badge variant="outline">{item.range}</Badge>{item.operation !== "LEVEL" && <Badge variant="outline">{item.operation.replaceAll("_", " ")}</Badge>}</div></CardContent></Card>
}

function ScalarCard({ response, series, point, aggregate, onEvidence }: { response: AskSuccessResponse; series: SeriesResult; point: SeriesPoint; aggregate: boolean; onEvidence: (selection: EvidenceSelection) => void }) {
  return <Card><CardContent className="py-6"><div className="flex flex-wrap items-start justify-between gap-5"><div><p className="text-sm text-muted-foreground">{series.entity ?? "Macroeconomic series"}</p><h2 className="mt-1 text-lg font-semibold">{aggregate ? `Average ${series.label}` : series.label}</h2><p className="mt-5 font-mono text-4xl font-semibold tabular-nums">{formatValue(point.value, series.unit, series.metric, response.validated_query.domain === "market" ? "exact" : "compact")}</p><div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground"><span>{aggregate ? response.interpretation.range : fiscalLabel(point)}</span><Badge variant={point.point_type === "SOURCE" ? "secondary" : "outline"}>{point.point_type === "SOURCE" ? "Reported" : "Calculated"}</Badge></div></div><Button variant="outline" size="sm" onClick={() => onEvidence({ series, point })}>{aggregate ? "View calculation/evidence" : "View evidence"}</Button></div></CardContent></Card>
}

export function universeRankingPresentation(query: CompanyQuery, series: SeriesResult[]) {
  if (!query.universe_ranking || !query.ranking || query.ranking === "NONE") return { visible: series, label: null }
  const count = Math.min(query.ranking_limit ?? 5, series.length)
  const visible = series.slice(0, count)
  const coverage = Number(series[0]?.context?.coverage_count ?? series.length)
  const period = visible[0]?.observations[0] ? fiscalLabel(visible[0].observations[0]) : "the requested period"
  const metric = visible[0]?.label ?? query.metric
  const prefix = query.ranking === "LOWEST" ? "Lowest" : "Top"
  return { visible, label: `${prefix} ${count} of ${coverage} companies with usable ${period} ${metric} data` }
}

function SinglePeriodComparison({ series, onEvidence, rankingLabel }: { series: SeriesResult[]; onEvidence: (selection: EvidenceSelection) => void; rankingLabel?: string | null }) {
  const metric = series[0]?.label
  const period = series[0]?.observations[0] ? fiscalLabel(series[0].observations[0]) : ""
  const coverage = Number(series[0]?.context?.coverage_count ?? 0)
  const ranked = coverage > 0
  return <section className="rounded-xl border bg-background p-5"><div className="mb-4"><h2 className="font-semibold">{rankingLabel ?? metric}</h2>{!rankingLabel && <p className="text-sm text-muted-foreground">{period}{ranked ? ` · ${coverage} certified companies with usable data` : ""}</p>}</div><div className="grid gap-3 sm:grid-cols-2">{series.map((item, index) => { const point = item.observations[0]; return point && <div key={item.id} className="rounded-lg border p-4"><p className="font-medium">{ranked ? `${index + 1}. ` : ""}{item.entity ?? item.label}</p><p className="mt-2 font-mono text-2xl font-semibold tabular-nums">{formatValue(point.value, item.unit, item.metric, "exact")}</p><div className="mt-3 flex items-center justify-between"><Badge variant={point.point_type === "SOURCE" ? "secondary" : "outline"}>{point.point_type === "SOURCE" ? "Reported" : "Calculated"}</Badge><Button variant="ghost" size="sm" onClick={() => onEvidence({ series: item, point })}>View evidence</Button></div></div> })}</div></section>
}

function SuccessfulResult({ response, dataRevision, parserModel, catalog }: { response: AskSuccessResponse; dataRevision: string; parserModel: string; catalog: Catalog }) {
  const [evidence, setEvidence] = useState<EvidenceSelection | null>(null)
  const shape = classifyResult(response)
  const firstSeries = response.results.series[0]
  const firstPoint = firstSeries?.observations[0]
  const explicitState = firstSeries && !firstPoint ? firstSeries.state : null
  const timeSeries = shape === "TIME_SERIES" || shape === "MULTI_SERIES_TIME_SERIES"
  const isDailyMarket = response.validated_query.domain === "market"
  const usefulMarketScalar = response.validated_query.domain === "market" && (response.validated_query.operation === "RETURN" || response.validated_query.operation === "MAX_DRAWDOWN")
  const pointCount = response.results.series.reduce((count, item) => count + item.observations.length, 0)
  const canAnalyze = response.results.status === "SUCCESS" && !explicitState && (
    (timeSeries && pointCount >= 2) ||
    (shape === "SINGLE_PERIOD_COMPARISON" && response.results.series.length >= 2) ||
    usefulMarketScalar
  )
  const insightKey = analysisRequestKey({ mode: "focused", query: response.validated_query, parser_model: parserModel, data_revision: dataRevision, version: 3 })
  const analysis = useAnalysis(insightKey, (signal) => api.analyzeResult(response.validated_query, signal))
  useEffect(() => {
    if (canAnalyze) analysis.analyze()
  }, [canAnalyze, insightKey])
  const entities = response.validated_query.domain === "company" && response.validated_query.universe_ranking
    ? ["Certified universe"]
    : "tickers" in response.validated_query ? response.validated_query.tickers : response.validated_query.series
  const title = entities.join(" vs ")
  const metricLabel = getMetricLabel(response.interpretation.metric, catalog)
  const ranking = response.validated_query.domain === "company" ? universeRankingPresentation(response.validated_query, response.results.series) : { visible: response.results.series, label: null }
  return <div className="flex items-start gap-6"><div className="min-w-0 flex-1 space-y-5"><Interpretation response={response} catalog={catalog}/>
    {response.results.status !== "SUCCESS" && !firstSeries && <ResultState response={response.results}/>} 
    {(shape === "SINGLE_SCALAR" || shape === "AGGREGATE_SCALAR") && firstSeries && firstPoint && <ScalarCard response={response} series={firstSeries} point={firstPoint} aggregate={shape === "AGGREGATE_SCALAR"} onEvidence={setEvidence}/>} 
    {explicitState && <Card><CardContent className="py-6"><p className="text-sm text-muted-foreground">{firstSeries.label}</p><p className="mt-3 text-2xl font-semibold">{explicitState === "NOT_MEANINGFUL" ? "Not meaningful" : "Unavailable"}</p><p className="mt-2 text-sm text-muted-foreground">{String(firstSeries.context?.basis ?? "No deterministic value is available.")}</p></CardContent></Card>}
    {shape === "SINGLE_PERIOD_COMPARISON" && <SinglePeriodComparison series={ranking.visible} rankingLabel={ranking.label} onEvidence={setEvidence}/>} 
    {timeSeries && !explicitState && <section className="space-y-6 rounded-xl border bg-background p-4 sm:p-6"><FinancialChart series={response.results.series} onPointClick={(series, index) => setEvidence({ series, point: series.observations[index] })}/>{!isDailyMarket && (shape === "MULTI_SERIES_TIME_SERIES" ? <ComparisonTable series={response.results.series} onEvidence={setEvidence}/> : <HistoryTable series={response.results.series} onEvidence={setEvidence}/>)}</section>}
    <EvidenceSheet selection={evidence} onClose={() => setEvidence(null)}/></div>{canAnalyze && <AnalystPanel title="Key insight" subtitle={`${title} · ${metricLabel} · ${response.interpretation.range}`} open={analysis.open} onOpenChange={analysis.setOpen} loading={analysis.loading} result={analysis.result} onRun={analysis.analyze} idleMessage="Generating a concise interpretation…"/>}</div>
}

export function SearchPage() {
  const [params, setParams] = useSearchParams()
  const question = params.get("q")?.trim() ?? ""
  const catalog = useCatalog()
  const model = persistedAIModel() ?? "default"
  const revision = catalog.data?.data_revision ?? "loading"
  const catalogReady = Boolean(catalog.data)
  const [response, setResponse] = useState<AskResponse | null>(null)
  const [recent, setRecent] = useState(recentSearches)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [retryToken, setRetryToken] = useState(0)
  const generation = useRef(0)

  useEffect(() => {
    if (!question) {
      const latest = recentSearches()[0]
      if (latest) setParams({ q: latest.question }, { replace: true })
      else { setResponse(null); setError(null); setLoading(false) }
      return
    }
    if (!catalog.data) return
    const cached = retryToken === 0 ? cachedSearch(question, model, revision) : null
    if (cached) { setResponse(cached); setError(null); setLoading(false); return }
    const requestGeneration = ++generation.current
    setLoading(true); setError(null); setResponse(null)
    const controller = new AbortController()
    void api.ask(question, persistedAIModel(), controller.signal).then((result) => {
      if (generation.current !== requestGeneration) return
      setResponse(result); setLoading(false)
      if (result.status === "success") { cacheSearch(question, result, model, revision); setRecent(recentSearches()) }
    }).catch((reason) => {
      if (generation.current === requestGeneration) { setError(reason instanceof Error ? reason.message : "Natural-language search failed."); setLoading(false) }
    })
    return () => { controller.abort(); generation.current += 1 }
  }, [question, retryToken, setParams, catalogReady, model, revision])
  const retry = useCallback(() => setRetryToken((value) => value + 1), [])

  if (!question) return <div className="space-y-6 rounded-xl border bg-background p-8"><div className="text-center"><Search className="mx-auto mb-3 size-7 text-blue-600"/><h1 className="text-xl font-semibold">Search</h1><p className="mx-auto mt-2 max-w-xl text-sm text-muted-foreground">Ask about a company or macro series.</p></div><section><h2 className="mb-3 text-sm font-semibold">Example questions</h2><div className="flex flex-wrap gap-1.5 xl:flex-nowrap">{examples.map((example) => <Button key={example} variant="outline" size="sm" className="px-2" onClick={() => setParams({ q: example })}>{example}</Button>)}</div></section>{recent.length > 0 && <section><h2 className="mb-3 text-sm font-semibold">Recent searches</h2>{recent.map((item) => <Button key={item.question} variant="ghost" className="block" onClick={() => setParams({ q: item.question })}>{item.question}</Button>)}</section>}</div>
  return <div className="space-y-6"><header><Badge variant="secondary" className="mb-3">Search</Badge><h1 className="text-2xl font-semibold tracking-tight">{question}</h1></header>
    {loading && <div><p className="mb-3 text-sm text-muted-foreground">Interpreting question...</p><ChartSkeleton/></div>}
    {error && <NetworkError message={error} retry={retry}/>} 
    {response?.status === "success" && <SuccessfulResult key={`${response.question}:${revision}`} response={response} dataRevision={revision} parserModel={model} catalog={catalog.data!}/>} 
    {response?.status === "unsupported" && <Alert><AlertCircle/><AlertTitle>Unsupported question</AlertTitle><AlertDescription>{response.message}</AlertDescription></Alert>}
    {response?.status === "clarification" && <Alert><HelpCircle/><AlertTitle>Clarification needed</AlertTitle><AlertDescription><p>{response.message}</p><div className="mt-4 flex flex-wrap gap-2">{response.choices.map((choice) => <Button key={choice.question} size="sm" variant="outline" onClick={() => setParams({ q: choice.question })}>{choice.label}</Button>)}</div></AlertDescription></Alert>}
    {response?.status === "error" && <Alert variant="destructive"><AlertCircle/><AlertTitle>Search unavailable</AlertTitle><AlertDescription>{response.message}</AlertDescription></Alert>}
    {recent.some((item) => item.question !== question) && <section className="rounded-xl border bg-background p-4"><h2 className="mb-2 text-xs font-semibold uppercase tracking-[0.13em] text-muted-foreground">Recent searches</h2><div className="flex flex-wrap gap-2">{recent.filter((item) => item.question !== question).map((item) => <Button key={item.question} variant="ghost" size="sm" onClick={() => setParams({ q: item.question })}>{item.question}</Button>)}</div></section>}
  </div>
}
