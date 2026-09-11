import { useState } from "react"
import { useSearchParams } from "react-router-dom"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { FinancialChart } from "@/components/charts/financial-chart"
import { ChartSkeleton, NetworkError, ResultState } from "@/components/finance/data-state"
import { EvidenceSheet, type EvidenceSelection } from "@/components/finance/evidence-sheet"
import { HistoryTable } from "@/components/finance/history-table"
import { useCatalog, useFinanceQuery } from "@/hooks/use-api"

const preferred = ["US_INFLATION_YOY", "US_CORE_INFLATION_YOY", "US_UNEMPLOYMENT_RATE", "US_FEDERAL_FUNDS_RATE", "US_10Y_TREASURY_YIELD", "US_NOMINAL_GDP", "US_REAL_GDP", "US_INDUSTRIAL_PRODUCTION", "US_HOUSING_STARTS", "US_NONFARM_PAYROLLS"]
const friendlyLabels: Record<string, string> = {
  US_INFLATION_YOY: "Inflation (YoY)", US_CORE_INFLATION_YOY: "Core Inflation (YoY)",
  US_HEADLINE_CPI: "Headline CPI", US_CORE_CPI: "Core CPI",
  US_PCE_PRICE_INDEX: "PCE Price Index", US_CORE_PCE: "Core PCE",
  US_UNEMPLOYMENT_RATE: "Unemployment Rate", US_NONFARM_PAYROLLS: "Nonfarm Payrolls",
  US_FEDERAL_FUNDS_RATE: "Federal Funds Rate", US_10Y_TREASURY_YIELD: "10-Year Treasury Yield",
  US_NOMINAL_GDP: "Nominal GDP", US_REAL_GDP: "Real GDP", US_INDUSTRIAL_PRODUCTION: "Industrial Production",
  US_HOUSING_STARTS: "Housing Starts",
}

export function macroDisplayLabel(code: string, catalogLabel: string) {
  return friendlyLabels[code] ?? catalogLabel
}

export function MacroPage() {
  const catalog = useCatalog()
  const [params, setParams] = useSearchParams()
  const seriesCode = params.get("series") ?? "US_INFLATION_YOY"
  const range = params.get("range") ?? "20"
  const [evidence, setEvidence] = useState<EvidenceSelection | null>(null)
  const startDate = range === "all" ? undefined : `${new Date().getFullYear() - Number(range)}-01-01`
  const query = useFinanceQuery(catalog.data ? { domain: "macro", series: [seriesCode], ...(startDate ? { start_date: startDate } : {}) } : null)
  if (catalog.loading) return <ChartSkeleton/>
  if (catalog.error || !catalog.data) return <NetworkError message={catalog.error ?? "Catalog unavailable."} retry={catalog.retry}/>
  const rank = (code: string) => { const index = preferred.indexOf(code); return index === -1 ? Number.MAX_SAFE_INTEGER : index }
  const available = [...catalog.data.macro_series].sort((a, b) => rank(a.code) - rank(b.code) || a.label.localeCompare(b.label))
  const definition = available.find((item) => item.code === seriesCode)
  return <div className="space-y-7"><header><Badge variant="secondary" className="mb-3">Curated FRED data</Badge><h1 className="text-3xl font-semibold tracking-tight">Macroeconomic research</h1><p className="mt-1 text-sm text-muted-foreground">Long-run economic series with source and transformation details.</p></header>
    <section className="rounded-xl border bg-background p-4 sm:p-6"><div className="mb-6 flex flex-col justify-between gap-3 lg:flex-row lg:items-center"><Select value={seriesCode} onValueChange={(next) => { if (next) setParams({ series: next, range }) }}><SelectTrigger className="w-full sm:w-80" aria-label="Macro series"><SelectValue>{definition ? macroDisplayLabel(definition.code, definition.label) : seriesCode}</SelectValue></SelectTrigger><SelectContent>{available.map((item) => <SelectItem key={item.code} value={item.code}>{macroDisplayLabel(item.code, item.label)}</SelectItem>)}</SelectContent></Select><div className="flex gap-1">{[["5", "5Y"], ["10", "10Y"], ["20", "20Y"], ["all", "All"]].map(([value, label]) => <Button key={value} size="sm" variant={range === value ? "secondary" : "ghost"} onClick={() => setParams({ series: seriesCode, range: value })}>{label}</Button>)}</div></div>
      {definition && <div className="mb-5 flex flex-wrap gap-x-6 gap-y-1 border-y py-3 text-xs text-muted-foreground"><span>Frequency <strong className="font-medium text-foreground">{definition.frequency}</strong></span><span>Unit <strong className="font-medium text-foreground">{definition.unit}</strong></span><span>Provider <strong className="font-medium text-foreground">FRED</strong></span>{definition.provider_series_id && <span>Series <strong className="font-mono font-medium text-foreground">{definition.provider_series_id}</strong></span>}</div>}
      {query.loading ? <ChartSkeleton/> : query.error ? <NetworkError message={query.error} retry={query.retry}/> : query.data ? <><ResultState response={query.data}/>{query.data.status === "SUCCESS" && <div className="space-y-6"><FinancialChart series={query.data.series} onPointClick={(series, index) => setEvidence({ series, point: series.observations[index] })}/><HistoryTable series={query.data.series} onEvidence={setEvidence}/></div>}</> : null}
    </section><EvidenceSheet selection={evidence} onClose={() => setEvidence(null)}/></div>
}
