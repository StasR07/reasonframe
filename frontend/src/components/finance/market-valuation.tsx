import { useState } from "react"
import type { MarketSupport } from "@/api/types"
import { FinancialChart } from "@/components/charts/financial-chart"
import { ChartSkeleton, NetworkError, ResultState } from "@/components/finance/data-state"
import type { EvidenceSelection } from "@/components/finance/evidence-sheet"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { useFinanceQuery } from "@/hooks/use-api"
import { dateLabel, formatValue } from "@/lib/format"
import { marketRangeCovered, yearsAgo } from "@/lib/market-coverage"

const valuationMetrics = [
  ["PE_RATIO", "P/E"], ["PS_RATIO", "P/S"], ["P_FCF_RATIO", "P/FCF"], ["FCF_YIELD", "FCF Yield"],
] as const

function defaultMarketRange(support: MarketSupport | undefined): "1Y" | "5Y" | "All" {
  if (marketRangeCovered(support, 5)) return "5Y"
  if (marketRangeCovered(support, 1)) return "1Y"
  return "All"
}

export function LatestMarketPrice({ ticker }: { ticker: string }) {
  const query = useFinanceQuery({ domain: "market", tickers: [ticker], series: "RAW_CLOSE", view: "latest" })
  const series = query.data?.series[0]; const point = series?.observations[0]
  if (query.loading) return <p className="text-xs text-muted-foreground">Loading latest EOD…</p>
  if (query.error) return <Button variant="link" size="sm" onClick={query.retry}>Could not load latest EOD · Try again</Button>
  if (!point || !series) return null
  return <div className="shrink-0 text-left sm:text-right"><p className="font-mono text-2xl font-semibold tabular-nums">{formatValue(point.value, series.unit, series.metric, "exact")}</p><p className="text-xs text-muted-foreground">Latest EOD · {dateLabel(point.date)}</p></div>
}

function Stat({ label, request }: { label: string; request: ReturnType<typeof useFinanceQuery> }) {
  const series = request.data?.series[0]; const point = series?.observations[0]
  const value = request.loading ? "Loading…" : request.error ? "Could not load" : point && series ? formatValue(point.value, series.unit, series.metric) : "Unavailable"
  return <Card className="shadow-none"><CardContent><p className="text-xs text-muted-foreground">{label}</p><p className="mt-2 font-mono text-lg font-semibold">{value}</p>{request.error && <Button className="mt-2 px-0" variant="link" size="sm" onClick={request.retry}>Try again</Button>}</CardContent></Card>
}

type ValuationRequest = ReturnType<typeof useFinanceQuery>
export const retryFailedValuations = (requests: Array<Pick<ValuationRequest, "error" | "retry">>) =>
  requests.filter((item) => item.error).forEach((item) => item.retry())
export const shouldRenderValuationChart = (observationCount: number) => observationCount >= 2

function ValuationCard({ request, label, onEvidence }: { request: ValuationRequest; label: string; onEvidence: (item: EvidenceSelection) => void }) {
  const series = request.data?.series[0]; const point = series?.observations[0]
  const state = series?.state ?? "UNAVAILABLE"
  return <Card className="shadow-none"><CardContent><p className="text-xs font-medium text-muted-foreground">{label}</p>
    <p className="mt-2 font-mono text-xl font-semibold">{request.loading ? "Loading…" : request.error ? "Could not load" : point && series ? formatValue(point.value, series.unit, series.metric) : state === "NOT_MEANINGFUL" ? "Not meaningful" : "Unavailable"}</p>
    <p className="mt-1 text-xs text-muted-foreground">Latest fiscal year{series?.context?.source_fiscal_year ? ` · FY${series.context.source_fiscal_year}` : ""}{series?.context?.price_date ? ` · ${dateLabel(String(series.context.price_date))}` : ""}</p>
    {request.error && <Button className="mt-3 px-0" variant="link" size="sm" onClick={request.retry}>Try again</Button>}
    {!request.error && point && series && <Button className="mt-3 px-0" variant="link" size="sm" onClick={() => onEvidence({ series, point })}>View evidence</Button>}
  </CardContent></Card>
}

