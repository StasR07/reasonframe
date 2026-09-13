import { useCallback, useEffect, useRef, useState } from "react"
import type { FormEvent } from "react"
import { Building2, Landmark, LogOut, Menu, RefreshCw, Scale, Search, Settings } from "lucide-react"
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { cn } from "@/lib/utils"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api } from "@/api/client"
import type { AIAccountStatus, AIModel } from "@/api/types"
import { persistedAIModel, persistAIModel } from "@/lib/ai-model"
import { openExternalUrl } from "@/lib/open-external"
import { useCatalog } from "@/hooks/use-api"
import { ReasonframeMark } from "@/components/brand/reasonframe-mark"

const navigation = [
  { to: "/search", label: "Search", icon: Search, match: "/search" },
  { to: "/company/AAPL", label: "Companies", icon: Building2, match: "/company" },
  { to: "/compare", label: "Compare", icon: Scale, match: "/compare" },
  { to: "/macro", label: "Macro", icon: Landmark, match: "/macro" },
  { to: "/settings", label: "Settings", icon: Settings, match: "/settings" },
]

export function connectionMessageText(reason: "service" | "timeout") {
  return reason === "timeout" ? "Login timed out - try connecting again." : "Connection service unavailable."
}

function Navigation() {
  return <nav className="space-y-1" aria-label="Primary">{navigation.map(({ to, label, icon: Icon, match }) => <NavLink key={to} to={to} className={({ isActive }) => cn("flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground", (isActive || location.pathname.startsWith(match)) && "bg-accent text-foreground")}><Icon className="size-4" />{label}</NavLink>)}</nav>
}

function SearchCommand() {
  const navigate = useNavigate()
  const location = useLocation()
  const catalog = useCatalog()
  const current = location.pathname === "/search" ? new URLSearchParams(location.search).get("q") ?? "" : ""
  const [question, setQuestion] = useState(current)
  useEffect(() => { if (location.pathname === "/search") setQuestion(current) }, [current, location.pathname])
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const value = question.trim()
    if (value) navigate(`/search?q=${encodeURIComponent(value)}`)
  }
  const unavailable = catalog.data?.features?.ai_search === false
  if (location.pathname !== "/search") return null
  return <div className="mb-6"><form onSubmit={submit} className="relative"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"/><input aria-label="Natural-language financial search" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask about a company or macro series..." disabled={unavailable} className="h-11 w-full rounded-lg border bg-background pl-10 pr-24 text-sm shadow-sm outline-none transition-shadow placeholder:text-muted-foreground focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-60"/><Button type="submit" size="sm" className="absolute right-1.5 top-1.5" disabled={unavailable || !question.trim()}>Search</Button></form>{unavailable && <p className="mt-1.5 text-xs text-muted-foreground">AI search is unavailable. Manual company, comparison, and macro workspaces remain available.</p>}</div>
}

