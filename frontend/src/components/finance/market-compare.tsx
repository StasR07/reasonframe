import { useEffect, useMemo, useState } from "react"
import type { MarketQuery, MarketSupport } from "@/api/types"
import { FinancialChart } from "@/components/charts/financial-chart"
import { ChartSkeleton, NetworkError, ResultState } from "@/components/finance/data-state"
import type { EvidenceSelection } from "@/components/finance/evidence-sheet"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { useFinanceQuery } from "@/hooks/use-api"
import { dateLabel, formatValue } from "@/lib/format"
import { sharedMarketRangeCovered, yearsAgo } from "@/lib/market-coverage"

function defaultRange(tickers: string[], support: MarketSupport[]): 1 | 5 | "max" {
  if (sharedMarketRangeCovered(tickers, support, 5)) return 5
  if (sharedMarketRangeCovered(tickers, support, 1)) return 1
  return "max"
}

export function StockPerformanceCompare({ tickers, marketSupport, onEvidence, onQueryChange }: { tickers: string[]; marketSupport: MarketSupport[]; onEvidence: (item: EvidenceSelection) => void; onQueryChange?: (query: MarketQuery) => void }) {
  const rangeScope = `${tickers.join(",")}:${marketSupport.map((item) => `${item.ticker}:${item.first_date}`).join(",")}`
  const [selection, setSelection] = useState<{ scope: string; value: 1 | 5 | "max" }>(() => ({ scope: rangeScope, value: defaultRange(tickers, marketSupport) }))
  const years = selection.scope === rangeScope ? selection.value : defaultRange(tickers, marketSupport)
  const request: MarketQuery = useMemo(() => ({ domain: "market", tickers, series: "ADJUSTED_CLOSE", operation: "INDEXED", ...(years === "max" ? {} : { start_date: yearsAgo(years) }) }), [tickers, years])
  useEffect(() => onQueryChange?.(request), [request, onQueryChange])
  const query = useFinanceQuery(request)
  return <div className="space-y-5"><div className="flex items-center justify-between gap-3"><div><h2 className="font-semibold">Indexed stock performance</h2><p className="text-sm text-muted-foreground">Provider-adjusted close, rebased to 100 on the first common trading date. No forward-fill.</p></div><div className="flex gap-1" aria-label="Date range">{([1, 5] as const).map((item) => <Button key={item} size="sm" disabled={!sharedMarketRangeCovered(tickers, marketSupport, item)} variant={years === item ? "secondary" : "ghost"} onClick={() => setSelection({ scope: rangeScope, value: item })}>{item}Y</Button>)}<Button size="sm" variant={years === "max" ? "secondary" : "ghost"} onClick={() => setSelection({ scope: rangeScope, value: "max" })}>All</Button></div></div>
    {query.loading ? <ChartSkeleton/> : query.error ? <NetworkError message={query.error} retry={query.retry}/> : query.data ? <><ResultState response={query.data}/>{query.data.status === "SUCCESS" && <FinancialChart series={query.data.series} onPointClick={(series, index) => onEvidence({ series, point: series.observations[index] })}/>}</> : null}
  </div>
}

const definitions = [["PE_RATIO", "P/E"], ["PS_RATIO", "P/S"], ["P_FCF_RATIO", "P/FCF"], ["FCF_YIELD", "FCF Yield"]] as const

export function ValuationCompare({ tickers, onEvidence }: { tickers: string[]; onEvidence: (item: EvidenceSelection) => void }) {
  const pe = useFinanceQuery({ domain: "valuation", tickers, metric: "PE_RATIO" })
  const ps = useFinanceQuery({ domain: "valuation", tickers, metric: "PS_RATIO" })
  const pfcf = useFinanceQuery({ domain: "valuation", tickers, metric: "P_FCF_RATIO" })
  const yieldQuery = useFinanceQuery({ domain: "valuation", tickers, metric: "FCF_YIELD" })
  const responses = [pe.data, ps.data, pfcf.data, yieldQuery.data]
  const failure = [pe, ps, pfcf, yieldQuery].find((item) => item.error)
  return <div className="space-y-4">
    <div><h2 className="font-semibold">Latest fiscal-year valuation</h2><p className="text-sm text-muted-foreground">Uses the quoted closing price and the latest annual SEC value available before that session.</p></div>
    {failure ? <NetworkError message={failure.error ?? "Valuation request failed."} retry={failure.retry}/> : <div className="grid gap-4 xl:grid-cols-2">{definitions.map(([metric, label], responseIndex) => <Card key={metric} className="shadow-none"><CardContent><h3 className="font-semibold">{label}</h3><div className="mt-3 grid gap-2 sm:grid-cols-2">{tickers.map((ticker) => {
      const series = responses[responseIndex]?.series.find((item) => item.entity === ticker)
      const point = series?.observations[0]
      return <div key={ticker} className="rounded-lg border p-3"><p className="text-xs font-medium text-muted-foreground">{ticker}</p><p className="mt-1 font-mono text-xl font-semibold">{point && series ? formatValue(point.value, series.unit, series.metric) : series?.state === "NOT_MEANINGFUL" ? "Not meaningful" : "Unavailable"}</p><p className="mt-1 text-xs text-muted-foreground">{series?.context?.source_fiscal_year ? `FY${series.context.source_fiscal_year}` : "Latest fiscal year"}{series?.context?.price_date ? ` · ${dateLabel(String(series.context.price_date))}` : ""}</p>{point && series && <Button variant="link" size="sm" className="mt-1 px-0" onClick={() => onEvidence({ series, point })}>Evidence</Button>}</div>
    })}</div></CardContent></Card>)}</div>}
  </div>
}
