import { useEffect, useState } from "react"
import { ChevronLeft, Workflow, X } from "lucide-react"
import type { AnalysisResponse, AnalystAssessment, AnalystClaim, CompanyStage, CompanyStageState, SeriesResult, SkepticReview } from "@/api/types"
import { EvidenceSheet, type EvidenceSelection, type EvidenceSheetSelection } from "@/components/finance/evidence-sheet"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

function evidenceSelections(series: SeriesResult[], refs: string[]): { selections: EvidenceSelection[]; validCount: number } {
  const wanted = new Set(refs)
  const found = new Set<string>()
  const selections: EvidenceSelection[] = []
  for (const item of series) for (const point of item.observations) {
    const pointRefs = [...point.evidence.map((evidence) => evidence.id), ...(point.observation_id ? [point.observation_id] : []), ...point.input_observation_ids]
    const matches = pointRefs.filter((reference) => wanted.has(reference))
    if (!matches.length) continue
    matches.forEach((reference) => found.add(reference))
    selections.push({ series: item, point })
  }
  for (const item of series) {
    if (item.observations.length || !Array.isArray(item.context?.evidence_refs)) continue
    const refs = item.context.evidence_refs.filter((reference): reference is string => typeof reference === "string")
    const matches = refs.filter((reference) => wanted.has(reference) && !found.has(reference))
    if (!matches.length) continue
    matches.forEach((reference) => found.add(reference))
    selections.push({
      series: item,
      point: {
        date: typeof item.context?.price_date === "string" ? item.context.price_date : "",
        value: "0", point_type: "CALCULATED", observation_id: null, formula: null,
        input_observation_ids: refs, evidence: [], fiscal_year: null, fiscal_period: null,
        state: item.state,
      },
    })
  }
  return { selections, validCount: found.size }
}

function Claim({ claim, series, onEvidence }: { claim: AnalystClaim; series: SeriesResult[]; onEvidence: (selection: EvidenceSheetSelection) => void }) {
  const evidence = evidenceSelections(series, claim.evidence_refs)
  return <div className="border-l-2 border-border pl-3"><p className="leading-relaxed">{claim.text}</p>{evidence.validCount > 0 && <Button className="mt-2 px-0" variant="link" size="sm" onClick={() => onEvidence(evidence.selections)}>{evidence.validCount} evidence {evidence.validCount === 1 ? "item" : "items"}</Button>}</div>
}

const humanize = (value: string) => value.replaceAll("_", " ").toLocaleLowerCase().replace(/^./, (letter) => letter.toLocaleUpperCase())

function Verdict({ stance, confidence }: { stance: string; confidence: string }) {
  const confidenceLabel = confidence === "MODERATE" ? "Medium" : humanize(confidence)
  const stanceStyle = stance === "NEGATIVE"
    ? "bg-destructive/10 text-destructive dark:bg-destructive/15"
    : stance === "POSITIVE"
      ? "bg-emerald-600/10 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300"
      : "bg-secondary text-secondary-foreground"
  return <div className="flex gap-2"><Badge className={stanceStyle}>{humanize(stance)}</Badge><Badge variant="outline">{confidenceLabel} confidence</Badge></div>
}

function overviewPresentation(value: import("@/api/types").OverviewOutput) {
  const legacy = value as typeof value & { conclusion?: AnalystClaim; summary?: AnalystClaim; strongest_evidence?: AnalystClaim[]; key_tension?: AnalystClaim | null; skeptic_challenge?: AnalystClaim | null }
  return {
    synthesis: value.synthesis ?? legacy.conclusion!,
    conclusions: value.key_conclusions ?? [legacy.summary, ...(legacy.strongest_evidence ?? []), legacy.key_tension].filter((item): item is AnalystClaim => Boolean(item)).slice(0, 4),
    risks: value.key_risks ?? [legacy.skeptic_challenge].filter((item): item is AnalystClaim => Boolean(item)),
  }
}