export function ChatGPTConnection() {
  const [status, setStatus] = useState<AIAccountStatus | null>(null)
  const [providers, setProviders] = useState<AIAccountStatus[]>([])
  const [models, setModels] = useState<AIModel[]>([])
  const [selectedModel, setSelectedModel] = useState("")
  const [busy, setBusy] = useState(false)
  const [loginPending, setLoginPending] = useState(false)
  const [setupToken, setSetupToken] = useState("")
  const [connectionMessage, setConnectionMessage] = useState<"service" | "timeout" | null>(null)
  const poll = useRef<number | null>(null)
  const pollAttempts = useRef(0)
  const refresh = useCallback(async () => {
    try {
      const next = await api.aiStatus()
      setStatus(next); setConnectionMessage(null)
      if (next.connected && poll.current !== null) { window.clearInterval(poll.current); poll.current = null; setLoginPending(false) }
      return next
    } catch { setConnectionMessage("service"); return null }
  }, [])
  useEffect(() => { void refresh(); return () => { if (poll.current !== null) window.clearInterval(poll.current) } }, [refresh])
  useEffect(() => { void (async () => { try { setProviders(await api.aiProviders()) } catch { /* older/dev backend */ } })() }, [status?.provider, status?.connected])
  useEffect(() => {
    if (!status?.connected) { setModels([]); setSelectedModel(""); return }
    void api.aiModels().then((available) => {
      setModels(available)
      const stored = persistedAIModel()
      const normalizedName = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, " ")
      const chosen = available.find((item) => item.id === stored)
        ?? available.find((item) => normalizedName(item.name).includes("gpt 5 6 terra"))
        ?? available.find((item) => normalizedName(item.name).includes("gpt 5 6 luna"))
        ?? available.find((item) => item.id === status.model)
        ?? available.find((item) => item.is_default)
        ?? available[0]
      if (chosen) { setSelectedModel(chosen.id); persistAIModel(chosen.id) }
    }).catch(() => setConnectionMessage("service"))
  }, [status?.connected, status?.model])
  const connect = async () => {
    setBusy(true)
    try {
      const credential = status?.provider === "claude_code" ? setupToken.trim() : undefined
      const result = await api.aiConnect(credential)
      if (result.auth_url) await openExternalUrl(result.auth_url)
      if (result.connected) { setSetupToken(""); await refresh() }
      else if (result.credential_required) return
      else if (poll.current === null) {
        setLoginPending(true)
        pollAttempts.current = 0
        poll.current = window.setInterval(() => {
          pollAttempts.current += 1
          if (pollAttempts.current >= 120) {
            if (poll.current !== null) window.clearInterval(poll.current)
            poll.current = null; setLoginPending(false); setConnectionMessage("timeout"); return
          }
          void refresh()
        }, 1500)
      }
    } catch { setLoginPending(false); setConnectionMessage("service") }
    finally { setBusy(false) }
  }
  const disconnect = async () => {
    setBusy(true)
    try { setStatus(await api.aiDisconnect()); setLoginPending(false); setSetupToken(""); setConnectionMessage(null) }
    catch { setConnectionMessage("service") }
    finally { setBusy(false) }
  }
  const retryStatus = async () => {
    setBusy(true); setConnectionMessage(null)
    try {
      const detected = await api.refreshAIProviders()
      setProviders(detected)
      const active = detected.find((item) => item.provider === status?.provider) ?? detected[0]
      if (active) setStatus(active)
    } catch { setConnectionMessage("service") }
    finally { setBusy(false) }
  }
  const providerName = status?.provider === "claude_code" ? "Claude subscription" : "ChatGPT subscription"
  const selectProvider = async (value: string | null) => {
    if (!value) return
    setBusy(true); setConnectionMessage(null)
    try { setStatus(await api.selectAIProvider(value)); setModels([]); setSelectedModel(""); setSetupToken("") }
    catch { setConnectionMessage("service") }
    finally { setBusy(false) }
  }
  const selector = providers.length > 0 && <div className="mb-3"><label className="mb-1 block text-xs" htmlFor="ai-provider">AI provider</label><Select value={status?.provider ?? "chatgpt_codex"} onValueChange={selectProvider}><SelectTrigger id="ai-provider" className="w-full" aria-label="AI provider"><SelectValue>{providerName}</SelectValue></SelectTrigger><SelectContent>{providers.map((item) => <SelectItem key={item.provider} value={item.provider}>{item.provider === "chatgpt_codex" ? "ChatGPT subscription" : "Claude subscription"}</SelectItem>)}</SelectContent></Select></div>
  if (status?.connected) return <div className="rounded-lg border bg-muted/40 p-3">{selector}<p className="text-xs font-medium text-foreground">{providerName} is connected</p>{models.length > 0 && <div className="mt-2"><label className="mb-1 block text-[11px]" htmlFor="ai-search-model">Model</label><Select value={selectedModel} onValueChange={(value) => { if (value) { setSelectedModel(value); persistAIModel(value) } }}><SelectTrigger id="ai-search-model" size="sm" className="w-full" aria-label="AI search model"><SelectValue>{models.find((model) => model.id === selectedModel)?.name}</SelectValue></SelectTrigger><SelectContent>{models.map((model) => <SelectItem key={model.id} value={model.id}>{model.name}</SelectItem>)}</SelectContent></Select></div>}{connectionMessage === "service" && <p className="mt-1 text-[11px]">Model list is currently unavailable.</p>}<div className="mt-2"><Button className="h-7 px-2 text-xs" variant="ghost" onClick={disconnect} disabled={busy}><LogOut className="size-3"/>Disconnect</Button></div></div>
  const missing = status?.state === "NOT_INSTALLED"
  const authError = status?.state === "RUNTIME_ERROR"
  const unsupported = status?.state === "UNSUPPORTED" || status?.supported === false
  const title = missing ? `${providerName} could not be found on this Mac` : authError ? `${providerName} could not be initialized` : unsupported ? `${providerName} is not supported yet` : status?.provider === "chatgpt_codex" ? "Not connected" : `Sign in to ${providerName}`
  const guidance = missing
    ? "Reinstall the latest desktop build, then try again."
    : status?.message
  return <div className="rounded-lg border bg-muted/40 p-3">{selector}<p className="text-xs font-medium text-foreground">{title}</p>{guidance && <p className="mt-1 text-[11px]">{guidance}</p>}{status?.provider === "claude_code" && !missing && !authError && !unsupported && <label className="mt-2 block text-[11px]">Claude setup token<input type="password" autoComplete="off" value={setupToken} onChange={(event) => setSetupToken(event.target.value)} className="mt-1 h-8 w-full rounded-md border bg-background px-2 text-xs" placeholder="sk-ant-oat…"/></label>}{connectionMessage && <p className="mt-1 text-[11px]">{connectionMessageText(connectionMessage)}</p>}<div className="mt-2 flex flex-wrap gap-2">{!missing && !authError && !unsupported && <Button className="h-7 px-2 text-xs" variant="outline" onClick={connect} disabled={busy || loginPending || (status?.provider === "claude_code" && !setupToken.trim())}>{loginPending ? "Waiting for sign-in…" : busy ? "Starting sign in..." : status?.provider === "chatgpt_codex" ? "Sign in with ChatGPT" : "Connect Claude"}</Button>}{(missing || authError) && <Button className="h-7 px-2 text-xs" variant="ghost" onClick={retryStatus} disabled={busy || loginPending}><RefreshCw className="size-3"/>Try again</Button>}</div></div>
}

