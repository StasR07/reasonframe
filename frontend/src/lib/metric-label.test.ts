import { describe, expect, it } from "vitest"
import type { Catalog } from "@/api/types"
import { catalog as baseCatalog } from "@/test/fixtures"
import { getMetricLabel } from "./metric-label"

describe("getMetricLabel", () => {
  const catalog: Catalog = {
    ...baseCatalog,
    company_metric_definitions: [
      ...baseCatalog.company_metric_definitions,
      { code: "COST_OF_REVENUE", label: "Cost of revenue", category: "income", unit_kind: "currency", kind: "direct" },
      { code: "OPERATING_CASH_FLOW", label: "Operating cash flow", category: "cash_flow", unit_kind: "currency", kind: "direct" },
      { code: "FREE_CASH_FLOW", label: "Free cash flow", category: "cash_flow", unit_kind: "currency", kind: "derived" },
    ],
  }

  it("uses catalog labels for canonical company metrics", () => {
    expect(getMetricLabel("COST_OF_REVENUE", catalog)).toBe("Cost of revenue")
    expect(getMetricLabel("OPERATING_CASH_FLOW", catalog)).toBe("Operating cash flow")
    expect(getMetricLabel("FREE_CASH_FLOW", catalog)).toBe("Free cash flow")
  })

  it("humanizes a canonical code only when catalog metadata is unavailable", () => {
    expect(getMetricLabel("OTHER_METRIC", catalog)).toBe("Other metric")
    expect(getMetricLabel("Already formatted", catalog)).toBe("Already formatted")
  })
})
