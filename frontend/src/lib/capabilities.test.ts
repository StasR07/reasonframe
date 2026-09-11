import { expect, it } from "vitest"
import { catalog } from "@/test/fixtures"
import { normalizeCompanySelection, supportedFrequencies, supportedMetrics } from "./capabilities"

it("intersects metric and frequency capabilities across companies", () => {
  expect(supportedFrequencies(catalog, ["AAPL"], "OPERATING_MARGIN")).toEqual(["annual"])
  expect(supportedMetrics(catalog, ["AAPL", "MSFT"], "quarterly").map((item) => item.code)).toEqual(["REVENUE"])
})

it("preserves supported selections and falls back to Revenue with a valid frequency", () => {
  expect(normalizeCompanySelection(catalog, ["AAPL"], "OPERATING_MARGIN", "annual")).toEqual({ metric: "OPERATING_MARGIN", frequency: "annual" })
  expect(normalizeCompanySelection(catalog, ["MSFT"], "OPERATING_MARGIN", "quarterly")).toEqual({ metric: "REVENUE", frequency: "quarterly" })
})
