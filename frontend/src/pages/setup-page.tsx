import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { CheckCircle2, Loader2 } from "lucide-react"
import { api, ApiError } from "@/api/client"
import type { AIAccountStatus, RuntimeStatus } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { openExternalUrl } from "@/lib/open-external"
import { ReasonframeMark } from "@/components/brand/reasonframe-mark"

const sourceLabel = { sec: "SEC filings", market: "Tiingo market data", macro: "FRED macro data" } as const
const requiredSources = ["sec", "market", "macro"] as const

export function SetupPage() {
  const navigate = useNavigate()
  const [step, setStep] = useState(1)
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null)
  const [providers, setProviders] = useState<AIAccountStatus[]>([])
  const [token, setToken] = useState("")
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [loginProvider, setLoginProvider] = useState<string | null>(null)
  const loginPoll = useRef<number | null>(null)
  const loginPollAttempts = useRef(0)
  const load = useCallback(async () => {
    try { setRuntime(await api.runtime()) } catch { setMessage("The local research service is unavailable. Retry after checking diagnostics.") }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => () => { if (loginPoll.current !== null) window.clearInterval(loginPoll.current) }, [])
  useEffect(() => {
    if (step !== 4 || !runtime?.data.busy) return
    const timer = window.setInterval(() => { void load() }, 1200)
    return () => window.clearInterval(timer)
  }, [load, runtime?.data.busy, step])
  const saveDataSources = async () => {
    setBusy(true); setMessage(null)
    try {
      if (!runtime?.data.tiingo_configured) await api.saveTiingo(token)
      setToken(""); await load(); setStep(3)
    }
    catch (reason) { setMessage(reason instanceof ApiError ? reason.message : "Data-source credentials could not be saved.") }
    finally { setBusy(false) }
  }
  const loadProviders = useCallback(async () => {
    setBusy(true); setMessage(null)
    try { setProviders(await api.aiProviders()); setStep(3) } catch { setMessage("AI provider status could not be loaded. Retry detection.") }
    finally { setBusy(false) }
  }, [])
  useEffect(() => { if (step === 3) void loadProviders() }, [loadProviders, step])
  const chooseProvider = async (provider: AIAccountStatus) => {
    if (provider.supported === false) return
    setBusy(true); setMessage(null)
    try {
      await api.selectAIProvider(provider.provider)
      if (provider.connected) { setProviders(await api.aiProviders()); return }
      const result = await api.aiConnect()
      if (result.auth_url) await openExternalUrl(result.auth_url)
      if (result.connected) { setProviders(await api.aiProviders()); return }
      setLoginProvider(provider.provider)
      loginPollAttempts.current = 0
      if (loginPoll.current !== null) window.clearInterval(loginPoll.current)
      loginPoll.current = window.setInterval(() => {
        loginPollAttempts.current += 1
        void api.aiProviders().then((detected) => {
          setProviders(detected)
          if (detected.find((item) => item.provider === provider.provider)?.connected) {
            if (loginPoll.current !== null) window.clearInterval(loginPoll.current)
            loginPoll.current = null; setLoginProvider(null); setMessage(null)
          } else if (loginPollAttempts.current >= 120) {
            if (loginPoll.current !== null) window.clearInterval(loginPoll.current)
            loginPoll.current = null; setLoginProvider(null)
            setMessage("ChatGPT sign-in did not complete. Try again.")
          }
        }).catch(() => undefined)
      }, 1500)
    } catch { setLoginProvider(null); setMessage(provider.provider === "chatgpt_codex" ? "ChatGPT sign-in could not be started. Try again." : "Claude Code sign-in could not be started. Try again.") }
    finally { setBusy(false) }
  }
  const initialize = async () => {
    setBusy(true); setMessage(null)
    try { await api.bootstrapData(); setStep(4); window.setTimeout(() => { void load() }, 300) }
    catch { setMessage("Initialization could not be started. Existing completed work is still saved.") }
    finally { setBusy(false) }
  }
  const finish = async () => {
    await api.updateSettings({ onboarding_completed: true })
    navigate("/search", { replace: true })
  }
  const initializationComplete = requiredSources.every((source) => runtime?.data.sources[source]?.status === "UP_TO_DATE")
  const initializationStarted = Boolean(runtime?.data.onboarding_ready || requiredSources.some((source) => {
    const item = runtime?.data.sources[source]
    return item && (item.status !== "NOT_STARTED" || item.completed > 0)
  }))
  return <main className="grid min-h-screen place-items-center bg-muted/35 p-4"><Card className="w-full max-w-2xl shadow-lg"><CardContent className="p-7 sm:p-9">
    <div className="mb-7 flex items-center gap-3"><ReasonframeMark className="size-10" title="Reasonframe"/><div><h1 className="text-2xl font-semibold">Welcome to Reasonframe</h1><p className="text-sm text-muted-foreground">Financial research with specialist AI analysts.</p><p className="mt-0.5 text-xs text-muted-foreground">Step {step} of 5</p></div></div>
    {step === 1 && <div className="space-y-4"><h2 className="text-lg font-semibold">Your research stays local</h2><p className="text-sm text-muted-foreground">Financial and market data are stored on this computer. Reasonframe includes access to its SEC and FRED sources; only your Tiingo market-data token is requested. Initial setup downloads the supported research dataset.</p><Button onClick={() => setStep(runtime?.data.tiingo_configured ? 3 : 2)}>Continue</Button></div>}
    {step === 2 && <div className="space-y-4"><div><h2 className="text-lg font-semibold">Connect market data</h2><p className="text-sm text-muted-foreground">Enter your Tiingo token. It is validated before being saved in Reasonframe's local application-data directory. SEC and FRED access are included with this release.</p></div><label className="block text-sm font-medium">Tiingo token<input type="password" autoComplete="off" aria-label="Tiingo token" value={token} onChange={(event) => setToken(event.target.value)} className="mt-1 h-10 w-full rounded-md border bg-background px-3 text-sm" placeholder="Tiingo API token"/></label><Button onClick={saveDataSources} disabled={busy || !token.trim()}>{busy ? "Validating…" : "Save and continue"}</Button></div>}
    {step === 3 && <div className="space-y-4"><div><h2 className="text-lg font-semibold">Choose one AI provider</h2><p className="text-sm text-muted-foreground">Authentication stays with the provider's official runtime. The application never copies OAuth credentials.</p></div>{providers.length === 0 ? <Button variant="outline" onClick={loadProviders} disabled={busy}>Detect providers</Button> : <div className="grid gap-2">{providers.map((provider) => { const name = provider.provider === "chatgpt_codex" ? "OpenAI Codex" : "Claude Code"; const signingIn = loginProvider === provider.provider; return <div key={provider.provider} className="flex items-center justify-between gap-3 rounded-lg border p-3"><span><span className="block text-sm font-medium">{name}</span><span className="text-xs text-muted-foreground">{provider.provider === "chatgpt_codex" && !provider.connected && provider.state === "SIGN_IN_REQUIRED" ? "Not connected" : provider.message ?? (provider.connected ? "Connected" : provider.installed === false ? "Not installed" : "Sign-in required")}</span></span><span className="flex shrink-0 items-center gap-2">{provider.connected ? <><CheckCircle2 className="size-5 text-emerald-600"/><Button size="sm" variant="outline" onClick={() => void chooseProvider(provider)} disabled={busy || loginProvider !== null}>Select</Button></> : provider.state !== "RUNTIME_ERROR" && provider.state !== "NOT_INSTALLED" ? <Button size="sm" onClick={() => void chooseProvider(provider)} disabled={busy || loginProvider !== null}>{signingIn ? <><Loader2 className="size-3 animate-spin"/>Waiting for sign-in…</> : provider.provider === "chatgpt_codex" ? "Sign in with ChatGPT" : "Sign in"}</Button> : null}</span></div> })}</div>}<div className="flex gap-2"><Button onClick={() => setStep(4)} disabled={!providers.some((item) => item.connected) || loginProvider !== null}>Continue</Button><Button variant="ghost" onClick={loadProviders} disabled={busy || loginProvider !== null}>Retry detection</Button></div></div>}
    {step === 4 && <div className="space-y-4"><div><h2 className="text-lg font-semibold">Initialize research data</h2><p className="text-sm text-muted-foreground">Initial SEC filing setup may take several minutes. Progress is saved as each company completes, so you can safely resume later.</p></div><div className="divide-y rounded-lg border">{requiredSources.map((source) => { const item = runtime?.data.sources[source]; return <div key={source} className="flex items-center justify-between p-3 text-sm"><span>{sourceLabel[source]}</span><span className="text-muted-foreground">{item?.status === "UPDATING" && <Loader2 className="mr-1 inline size-3 animate-spin"/>}{item ? `${item.completed} / ${item.total} · ${item.status.replaceAll("_", " ").toLowerCase()}` : "Waiting"}</span></div> })}</div>{runtime?.data.sources.market.message && <p className="rounded-md bg-muted p-3 text-sm">{runtime.data.sources.market.message}</p>}{initializationComplete && !runtime?.data.busy ? <Button onClick={() => setStep(5)}>Continue to summary</Button> : <Button onClick={runtime?.data.busy ? undefined : initialize} disabled={busy || runtime?.data.busy}>{runtime?.data.busy ? "Initializing…" : initializationStarted ? "Resume initialization" : "Initialize data"}</Button>}</div>}
    {step === 5 && <div className="space-y-4"><h2 className="text-lg font-semibold">Ready</h2><p className="text-sm text-muted-foreground">{runtime?.data.market_ready ?? 0} of {runtime?.data.market_total ?? 50} companies have local market data{runtime?.data.latest_market_date ? ` through ${runtime.data.latest_market_date}` : ""}. Any deferred work can resume from Settings.</p><Button onClick={finish}>Open Reasonframe</Button></div>}
    {message && <p role="alert" className="mt-4 rounded-md bg-muted p-3 text-sm">{message}</p>}
  </CardContent></Card></main>
}