function LoadedMarketAndValuation({ ticker, marketSupport, onEvidence }: { ticker: string; marketSupport?: MarketSupport; onEvidence: (item: EvidenceSelection) => void }) {
  const rangeScope = `${ticker}:${marketSupport?.first_date ?? "none"}`
  const [rangeSelection, setRangeSelection] = useState<{ scope: string; value: "1Y" | "5Y" | "All" }>(() => ({ scope: rangeScope, value: defaultMarketRange(marketSupport) }))
  const range = rangeSelection.scope === rangeScope ? rangeSelection.value : defaultMarketRange(marketSupport)
  const [priceSeries, setPriceSeries] = useState<"ADJUSTED_CLOSE" | "RAW_CLOSE">("ADJUSTED_CLOSE")
  const [valuationMetric, setValuationMetric] = useState<"PE_RATIO" | "PS_RATIO" | "P_FCF_RATIO">("PE_RATIO")
  const [valuationRange, setValuationRange] = useState<"5Y" | "All">("5Y")
  const startDate = range === "All" ? undefined : yearsAgo(Number(range.slice(0, -1)))
  const history = useFinanceQuery({ domain: "market", tickers: [ticker], series: priceSeries, view: "history", ...(startDate ? { start_date: startDate } : {}) })
  const return1 = useFinanceQuery({ domain: "market", tickers: [ticker], series: "ADJUSTED_CLOSE", start_date: yearsAgo(1), operation: "RETURN" })
  const return5 = useFinanceQuery({ domain: "market", tickers: [ticker], series: "ADJUSTED_CLOSE", start_date: yearsAgo(5), operation: "RETURN" })
  const drawdown = useFinanceQuery({ domain: "market", tickers: [ticker], series: "ADJUSTED_CLOSE", start_date: yearsAgo(5), operation: "MAX_DRAWDOWN" })
  const pe = useFinanceQuery({ domain: "valuation", tickers: [ticker], metric: "PE_RATIO", view: "latest" })
  const ps = useFinanceQuery({ domain: "valuation", tickers: [ticker], metric: "PS_RATIO", view: "latest" })
  const pfcf = useFinanceQuery({ domain: "valuation", tickers: [ticker], metric: "P_FCF_RATIO", view: "latest" })
  const yieldQuery = useFinanceQuery({ domain: "valuation", tickers: [ticker], metric: "FCF_YIELD", view: "latest" })
  const valuationHistory = useFinanceQuery({ domain: "valuation", tickers: [ticker], metric: valuationMetric, view: "history", ...(valuationRange === "5Y" ? { start_year: new Date().getFullYear() - 5 } : {}) })
  const valuationRequests = [pe, ps, pfcf, yieldQuery]
  const valuationFailures = valuationRequests.filter((item) => item.error)
  return <section className="space-y-7 rounded-xl border bg-background p-4 sm:p-6"><div><h2 className="text-lg font-semibold">Market &amp; valuation</h2><p className="text-sm text-muted-foreground">End-of-day market data and valuation based on the latest fiscal year.</p></div>
    <div className="space-y-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-semibold">Price history</h3><p className="text-xs text-muted-foreground">Adjusted uses Tiingo&apos;s adjusted close for performance; Raw shows the quoted closing price.{marketSupport?.first_date ? ` Data available since ${dateLabel(marketSupport.first_date)}.` : ""}</p></div><div className="flex flex-wrap gap-1" aria-label="Date range">{(["1Y", "5Y", "All"] as const).map((item) => { const years = item === "All" ? null : Number(item.slice(0, -1)); const disabled = years !== null && !marketRangeCovered(marketSupport, years); return <Button key={item} size="sm" disabled={disabled} title={disabled && marketSupport?.first_date ? `Available since ${dateLabel(marketSupport.first_date)}` : undefined} variant={range === item ? "secondary" : "ghost"} onClick={() => setRangeSelection({ scope: rangeScope, value: item })}>{item}</Button> })}<span className="mx-1 border-l"/>{(["ADJUSTED_CLOSE", "RAW_CLOSE"] as const).map((item) => <Button key={item} size="sm" variant={priceSeries === item ? "secondary" : "ghost"} onClick={() => setPriceSeries(item)}>{item === "ADJUSTED_CLOSE" ? "Adjusted" : "Raw"}</Button>)}</div></div>
      {history.loading ? <ChartSkeleton/> : history.error ? <NetworkError message={history.error} retry={history.retry}/> : history.data ? <><ResultState response={history.data}/>{history.data.status === "SUCCESS" && <FinancialChart series={history.data.series} onPointClick={(series, index) => onEvidence({ series, point: series.observations[index] })}/>}</> : null}
      <div className="grid gap-3 sm:grid-cols-3"><Stat label="1Y return" request={return1}/><Stat label="5Y return" request={return5}/><Stat label="5Y max drawdown" request={drawdown}/></div>
    </div>
    <div className="space-y-5 border-t pt-6"><div><h3 className="font-semibold">Valuation</h3><p className="text-xs text-muted-foreground">Quoted closing price divided by the latest annual SEC value available before that trading session.</p></div><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{valuationMetrics.map(([, label], index) => <ValuationCard key={label} label={label} request={valuationRequests[index]} onEvidence={onEvidence}/>)}</div>{valuationFailures.length > 1 && <Button variant="outline" size="sm" onClick={() => retryFailedValuations(valuationRequests)}>Retry all failed valuation requests</Button>}
      <div><div className="mb-3 flex flex-wrap items-center justify-between gap-2"><div><h4 className="font-medium">Historical valuation</h4><p className="text-xs text-muted-foreground">Uses the first trading-day raw close after each filing became available.</p></div><div className="flex flex-wrap gap-1">{(["5Y", "All"] as const).map((item) => <Button key={item} size="sm" variant={valuationRange === item ? "secondary" : "ghost"} onClick={() => setValuationRange(item)}>{item}</Button>)}<span className="mx-1 border-l"/>{valuationMetrics.slice(0, 3).map(([metric, label]) => <Button key={metric} size="sm" variant={valuationMetric === metric ? "secondary" : "ghost"} onClick={() => setValuationMetric(metric as typeof valuationMetric)}>{label}</Button>)}</div></div>
        {valuationHistory.loading ? <ChartSkeleton/> : valuationHistory.error ? <NetworkError message={valuationHistory.error} retry={valuationHistory.retry}/> : (valuationHistory.data?.series[0]?.observations.length ?? 0) >= 2 ? <><FinancialChart series={valuationHistory.data!.series} onPointClick={(series, index) => onEvidence({ series, point: series.observations[index] })}/><p className="mt-2 text-xs text-muted-foreground">{valuationHistory.data!.series[0].context?.interpretation_available ? `Five-point context median: ${formatValue(String(valuationHistory.data!.series[0].context?.median), valuationHistory.data!.series[0].unit, valuationHistory.data!.series[0].metric)}` : "Fewer than three usable annual points; median-range interpretation is suppressed."}</p></> : valuationHistory.data?.series[0]?.observations.length === 1 ? <div className="rounded-lg border p-4"><p className="font-medium">{valuationMetrics.find(([code]) => code === valuationMetric)?.[1]}</p><p className="mt-1 font-mono text-lg font-semibold">{formatValue(valuationHistory.data.series[0].observations[0].value, valuationHistory.data.series[0].unit, valuationHistory.data.series[0].metric)} · FY{valuationHistory.data.series[0].observations[0].source_fiscal_year ?? valuationHistory.data.series[0].observations[0].fiscal_year ?? "—"}</p><p className="mt-2 text-sm text-muted-foreground">Historical-relative context is not yet available. At least two usable annual observations are required.</p></div> : <p className="text-sm text-muted-foreground">Historical valuation is unavailable.</p>}
      </div>
    </div>
  </section>
}

export function MarketAndValuation({ ticker, marketLoaded, marketSupport, providerConnected = true, onEvidence }: { ticker: string; marketLoaded: boolean; marketSupport?: MarketSupport; providerConnected?: boolean; onEvidence: (item: EvidenceSelection) => void }) {
  if (!marketLoaded) return <section className="rounded-xl border bg-background p-4 sm:p-6"><h2 className="text-lg font-semibold">Market &amp; Valuation</h2><p className="mt-2 text-sm text-muted-foreground">{providerConnected ? <>No Tiingo EOD market observations are loaded for {ticker}. Run <code>reasonframe market {ticker}</code>.</> : <>Tiingo market data is not connected. Add a Tiingo token in Settings, then refresh local data.</>}</p></section>
  return <LoadedMarketAndValuation ticker={ticker} marketSupport={marketSupport} onEvidence={onEvidence}/>
}
