import type { MarketSupport } from "@/api/types"

export function yearsAgo(years: number) {
  const value = new Date()
  value.setUTCFullYear(value.getUTCFullYear() - years)
  return value.toISOString().slice(0, 10)
}

export function marketRangeCovered(support: MarketSupport | undefined, years: number) {
  return Boolean(support?.first_date && support.first_date <= yearsAgo(years))
}

export function sharedMarketRangeCovered(tickers: string[], support: MarketSupport[], years: number) {
  return tickers.every((ticker) => marketRangeCovered(
    support.find((entry) => entry.ticker === ticker), years,
  ))
}
