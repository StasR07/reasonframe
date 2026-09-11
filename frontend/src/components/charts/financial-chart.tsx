import { useMemo, useState } from "react"
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts"
import type { SeriesPoint, SeriesResult } from "@/api/types"
import { ChartContainer, ChartTooltip, type ChartConfig } from "@/components/ui/chart"
import { dateLabel, fiscalLabel, formatValue } from "@/lib/format"
import { cn } from "@/lib/utils"

const colors = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)"]

export function permanentDotsVisible(series: SeriesResult[]) {
  return series.some((item) => item.frequency === "quarterly") || (series.length === 1 && (series[0]?.observations.length ?? 0) <= 16)
}

export function chartYAxisDomain(series: SeriesResult[]): [number | "auto", number | "auto"] {
  const metric = series[0]?.metric ?? ""
  const values = series.flatMap((item) => item.observations.map((point) => Number(point.value))).filter(Number.isFinite)
  if (!values.length) return ["auto", "auto"]
  const min = Math.min(...values); const max = Math.max(...values)
  const padded: [number, number] = min === max
    ? [min - Math.max(Math.abs(min) * 0.05, 1), max + Math.max(Math.abs(max) * 0.05, 1)]
    : [min - (max - min) * 0.08, max + (max - min) * 0.08]
  if (["RAW_CLOSE", "ADJUSTED_CLOSE", "PE_RATIO", "PS_RATIO", "P_FCF_RATIO", "INDEXED", "RETURN", "MAX_DRAWDOWN", "FCF_YIELD"].includes(metric) || metric.includes("MARGIN") || metric.includes("GROWTH")) return padded
  return min >= 0 ? [0, padded[1]] : padded
}

export function chartPointLabels(point: Pick<SeriesPoint, "fiscal_year" | "fiscal_period" | "date">, frequency: string) {
  return {
    axis: fiscalLabel(point),
    tooltip: frequency === "daily" ? dateLabel(point.date) : fiscalLabel(point),
  }
}

export const chartRowKey = (point: Pick<SeriesPoint, "fiscal_year" | "fiscal_period" | "date">) =>
  point.fiscal_year && point.fiscal_period ? `${point.fiscal_year}-${point.fiscal_period}` : point.date

export function FinancialChart({ series, onPointClick }: { series: SeriesResult[]; onPointClick?: (series: SeriesResult, index: number) => void }) {
  const seriesKey = series.map((item) => item.id).join("|")
  const [visibility, setVisibility] = useState<{ key: string; hidden: Set<string> }>(() => ({ key: seriesKey, hidden: new Set() }))
  const hidden = visibility.key === seriesKey ? visibility.hidden : new Set<string>()

  const data = useMemo(() => {
    const rows = new Map<string, Record<string, string | number | null>>()
    series.forEach((item) => item.observations.forEach((point, pointIndex) => {
      const key = chartRowKey(point)
      const labels = chartPointLabels(point, item.frequency)
      const row = rows.get(key) ?? { key, label: labels.axis, tooltipLabel: labels.tooltip, date: point.date }
      row[item.id] = Number(point.value); row[`${item.id}:index`] = pointIndex
      rows.set(key, row)
    }))
    return [...rows.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)))
  }, [series])
  const seriesName = (item: SeriesResult) => series.length > 1 ? item.entity ?? item.label : item.label
  const config: ChartConfig = Object.fromEntries(series.map((item, index) => [item.id, { label: seriesName(item), color: colors[index] }]))
  const labels = new Map(data.map((row) => [String(row.key), String(row.label)]))
  const unit = series[0]?.unit ?? "number"
  const metric = series[0]?.metric ?? ""
  const visibleCount = series.length - hidden.size
  const toggleSeries = (id: string) => setVisibility(() => {
    if (!hidden.has(id) && visibleCount === 1) return { key: seriesKey, hidden }
    const next = new Set(hidden)
    if (next.has(id)) next.delete(id); else next.add(id)
    return { key: seriesKey, hidden: next }
  })

  return <div className="space-y-3">
    {series.length > 1 && <div className="flex flex-wrap justify-center gap-2" aria-label="Chart series visibility">{series.map((item, index) => {
      const visible = !hidden.has(item.id)
      return <button key={item.id} type="button" aria-pressed={visible} aria-label={`${visible ? "Hide" : "Show"} ${item.entity ?? item.label}`} onClick={() => toggleSeries(item.id)}
        className={cn("inline-flex items-center gap-1.5 rounded-md border bg-background px-2 py-1 text-xs text-foreground transition-opacity", !visible && "opacity-45 line-through")}>
        <span className="size-2 rounded-sm" style={{ backgroundColor: colors[index] }}/>{item.entity ?? item.label}
      </button>
    })}</div>}
    <ChartContainer config={config} className="h-[320px] w-full aspect-auto sm:h-[390px]">
      <LineChart data={data} margin={{ top: 12, right: 18, left: 8, bottom: 8 }} accessibilityLayer onClick={(state) => {
        const rowIndex = Number(state?.activeTooltipIndex)
        const row = Number.isInteger(rowIndex) ? data[rowIndex] : undefined
        if (!onPointClick || !row) return
        const item = series.find((candidate) => row[candidate.id] != null && !hidden.has(candidate.id))
        const pointIndex = item ? Number(row[`${item.id}:index`]) : NaN
        if (item && Number.isInteger(pointIndex)) onPointClick(item, pointIndex)
      }}>
        <CartesianGrid vertical={false} stroke="var(--border)" strokeOpacity={0.65} strokeDasharray="3 3" />
        <XAxis dataKey="key" tickFormatter={(value) => labels.get(String(value)) ?? String(value)} tickLine={false} axisLine={false} minTickGap={24} />
        <YAxis domain={chartYAxisDomain(series)} tickLine={false} axisLine={false} width={72} tickFormatter={(value) => formatValue(value, unit, metric)} />
        <ChartTooltip cursor={{ stroke: "var(--border)", strokeDasharray: "3 3" }} content={({ active, payload, label }) => active && payload?.length ? <div className="min-w-48 rounded-lg border bg-popover p-3 text-xs text-popover-foreground shadow-lg">
          <p className="mb-2 font-medium">{String((payload[0]?.payload as { tooltipLabel?: string } | undefined)?.tooltipLabel ?? label)}</p>{payload.filter((entry) => entry.value != null).map((entry) => <div key={String(entry.dataKey)} className="flex items-center justify-between gap-5 py-1"><span style={{ color: entry.color }}>{entry.name}</span><span className="font-mono font-semibold tabular-nums">{formatValue(Number(entry.value), unit, metric, "exact")}</span></div>)}</div> : null} />
        {series.map((item, index) => <Line key={item.id} type="linear" dataKey={item.id} name={seriesName(item)} stroke={colors[index]} strokeWidth={2} connectNulls={false} isAnimationActive={false} hide={hidden.has(item.id)}
          dot={permanentDotsVisible(series) ? { r: 3, fill: colors[index], strokeWidth: 0 } : false} activeDot={(props) => {
            const row = props.payload as Record<string, number>
            return <circle cx={props.cx} cy={props.cy} r={5} fill={colors[index]} className={onPointClick ? "cursor-pointer" : undefined} onClick={onPointClick ? () => onPointClick(item, row[`${item.id}:index`]) : undefined} />
          }} />)}
      </LineChart>
    </ChartContainer>
  </div>
}
