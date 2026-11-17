"use client"

import { useCallback, useEffect, useState } from "react"
import { CheckCircle2, Clock, Loader2, RefreshCw, RotateCcw, Shield, XCircle } from "@/lib/icons"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface HermesRevision {
  id: string
  revision: number
  state: string
  config_hash: string
  reason: string
  redacted_diff: { operation?: string; path?: string }[]
  error?: string | null
  created_at: string
  created_by: string
  completed_at?: string | null
  is_active: boolean
  is_last_good: boolean
}

interface HermesSnapshot {
  state: {
    controller_status?: string
    gateway_status?: string
    active_config_hash?: string
    desired_revision_id?: string | null
    updated_at?: string
  }
  supervisor: {
    status?: string
    gateway_running?: boolean
    restart_count?: number
    config_hash?: string
    error?: string
  }
  revisions: HermesRevision[]
}

async function responseJson(response: Response): Promise<Record<string, unknown>> {
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = body?.error as { message?: string } | undefined
    throw new Error(error?.message || `HTTP ${response.status}`)
  }
  return body
}

export function HermesControlWindow() {
  const [snapshot, setSnapshot] = useState<HermesSnapshot | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch("/api/calliope/admin/hermes", {
        credentials: "same-origin",
        cache: "no-store",
      })
      setSnapshot((await responseJson(response)) as unknown as HermesSnapshot)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const mutate = useCallback(async (path: string, body: Record<string, unknown>, label: string) => {
    setBusy(label)
    setError(null)
    setNotice(null)
    try {
      const response = await fetch(path, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Calliope-Admin": "1" },
        body: JSON.stringify(body),
      })
      const result = await responseJson(response)
      setNotice(String(result.message || `${label} queued. The controller will verify it in the background.`))
      await load()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(null)
    }
  }, [load])

  const healthy = snapshot?.state.gateway_status === "healthy" && snapshot?.supervisor.gateway_running
  const applying = snapshot?.state.controller_status === "applying" || Boolean(snapshot?.state.desired_revision_id)

  return (
    <div className="flex h-full flex-col bg-chrome-bg/20 text-[12px]">
      <div className="flex items-center gap-2 border-b border-chrome-border bg-chrome-bg/40 px-3 py-2">
        <Shield className="h-4 w-4 text-rvbbit-accent" />
        <div>
          <div className="font-medium text-foreground">Hermes Recovery</div>
          <div className="text-[10px] text-chrome-text/55">verified configuration · automatic rollback</div>
        </div>
        <div className="flex-1" />
        <StatusPill ok={Boolean(healthy)} label={healthy ? "gateway healthy" : "attention needed"} />
        <Button size="sm" variant="ghost" onClick={() => void load()} disabled={loading} title="Refresh">
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
        </Button>
      </div>

      {error ? <div className="border-b border-destructive/30 bg-destructive/10 px-3 py-2 text-destructive">{error}</div> : null}
      {notice ? <div className="border-b border-success/30 bg-success/10 px-3 py-2 text-success">{notice}</div> : null}

      {!snapshot && loading ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-chrome-text/60">
          <Loader2 className="h-4 w-4 animate-spin" /> reading the independent controller…
        </div>
      ) : null}

      {snapshot ? (
        <div className="flex-1 overflow-y-auto p-3">
          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            <Fact label="Controller" value={snapshot.state.controller_status || "unknown"} />
            <Fact label="Gateway" value={snapshot.state.gateway_status || "unknown"} />
            <Fact label="Restarts" value={String(snapshot.supervisor.restart_count ?? "—")} />
          </div>

          <div className="mt-3 flex items-center gap-2 rounded border border-chrome-border bg-background/25 p-3">
            <div className="min-w-0 flex-1">
              <div className="font-medium text-foreground">Restart from the active revision</div>
              <div className="mt-0.5 text-[11px] text-chrome-text/60">
                Re-resolves canonical credentials, restarts Hermes, and verifies the API, command catalog, and Warehouse bridge.
              </div>
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={Boolean(busy) || applying}
              onClick={() => void mutate(
                "/api/calliope/admin/hermes/restart",
                { reason: "operator restart from DataRabbit recovery" },
                "Restart",
              )}
            >
              {busy === "Restart" ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <RotateCcw className="mr-1 h-3 w-3" />}
              Restart safely
            </Button>
          </div>

          <div className="mt-4 flex items-end justify-between gap-2">
            <div>
              <div className="font-medium text-foreground">Revision history</div>
              <div className="text-[10px] text-chrome-text/55">Secret values and raw historical YAML are never shown here.</div>
            </div>
            {applying ? <span className="flex items-center gap-1 text-[10px] text-warning"><Loader2 className="h-3 w-3 animate-spin" /> applying</span> : null}
          </div>

          <div className="mt-2 overflow-hidden rounded border border-chrome-border">
            {snapshot.revisions.length ? snapshot.revisions.map((revision) => (
              <div key={revision.id} className="flex items-start gap-3 border-b border-chrome-border/70 bg-background/20 p-3 last:border-b-0">
                <div className="pt-0.5">
                  {revision.state === "active" ? <CheckCircle2 className="h-4 w-4 text-success" /> :
                    revision.state === "failed" || revision.state === "rolled_back" || revision.state === "rejected" ?
                      <XCircle className="h-4 w-4 text-destructive" /> : <Clock className="h-4 w-4 text-chrome-text/50" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[11px] text-foreground">r{revision.revision}</span>
                    <StatusPill ok={revision.state === "active"} label={revision.state.replaceAll("_", " ")} subtle />
                    {revision.is_last_good && !revision.is_active ? <span className="text-[10px] text-success">last good</span> : null}
                  </div>
                  <div className="mt-1 text-[11px] text-chrome-text/75">{revision.reason || "No reason recorded"}</div>
                  <div className="mt-1 font-mono text-[9px] text-chrome-text/40">
                    {revision.config_hash.slice(0, 12)} · {revision.created_by} · {new Date(revision.created_at).toLocaleString()}
                  </div>
                  {revision.redacted_diff?.length ? (
                    <div className="mt-1 truncate text-[10px] text-chrome-text/50">
                      {revision.redacted_diff.slice(0, 5).map((item) => `${item.operation} ${item.path}`).join(" · ")}
                    </div>
                  ) : null}
                  {revision.error ? <div className="mt-1 text-[10px] text-destructive">{revision.error}</div> : null}
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={revision.is_active || Boolean(busy) || applying}
                  onClick={() => void mutate(
                    "/api/calliope/admin/hermes/restore",
                    { revision_id: revision.id, reason: `operator restored revision ${revision.revision} from DataRabbit` },
                    `Restore r${revision.revision}`,
                  )}
                >
                  {busy === `Restore r${revision.revision}` ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <RotateCcw className="mr-1 h-3 w-3" />}
                  Restore
                </Button>
              </div>
            )) : <div className="p-4 text-chrome-text/55">No Hermes revisions have been recorded yet.</div>}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-chrome-border bg-background/25 px-3 py-2">
      <div className="text-[9px] uppercase tracking-wider text-chrome-text/45">{label}</div>
      <div className="mt-0.5 font-mono text-[11px] text-foreground">{value}</div>
    </div>
  )
}

function StatusPill({ ok, label, subtle = false }: { ok: boolean; label: string; subtle?: boolean }) {
  return (
    <span className={cn(
      "rounded-full border px-2 py-0.5 text-[9px] uppercase tracking-wider",
      ok ? "border-success/35 bg-success/10 text-success" :
        subtle ? "border-chrome-border bg-foreground/5 text-chrome-text/55" :
          "border-warning/35 bg-warning/10 text-warning",
    )}>
      {label}
    </span>
  )
}
