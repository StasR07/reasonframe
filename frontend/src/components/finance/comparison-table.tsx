import { Search } from "lucide-react"
import { useState } from "react"
import type { SeriesPoint, SeriesResult } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { fiscalLabel, formatValue } from "@/lib/format"
import type { EvidenceSelection } from "./evidence-sheet"

export interface ComparisonRow {
  key: string
  label: string
  date: string
  points: Map<string, { point: SeriesPoint; index: number }>
}

export function comparisonRows(series: SeriesResult[]): ComparisonRow[] {
  const rows = new Map<string, ComparisonRow>()
  series.forEach((item) => item.observations.forEach((point, index) => {
    const key = point.fiscal_year && point.fiscal_period ? `${point.fiscal_year}-${point.fiscal_period}` : point.date
    const row = rows.get(key) ?? { key, label: fiscalLabel(point), date: point.date, points: new Map() }
    row.points.set(item.id, { point, index })
    if (point.date > row.date) row.date = point.date
    rows.set(key, row)
  }))
  return [...rows.values()].sort((a, b) => b.date.localeCompare(a.date))
}

export function ComparisonTable({ series, onEvidence }: { series: SeriesResult[]; onEvidence: (value: EvidenceSelection) => void }) {
  const rows = comparisonRows(series)
  const observationCount = series.reduce((count, item) => count + item.observations.length, 0)
  const scope = `${series.map((item) => item.id).join(",")}:${observationCount}`
  const [disclosure, setDisclosure] = useState({ scope, open: observationCount <= 10 })
  const open = disclosure.scope === scope ? disclosure.open : observationCount <= 10
  return <details open={open} onToggle={(event) => setDisclosure({ scope, open: event.currentTarget.open })} className="rounded-lg border bg-background">
    <summary className="cursor-pointer select-none px-4 py-3 text-sm font-medium">Data &amp; evidence · {observationCount} {observationCount === 1 ? "observation" : "observations"}</summary>
    <div className="overflow-x-auto border-t"><Table className="min-w-max">
    <TableHeader><TableRow><TableHead className="sticky left-0 z-10 min-w-36 bg-background">Fiscal period</TableHead>{series.map((item) => <TableHead key={item.id} className="min-w-48 text-right">{item.entity ?? item.label}</TableHead>)}</TableRow></TableHeader>
    <TableBody>{rows.map((row) => <TableRow key={row.key}><TableCell className="sticky left-0 z-10 bg-background font-medium">{row.label}</TableCell>{series.map((item) => {
      const cell = row.points.get(item.id)
      return <TableCell key={item.id} className="text-right">{cell ? <div className="flex items-center justify-end gap-1"><span className="font-mono tabular-nums">{formatValue(cell.point.value, item.unit, item.metric, "exact")}</span><Button variant="ghost" size="icon-sm" aria-label={`Inspect evidence for ${item.entity ?? item.label} ${row.label}`} onClick={() => onEvidence({ series: item, point: cell.point })}><Search /></Button></div> : <span className="text-muted-foreground" aria-label={`No data for ${item.entity ?? item.label} ${row.label}`}>—</span>}</TableCell>
    })}</TableRow>)}</TableBody>
  </Table></div></details>
}
