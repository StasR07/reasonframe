import type { Catalog, Frequency, MetricDefinition } from "@/api/types"

export function supportedFrequencies(catalog: Catalog, tickers: string[], metric: string): Frequency[] {
  return (["annual", "quarterly"] as Frequency[]).filter((frequency) => tickers.every((ticker) => catalog.company_metric_support.some((item) => item.ticker === ticker && item.metric === metric && item.frequency === frequency)))
}

export function supportedMetrics(catalog: Catalog, tickers: string[], frequency?: Frequency): MetricDefinition[] {
  return catalog.company_metric_definitions.filter((metric) => tickers.every((ticker) => catalog.company_metric_support.some((item) => item.ticker === ticker && item.metric === metric.code && (!frequency || item.frequency === frequency))))
}

export interface NormalizedCompanySelection {
  metric: string
  frequency: Frequency
}

export function normalizeCompanySelection(catalog: Catalog, tickers: string[], metric: string, frequency: Frequency): NormalizedCompanySelection {
  const metrics = supportedMetrics(catalog, tickers)
  const selectedMetric = metrics.some((item) => item.code === metric)
    ? metric
    : metrics.some((item) => item.code === "REVENUE") ? "REVENUE" : metrics[0]?.code ?? metric
  const frequencies = supportedFrequencies(catalog, tickers, selectedMetric)
  const selectedFrequency = frequencies.includes(frequency)
    ? frequency
    : frequencies.includes("annual") ? "annual" : frequencies[0] ?? frequency
  return { metric: selectedMetric, frequency: selectedFrequency }
}
