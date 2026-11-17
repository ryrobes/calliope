"use client"

/**
 * Colony — docs/PEER_CAPABILITIES_PLAN.md. Share a local LLM/ML model with
 * anyone in scope on this Postgres, and see everything currently shared
 * (by this instance or others). Combines P2's "Share a Capability" and
 * P3's "My Shared Capabilities" into one pane for this first pass — a
 * dedicated system-wide roster (reusing Finder's live-vitals rendering)
 * is a follow-up; this list already shows every peer backend live.
 */

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  AlertTriangle,
  Loader2,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  Users,
  Trash2,
  X,
} from "@/lib/icons"
import { cn } from "@/lib/utils"
import { usePolling } from "@/lib/desktop/use-polling"
import { Panel, Metric, fmtAgo } from "./instruments"
import {
  fetchPeerBackends,
  fetchScopeRoleOptions,
  type PeerBackend,
} from "@/lib/rvbbit/colony"

interface ColonyWindowProps {
  activeConnectionId: string | null
  onOpenSqlData?: (sql: string, title: string) => void
}

interface LocalShare {
  backendName: string
  localUrl: string
  model?: string
}

interface ScanCandidate {
  shape: "openai_chat" | "gradio"
  suggestedName: string
  localUrl: string
  confidence: number
  detail: string
  models?: string[]
}

const KIND_OPTIONS = ["llm", "specialist", "mcp"] as const
const SHAPE_OPTIONS = ["openai_chat", "gradio", "mcp"] as const
const SHAPE_LABELS: Record<(typeof SHAPE_OPTIONS)[number], string> = {
  openai_chat: "OpenAI-compatible chat (Ollama, vLLM, ...)",
  gradio: "Gradio app",
  mcp: "MCP server (stdio)",
}

export function ColonyWindow({ activeConnectionId, onOpenSqlData }: ColonyWindowProps) {
  const [backends, setBackends] = useState<PeerBackend[]>([])
  const [localShares, setLocalShares] = useState<LocalShare[]>([])
  const [roles, setRoles] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [busyName, setBusyName] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!activeConnectionId) return
    setLoading(true)
    const [b, s] = await Promise.all([
      fetchPeerBackends(activeConnectionId),
      fetch(`/api/db/colony/status?connectionId=${encodeURIComponent(activeConnectionId)}`, { cache: "no-store" })
        .then((r) => r.json())
        .catch(() => ({ ok: false })),
    ])
    setError(b.error)
    setBackends(b.backends)
    if (s?.ok) setLocalShares(s.shares ?? [])
    setLoading(false)
  }, [activeConnectionId])

  useEffect(() => {
    if (!activeConnectionId) return
    void fetchScopeRoleOptions(activeConnectionId).then((r) => setRoles(r.roles))
  }, [activeConnectionId])

  usePolling(load, 5000, { enabled: !!activeConnectionId, resetKey: activeConnectionId })

  const localNames = useMemo(() => new Set(localShares.map((s) => s.backendName)), [localShares])

  const toggleEnabled = useCallback(
    async (b: PeerBackend) => {
      if (!activeConnectionId) return
      setBusyName(b.backendName)
      setError(null)
      try {
        const res = await fetch("/api/db/colony/set-enabled", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ connectionId: activeConnectionId, backendName: b.backendName, enabled: !b.enabled }),
        })
        const body = (await res.json()) as { ok: boolean; error?: string }
        if (!body.ok) setError(body.error || "failed to update backend")
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusyName(null)
        await load()
      }
    },
    [activeConnectionId, load],
  )

  const detach = useCallback(
    async (b: PeerBackend) => {
      if (!activeConnectionId) return
      if (!confirm(`Stop sharing "${b.backendName}"? Any pending requests against it will fail.`)) return
      setBusyName(b.backendName)
      setError(null)
      try {
        const res = await fetch("/api/db/colony/deregister", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ connectionId: activeConnectionId, backendName: b.backendName }),
        })
        const body = (await res.json()) as { ok: boolean; error?: string }
        if (!body.ok) setError(body.error || "failed to stop sharing")
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusyName(null)
        await load()
      }
    },
    [activeConnectionId, load],
  )

  return (
    <div className="flex h-full flex-col bg-chrome-bg/20 text-[12px]">
      <div className="flex items-center gap-2 border-b border-chrome-border bg-chrome-bg/40 px-3 py-1.5">
        <Users className="h-3.5 w-3.5 text-rvbbit-accent" />
        <span className="font-medium text-foreground">Colony</span>
        <span className="text-[10px] uppercase tracking-wider text-chrome-text/60">
          shared local capabilities
        </span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => setFormOpen((v) => !v)}
          className="inline-flex items-center gap-1 rounded border border-rvbbit-accent/50 bg-rvbbit-bg px-2 py-1 text-[11px] font-medium text-rvbbit-accent hover:bg-rvbbit-accent/15"
        >
          <Plus className="h-3 w-3" />
          Share a capability
        </button>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="grid h-6 w-6 place-items-center rounded text-chrome-text/60 hover:bg-foreground/[0.08] hover:text-foreground"
          title="Refresh"
        >
          <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
        </button>
      </div>

      {error ? (
        <div className="flex items-start gap-1.5 border-b border-danger/30 bg-danger/10 px-3 py-1.5 text-[11px] text-danger">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
          {error}
        </div>
      ) : null}

      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {formOpen ? (
          <ShareForm
            connId={activeConnectionId}
            roles={roles}
            onCancel={() => setFormOpen(false)}
            onDone={async () => {
              setFormOpen(false)
              await load()
            }}
          />
        ) : null}

        {!loading && backends.length === 0 ? (
          <div className="rounded border border-chrome-border/60 bg-chrome-bg/30 p-3 text-[11px] text-chrome-text/60">
            No peer backends shared on this database yet. Click &ldquo;Share a capability&rdquo; to attach a local
            model (e.g. Ollama) that anyone in scope can call.
          </div>
        ) : null}

        {backends.map((b) => (
          <BackendCard
            key={b.backendName}
            b={b}
            mine={localNames.has(b.backendName)}
            busy={busyName === b.backendName}
            onToggleEnabled={() => void toggleEnabled(b)}
            onDetach={() => void detach(b)}
            onTryIt={
              onOpenSqlData
                ? () => {
                    const payload =
                      b.kind === "mcp"
                        ? '\'{"tool": "REPLACE_WITH_TOOL_NAME", "args": {}}\''
                        : '\'{"user": "hello"}\''
                    onOpenSqlData(
                      `SELECT rvbbit.call_specialist('${b.backendName.replace(/'/g, "''")}', ${payload}::jsonb);`,
                      `Try ${b.backendName}`,
                    )
                  }
                : undefined
            }
          />
        ))}
      </div>
    </div>
  )
}

