import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"
import type { AnalysisResponse } from "@/api/types"
import { sourceResponse } from "@/test/fixtures"
import { AnalystPanel } from "./analyst-panel"

describe("AnalystPanel evidence", () => {
  it("opens every valid source attached to a claim and hides invalid refs", async () => {
    const user = userEvent.setup()
    const first = sourceResponse.series[0].observations[0]
    const second = { ...first, date: "2024-09-28", fiscal_year: 2024, evidence: [{ ...first.evidence[0], id: "sec:2", accession_number: "0001-24", period_end: "2024-09-28" }] }
    const third = { ...first, date: "2023-09-30", fiscal_year: 2023, evidence: [{ ...first.evidence[0], id: "sec:3", accession_number: "0001-23", period_end: "2023-09-30" }] }
    const response: AnalysisResponse = {
      status: "success", mode: "focused", packet_version: 1, cache_key: "multi-source",
      interpretation: { observations: [{ text: "Revenue increased across the displayed interval.", evidence_refs: ["sec:1", "sec:2", "sec:3", "fabricated"] }], caveat: null },
      evidence_context: [{ ...sourceResponse.series[0], observations: [third, second, first] }],
    }
    render(<AnalystPanel title="Apple" subtitle="Revenue" open onOpenChange={() => {}} loading={false} result={response}/>)
    const sources = await screen.findByRole("button", { name: "3 evidence items" })
    await user.click(sources)
    expect(await screen.findByText("Evidence for this claim")).toBeInTheDocument()
    expect(screen.getByText("3 supporting observations")).toBeInTheDocument()
    expect(screen.getByText("0001-23")).toBeInTheDocument()
    expect(screen.getByText("0001-24")).toBeInTheDocument()
    expect(screen.getByText("0001-25")).toBeInTheDocument()
  })

  it("matches thin market observation and input refs without expanded evidence", async () => {
    const source = { ...sourceResponse.series[0].observations[0], evidence: [], observation_id: "market:source", input_observation_ids: [] }
    const calculated = { ...source, date: "2025-08-01", point_type: "CALCULATED" as const, observation_id: null, input_observation_ids: ["market:left", "market:right"] }
    const response: AnalysisResponse = {
      status: "success", mode: "focused", packet_version: 3, cache_key: "thin-market",
      interpretation: { observations: [{ text: "The displayed market history supports the observation.", evidence_refs: ["market:source", "market:left", "market:right", "fabricated"] }], caveat: null },
      evidence_context: [{ ...sourceResponse.series[0], metric: "RETURN", observations: [source, calculated] }],
    }
    render(<AnalystPanel title="Apple" subtitle="Market context" open onOpenChange={() => {}} loading={false} result={response}/>)
    expect(await screen.findByRole("button", { name: "3 evidence items" })).toBeInTheDocument()
  })

  it("shows the classified Overview failure instead of only a failed progress label", async () => {
    const response: AnalysisResponse = {
      status: "success", mode: "company", packet_version: 3, cache_key: "overview-timeout", ticker: "AAPL",
      fundamentals: null, valuation: null, skeptic: null, overview: null,
      failures: ["overview"], failure_details: { overview: "Analysis timed out. Financial data remains available." }, evidence_context: [],
    }
    render(<AnalystPanel title="Apple" subtitle="Company review" open onOpenChange={() => {}} loading={false} result={response} stages={{ fundamentals: "Complete", valuation: "Complete", skeptic: "Complete", overview: "Failed" }}/>)
    expect(await screen.findByText("Analysis timed out. Financial data remains available.")).toBeInTheDocument()
  })

  it("presents the overall evidence stance separately from confidence", async () => {
    const user = userEvent.setup()
    const grounded = { text: "The supported evidence is overall positive, tempered by valuation uncertainty.", evidence_refs: ["sec:1"] }
    const response: AnalysisResponse = {
      status: "success", mode: "company", packet_version: 3, cache_key: "overview-stance", ticker: "AAPL",
      fundamentals: null, valuation: null, skeptic: null,
      overview: { stance: "POSITIVE", confidence: "MODERATE", synthesis: grounded, key_conclusions: [grounded, grounded], key_risks: [], uncertainties: [] },
      failures: [], evidence_context: sourceResponse.series,
    }
    render(<AnalystPanel title="Apple" subtitle="Company review" open onOpenChange={() => {}} loading={false} result={response}/>)
    expect(screen.getByText("AI analysts")).toBeInTheDocument()
    expect(screen.getByText("Overall interpretation")).not.toHaveClass("uppercase", "tracking-wider")
    expect(screen.getByText(/Medium confidence/).parentElement).toHaveTextContent("Positive")
    expect(screen.getByText("Positive")).not.toHaveClass("bg-primary")
    expect(screen.getByText("Key conclusions")).toBeInTheDocument()
    const tabList = screen.getByTestId("company-analyst-tabs")
    expect(tabList).toHaveClass(
      "analyst-tabs",
      "flex",
      "justify-between",
      "gap-2",
      "[&_[role=tab]]:min-w-fit",
      "[&_[role=tab]]:text-[15px]",
      "[&_[role=tab]]:font-medium",
    )
    expect(tabList).toHaveAttribute("data-variant", "line")
    expect(tabList).toHaveClass("h-11")
    expect(tabList).not.toHaveClass("border-b")
    expect(screen.getAllByRole("tab")).toHaveLength(4)
    screen.getAllByRole("tab").forEach((tab) => expect(tab).toHaveStyle({ flex: "0 0 auto", minWidth: "max-content" }))
    await user.click(screen.getByRole("tab", { name: "Fundamentals" }))
    expect(screen.getByRole("tab", { name: "Fundamentals" })).toHaveAttribute("aria-selected", "true")
  })
})
