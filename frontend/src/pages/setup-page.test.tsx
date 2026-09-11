import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

const { runtimeMock, providersMock, selectMock, connectMock, openMock, saveTiingoMock, saveFredMock, saveEdgarMock } = vi.hoisted(() => ({
  runtimeMock: vi.fn(), providersMock: vi.fn(), selectMock: vi.fn(), connectMock: vi.fn(), openMock: vi.fn(),
  saveTiingoMock: vi.fn(), saveFredMock: vi.fn(), saveEdgarMock: vi.fn(),
}))

vi.mock("@/api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    runtime: runtimeMock,
    aiProviders: providersMock,
    selectAIProvider: selectMock,
    aiConnect: connectMock,
    saveTiingo: saveTiingoMock, saveFred: saveFredMock, saveEdgarIdentity: saveEdgarMock,
    bootstrapData: vi.fn(), updateSettings: vi.fn(),
  },
}))
vi.mock("@/lib/open-external", () => ({ openExternalUrl: openMock }))

import { SetupPage } from "./setup-page"

const disconnected = {
  provider: "chatgpt_codex", connected: false, state: "SIGN_IN_REQUIRED",
  model: null, plan_type: null, installed: true, supported: true,
  message: "Sign in with ChatGPT to use AI analysis.",
}

const connected = { ...disconnected, connected: true, state: "CONNECTED", message: "Connected with ChatGPT" }
const source = (status: string, completed = 0, total = 50) => ({
  status, completed, total, last_attempted_refresh: null, last_successful_refresh: null, message: null,
})
const runtime = (sources: Record<string, ReturnType<typeof source>>, onboardingReady = false) => ({
  data: {
    tiingo_configured: true, fred_configured: true, sec_configured: true, busy: false,
    onboarding_ready: onboardingReady, sources,
  },
})

beforeEach(() => {
  vi.clearAllMocks()
  runtimeMock.mockResolvedValue({ data: { tiingo_configured: true, fred_configured: true, sec_configured: true, busy: false, sources: {} } })
  providersMock.mockResolvedValue([disconnected])
  selectMock.mockResolvedValue(disconnected)
  connectMock.mockResolvedValue({ provider: "chatgpt_codex", connected: false, auth_url: "https://auth.openai.com/test" })
  openMock.mockResolvedValue(undefined)
})

async function reachProviderStep() {
  const user = userEvent.setup()
  render(<MemoryRouter><SetupPage/></MemoryRouter>)
  expect(await screen.findByRole("heading", { name: "Welcome to Reasonframe" })).toBeInTheDocument()
  expect(screen.getByText("Financial research with specialist AI analysts.")).toBeInTheDocument()
  await user.click(await screen.findByRole("button", { name: "Continue" }))
  await screen.findByText("OpenAI Codex")
  return user
}

it("offers SDK browser sign-in for unauthenticated Codex and disables duplicate attempts", async () => {
  const user = await reachProviderStep()
  expect(screen.getByText("Not connected")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }))
  await waitFor(() => expect(selectMock).toHaveBeenCalledWith("chatgpt_codex"))
  expect(connectMock).toHaveBeenCalledOnce()
  expect(openMock).toHaveBeenCalledWith("https://auth.openai.com/test")
  expect(screen.getByRole("button", { name: /Waiting for sign-in/ })).toBeDisabled()
})

it("enables Continue immediately for an authenticated SDK account", async () => {
  providersMock.mockResolvedValue([connected])
  await reachProviderStep()
  expect(screen.getByText("Connected with ChatGPT")).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled()
})

async function reachInitializationStep() {
  providersMock.mockResolvedValue([connected])
  const user = await reachProviderStep()
  await user.click(screen.getByRole("button", { name: "Continue" }))
  expect(await screen.findByRole("heading", { name: "Initialize research data" })).toBeInTheDocument()
  return user
}

it("offers Initialize data before initialization has started and shows SEC timing guidance", async () => {
  runtimeMock.mockResolvedValue(runtime({
    sec: source("NOT_STARTED"), market: source("NOT_STARTED"), macro: source("NOT_STARTED", 0, 8),
  }))
  await reachInitializationStep()
  expect(screen.getByText("Initial SEC filing setup may take several minutes. Progress is saved as each company completes, so you can safely resume later.")).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Initialize data" })).toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "Resume initialization" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "Continue to summary" })).not.toBeInTheDocument()
})

it("offers Resume initialization for interrupted work", async () => {
  runtimeMock.mockResolvedValue(runtime({
    sec: source("PARTIALLY_READY", 12), market: source("NOT_STARTED"), macro: source("NOT_STARTED", 0, 8),
  }, true))
  await reachInitializationStep()
  expect(screen.getByRole("button", { name: "Resume initialization" })).toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "Initialize data" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "Continue to summary" })).not.toBeInTheDocument()
})

it("advances completed initialization to the Ready summary without offering Resume", async () => {
  runtimeMock.mockResolvedValue(runtime({
    sec: source("UP_TO_DATE", 50), market: source("UP_TO_DATE", 50), macro: source("UP_TO_DATE", 8, 8),
  }, true))
  const user = await reachInitializationStep()
  expect(screen.queryByRole("button", { name: "Resume initialization" })).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Continue to summary" }))
  expect(screen.getByRole("heading", { name: "Ready" })).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Open Reasonframe" })).toBeInTheDocument()
})

it("asks only for the user-supplied Tiingo token during first-run setup", async () => {
  runtimeMock.mockResolvedValue({ data: { tiingo_configured: false, fred_configured: false, sec_configured: false, busy: false, sources: {} } })
  const user = userEvent.setup()
  render(<MemoryRouter><SetupPage/></MemoryRouter>)
  await user.click(await screen.findByRole("button", { name: "Continue" }))
  expect(screen.getByLabelText("Tiingo token")).toBeInTheDocument()
  expect(screen.queryByLabelText("FRED API key")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("SEC fair-access identity")).not.toBeInTheDocument()
  await user.type(screen.getByLabelText("Tiingo token"), "tiingo-test")
  await user.click(screen.getByRole("button", { name: "Save and continue" }))
  await waitFor(() => expect(saveTiingoMock).toHaveBeenCalledWith("tiingo-test"))
  expect(saveFredMock).not.toHaveBeenCalled()
  expect(saveEdgarMock).not.toHaveBeenCalled()
})