function BackendCard({
  b,
  mine,
  busy,
  onToggleEnabled,
  onDetach,
  onTryIt,
}: {
  b: PeerBackend
  mine: boolean
  busy: boolean
  onToggleEnabled: () => void
  onDetach: () => void
  onTryIt?: () => void
}) {
  const live = b.live && b.enabled
  return (
    <Panel
      title={b.backendName}
      icon={Users}
      right={
        <>
          <span
            className={cn(
              "flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] normal-case",
              live ? "bg-success/15 text-success" : "bg-foreground/10 text-chrome-text/50",
            )}
          >
            <span className={cn("h-1.5 w-1.5 rounded-full", live ? "bg-success" : "bg-chrome-text/40")} />
            {!b.enabled ? "paused" : live ? `live · ${b.instanceCount} instance${b.instanceCount === 1 ? "" : "s"}` : "offline"}
          </span>
          {live && onTryIt ? (
            <button
              type="button"
              onClick={onTryIt}
              title="Open a prefilled call to this backend in a SQL window"
              className="rounded border border-chrome-border px-1.5 py-0.5 text-[10px] font-medium text-foreground hover:bg-foreground/[0.08]"
            >
              Try it
            </button>
          ) : null}
          {mine ? (
            <>
              <button
                type="button"
                onClick={onToggleEnabled}
                disabled={busy}
                title={b.enabled ? "Pause sharing" : "Resume sharing"}
                className="grid h-5 w-5 place-items-center rounded text-chrome-text/60 hover:bg-foreground/[0.08] hover:text-foreground disabled:opacity-40"
              >
                {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : b.enabled ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3" />}
              </button>
              <button
                type="button"
                onClick={onDetach}
                disabled={busy}
                title="Stop sharing (detach)"
                className="grid h-5 w-5 place-items-center rounded text-chrome-text/60 hover:bg-danger/15 hover:text-danger disabled:opacity-40"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </>
          ) : null}
        </>
      }
    >
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <Metric label="kind" value={b.template ? `${b.kind} · ${b.template}` : b.kind} />
        <Metric label="scope" value={b.scopeRole} />
        <Metric label="shared by" value={mine ? "you (this client)" : b.sharedBy} />
        <Metric label="queue depth" value={b.minQueueDepth ?? "—"} />
        <Metric
          label="calls"
          value={`${b.done} ok${b.failed ? ` · ${b.failed} failed` : ""}${b.pending ? ` · ${b.pending} pending` : ""}`}
        />
      </div>
      {b.description ? (
        <p className="mt-2 text-[11px] text-chrome-text/70">{b.description}</p>
      ) : null}
      {b.createdMs ? (
        <p className="mt-1 text-[10px] text-chrome-text/45">shared {fmtAgo(b.createdMs)}</p>
      ) : null}
    </Panel>
  )
}

function ShareForm({
  connId,
  roles,
  onDone,
  onCancel,
}: {
  connId: string | null
  roles: string[]
  onDone: () => Promise<void>
  onCancel: () => void
}) {
  const [name, setName] = useState("")
  const [kind, setKind] = useState<(typeof KIND_OPTIONS)[number]>("llm")
  const [scopeRole, setScopeRole] = useState("")
  const [shape, setShape] = useState<(typeof SHAPE_OPTIONS)[number]>("openai_chat")
  const [localUrl, setLocalUrl] = useState("http://localhost:11434/v1/chat/completions")
  const [model, setModel] = useState("llama3.1:latest")
  const [description, setDescription] = useState("")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const [scanning, setScanning] = useState(false)
  const [scanned, setScanned] = useState(false)
  const [scanResults, setScanResults] = useState<ScanCandidate[]>([])
  const [scanError, setScanError] = useState<string | null>(null)

  const [mcpCommand, setMcpCommand] = useState("")
  const [mcpArgs, setMcpArgs] = useState("")
  const [mcpEnvText, setMcpEnvText] = useState("")
  const [mcpTools, setMcpTools] = useState<{ name: string; description?: string }[] | null>(null)
  const [mcpTesting, setMcpTesting] = useState(false)
  const [mcpTestError, setMcpTestError] = useState<string | null>(null)

  const changeShape = (next: (typeof SHAPE_OPTIONS)[number]) => {
    setShape(next)
    if (next === "gradio") setLocalUrl("http://127.0.0.1:7860")
    else if (next === "openai_chat") setLocalUrl("http://localhost:11434/v1/chat/completions")
  }

  const parseMcpEnv = (): Record<string, string> | undefined => {
    if (!mcpEnvText.trim()) return undefined
    const env: Record<string, string> = {}
    for (const line of mcpEnvText.split("\n")) {
      const eq = line.indexOf("=")
      if (eq < 0) continue
      const k = line.slice(0, eq).trim()
      const v = line.slice(eq + 1).trim()
      if (k) env[k] = v
    }
    return Object.keys(env).length > 0 ? env : undefined
  }

  const testMcpConnection = async () => {
    if (!mcpCommand.trim()) return
    setMcpTesting(true)
    setMcpTestError(null)
    setMcpTools(null)
    try {
      const res = await fetch("/api/db/colony/mcp-list-tools", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          command: mcpCommand.trim(),
          args: mcpArgs.split(/\s+/).filter(Boolean),
          env: parseMcpEnv(),
        }),
      })
      const body = (await res.json()) as { ok: boolean; tools?: { name: string; description?: string }[]; error?: string }
      if (!body.ok) throw new Error(body.error || "connection failed")
      setMcpTools(body.tools ?? [])
    } catch (e) {
      setMcpTestError(e instanceof Error ? e.message : String(e))
    } finally {
      setMcpTesting(false)
    }
  }

  const runScan = async () => {
    setScanning(true)
    setScanError(null)
    try {
      const res = await fetch("/api/db/colony/scan", { cache: "no-store" })
      const body = (await res.json()) as { ok: boolean; candidates?: ScanCandidate[]; error?: string }
      if (!body.ok) throw new Error(body.error || "scan failed")
      setScanResults(body.candidates ?? [])
    } catch (e) {
      setScanError(e instanceof Error ? e.message : String(e))
    } finally {
      setScanning(false)
      setScanned(true)
    }
  }

  const applyCandidate = (c: ScanCandidate) => {
    setName(c.suggestedName)
    setShape(c.shape)
    setLocalUrl(c.localUrl)
    setKind(c.shape === "gradio" ? "specialist" : "llm")
    if (c.models && c.models.length > 0) setModel(c.models[0])
  }

  const locationReady = shape === "mcp" ? !!mcpCommand.trim() : !!localUrl.trim()

  const submit = async () => {
    if (!connId || !name.trim() || !scopeRole.trim() || !locationReady) return
    setBusy(true)
    setErr(null)
    try {
      const res = await fetch("/api/db/colony/register", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          connectionId: connId,
          backendName: name.trim(),
          kind,
          scopeRole: scopeRole.trim(),
          shape,
          localUrl: shape === "mcp" ? undefined : localUrl.trim(),
          model: shape === "openai_chat" ? model.trim() || undefined : undefined,
          command: shape === "mcp" ? mcpCommand.trim() : undefined,
          args: shape === "mcp" ? mcpArgs.split(/\s+/).filter(Boolean) : undefined,
          env: shape === "mcp" ? parseMcpEnv() : undefined,
          description: description.trim() || undefined,
        }),
      })
      const body = (await res.json()) as { ok: boolean; error?: string }
      if (!body.ok) throw new Error(body.error || "register failed")
      await onDone()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      icon={Plus}
      title="Share a capability"
      right={
        <button
          type="button"
          onClick={onCancel}
          className="grid h-5 w-5 place-items-center rounded text-chrome-text/55 hover:bg-foreground/[0.08] hover:text-foreground"
        >
          <X className="h-3 w-3" />
        </button>
      }
    >
      <div className="mb-2 flex flex-col gap-1.5 rounded border border-chrome-border/60 bg-chrome-bg/30 p-2">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void runScan()}
            disabled={scanning}
            className="inline-flex items-center gap-1 rounded border border-chrome-border px-2 py-1 text-[11px] font-medium text-foreground hover:bg-foreground/[0.08] disabled:opacity-40"
          >
            {scanning ? <Loader2 className="h-3 w-3 animate-spin" /> : <Search className="h-3 w-3" />}
            {scanning ? "Scanning…" : "Scan for local models"}
          </button>
          <span className="text-[10px] text-chrome-text/55">
            Checks Ollama&apos;s default port + common Gradio ports. Never runs on its own — only when you click this.
          </span>
        </div>
        {scanError ? <p className="text-[10px] text-danger">{scanError}</p> : null}
        {scanned && !scanning && scanResults.length === 0 && !scanError ? (
          <p className="text-[10px] text-chrome-text/55">
            Nothing found. Use the fields below to add one manually.
          </p>
        ) : null}
        {scanResults.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {scanResults.map((c) => (
              <button
                key={c.localUrl}
                type="button"
                onClick={() => applyCandidate(c)}
                className="rounded border border-rvbbit-accent/40 bg-rvbbit-bg px-2 py-1 text-left text-[11px] text-foreground hover:bg-rvbbit-accent/15"
                title={c.localUrl}
              >
                <span className="font-medium">{c.suggestedName}</span>
                <span className="ml-1.5 text-chrome-text/60">{c.detail}</span>
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Field label="name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value.replace(/[^a-z0-9_-]/gi, "_").toLowerCase())}
            placeholder="my_ollama"
            className={inputCls}
          />
        </Field>
        <Field label="kind">
          <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} className={inputCls}>
            {KIND_OPTIONS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </Field>
        <Field label="scope (burrow role — who can call this)">
          <input
            value={scopeRole}
            onChange={(e) => setScopeRole(e.target.value)}
            placeholder="rvbbit_users"
            list="colony-role-options"
            className={inputCls}
          />
          <datalist id="colony-role-options">
            {roles.map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
        </Field>
        <Field label="shape">
          <select value={shape} onChange={(e) => changeShape(e.target.value as typeof shape)} className={inputCls}>
            {SHAPE_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {SHAPE_LABELS[s]}
              </option>
            ))}
          </select>
        </Field>
        {shape === "openai_chat" ? (
          <Field label="model name">
            <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="llama3.1:latest" className={inputCls} />
          </Field>
        ) : null}
        {shape === "mcp" ? (
          <>
            <Field label="command">
              <input
                value={mcpCommand}
                onChange={(e) => {
                  setMcpCommand(e.target.value)
                  setMcpTools(null)
                }}
                placeholder="npx, python, /usr/bin/foo"
                className={inputCls}
              />
            </Field>
            <Field label="args (space-separated)">
              <input
                value={mcpArgs}
                onChange={(e) => {
                  setMcpArgs(e.target.value)
                  setMcpTools(null)
                }}
                placeholder="-y @modelcontextprotocol/server-everything"
                className={inputCls}
              />
            </Field>
            <Field label="env vars (KEY=value, one per line)" wide>
              <textarea
                value={mcpEnvText}
                onChange={(e) => {
                  setMcpEnvText(e.target.value)
                  setMcpTools(null)
                }}
                rows={2}
                placeholder="API_KEY=${API_KEY}"
                className={areaCls}
              />
            </Field>
          </>
        ) : (
          <Field
            label={shape === "gradio" ? "local URL (Gradio app root)" : "local URL (OpenAI-compatible chat endpoint)"}
            wide
          >
            <input
              value={localUrl}
              onChange={(e) => setLocalUrl(e.target.value)}
              placeholder={shape === "gradio" ? "http://127.0.0.1:7860" : "http://localhost:11434/v1/chat/completions"}
              className={inputCls}
            />
          </Field>
        )}
        <Field label="description" wide>
          <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="optional notes" className={inputCls} />
        </Field>
      </div>

      {shape === "mcp" ? (
        <div className="mt-2 flex flex-col gap-1.5 rounded border border-chrome-border/60 bg-chrome-bg/30 p-2">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void testMcpConnection()}
              disabled={mcpTesting || !mcpCommand.trim()}
              className="inline-flex items-center gap-1 rounded border border-chrome-border px-2 py-1 text-[11px] font-medium text-foreground hover:bg-foreground/[0.08] disabled:opacity-40"
            >
              {mcpTesting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Search className="h-3 w-3" />}
              {mcpTesting ? "Connecting…" : "Test connection & list tools"}
            </button>
            <span className="text-[10px] text-chrome-text/55">
              Spawns the command once to discover its tools, then closes it — the claim-loop spawns its own long-lived copy separately.
            </span>
          </div>
          {mcpTestError ? <p className="text-[10px] text-danger">{mcpTestError}</p> : null}
          {mcpTools ? (
            mcpTools.length > 0 ? (
              <ul className="space-y-0.5">
                {mcpTools.map((t) => (
                  <li key={t.name} className="text-[11px] text-chrome-text/80">
                    <span className="font-mono text-foreground">{t.name}</span>
                    {t.description ? <span className="ml-1.5 text-chrome-text/55">{t.description}</span> : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[10px] text-chrome-text/55">Connected, but this server reports no tools.</p>
            )
          ) : null}
        </div>
      ) : null}

      {err ? (
        <div className="mt-2 flex items-start gap-1.5 rounded border border-danger/40 bg-danger/10 px-2 py-1 text-[10px] text-danger">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
          <span className="break-words font-mono">{err}</span>
        </div>
      ) : null}

      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy || !name.trim() || !scopeRole.trim() || !locationReady}
          className="inline-flex items-center gap-1 rounded border border-rvbbit-accent/50 bg-rvbbit-bg px-2 py-1 text-[11px] font-medium text-rvbbit-accent hover:bg-rvbbit-accent/15 disabled:opacity-40"
        >
          {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Users className="h-3 w-3" />}
          {busy ? "Sharing…" : "Share"}
        </button>
        <span className="text-[10px] text-chrome-text/55">
          Only roles granted membership in the scope role can call this — anyone else is rejected.
        </span>
      </div>
    </Panel>
  )
}

function Field({ label, wide, children }: { label: string; wide?: boolean; children: React.ReactNode }) {
  return (
    <label className={cn("block", wide ? "col-span-2" : "")}>
      <span className="mb-0.5 block text-[10px] text-chrome-text/60">{label}</span>
      {children}
    </label>
  )
}

const inputCls =
  "h-7 w-full rounded border border-chrome-border bg-doc-bg px-2 font-mono text-[11px] text-foreground outline-none focus:border-main/60"
const areaCls =
  "w-full rounded border border-chrome-border bg-doc-bg px-2 py-1 font-mono text-[11px] leading-snug text-foreground outline-none focus:border-main/60"
