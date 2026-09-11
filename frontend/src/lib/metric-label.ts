import type { Catalog } from "@/api/types"

const humanizeMetricCode = (metricCode: string) =>
  metricCode.replaceAll("_", " ").toLocaleLowerCase().replace(/^./, (letter) => letter.toLocaleUpperCase())

export function getMetricLabel(metricCode: string, catalog?: Catalog | null): string {
  const definitions = [
    ...(catalog?.company_metric_definitions ?? []),
    ...(catalog?.market_metric_definitions ?? []),
    ...(catalog?.valuation_metric_definitions ?? []),
    ...(catalog?.macro_series ?? []),
  ]
  const definition = definitions.find((item) => item.code === metricCode)
  if (definition) return definition.label
  return /^[A-Z0-9_]+$/.test(metricCode) ? humanizeMetricCode(metricCode) : metricCode
}