function Assessment({ label, value, series, onEvidence, failure }: { label: string; value: AnalystAssessment | null; series: SeriesResult[]; onEvidence: (selection: EvidenceSheetSelection) => void; failure?: string }) {
  if (!value) return <Alert><AlertDescription>{failure ?? "This perspective could not be completed. Financial evidence remains available."}</AlertDescription></Alert>
  return <div className="space-y-4"><p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{label} assessment</p><Verdict stance={value.stance} confidence={value.confidence}/>
    <section><h3 className="mb-2 text-sm font-semibold">Thesis</h3><Claim claim={value.thesis} series={series} onEvidence={onEvidence}/></section>
    <section><h3 className="mb-2 text-sm font-semibold">Supporting evidence</h3><div className="space-y-2">{value.supporting_claims.map((claim, index) => <Claim key={index} claim={claim} series={series} onEvidence={onEvidence}/>)}</div></section>
    {value.uncertainties.length > 0 && <section><h3 className="mb-2 text-sm font-semibold">Uncertainties</h3><ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">{value.uncertainties.map((item) => <li key={item}>{item}</li>)}</ul></section>}
  </div>
}

function Skeptic({ value, series, onEvidence, failure }: { value: SkepticReview | null; series: SeriesResult[]; onEvidence: (selection: EvidenceSheetSelection) => void; failure?: string }) {
  if (!value) return <Alert><AlertDescription>{failure ?? "The Skeptic review has not completed."}</AlertDescription></Alert>
  return <div className="space-y-4">{value.challenges.length === 0 && <p className="text-sm text-muted-foreground">No material evidence-grounded challenge was identified.</p>}{value.challenges.map((challenge, index) => <div key={index}><p className="mb-1 text-xs font-medium text-muted-foreground">{humanize(challenge.target)} · {humanize(challenge.issue_type)}</p><Claim claim={challenge} series={series} onEvidence={onEvidence}/></div>)}{value.missing_evidence.length > 0 && <section><h3 className="text-sm font-semibold">Evidence limitations</h3><ul className="list-disc pl-5 text-sm text-muted-foreground">{value.missing_evidence.map((item) => <li key={item}>{item}</li>)}</ul></section>}</div>
}

interface AnalystPanelProps {
  title: string
  subtitle: string
  open: boolean
  onOpenChange: (open: boolean) => void
  loading: boolean
  result: AnalysisResponse | null
  actionLabel?: string
  onRun?: () => void
  idleMessage?: string
  stages?: Record<CompanyStage, CompanyStageState>
}

function AnalysisContent({ title, subtitle, loading, result, actionLabel, onRun, idleMessage, onEvidence, stages }: AnalystPanelProps & { onEvidence: (selection: EvidenceSheetSelection) => void }) {
  const series = result?.status === "success" ? result.evidence_context : []
  const company = result?.status === "success" && result.mode === "company" ? result : null
  const focused = result?.status === "success" && result.mode === "focused" ? result : null
  const comparison = result?.status === "success" && result.mode === "comparison" ? result : null
  const [tab, setTab] = useState("overview")
  const progress = stages ?? { fundamentals: "Waiting", valuation: "Waiting", skeptic: "Waiting", overview: "Waiting" }
  const presentedOverview = company?.overview ? overviewPresentation(company.overview) : null
  return <div className="space-y-5"><div><div className="mb-2 flex items-center gap-2 text-muted-foreground"><Workflow className="size-4"/><span className="text-sm font-medium">AI analysts</span></div><h2 className="text-lg font-semibold">{title}</h2><p className="text-sm text-muted-foreground">{subtitle}</p></div>
    {loading && !stages && <div className="space-y-3 rounded-lg border p-5 text-sm"><p className="font-medium">Preparing financial evidence</p><p className="text-muted-foreground">Running contextual interpretation</p></div>}
    {!loading && !result && <div className="space-y-4"><p className="text-sm leading-relaxed text-muted-foreground">{idleMessage ?? "Analyze the supported financial evidence on demand."}</p>{actionLabel && onRun && <Button className="w-full" onClick={onRun}>{actionLabel}</Button>}</div>}
    {result?.status === "error" && <Alert><AlertDescription>{result.message}</AlertDescription>{onRun && <Button className="mt-3" variant="outline" size="sm" onClick={onRun}>Retry</Button>}</Alert>}
    {focused && <div className="space-y-3">{focused.interpretation.observations.map((claim, index) => <Claim key={index} claim={claim} series={series} onEvidence={onEvidence}/>)}{focused.interpretation.caveat && <p className="text-sm text-muted-foreground">{focused.interpretation.caveat}</p>}</div>}
    {comparison && <div className="space-y-5">{comparison.interpretation.confidence && <Badge variant="outline">{comparison.interpretation.confidence === "MODERATE" ? "Medium" : humanize(comparison.interpretation.confidence)} confidence</Badge>}<section><h3 className="mb-2 text-sm font-semibold">Most important conclusion</h3><Claim claim={comparison.interpretation.summary} series={series} onEvidence={onEvidence}/></section>{[
      ["Fundamental differences", comparison.interpretation.fundamentals_comparison],
      ["Valuation differences", comparison.interpretation.valuation_comparison],
      ["Market context", comparison.interpretation.market_context],
      ["Key trade-offs", comparison.interpretation.key_tradeoffs],
    ].map(([label, claims]) => (claims as AnalystClaim[]).length > 0 && <section key={label as string}><h3 className="mb-2 text-sm font-semibold">{label as string}</h3><div className="space-y-2">{(claims as AnalystClaim[]).map((claim, index) => <Claim key={index} claim={claim} series={series} onEvidence={onEvidence}/>)}</div></section>)}{comparison.interpretation.limitations.length > 0 && <section><h3 className="mb-1 text-sm font-semibold">Limitations</h3><ul className="list-disc pl-5 text-sm text-muted-foreground">{comparison.interpretation.limitations.map((item) => <li key={item}>{item}</li>)}</ul></section>}</div>}
    {(company || stages) && <Tabs value={tab} onValueChange={setTab} className="pt-1"><TabsList data-testid="company-analyst-tabs" variant="line" className="analyst-tabs mb-7 flex h-11 w-full items-center justify-between gap-2 p-0 [&_[role=tab]]:h-9 [&_[role=tab]]:min-w-fit [&_[role=tab]]:rounded-md [&_[role=tab]]:px-1 [&_[role=tab]]:text-[15px] [&_[role=tab]]:font-medium [&_[role=tab]]:tracking-tight"><TabsTrigger style={{ flex: "0 0 auto", minWidth: "max-content" }} value="overview">Overview</TabsTrigger><TabsTrigger style={{ flex: "0 0 auto", minWidth: "max-content" }} value="fundamentals">Fundamentals</TabsTrigger><TabsTrigger style={{ flex: "0 0 auto", minWidth: "max-content" }} value="valuation">Valuation</TabsTrigger><TabsTrigger style={{ flex: "0 0 auto", minWidth: "max-content" }} value="skeptic">Skeptic</TabsTrigger></TabsList>
      <TabsContent value="overview">{company?.overview && presentedOverview ? <div className="space-y-5"><section className="space-y-3 rounded-lg border bg-muted/30 p-4"><p className="text-sm font-semibold text-muted-foreground">Overall interpretation</p><Verdict stance={company.overview.stance} confidence={company.overview.confidence}/><Claim claim={presentedOverview.synthesis} series={series} onEvidence={onEvidence}/></section><section><h3 className="mb-2 text-sm font-semibold">Key conclusions</h3><div className="space-y-2">{presentedOverview.conclusions.map((claim, index) => <Claim key={index} claim={claim} series={series} onEvidence={onEvidence}/>)}</div></section>{presentedOverview.risks.length > 0 && <section><h3 className="mb-2 text-sm font-semibold">Key risks / counterpoints</h3><div className="space-y-2">{presentedOverview.risks.map((claim, index) => <Claim key={index} claim={claim} series={series} onEvidence={onEvidence}/>)}</div></section>}{company.overview.uncertainties.length > 0 && <section><h3 className="text-sm font-semibold">Uncertainties</h3><ul className="list-disc pl-5 text-sm text-muted-foreground">{company.overview.uncertainties.map((item) => <li key={item}>{item}</li>)}</ul></section>}</div> : <div className="space-y-3"><div className="space-y-2 rounded-lg border p-4">{(["fundamentals", "valuation", "skeptic", "overview"] as CompanyStage[]).map((stage) => <div className="flex justify-between text-sm" key={stage}><span className="capitalize">{stage}</span><span className="text-muted-foreground">{progress[stage]}</span></div>)}</div>{company?.failure_details?.overview && <Alert><AlertDescription>{company.failure_details.overview}</AlertDescription></Alert>}</div>}</TabsContent>
      <TabsContent value="fundamentals"><Assessment label="Fundamentals" value={company?.fundamentals ?? null} series={series} onEvidence={onEvidence} failure={company?.failure_details?.fundamentals}/></TabsContent><TabsContent value="valuation"><Assessment label="Valuation" value={company?.valuation ?? null} series={series} onEvidence={onEvidence} failure={company?.failure_details?.valuation}/></TabsContent><TabsContent value="skeptic"><Skeptic value={company?.skeptic ?? null} series={series} onEvidence={onEvidence} failure={company?.failure_details?.skeptic}/></TabsContent>
    </Tabs>}
    {company && company.failures.length > 0 && onRun && <Button variant="outline" className="w-full" onClick={onRun}>Retry analysis</Button>}
  </div>
}

export function AnalystPanel(props: AnalystPanelProps) {
  const [evidence, setEvidence] = useState<EvidenceSheetSelection | null>(null)
  const [desktop, setDesktop] = useState(() => typeof window !== "undefined" && window.innerWidth >= 1280)
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return
    const media = window.matchMedia("(min-width: 1280px)")
    const update = () => setDesktop(media.matches)
    update(); media.addEventListener("change", update)
    return () => media.removeEventListener("change", update)
  }, [])
  const content = <AnalysisContent {...props} onEvidence={setEvidence}/>
  return <>{desktop ? props.open ? <aside data-testid="analysis-rail" className="relative w-96 shrink-0 rounded-xl border bg-background p-5 shadow-sm"><Button aria-label="Collapse AI Analysis" className="absolute right-3 top-3" variant="ghost" size="icon" onClick={() => props.onOpenChange(false)}><X/></Button>{content}</aside> : <Button className="sticky top-8 shrink-0" variant="outline" onClick={() => props.onOpenChange(true)}><ChevronLeft/>AI Analysis</Button> : <><Button className="w-full" variant="outline" onClick={() => props.onOpenChange(true)}><Workflow/>AI Analysis</Button><Sheet open={props.open} onOpenChange={props.onOpenChange}><SheetContent className="w-full overflow-y-auto sm:max-w-lg"><SheetHeader className="sr-only"><SheetTitle>AI Analysis</SheetTitle><SheetDescription>Contextual financial analysis</SheetDescription></SheetHeader><div className="px-1 pb-8">{content}</div></SheetContent></Sheet></>}
    <EvidenceSheet selection={evidence} onClose={() => setEvidence(null)}/></>
}
