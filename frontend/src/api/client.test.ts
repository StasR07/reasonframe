import { afterEach, expect, it, vi } from "vitest"
import { api, ApiError } from "./client"

afterEach(() => vi.unstubAllGlobals())

it("parses successful API responses", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok" }), { status: 200, headers: { "Content-Type": "application/json" } })))
  await expect(api.health()).resolves.toEqual({ status: "ok" })
})

it("submits one question to the ask orchestration endpoint", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "unsupported", question: "q", reason_code: "OUT_OF_SCOPE", message: "No." }), { status: 200, headers: { "Content-Type": "application/json" } }))
  vi.stubGlobal("fetch", fetchMock)
  await api.ask("q")
  expect(fetchMock).toHaveBeenCalledWith("/api/v1/ask", expect.objectContaining({ method: "POST", body: JSON.stringify({ question: "q" }) }))
})

it("uses the ChatGPT connection endpoints", async () => {
  const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ provider: "chatgpt_codex", connected: false }), { status: 200, headers: { "Content-Type": "application/json" } }))
  vi.stubGlobal("fetch", fetchMock)
  await api.aiStatus()
  await api.aiConnect()
  await api.aiDisconnect()
  expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/v1/ai/status", expect.anything())
  expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/v1/ai/connect", expect.objectContaining({ method: "POST" }))
  expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/v1/ai/disconnect", expect.objectContaining({ method: "POST" }))
})

it("does not inject a forbidden operation into valuation queries", async () => {
  const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ status: "UNAVAILABLE", series: [], errors: [] }), { status: 200, headers: { "Content-Type": "application/json" } }))
  vi.stubGlobal("fetch", fetchMock)
  await api.query({ domain: "valuation", tickers: ["AAPL"], metric: "PE_RATIO", view: "latest" })
  const valuationBody = JSON.parse(fetchMock.mock.calls[0][1].body as string)
  expect(valuationBody).toEqual({ domain: "valuation", tickers: ["AAPL"], metric: "PE_RATIO", view: "latest" })
  expect(valuationBody).not.toHaveProperty("operation")

  await api.query({ domain: "company", tickers: ["AAPL"], metric: "REVENUE", frequency: "annual" })
  expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toHaveProperty("operation", "LEVEL")
})

it("maps network and validation failures to safe errors", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("secret stack")))
  await expect(api.catalog()).rejects.toEqual(new ApiError("Could not reach the finance data service."))
})
