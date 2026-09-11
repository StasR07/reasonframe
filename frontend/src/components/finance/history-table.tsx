import { Search } from "lucide-react"
import { useState } from "react"
import type { SeriesResult } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { fiscalLabel, formatValue } from "@/lib/format"
import type { EvidenceSelection } from "./evidence-sheet"

export function HistoryTable({ series, onEvidence, comparison = false }: { series: SeriesResult[]; onEvidence: (value: EvidenceSelection) => void; comparison?: boolean }) {
  const rows = series.flatMap((item) => item.observations.map((point) => ({ item, point }))).sort((a, b) => b.point.date.localeCompare(a.point.date))
  const scope = `${series.map((item) => item.id).join(",")}:${rows.length}`
  const [disclosure, setDisclosure] = useState({ scope, open: rows.length <= 10 })
  const open = disclosure.scope === scope ? disclosure.open : rows.length <= 10
  return <details open={open} onToggle={(event) => setDisclosure({ scope, open: event.currentTarget.open })} className="rounded-lg border bg-background">
    <summary className="cursor-pointer select-none px-4 py-3 text-sm font-medium">Data &amp; evidence · {rows.length} {rows.length === 1 ? "observation" : "observations"}</summary>
    <div className="overflow-x-auto border-t"><Table>
    <TableHeader><TableRow>{comparison && <TableHead>Company</TableHead>}<TableHead>Fiscal period</TableHead><TableHead className="text-right">Exact value</TableHead><TableHead>Type</TableHead><TableHead className="w-16"><span className="sr-only">Evidence</span></TableHead></TableRow></TableHeader>
    <TableBody>{rows.map(({ item, point }) => <TableRow key={`${item.id}-${point.date}-${point.fiscal_period ?? "date"}`}>
      {comparison && <TableCell className="font-medium">{item.entity}</TableCell>}<TableCell><span className="font-medium">{fiscalLabel(point)}</span><span className="ml-2 text-xs text-muted-foreground">{point.date}</span></TableCell>
      <TableCell className="text-right font-mono tabular-nums">{formatValue(point.value, item.unit, item.metric, "exact")}</TableCell><TableCell><Badge variant={point.point_type === "SOURCE" ? "secondary" : "outline"}>{point.point_type === "SOURCE" ? "Reported" : "Calculated"}</Badge></TableCell>
      <TableCell><Button variant="ghost" size="icon-sm" aria-label={`Inspect evidence for ${fiscalLabel(point)}`} onClick={() => onEvidence({ series: item, point })}><Search /></Button></TableCell>
    </TableRow>)}</TableBody>
  </Table></div></details>
}
