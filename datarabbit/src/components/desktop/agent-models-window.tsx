"use client"

import Image from "next/image"
import { useCallback, useEffect, useMemo, useState } from "react"
import { RefreshCw, RotateCcw, Search, Settings2, Loader2 } from "@/lib/icons"
import { Button } from "@/components/ui/button"

type Agent = {
  key: string; name: string; role_label?: string; provider?: string; model?: string
  reasoning_effort?: string; config_hash?: string; error?: string; avatar_file?: string
}
type Snapshot = {
  agents: Agent[]
  providers: { provider: string; display_name: string; auth_state: string }[]
  models: { provider: string; model: string; display_name?: string }[]
  inventory_initialized?: boolean
}
type ApplyResult = { default?: { revision_id?: string }; revision_id?: string }
const levels = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
const field = "h-9 w-full min-w-0 rounded-md border border-chrome-border bg-background/70 px-2.5 text-[12px] text-foreground focus:outline-none focus:ring-1 focus:ring-rvbbit-accent disabled:opacity-50"

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/calliope/admin/${path}`, {
    credentials: "same-origin", cache: "no-store",
    ...(body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Calliope-Admin": "1" }, body: JSON.stringify(body),
    }),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error?.message || `Request failed (${response.status})`)
  return data as T
}

export function AgentModelsWindow() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState("")
  const [provider, setProvider] = useState("")
  const [model, setModel] = useState("")
  const [modelQuery, setModelQuery] = useState("")
  const [modelOpen, setModelOpen] = useState(false)
  const [modelIndex, setModelIndex] = useState(-1)
  const [effort, setEffort] = useState("keep")
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [pendingRevision, setPendingRevision] = useState<string | null>(null)
  const [clearAfterRevision, setClearAfterRevision] = useState(false)
  const [notice, setNotice] = useState("")
  const [error, setError] = useState("")
  const disabled = busy || loading || Boolean(pendingRevision)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const saved = await request<Snapshot>("providers")
      setSnapshot(saved)
      if (saved.inventory_initialized === false) {
        setNotice("Discovering providers for the first time…")
        setSnapshot(await request<Snapshot>("providers/refresh", { only_if_empty: true }))
        setNotice("Provider catalog initialized.")
      }
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { const timer = setTimeout(() => void load(), 0); return () => clearTimeout(timer) }, [load])

  useEffect(() => {
    if (modelOpen && modelIndex >= 0) document.getElementById(`agent-model-option-${modelIndex}`)?.scrollIntoView({ block: "nearest" })
  }, [modelOpen, modelIndex])

  // Poll only operation status. Never replace a draft with a refreshed model selection.
  useEffect(() => {
    if (!pendingRevision) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const status = await request<{ revisions: { id: string; state: string; error?: string }[] }>("hermes")
        if (cancelled) return
        const revision = status.revisions.find((row) => row.id === pendingRevision)
        if (revision?.state === "active") {
          setPendingRevision(null); setNotice(clearAfterRevision ? "Changes applied. Hermes is healthy." : "Hermes restarted and verified. Your selection is unchanged."); setError(""); if (clearAfterRevision) setSelected(new Set()); void load(); return
        }
        if (revision && ["failed", "rolled_back", "rejected"].includes(revision.state)) {
          setPendingRevision(null); setError(revision.error || `Gateway update ${revision.state}. Review current assignments before retrying.`); void load(); return
        }
      } catch (cause) {
        if (!cancelled) setError(`Still waiting for gateway verification: ${cause instanceof Error ? cause.message : String(cause)}`)
      }
      if (!cancelled) timer = setTimeout(poll, 3000)
    }
    void poll()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [pendingRevision, clearAfterRevision, load])

  const agents = snapshot?.agents || []
  const visible = agents.filter((agent) => `${agent.name} ${agent.key} ${agent.role_label || ""} ${agent.provider || ""} ${agent.model || ""}`.toLowerCase().includes(filter.toLowerCase()))
  const chosen = agents.filter((agent) => selected.has(agent.key) && !agent.error)
  const providers = (snapshot?.providers || []).filter((item) => item.auth_state === "configured")
  const models = (snapshot?.models || []).filter((item) => item.provider === provider)
  const matches = models.filter((item) => `${item.display_name || ""} ${item.model}`.toLowerCase().includes(modelQuery.toLowerCase()))
  const chooseModel = (item: Snapshot["models"][number]) => {
    setNotice(""); setModel(item.model); setModelQuery(item.display_name || item.model); setModelOpen(false); setModelIndex(-1)
  }
  const validModel = providers.some((item) => item.provider === provider) && models.some((item) => item.model === model)
  const changes = useMemo(() => (snapshot?.agents || []).filter((agent) => selected.has(agent.key) && !agent.error &&
    (agent.provider !== provider || agent.model !== model || (effort !== "keep" && (agent.reasoning_effort || "") !== effort)))
    .map((agent) => ({ agent: agent.key, provider, model,
      ...(effort === "keep" ? {} : { reasoning_effort: effort }),
      ...(agent.config_hash ? { expected_hash: agent.config_hash } : {}),
    })), [snapshot, selected, provider, model, effort])
  const toggle = (key: string) => setSelected((old) => { const next = new Set(old); if (next.has(key)) next.delete(key); else next.add(key); return next })
  const refreshCatalog = async () => {
    setLoading(true); setError(""); setNotice("Refreshing the provider catalog…")
    try { setSnapshot(await request<Snapshot>("providers/refresh", {})); setNotice("Provider catalog refreshed. Your selection is unchanged.") }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setLoading(false) }
  }
  const apply = async () => {
    if (disabled || !validModel || !changes.length) return
    setBusy(true); setError(""); setNotice(`Applying changes to ${changes.length} agents and verifying Hermes…`)
    try {
      const result = await request<ApplyResult>("providers/agents", { changes })
      if (result.default?.revision_id) {
        setClearAfterRevision(true); setPendingRevision(result.default.revision_id); setNotice(changes.some((change) => change.agent !== "default") ? "Specialist updates finished. Waiting for Calliope’s gateway update to verify…" : "Waiting for Calliope’s gateway update to verify…")
      } else {
        setNotice(`Updated ${changes.length} agents. Hermes is healthy.`); setSelected(new Set()); await load()
      }
    } catch (cause) {
      setError(`${cause instanceof Error ? cause.message : String(cause)} Your selection has been kept. Check current assignments before retrying.`)
      await load()
    } finally { setBusy(false) }
  }
  const restart = async () => {
    setBusy(true); setError("")
    try {
      const result = await request<ApplyResult>("hermes/restart", { reason: "Operator restart from DataRabbit Agent Models" })
      if (!result.revision_id) throw new Error("The restart did not return a revision to track")
      setClearAfterRevision(false); setPendingRevision(result.revision_id); setNotice("Restart queued. Waiting for gateway verification…")
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false) }
  }

  return <div className="flex h-full min-h-0 flex-col bg-chrome-bg/20 text-[12px] text-foreground">
    <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-chrome-border px-4 py-3">
      <Settings2 className="h-5 w-5 text-rvbbit-accent" />
      <div className="flex-1"><h2 className="font-semibold">Agent Models</h2><p className="text-[11px] text-chrome-text/70">Select agents. Choose once. Apply to the selection.</p></div>
      <Button size="sm" variant="ghost" disabled={disabled} onClick={() => void load()}><RefreshCw className="mr-1.5 h-3.5 w-3.5" />Read current</Button>
      <Button size="sm" variant="outline" disabled={disabled} onClick={() => void restart()}><RotateCcw className="mr-1.5 h-3.5 w-3.5" />Restart Hermes</Button>
    </header>
    <section className="z-10 shrink-0 border-b border-chrome-border bg-background/25 px-4 py-3">
      <div className="grid grid-cols-[minmax(120px,1fr)_minmax(160px,2fr)_minmax(120px,1fr)] gap-3">
        <label className="min-w-0 space-y-1"><span className="text-[10px] uppercase tracking-wide text-chrome-text/75">Provider</span>
          <select className={field} aria-label="Provider" value={provider} disabled={disabled} onChange={(e) => { setNotice(""); setProvider(e.target.value); setModel(""); setModelQuery("") }}>
            <option value="">Choose provider</option>{providers.map((item) => <option key={item.provider} value={item.provider}>{item.display_name}</option>)}
          </select>
        </label>
        <div className="relative min-w-0 space-y-1"><label htmlFor="agent-model-search" className="text-[10px] uppercase tracking-wide text-chrome-text/75">Search model</label>
          <input id="agent-model-search" className={field} role="combobox" aria-expanded={modelOpen} aria-controls="agent-model-options" aria-activedescendant={modelOpen && modelIndex >= 0 ? `agent-model-option-${modelIndex}` : undefined} autoComplete="off" value={modelQuery} disabled={disabled || !provider}
            placeholder={provider ? `Search ${models.length} models…` : "Choose a provider first"}
            onFocus={() => setModelOpen(true)} onBlur={() => setModelOpen(false)}
            onChange={(e) => { setModelQuery(e.target.value); setModel(""); setModelIndex(-1); setModelOpen(true) }}
            onKeyDown={(e) => {
              if (e.key === "Escape") setModelOpen(false)
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault(); setModelOpen(true)
                setModelIndex((current) => Math.max(0, Math.min(matches.length - 1, current + (e.key === "ArrowDown" ? 1 : -1))))
              }
              if (e.key === "Enter" && modelOpen && matches[modelIndex]) { e.preventDefault(); chooseModel(matches[modelIndex]) }
            }} />
          {modelOpen && <div id="agent-model-options" role="listbox" aria-label="Models" className="absolute left-0 right-0 top-full z-30 max-h-64 overflow-y-auto rounded-md border border-chrome-border bg-chrome-bg p-1 shadow-xl"
            onMouseDown={(e) => e.preventDefault()} onFocus={() => setModelOpen(true)} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setModelOpen(false) }}>
            {matches.map((item, index) => <button id={`agent-model-option-${index}`} key={item.model} type="button" role="option" aria-selected={model === item.model} className={`block w-full rounded px-2.5 py-2 text-left hover:bg-rvbbit-accent/15 focus:bg-rvbbit-accent/15 focus:outline-none ${modelIndex === index ? "bg-rvbbit-accent/15" : ""}`}
              onClick={() => chooseModel(item)}>
              <span className="block truncate">{item.display_name || item.model}</span><span className="block truncate font-mono text-[10px] text-chrome-text/65">{item.model}</span>
            </button>)}
            {!matches.length && <p className="p-3 text-chrome-text/70">No matching models. Try another search or refresh the catalog.</p>}
          </div>}
        </div>
        <label className="min-w-0 space-y-1"><span className="text-[10px] uppercase tracking-wide text-chrome-text/75">Thinking level</span>
          <select className={field} aria-label="Thinking level" value={effort} disabled={disabled} onChange={(e) => { setNotice(""); setEffort(e.target.value) }}>
            <option value="keep">Keep each agent’s setting</option><option value="">Auto</option>{levels.map((level) => <option key={level} value={level}>{level}</option>)}
          </select>
        </label>
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-chrome-text/70"><span>Choices affect selected agents only. Fallback, image, and background-task settings stay as they are.</span><button className="shrink-0 text-rvbbit-accent hover:underline disabled:opacity-50" disabled={disabled} onClick={() => void refreshCatalog()}>Refresh model catalog</button></div>
    </section>
    {error && <p role="alert" className="shrink-0 border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-destructive">{error}</p>}
    {notice && <p role="status" className="shrink-0 border-b border-chrome-border px-4 py-2 text-chrome-text/80">{notice}</p>}
    <div className="flex shrink-0 flex-wrap items-center gap-2 px-4 py-2">
      <label className="relative min-w-40 flex-1"><Search className="absolute left-2 top-2.5 h-3.5 w-3.5 text-chrome-text/60" /><input aria-label="Filter agents" className={`${field} pl-7`} placeholder="Filter agents, roles, or current models…" value={filter} onChange={(e) => setFilter(e.target.value)} /></label>
      <Button size="sm" variant="ghost" disabled={disabled} onClick={() => setSelected(new Set(agents.filter((a) => !a.error).map((a) => a.key)))}>All agents</Button>
      <Button size="sm" variant="ghost" disabled={disabled} onClick={() => setSelected(new Set(agents.filter((a) => a.key !== "default" && !a.error).map((a) => a.key)))}>Specialists</Button>
      <Button size="sm" variant="ghost" disabled={disabled} onClick={() => setSelected((old) => new Set([...old, ...visible.filter((a) => !a.error).map((a) => a.key)]))}>Select filtered</Button>
      <Button size="sm" variant="ghost" disabled={disabled || !selected.size} onClick={() => setSelected(new Set())}>Clear</Button>
    </div>
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="w-full border-collapse text-left">
        <thead className="sticky top-0 z-[1] bg-chrome-bg text-[10px] uppercase tracking-wide text-chrome-text/70"><tr><th className="w-10 px-4 py-2"><span className="sr-only">Select</span></th><th className="py-2 pr-3">Agent</th><th className="py-2 pr-3">Current model</th><th className="py-2 pr-3">Thinking</th><th className="py-2 pr-4">Proposed</th></tr></thead>
        <tbody>{visible.map((agent) => <tr key={agent.key} className={`border-b border-chrome-border/50 ${selected.has(agent.key) ? "bg-rvbbit-accent/[0.07]" : "hover:bg-foreground/[0.025]"}`}>
          <td className="px-4 py-2.5"><input type="checkbox" aria-label={`Select ${agent.name}`} checked={selected.has(agent.key)} disabled={disabled || Boolean(agent.error)} onChange={() => toggle(agent.key)} className="h-4 w-4 accent-[var(--rvbbit-accent)]" /></td>
          <td className="py-2.5 pr-3"><label className="flex items-center gap-2"><span className="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-full border border-chrome-border text-rvbbit-accent">{agent.avatar_file ? <Image src={`/calliope/specialists/${encodeURIComponent(agent.avatar_file)}`} alt="" width={28} height={28} unoptimized className="h-full w-full object-cover" /> : agent.name.slice(0,1)}</span><span><span className="block font-medium">{agent.name}</span><span className="block text-[10px] text-chrome-text/65">{agent.role_label || agent.key}</span></span></label></td>
          <td className="max-w-64 break-words py-2.5 pr-3"><span className="block">{agent.model || "Unconfigured"}</span><span className="text-[10px] text-chrome-text/65">{agent.provider || "—"}</span>{agent.error && <span className="block text-destructive">{agent.error}</span>}</td>
          <td className="py-2.5 pr-3 text-chrome-text/80">{agent.reasoning_effort || "Auto"}</td>
          <td className="max-w-64 break-words py-2.5 pr-4 text-rvbbit-accent">{selected.has(agent.key) && validModel ? <><span className="block">{model}</span><span className="text-[10px]">{provider} · {effort === "keep" ? "thinking unchanged" : effort || "Auto"}</span></> : <span className="text-chrome-text/45">—</span>}</td>
        </tr>)}</tbody>
      </table>
      {!visible.length && <p className="p-8 text-center text-chrome-text/70">{loading ? "Reading agents…" : snapshot ? "No agents match this view." : "Agent settings are not available."}</p>}
    </div>
    <footer className="flex shrink-0 flex-wrap items-center gap-3 border-t border-chrome-border bg-chrome-bg px-4 py-3">
      <div className="min-w-0 flex-1"><strong>{chosen.length} selected</strong><span className="ml-2 text-chrome-text/65">{selected.size - visible.filter((a) => selected.has(a.key)).length > 0 ? "Includes agents hidden by the filter." : `${agents.length} agents total`}</span>
        <p className="mt-1 text-[11px] text-chrome-text/70">{busy || pendingRevision ? "Gateway changes are being verified. Your selection is preserved until completion." : validModel ? `${changes.length} assignments will change. Applying restarts Hermes${changes.some((c) => c.agent === "default") && changes.length > 1 ? "; Calliope verifies separately" : ""}.` : "Choose a provider and a model to preview the selected assignments."}</p>
      </div>
      <Button disabled={disabled || !validModel || !changes.length} onClick={() => void apply()}>{busy || pendingRevision ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}Apply to {validModel ? changes.length : chosen.length} agents</Button>
    </footer>
  </div>
}
