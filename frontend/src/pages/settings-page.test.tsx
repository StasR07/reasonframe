import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"
import { ThemeProvider } from "@/components/theme-provider"

const { catalogMock, statusMock, modelsMock, providersMock, refreshProvidersMock, dataStatusMock } = vi.hoisted(() => ({ catalogMock: vi.fn(), statusMock: vi.fn(), modelsMock: vi.fn(), providersMock: vi.fn(), refreshProvidersMock: vi.fn(), dataStatusMock: vi.fn() }))
vi.mock("@/api/client", () => ({ api: {
  catalog: catalogMock, aiStatus: statusMock, aiModels: modelsMock,
  aiProviders: providersMock, refreshAIProviders: refreshProvidersMock,
  aiConnect: vi.fn(), aiDisconnect: vi.fn(), dataStatus: dataStatusMock,
  saveTiingo: vi.fn(), removeTiingo: vi.fn(), saveFred: vi.fn(), removeFred: vi.fn(),
  saveEdgarIdentity: vi.fn(), removeEdgarIdentity: vi.fn(), updateSettings: vi.fn(), refreshData: vi.fn(),
} }))

import { SettingsPage } from "./settings-page"

beforeEach(() => {
  catalogMock.mockResolvedValue({ provider_connected: true, market_support: [{ ticker: "AAPL", last_date: "2026-09-04" }] })
  const connected = { provider: "chatgpt_codex", connected: true, state: "CONNECTED", model: "terra", plan_type: "plus", installed: true, supported: true, message: "Connected with ChatGPT" }
  statusMock.mockResolvedValue(connected)
  providersMock.mockResolvedValue([connected])
  refreshProvidersMock.mockResolvedValue([connected])
  modelsMock.mockResolvedValue([{ id: "terra", name: "GPT-5.6-Terra", is_default: true }])
  const source = { status: "UP_TO_DATE", completed: 1, total: 1, last_attempted_refresh: null, last_successful_refresh: null, message: null }
  dataStatusMock.mockResolvedValue({
    tiingo_configured: true, fred_configured: true, sec_configured: true,
    busy: false, automatic_refresh: true, market_ready: 1, market_total: 1,
    latest_market_date: null, sources: { market: source, sec: source, macro: source },
  })
})

it("groups appearance, AI, data, and About settings with an explicit Theme label", async () => {
  render(<MemoryRouter><ThemeProvider><SettingsPage/></ThemeProvider></MemoryRouter>)
  expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument()
  expect(screen.getByRole("combobox", { name: "Theme" })).toHaveTextContent("System")
  expect(await screen.findByText("Connected with ChatGPT")).toBeInTheDocument()
  await userEvent.click(screen.getByRole("button", { name: "Retry detection" }))
  await waitFor(() => expect(refreshProvidersMock).toHaveBeenCalledOnce())
  expect(screen.getByText("Tiingo")).toBeInTheDocument()
  expect(await screen.findAllByText("Included with this release")).toHaveLength(2)
  expect(screen.getByText("Financial research with specialist AI analysts.")).toBeInTheDocument()
  expect(screen.getByText("Version 0.1.0-rc.1")).toBeInTheDocument()
  expect(screen.getByText(/Reasonframe combines company fundamentals/)).toBeInTheDocument()
  expect(screen.getByText(/Reasonframe does not provide investment advice/)).toBeInTheDocument()
})

it("shows the ChatGPT sign-in action without Codex CLI installation guidance", async () => {
  const disconnected = { provider: "chatgpt_codex", connected: false, state: "SIGN_IN_REQUIRED", model: null, plan_type: null, installed: true, supported: true, message: "Sign in with ChatGPT to use AI analysis." }
  statusMock.mockResolvedValue(disconnected)
  providersMock.mockResolvedValue([disconnected])
  render(<MemoryRouter><ThemeProvider><SettingsPage/></ThemeProvider></MemoryRouter>)
  expect(await screen.findByText("Not connected")).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Sign in with ChatGPT" })).toBeInTheDocument()
  expect(screen.queryByText(/install.*Codex CLI/i)).not.toBeInTheDocument()
})