export function AppShell() {
  const navigate = useNavigate()
  const startupChecked = useRef(false)
  const [backendError, setBackendError] = useState<string | null>(null)
  useEffect(() => {
    if (startupChecked.current) return
    startupChecked.current = true
    if (typeof (api as Partial<typeof api>).runtime !== "function") return
    void api.runtime().then((runtime) => {
      if (!runtime.onboarding_completed) navigate("/setup", { replace: true })
      else if (runtime.automatic_refresh) void api.refreshData(true).catch(() => undefined)
    }).catch(() => setBackendError(window.__FINANCE_BACKEND_ERROR__ ?? "The local research service could not start. Retry or open diagnostics."))
  }, [navigate])
  if (backendError) return <main className="grid min-h-screen place-items-center bg-muted/35 p-6"><div className="max-w-lg rounded-xl border bg-background p-7 shadow-sm"><h1 className="text-xl font-semibold">Local service unavailable</h1><p className="mt-2 text-sm text-muted-foreground">{backendError}</p><Button className="mt-5" onClick={() => window.location.reload()}>Retry</Button></div></main>
  return <div className="min-h-screen bg-muted/35 text-foreground">
    <header className="sticky top-0 z-30 flex h-14 items-center border-b bg-background px-4 md:hidden"><Sheet><SheetTrigger render={<Button size="icon" variant="ghost" aria-label="Open navigation"/>}><Menu /></SheetTrigger><SheetContent side="left" className="w-72 p-5"><SheetTitle className="flex items-center gap-2.5 text-base"><ReasonframeMark className="size-6" />Reasonframe</SheetTitle><Separator className="my-5" /><Navigation /></SheetContent></Sheet><span className="ml-3 mr-auto text-sm font-semibold">Reasonframe</span></header>
    <aside className="fixed inset-y-0 left-0 hidden w-60 border-r bg-background md:flex md:flex-col"><div className="flex h-16 items-center gap-2.5 px-5 text-sm font-semibold"><ReasonframeMark className="size-6" /><span>Reasonframe</span></div><Separator/><div className="flex-1 p-4"><p className="mb-3 px-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Workspace</p><Navigation /></div></aside>
    <main className="min-w-0 md:pl-60"><div className="mx-auto max-w-[1500px] p-4 sm:p-6 lg:p-8"><SearchCommand/><Outlet /></div></main>
  </div>
}
