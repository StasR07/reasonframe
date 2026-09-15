import { useCallback, useEffect, useState } from "react"
import { ChatGPTConnection } from "@/components/layout/app-shell"
import { useTheme } from "@/components/theme-provider"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { api, ApiError } from "@/api/client"
import type { DataSourceStatus, DataSyncStatus } from "@/api/types"
import { ReasonframeLockup } from "@/components/brand/reasonframe-mark"

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <Card className="shadow-none"><CardContent className="space-y-4"><h2 className="text-lg font-semibold">{title}</h2>{children}</CardContent></Card>
}

function friendlyState(source: DataSourceStatus | undefined) {
  if (!source) return "Unavailable"
  return ({ NOT_STARTED: "Not initialized", NOT_CONFIGURED: "Not configured", UPDATING: "Updating", UP_TO_DATE: "Up to date", PARTIALLY_READY: "Partially ready", RATE_LIMITED: "Rate limited", OFFLINE: "Offline", FAILED: "Last refresh failed" } as const)[source.status]
}

function formatDate(value: string | null | undefined) {
  if (!value) return "Never"
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value))
}

function LocalDataSettings() {
  const [status, setStatus] = useState<DataSyncStatus | null>(null)
  const [token, setToken] = useState("")
  const [fredKey, setFredKey] = useState("")
  const [edgarIdentity, setEdgarIdentity] = useState("")
  const [editingTiingo, setEditingTiingo] = useState(false)
  const [editingFred, setEditingFred] = useState(false)
  const [editingEdgar, setEditingEdgar] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const load = useCallback(async () => {
    try { setStatus(await api.dataStatus()) } catch { setMessage("Local data status is unavailable.") }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!status?.busy) return
    const timer = window.setInterval(() => { void load() }, 1200)
    return () => window.clearInterval(timer)
  }, [load, status?.busy])
  const saveToken = async () => {
    setBusy(true); setMessage(null)
    try { await api.saveTiingo(token); setToken(""); setEditingTiingo(false); setMessage("Tiingo connected."); await load() }
    catch (reason) { setMessage(reason instanceof ApiError ? reason.message : "Tiingo token could not be saved.") }
    finally { setBusy(false) }
  }
  const removeToken = async () => {
    setBusy(true); setMessage(null)
    try { await api.removeTiingo(); setEditingTiingo(false); setMessage("Tiingo token removed."); await load() }
    catch { setMessage("Tiingo token could not be removed.") }
    finally { setBusy(false) }
  }
  const saveFred = async () => {
    setBusy(true); setMessage(null)
    try { await api.saveFred(fredKey); setFredKey(""); setEditingFred(false); setMessage("FRED API key saved."); await load() }
    catch { setMessage("FRED API key could not be saved.") }
    finally { setBusy(false) }
  }
  const removeFred = async () => {
    setBusy(true); setMessage(null)
    try { await api.removeFred(); setEditingFred(false); setMessage("FRED API key removed."); await load() }
    catch { setMessage("FRED API key could not be removed.") }
    finally { setBusy(false) }
  }
  const saveEdgar = async () => {
    setBusy(true); setMessage(null)
    try { await api.saveEdgarIdentity(edgarIdentity); setEdgarIdentity(""); setEditingEdgar(false); setMessage("SEC fair-access identity saved."); await load() }
    catch (reason) { setMessage(reason instanceof ApiError ? reason.message : "SEC fair-access identity could not be saved.") }
    finally { setBusy(false) }
  }
  const removeEdgar = async () => {
    setBusy(true); setMessage(null)
    try { await api.removeEdgarIdentity(); setEditingEdgar(false); setMessage("SEC fair-access identity removed."); await load() }
    catch { setMessage("SEC fair-access identity could not be removed.") }
    finally { setBusy(false) }
  }
  const refresh = async () => {
    setBusy(true); setMessage(null)
    try { setStatus(await api.refreshData()); window.setTimeout(() => { void load() }, 300) }
    catch { setMessage("Refresh could not be started. Existing local data remains available.") }
    finally { setBusy(false) }
  }
  const toggleAutomatic = async (checked: boolean) => {
    setStatus((current) => current ? { ...current, automatic_refresh: checked } : current)
    try { await api.updateSettings({ automatic_refresh: checked }) }
    catch { setMessage("Automatic refresh setting could not be saved."); await load() }
  }
  return <div className="space-y-5">
    <div className="sr-only">Market data provider: <span>Tiingo</span></div>
    <div className="rounded-lg border p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-medium">Tiingo market data</p><p className="text-xs text-muted-foreground">{status?.tiingo_configured ? "Connected · token stored locally" : "Not configured"}</p></div>{status?.tiingo_configured && !editingTiingo && <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => setEditingTiingo(true)}>Replace token</Button><Button size="sm" variant="ghost" onClick={removeToken} disabled={busy}>Remove token</Button></div>}</div>
      {(!status?.tiingo_configured || editingTiingo) && <div className="mt-3 flex gap-2"><input type="password" autoComplete="off" aria-label="Tiingo token" value={token} onChange={(event) => setToken(event.target.value)} placeholder="Enter Tiingo token" className="h-9 min-w-0 flex-1 rounded-md border bg-background px-3 text-sm"/><Button size="sm" onClick={saveToken} disabled={busy || !token.trim()}>{busy ? "Validating..." : "Save token"}</Button></div>}
    </div>
    <div className="rounded-lg border p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-medium">FRED macro data</p><p className="text-xs text-muted-foreground">{status?.fred_configured ? "Connected · API key stored locally" : "Not configured"}</p></div>{status?.fred_configured && !editingFred && <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => setEditingFred(true)}>Replace key</Button><Button size="sm" variant="ghost" onClick={removeFred} disabled={busy}>Remove key</Button></div>}</div>
      {(!status?.fred_configured || editingFred) && <div className="mt-3 flex gap-2"><input type="password" autoComplete="off" aria-label="FRED API key" value={fredKey} onChange={(event) => setFredKey(event.target.value)} placeholder="Enter FRED API key" className="h-9 min-w-0 flex-1 rounded-md border bg-background px-3 text-sm"/><Button size="sm" onClick={saveFred} disabled={busy || !fredKey.trim()}>Save key</Button></div>}
    </div>
    <div className="rounded-lg border p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-medium">SEC filings</p><p className="text-xs text-muted-foreground">{status?.sec_configured ? "Connected · fair-access identity stored locally" : "Not configured"}</p></div>{status?.sec_configured && !editingEdgar && <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => setEditingEdgar(true)}>Replace identity</Button><Button size="sm" variant="ghost" onClick={removeEdgar} disabled={busy}>Remove identity</Button></div>}</div>
      {(!status?.sec_configured || editingEdgar) && <div className="mt-3 flex gap-2"><input type="text" autoComplete="name" aria-label="SEC fair-access identity" value={edgarIdentity} onChange={(event) => setEdgarIdentity(event.target.value)} placeholder="Your Name you@example.com" className="h-9 min-w-0 flex-1 rounded-md border bg-background px-3 text-sm"/><Button size="sm" onClick={saveEdgar} disabled={busy || !edgarIdentity.trim()}>Save identity</Button></div>}
    </div>
    {message && <p role="status" className="text-sm text-muted-foreground">{message}</p>}
    <div><h3 className="text-sm font-medium">Local data</h3><dl className="mt-3 divide-y rounded-lg border text-sm">
      <div className="flex justify-between gap-4 p-3"><div><dt className="font-medium">Market data</dt><dd className="text-xs text-muted-foreground">{status ? `${status.market_ready} / ${status.market_total} supported companies${status.latest_market_date ? ` · through ${status.latest_market_date}` : ""}` : "Loading…"}</dd></div><div className="text-right"><dd>{friendlyState(status?.sources.market)}</dd><dd className="text-xs text-muted-foreground">{formatDate(status?.sources.market.last_successful_refresh)}</dd></div></div>
      <div className="flex justify-between gap-4 p-3"><div><dt className="font-medium">SEC filings</dt><dd className="text-xs text-muted-foreground">Stored locally for deterministic research</dd></div><div className="text-right"><dd>{friendlyState(status?.sources.sec)}</dd><dd className="text-xs text-muted-foreground">{formatDate(status?.sources.sec.last_successful_refresh)}</dd></div></div>
      <div className="flex justify-between gap-4 p-3"><div><dt className="font-medium">Macro data</dt><dd className="text-xs text-muted-foreground">Curated FRED series</dd></div><div className="text-right"><dd>{friendlyState(status?.sources.macro)}</dd><dd className="text-xs text-muted-foreground">{formatDate(status?.sources.macro.last_successful_refresh)}</dd></div></div>
    </dl></div>
    {status?.sources.market.message && <p className="rounded-md bg-muted p-3 text-sm">{status.sources.market.message}</p>}
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={status?.automatic_refresh ?? true} onChange={(event) => void toggleAutomatic(event.target.checked)}/>Refresh stale data when the app opens</label>
    <Button onClick={refresh} disabled={busy || status?.busy}>{status?.busy ? "Refresh in progress…" : "Refresh data"}</Button>
  </div>
}

