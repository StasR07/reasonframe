import type { SeriesPoint } from "@/api/types"

export type FormatMode = "compact" | "exact"

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 })
const exact = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 })

export function formatValue(value: string | number, unit: string, metric = "", mode: FormatMode = "compact") {
  const number = typeof value === "number" ? value : Number(value)
  if (!Number.isFinite(number)) return "Unavailable"
  const formatter = mode === "compact" ? compact : exact
  if (unit.toLowerCase() === "ratio") {
    if (metric.includes("MARGIN") || metric.includes("GROWTH")) return `${(number * 100).toFixed(mode === "compact" ? 1 : 2)}%`
    return `${number.toFixed(2)}x`
  }
  if (unit.toLowerCase() === "percent") return `${number.toFixed(mode === "compact" ? 1 : 2)}%`
  if (unit.toLowerCase() === "return" || unit.toLowerCase() === "yield") return `${(number * 100).toFixed(mode === "compact" ? 1 : 2)}%`
  if (unit.toLowerCase() === "multiple") return `${number.toFixed(2)}x`
  if (unit.toLowerCase() === "index") return number.toFixed(1)
  if (unit.toLowerCase().includes("share") && metric.includes("EPS")) return `$${number.toFixed(2)}`
  if (unit.toLowerCase().includes("share")) return `${formatter.format(number)} shares`
  if (unit.toUpperCase() === "USD" || unit.toLowerCase() === "currency") return `$${formatter.format(number)}`
  return formatter.format(number)
}

export function fiscalLabel(point: Pick<SeriesPoint, "fiscal_year" | "fiscal_period" | "date">) {
  if (point.fiscal_year && point.fiscal_period) {
    return point.fiscal_period === "FY" ? `FY${point.fiscal_year}` : `${point.fiscal_period} FY${point.fiscal_year}`
  }
  return new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(`${point.date}T00:00:00Z`))
}

export function dateLabel(value: string) {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`))
}
