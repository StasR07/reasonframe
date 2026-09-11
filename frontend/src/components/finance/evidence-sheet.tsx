import { useEffect, useMemo, useState } from "react"
import { api } from "@/api/client"
import type { Evidence, SeriesPoint, SeriesResult } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { dateLabel, fiscalLabel, formatValue } from "@/lib/format"

export interface EvidenceSelection { series: SeriesResult; point: SeriesPoint }
export type EvidenceSheetSelection = EvidenceSelection | EvidenceSelection[]

function EvidenceDetail({ selection, heading }: { selection: EvidenceSelection; heading?: string }) {
  const { point, series } = selection
  const references = useMemo(() => [...new Set([
    ...(point.observation_id ? [point.observation_id] : []),
    ...point.input_observation_ids,
  ])], [point.input_observation_ids, point.observation_id])
  const resolutionKey = `${series.id}:${point.date}:${references.join("|")}`
  const [resolution, setResolution] = useState<{ key: string; evidence: Evidence[] } | null>(null)
  const resolved = point.evidence.length ? point.evidence : resolution?.key === resolutionKey ? resolution.evidence : []
  const resolving = !point.evidence.length && references.length > 0 && resolution?.key !== resolutionKey
  useEffect(() => {
    if (point.evidence.length || !references.length) return
    let current = true
    void Promise.allSettled(references.map((id) => api.evidence(id))).then((items) => {
      if (!current) return
      setResolution({ key: resolutionKey, evidence: items.flatMap((item) => item.status === "fulfilled" ? [item.value] : []) })
    })
    return () => { current = false }
  }, [point.evidence, references, resolutionKey])
  return <section className="space-y-6">
    {heading && <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{heading}</p>}
    <div><div className="mb-2"><Badge variant={point.point_type === "SOURCE" ? "secondary" : "outline"}>{point.point_type === "SOURCE" ? "Reported" : "Calculated"}</Badge></div>
      <h3 className="text-base font-semibold">{series.entity ? `${series.entity} ${series.label}` : series.label}</h3><p className="text-sm text-muted-foreground">{fiscalLabel(point)} · {dateLabel(point.date)}</p></div>
    <div className="font-mono text-3xl font-semibold tabular-nums">{point.state === "NOT_MEANINGFUL" ? "Not meaningful" : formatValue(point.value, series.unit, series.metric, "exact")}</div>
    {point.formula && <section><p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Formula</p><code className="block rounded-md bg-muted p-3 text-sm">{point.formula}</code></section>}
    <section className="space-y-4"><div><p className="text-sm font-semibold">{point.point_type === "SOURCE" ? "Source evidence" : "Calculation inputs"}</p><p className="text-sm text-muted-foreground">Exact observations used by the deterministic backend.</p></div>
      {resolved.length ? resolved.map((item) => <div key={item.id} className="rounded-lg border p-4 text-sm">
        <div className="mb-3 flex items-start justify-between gap-4"><div><p className="font-medium">{item.label}</p><p className="font-mono text-base tabular-nums">{formatValue(item.value, item.unit, item.metric, "exact")}</p></div><Badge variant="secondary">{item.source_type}</Badge></div>
        <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-2 text-xs">
          {item.filing_form && <><dt className="text-muted-foreground">Form</dt><dd>{item.filing_form}</dd></>}
          {item.source_concept && <><dt className="text-muted-foreground">Concept</dt><dd className="break-all font-mono">{item.source_concept}</dd></>}
          {item.accession_number && <><dt className="text-muted-foreground">Accession</dt><dd className="break-all font-mono">{item.accession_number}</dd></>}
          {item.filing_date && <><dt className="text-muted-foreground">Filed</dt><dd>{dateLabel(item.filing_date)}</dd></>}
          <dt className="text-muted-foreground">Period end</dt><dd>{dateLabel(item.period_end)}</dd>
          {item.provider_series_id && <><dt className="text-muted-foreground">FRED series</dt><dd className="font-mono">{item.provider_series_id}</dd></>}
          {item.provider && <><dt className="text-muted-foreground">Provider</dt><dd>{item.provider}</dd></>}
          {item.symbol && <><dt className="text-muted-foreground">Instrument</dt><dd className="font-mono">{item.symbol}{item.exchange_mic ? ` / ${item.exchange_mic}` : ""}</dd></>}
          {item.market_field && <><dt className="text-muted-foreground">Market field</dt><dd>{item.market_field.replaceAll("_", " ")}</dd></>}
          {item.action_type && <><dt className="text-muted-foreground">Action</dt><dd>{item.action_type.replaceAll("_", " ")}{item.date_type ? ` · ${item.date_type.replaceAll("_", " ")}` : ""}</dd></>}
          {item.retrieved_at && <><dt className="text-muted-foreground">Retrieved</dt><dd>{dateLabel(item.retrieved_at.slice(0, 10))}</dd></>}
          {item.derivation && item.derivation !== "DIRECT" && <><dt className="text-muted-foreground">Derivation</dt><dd>{item.derivation === "PERIOD_DERIVED" ? "Derived quarter" : item.derivation}</dd></>}
        </dl>
      </div>) : <p className="text-sm text-muted-foreground">{resolving ? "Resolving canonical evidence from local storage…" : "Source identifiers are retained, but detailed evidence is not available for this transformation."}</p>}
    </section>
  </section>
}

export function EvidenceSheet({ selection, onClose }: { selection: EvidenceSheetSelection | null; onClose: () => void }) {
  const selections = selection ? (Array.isArray(selection) ? selection : [selection]) : []
  return <Sheet open={Boolean(selection)} onOpenChange={(open) => { if (!open) onClose() }}>
    <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
      {selections.length > 0 && <div className="space-y-6 px-1"><SheetHeader><SheetTitle>{selections.length > 1 ? "Evidence for this claim" : `${selections[0].series.entity ? `${selections[0].series.entity} ` : ""}${selections[0].series.label}`}</SheetTitle><SheetDescription>{selections.length > 1 ? `${selections.length} supporting observations` : `${fiscalLabel(selections[0].point)} · ${dateLabel(selections[0].point.date)}`}</SheetDescription></SheetHeader>
        {selections.map((item, index) => <div key={`${item.series.id}:${item.point.date}`}><EvidenceDetail selection={item} heading={selections.length > 1 ? `Source ${index + 1}` : undefined}/>{index < selections.length - 1 && <Separator className="mt-6"/>}</div>)}
      </div>}
    </SheetContent>
  </Sheet>
}