export function SettingsPage() {
  const { theme, setTheme } = useTheme()
  return <div className="max-w-3xl space-y-6"><header><h1 className="text-3xl font-semibold tracking-tight">Settings</h1><p className="mt-1 text-sm text-muted-foreground">Configure data sources, AI providers, and application preferences.</p></header>
    <Section title="Appearance"><div className="flex items-center justify-between gap-4"><label htmlFor="settings-theme" className="text-sm font-medium">Theme</label><Select value={theme} onValueChange={(value) => setTheme(value as "system" | "light" | "dark")}><SelectTrigger id="settings-theme" className="w-40" aria-label="Theme"><SelectValue>{theme === "system" ? "System" : theme === "light" ? "Light" : "Dark"}</SelectValue></SelectTrigger><SelectContent><SelectItem value="system">System</SelectItem><SelectItem value="light">Light</SelectItem><SelectItem value="dark">Dark</SelectItem></SelectContent></Select></div></Section>
    <Section title="AI Provider"><ChatGPTConnection/></Section>
    <Section title="Data Sources"><LocalDataSettings/></Section>
    <Section title="About"><div className="space-y-3 text-sm text-muted-foreground"><ReasonframeLockup className="text-xl text-foreground"/><div><p className="font-medium text-foreground">Financial research with specialist AI analysts.</p><p className="mt-1 text-xs">Version 0.1.0</p></div><p>Reasonframe combines company fundamentals, market data, macroeconomic context, valuation, and structured AI analysis in a local-first desktop application.</p><p>Data comes from SEC filings, FRED, and locally stored Tiingo market data. AI analysis is backed by inspectable evidence. Reasonframe does not provide investment advice.</p></div></Section>
  </div>
}
