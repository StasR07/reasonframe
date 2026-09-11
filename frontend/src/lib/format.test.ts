import { describe, expect, it } from "vitest"
import { fiscalLabel, formatValue } from "./format"

describe("financial formatting", () => {
  it("formats money, percentages, ratios and EPS", () => {
    expect(formatValue("416161000000", "USD")).toBe("$416.2B")
    expect(formatValue("0.316", "ratio", "OPERATING_MARGIN")).toBe("31.6%")
    expect(formatValue("1.342", "ratio", "CURRENT_RATIO")).toBe("1.34x")
    expect(formatValue("7.25", "currency_per_share", "DILUTED_EPS")).toBe("$7.25")
    expect(formatValue("31.25", "multiple", "PE_RATIO")).toBe("31.25x")
    expect(formatValue("0.034", "yield", "FCF_YIELD")).toBe("3.4%")
    expect(formatValue("0.421", "return", "RETURN")).toBe("42.1%")
    expect(formatValue("100", "index", "INDEXED")).toBe("100.0")
  })
  it("preserves fiscal semantics", () => expect(fiscalLabel({ fiscal_year: 2026, fiscal_period: "Q1", date: "2025-12-27" })).toBe("Q1 FY2026"))
})
