import { useState } from "react"
import type { KeyboardEvent } from "react"
import type { Catalog, Frequency, MetricDefinition } from "@/api/types"
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"

const categoryLabels = { income: "Income statement", cash_flow: "Cash flow", profitability: "Profitability", balance: "Balance sheet" }

export function CompanySelector({ catalog, value, onChange }: { catalog: Catalog; value: string; onChange: (value: string) => void }) {
  return <Select value={value} onValueChange={(next) => { if (next) onChange(next) }}>
    <SelectTrigger className="w-full sm:w-64" aria-label="Company"><SelectValue /></SelectTrigger>
    <SelectContent>{catalog.companies.map((company) => <SelectItem key={company.ticker} value={company.ticker}>
      <span className="font-medium">{company.ticker}</span><span className="text-muted-foreground"> · {company.name}</span>
    </SelectItem>)}</SelectContent>
  </Select>
}

export function matchingCompanies(catalog: Catalog, query: string, excluded: string[] = []) {
  const needle = query.trim().toLocaleLowerCase()
  if (!needle) return []
  const excludedSet = new Set(excluded)
  const aliases = catalog.company_aliases ?? {}
  return catalog.companies.filter((company) => {
    if (excludedSet.has(company.ticker)) return false
    const companyAliases = Object.entries(aliases).filter(([, ticker]) => ticker === company.ticker).map(([alias]) => alias)
    return [company.ticker, company.name, ...companyAliases].some((value) => value.toLocaleLowerCase().includes(needle))
  }).slice(0, 8)
}

export function CompanyAutocomplete({ catalog, onSelect, excluded = [], placeholder = "Search companies...", label = "Search companies", disabled = excluded.length >= 4 }: { catalog: Catalog; onSelect: (ticker: string) => void; excluded?: string[]; placeholder?: string; label?: string; disabled?: boolean }) {
  const [query, setQuery] = useState("")
  const [active, setActive] = useState(0)
  const [open, setOpen] = useState(false)
  const matches = matchingCompanies(catalog, query, excluded)
  const choose = (ticker: string) => { onSelect(ticker); setQuery(""); setOpen(false); setActive(0) }
  const keyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") { setOpen(false); return }
    if (!matches.length) return
    if (event.key === "ArrowDown") { event.preventDefault(); setOpen(true); setActive((value) => (value + 1) % matches.length) }
    if (event.key === "ArrowUp") { event.preventDefault(); setOpen(true); setActive((value) => (value - 1 + matches.length) % matches.length) }
    if (event.key === "Enter" && open) { event.preventDefault(); choose(matches[active]?.ticker ?? matches[0].ticker) }
  }
  return <div className="relative w-full sm:w-72"><input type="search" value={query} disabled={disabled} onChange={(event) => { setQuery(event.target.value); setOpen(Boolean(event.target.value.trim())); setActive(0) }} onFocus={() => setOpen(Boolean(query.trim()))} onKeyDown={keyDown} placeholder={placeholder} aria-label={label} aria-expanded={open} aria-controls="company-suggestions" aria-activedescendant={open && matches[active] ? `company-option-${matches[active].ticker}` : undefined} role="combobox" autoComplete="off" className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-60"/>
    {open && <div id="company-suggestions" role="listbox" className="absolute z-40 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border bg-popover p-1 shadow-lg">{matches.map((company, index) => <button id={`company-option-${company.ticker}`} role="option" aria-selected={index === active} type="button" key={company.ticker} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(company.ticker)} className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm ${index === active ? "bg-accent" : "hover:bg-accent"}`}><span className="font-semibold">{company.ticker}</span><span className="truncate text-muted-foreground">{company.name}</span></button>)}{matches.length === 0 && <p className="px-3 py-2 text-sm text-muted-foreground">No matching companies.</p>}</div>}
  </div>
}

export function MetricSelector({ metrics, value, onChange }: { metrics: MetricDefinition[]; value: string; onChange: (value: string) => void }) {
  const categories = Object.keys(categoryLabels) as Array<keyof typeof categoryLabels>
  const selected = metrics.find((metric) => metric.code === value)
  return <Select value={metrics.length ? value : ""} disabled={!metrics.length} onValueChange={(next) => { if (next) onChange(next) }}>
    <SelectTrigger className="w-full sm:w-72" aria-label="Metric"><SelectValue>{selected?.label ?? "Financial metrics unavailable"}</SelectValue></SelectTrigger>
    <SelectContent align="start" alignItemWithTrigger={false} sideOffset={6} collisionPadding={12} style={{ maxHeight: "min(20rem, var(--available-height))" }} className="min-w-72">
      {categories.map((category) => {
        const grouped = metrics.filter((metric) => metric.category === category)
        return grouped.length ? <SelectGroup key={category}><SelectLabel className="px-2 pt-2 pb-1 font-semibold uppercase tracking-[0.11em]">{categoryLabels[category]}</SelectLabel>{grouped.map((metric) => <SelectItem className="px-2 py-1.5 pr-9" key={metric.code} value={metric.code}>{metric.label}</SelectItem>)}</SelectGroup> : null
      })}
    </SelectContent>
  </Select>
}

export function FrequencyToggle({ value, supported, onChange }: { value: Frequency; supported: Frequency[]; onChange: (value: Frequency) => void }) {
  return <Tabs value={value} onValueChange={(next) => onChange(next as Frequency)}>
    <TabsList>{supported.includes("annual") && <TabsTrigger value="annual">Annual</TabsTrigger>}
      {supported.includes("quarterly") && <TabsTrigger value="quarterly">Quarterly</TabsTrigger>}</TabsList>
  </Tabs>
}
