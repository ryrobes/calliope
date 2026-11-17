"use client"

/**
 * System Health — dedicated maintenance window for the rvbbit engine.
 *
 * Adaptive Routing answers "is it fast?"; this window answers "is it
 * hygienic?" — metadata weight, tombstone accrual, time-travel history
 * buildup, catalog snapshot retention, orphan backlog, vacuum pressure,
 * and whether the maintenance crons are installed at all.
 *
 * Routine hygiene is engine-owned. This window explains that automation and
 * opens reviewable SQL for diagnostics or an exceptional run-now operation.
 */

import { useCallback, useEffect, useState } from "react"
import {
  Activity,
  Clock,
  Database,
  Layers,
  Loader2,
  RefreshCw,
  Trash2,
  Wrench,
} from "@/lib/icons"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { fmtAgo, fmtBytes, fmtRows } from "@/lib/rvbbit/finder-format"
import type { SystemHealth } from "@/lib/db/system-health"

interface SystemHealthWindowProps {
  activeConnectionId: string | null
  onOpenSql?: (title: string, sql: string, run: boolean) => void
}

type ScriptKind =
  | "rebuild"
  | "reap-generations"
  | "snapshots-retention"
  | "orphaned-files"
  | "vacuum-metadata"
  | "install-jobs"

export function SystemHealthWindow({ activeConnectionId, onOpenSql }: SystemHealthWindowProps) {
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [building, setBuilding] = useState<ScriptKind | null>(null)

  const load = useCallback(async () => {
    if (!activeConnectionId) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(
        `/api/db/system-health?connectionId=${encodeURIComponent(activeConnectionId)}`,
        { cache: "no-store" },
      )
      if (!res.ok) throw new Error((await res.text().catch(() => "")) || `HTTP ${res.status}`)
      setHealth((await res.json()) as SystemHealth)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [activeConnectionId])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const buildScript = useCallback(
    async (kind: ScriptKind, title: string, params: Record<string, string | number>) => {
      if (!activeConnectionId || !onOpenSql) return
      setBuilding(kind)
      try {
        const qs = new URLSearchParams({
          connectionId: activeConnectionId,
          kind,
          ...Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)])),
        })
        const res = await fetch(`/api/db/maintenance-script?${qs}`, { cache: "no-store" })
        const body = (await res.json()) as { sql?: string; error?: string }
        if (!res.ok || !body.sql) throw new Error(body.error || `HTTP ${res.status}`)
        onOpenSql(title, body.sql, false)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBuilding(null)
      }
    },
    [activeConnectionId, onOpenSql],
  )

  const metaBytes = (health?.metaTables ?? []).reduce((a, t) => a + t.bytes, 0)
  const metaPct = health && health.dbSizeBytes > 0 ? (metaBytes / health.dbSizeBytes) * 100 : 0
  const metaTone = metaPct > 50 ? "text-destructive" : metaPct > 25 ? "text-warning" : "text-chrome-text"

  const maintenanceJobs = (health?.cron.jobs ?? []).filter((j) =>
    /maintenance|vacuum|reap|prune|retention/i.test(j.jobname),
  )
  const diagnosticMissing = health?.postgresDiagnostics
    ? [
        ["pg_stat_statements", "pg_stat_statements"],
        ["hypopg", "HypoPG"],
        ["generic_plan", "Postgres 16 generic plans"],
      ]
        .filter(([key]) => health.postgresDiagnostics?.capabilities[key] === false)
        .map(([, label]) => label)
    : []

  return (
    <div className="flex h-full flex-col bg-chrome-bg/20 text-[12px]">
      {/* header */}
      <div className="flex items-center gap-2 border-b border-chrome-border bg-chrome-bg/40 px-3 py-1.5">
        <Wrench className="h-3.5 w-3.5 text-rvbbit-accent" />
        <span className="font-medium text-foreground">System Health</span>
        <span className="text-[10px] uppercase tracking-wider text-chrome-text/60">
          metadata · maintenance
        </span>
        <div className="flex-1" />
        {health ? (
          <span className="text-[10px] text-chrome-text/50">as of {fmtAgo(health.generatedAt)}</span>
        ) : null}
        <Button size="sm" variant="ghost" onClick={() => void load()} disabled={loading} title="Refresh">
          <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
        </Button>
      </div>

      {error ? (
        <div className="border-b border-destructive/30 bg-destructive/10 px-3 py-1.5 text-[11px] text-destructive">
          {error}
        </div>
      ) : null}

      {!health && loading ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-chrome-text/60">
          <Loader2 className="h-4 w-4 animate-spin" /> reading the engine…
        </div>
      ) : null}

      {health ? (
        <div className="flex-1 space-y-3 overflow-y-auto p-3">
          {/* ── storage split ── */}
          <Card
            icon={<Database className="h-3.5 w-3.5" />}
            title="Metadata weight"
            summary={
              <>
                <b className={metaTone}>{fmtBytes(metaBytes)}</b> of {fmtBytes(health.dbSizeBytes)} database (
                <b className={metaTone}>{metaPct.toFixed(0)}%</b>) is rvbbit metadata & exhaust
              </>
            }
          >
            <div className="flex h-2 w-full overflow-hidden rounded-sm border border-chrome-border/50">
              {health.metaTables.slice(0, 6).map((t, i) => (
                <span
                  key={t.name}
                  title={`rvbbit.${t.name} — ${fmtBytes(t.bytes)}`}
                  style={{
                    width: `${health.dbSizeBytes > 0 ? (t.bytes / health.dbSizeBytes) * 100 : 0}%`,
                    background: `color-mix(in srgb, var(--rvbbit-accent) ${85 - i * 12}%, var(--info))`,
                  }}
                />
              ))}
              <span className="flex-1 bg-foreground/10" title="table data + everything else" />
            </div>
            <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-0.5 md:grid-cols-3">
              {health.metaTables.slice(0, 9).map((t) => (
                <div key={t.name} className="flex justify-between gap-2 text-[11px]">
                  <span className="truncate text-chrome-text/70">{t.name}</span>
                  <span className="tabular-nums text-chrome-text">{fmtBytes(t.bytes)}</span>
                </div>
              ))}
            </div>
          </Card>

          {/* ── deterministic PostgreSQL diagnostics ── */}
          <Card
            icon={<Activity className="h-3.5 w-3.5" />}
            title="PostgreSQL diagnostics"
            tone={
              health.postgresDiagnostics?.findings.some((finding) => finding.severity === "critical")
                ? "bad"
                : health.postgresDiagnostics?.findings.some((finding) => finding.severity === "warn")
                  ? "warn"
                  : undefined
            }
            summary={
              health.postgresDiagnostics ? (
                <>
                  <b>{health.postgresDiagnostics.findings.length}</b> deterministic finding
                  {health.postgresDiagnostics.findings.length === 1 ? "" : "s"} ·{" "}
                  <b>{health.postgresDiagnostics.indexAdvice.length}</b> planner-validated index candidate
                  {health.postgresDiagnostics.indexAdvice.length === 1 ? "" : "s"}. Exactness and safety
                  travel with the evidence; inspected query text is not retained.
                  {health.postgresDiagnostics.status === "degraded" && diagnosticMissing.length > 0 ? (
                    <> Index advice is unavailable without {diagnosticMissing.join(", ")}.</>
                  ) : null}
                </>
              ) : health.fns.collect_postgres_diagnostics ? (
                <>No diagnostic snapshot has been collected yet.</>
              ) : (
                <>Deterministic PostgreSQL diagnostics are unavailable on this extension build.</>
              )
            }
            action={
              health.fns.collect_postgres_diagnostics && onOpenSql ? (
                <RemedyButton
                  label="Open collection SQL"
                  busy={false}
                  onClick={() => onOpenSql(
                    "PostgreSQL diagnostics · collect",
                    "-- Collects deterministic findings and optional HypoPG advice.\n-- It never executes or stores inspected query text.\nSELECT jsonb_pretty(rvbbit.collect_postgres_diagnostics());\n\nSELECT * FROM rvbbit.postgres_diagnostic_current_findings;\nSELECT * FROM rvbbit.postgres_index_advice_current;",
                    false,
                  )}
                />
              ) : undefined
            }
          >
            {health.postgresDiagnostics?.findings.map((finding) => (
              <div
                key={`${finding.findingKey}:${finding.objectIdentity}`}
                className="flex gap-2 border-t border-chrome-border/40 py-1.5 first:border-0 first:pt-0"
              >
                <span
                  className={cn(
                    "mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full",
                    finding.severity === "critical"
                      ? "bg-destructive"
                      : finding.severity === "warn"
                        ? "bg-warning"
                        : "bg-info",
                  )}
                />
                <div className="min-w-0 flex-1">
                  <div className="text-[11px] text-foreground">{finding.summary}</div>
                  <div className="mt-0.5 text-[10px] text-chrome-text/55">
                    {finding.findingKey} · {finding.objectIdentity} · {finding.exactness}
                  </div>
                </div>
              </div>
            ))}
            {(health.postgresDiagnostics?.indexAdvice ?? []).map((advice) => (
              <div
                key={advice.candidateKey}
                className="flex items-start justify-between gap-3 border-t border-chrome-border/40 py-1.5 first:border-0 first:pt-0"
              >
                <div className="min-w-0">
                  <div className="truncate text-[11px] text-foreground">
                    {advice.tableName} ({advice.columns.join(", ")})
                  </div>
                  <div className="text-[10px] text-chrome-text/55">
                    query {advice.queryId} · {advice.dbTimePct.toFixed(1)}% database time · planner estimate
                  </div>
                </div>
                <b className="shrink-0 tabular-nums text-success">
                  −{advice.improvementPct.toFixed(0)}% cost
                </b>
              </div>
            ))}
          </Card>

          {/* ── tombstones ── */}
          {health.deleteLog ? (
            <Card
              icon={<Trash2 className="h-3.5 w-3.5" />}
              title="Tombstones (delete_log)"
              tone={
                health.deleteLog.top.some((t) => t.recentFinalLockFailures > 0)
                  ? "warn"
                  : health.deleteLog.totalRows > 50_000_000
                    ? "bad"
                    : health.deleteLog.totalRows > 5_000_000
                      ? "warn"
                      : undefined
              }
              summary={
                <>
                  <b>{fmtRows(health.deleteLog.totalRows)}</b> live tombstones · {fmtBytes(health.deleteLog.bytes)}.
                  The engine folds these into clean baselines; blocked folds cool down instead of retrying continuously.
                </>
              }
              action={
                <RemedyButton
                  label="Open pressure SQL"
                  busy={building === "rebuild"}
                  disabled={!onOpenSql}
                  onClick={() => void buildScript("rebuild", "Maintenance · accelerator pressure", {})}
                />
              }
            >
              <BarList
                rows={health.deleteLog.top.map((t) => ({
                  key: String(t.oid),
                  label: t.dropped ? `(dropped oid ${t.oid})` : `${t.schema}.${t.table}`,
                  value: t.tombstones,
                  note: t.retryAfter
                    ? `cooldown · ${t.recentFinalLockFailures} lock miss${t.recentFinalLockFailures === 1 ? "" : "es"}/h`
                    : t.liveRows != null && t.liveRows > 0
                      ? `${(t.tombstones / t.liveRows).toFixed(1)}× live · ${fmtRows(t.rowGroups)} groups`
                      : undefined,
                  dropped: t.dropped,
                }))}
              />
            </Card>
          ) : null}

          {/* ── generations ── */}
          {health.generations ? (
            <Card
              icon={<Layers className="h-3.5 w-3.5" />}
              title="Time-travel history (generations)"
              summary={
                <>
                  Snapshot histories follow their automatic retention policy (30 days by default).
                  Cumulative histories contain live rows, so the engine bounds them by folding rather than age-pruning.
                </>
              }
              action={
                <RemedyButton
                  label="Open retention SQL"
                  busy={building === "reap-generations"}
                  disabled={!onOpenSql}
                  onClick={() => void buildScript("reap-generations", "Maintenance · generation retention", {})}
                />
              }
            >
              <BarList
                rows={health.generations.top.map((g) => ({
                  key: String(g.oid),
                  label: g.dropped ? `(dropped oid ${g.oid})` : `${g.schema}.${g.table}`,
                  value: g.generations,
                  note: `${g.generationSemantics ?? "?"}/${g.historyPolicy ?? "?"} · ${g.minVisibleGeneration > 0 ? `${g.effectiveKeepDays}d retention` : "fold-bounded"}`,
                  dropped: g.dropped,
                }))}
              />
            </Card>
          ) : null}

          {/* ── catalog history ── */}
          {health.catalog ? (
            <Card
              icon={<Clock className="h-3.5 w-3.5" />}
              title="Catalog crawl history"
              tone={health.catalog.snapshotBytes > 5 * 1024 ** 3 ? "warn" : undefined}
              summary={
                <>
                  <b>{health.catalog.runs}</b> runs across {health.catalog.scopes} crawl scopes ·{" "}
                  {fmtRows(health.catalog.snapshotRows)} snapshots · <b>{fmtBytes(health.catalog.snapshotBytes)}</b>.
                  {health.fns.catalog_maintain ? (
                    <> The engine retains 15 runs per scope automatically.</>
                  ) : (
                    <> <b className="text-warning">Automatic catalog hygiene is unavailable on this extension build.</b></>
                  )}
                  {health.catalog.oversizedSnapshots > 0 ? (
                    <> <b className="text-warning"> {fmtRows(health.catalog.oversizedSnapshots)} snapshots exceed 1MB.</b></>
                  ) : null}
                </>
              }
              action={
                <RemedyButton
                  label="Run hygiene now"
                  busy={building === "snapshots-retention"}
                  disabled={!health.fns.catalog_maintain || !onOpenSql}
                  onClick={() => void buildScript("snapshots-retention", "Maintenance · catalog hygiene", {})}
                />
              }
            />
          ) : null}

          {/* ── orphaned files ── */}
          {health.orphaned ? (
            <Card
              icon={<Trash2 className="h-3.5 w-3.5" />}
              title="Orphaned file backlog"
              tone={health.orphaned.erroring > 0 ? "warn" : undefined}
              summary={
                <>
                  <b>{fmtRows(health.orphaned.backlog)}</b> queued ·{" "}
                  <b>{fmtRows(health.orphaned.reapable)}</b> reapable ·{" "}
                  <b>{fmtRows(health.orphaned.referenced)}</b> still referenced
                  {health.orphaned.erroring > 0 ? (
                    <>
                      {" · "}
                      <b className="text-warning">{fmtRows(health.orphaned.erroring)} erroring</b>
                    </>
                  ) : null}
                  {health.orphaned.oldestQueuedAt ? <> · oldest {fmtAgo(health.orphaned.oldestQueuedAt)}</> : null}
                </>
              }
              action={
                <RemedyButton
                  label="Build reap script"
                  busy={building === "orphaned-files"}
                  disabled={!health.fns.reap_orphaned_files || !onOpenSql}
                  onClick={() => void buildScript("orphaned-files", "Maintenance · reap orphaned files", {})}
                />
              }
            />
          ) : null}

          {/* ── vacuum pressure ── */}
          <Card
            icon={<Activity className="h-3.5 w-3.5" />}
            title="Vacuum pressure"
            summary={
              <>
                {health.vacuum.running > 0 ? (
                  <b>{health.vacuum.running} vacuum{health.vacuum.running > 1 ? "s" : ""} running now · </b>
                ) : null}
                highest dead-tuple tables below
              </>
            }
            action={
              <RemedyButton
                label="Build vacuum script"
                busy={building === "vacuum-metadata"}
                disabled={!onOpenSql}
                onClick={() => void buildScript("vacuum-metadata", "Maintenance · vacuum metadata", {})}
              />
            }
          >
            <BarList
              rows={health.vacuum.top.slice(0, 8).map((v) => ({
                key: `${v.schema}.${v.table}`,
                label: `${v.schema}.${v.table}`,
                value: v.dead,
                note: v.lastAutovacuum
                  ? `av ${fmtAgo(v.lastAutovacuum)} ×${v.autovacuumCount}`
                  : "never autovacuumed",
              }))}
            />
          </Card>

          {/* ── maintenance jobs ── */}
          <Card
            icon={<Clock className="h-3.5 w-3.5" />}
            title="Maintenance jobs"
            tone={health.cron.readable && maintenanceJobs.length === 0 ? "warn" : undefined}
            summary={
              health.cron.readable ? (
                maintenanceJobs.length > 0 ? (
                  <>{maintenanceJobs.length} maintenance-related cron job{maintenanceJobs.length > 1 ? "s" : ""} scheduled</>
                ) : (
                  <>
                    <b className="text-warning">No maintenance jobs scheduled.</b> The engine ships them —
                    they just need installing.
                  </>
                )
              ) : (
                <>
                  cron tables not readable from this database
                  {health.cron.home ? (
                    <>
                      {" "}(pg_cron home is <code>{health.cron.home}</code> — the install script below
                      is built to run there)
                    </>
                  ) : null}
                  .
                </>
              )
            }
            action={
              <RemedyButton
                label="Build install script"
                busy={building === "install-jobs"}
                disabled={!health.fns.install_maintenance_jobs || !onOpenSql}
                onClick={() => void buildScript("install-jobs", "Maintenance · install cron jobs", {})}
              />
            }
          >
            {health.cron.readable && health.cron.jobs.length > 0 ? (
              <div className="grid grid-cols-1 gap-y-0.5 md:grid-cols-2">
                {health.cron.jobs.map((j) => (
                  <div key={j.jobname} className="flex items-center gap-2 text-[11px]">
                    <span className={cn("h-1.5 w-1.5 rounded-full", j.active ? "bg-success" : "bg-foreground/25")} />
                    <span className="truncate text-chrome-text/80">{j.jobname}</span>
                    <span className="ml-auto font-mono text-[10px] text-chrome-text/50">{j.schedule}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </Card>

          {!health.hasRvbbit ? (
            <div className="rounded border border-chrome-border/60 bg-chrome-bg/30 p-3 text-[11px] text-chrome-text/60">
              pg_rvbbit is not installed on this connection — only the vacuum section applies.
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

// ─── little pieces ───────────────────────────────────────────────────

function Card({
  icon,
  title,
  summary,
  tone,
  action,
  children,
}: {
  icon: React.ReactNode
  title: string
  summary: React.ReactNode
  tone?: "warn" | "bad"
  action?: React.ReactNode
  children?: React.ReactNode
}) {
  return (
    <div
      className={cn(
        "rounded border bg-chrome-bg/30 p-3",
        tone === "bad"
          ? "border-destructive/40"
          : tone === "warn"
            ? "border-warning/40"
            : "border-chrome-border/60",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={cn(
            tone === "bad" ? "text-destructive" : tone === "warn" ? "text-warning" : "text-rvbbit-accent",
          )}
        >
          {icon}
        </span>
        <span className="font-medium text-foreground">{title}</span>
        <div className="flex-1" />
        {action}
      </div>
      <p className="mt-1.5 text-[11.5px] leading-relaxed text-chrome-text/80">{summary}</p>
      {children ? <div className="mt-2">{children}</div> : null}
    </div>
  )
}

function RemedyButton({
  label,
  busy,
  disabled,
  onClick,
}: {
  label: string
  busy: boolean
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <Button
      size="sm"
      variant="outline"
      className="h-6 gap-1.5 text-[11px]"
      disabled={disabled || busy}
      onClick={onClick}
      title="Generates a reviewable SQL script and opens it in a SQL window — nothing runs from here"
    >
      {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Wrench className="h-3 w-3" />}
      {label} →
    </Button>
  )
}

function BarList({
  rows,
}: {
  rows: Array<{ key: string; label: string; value: number; note?: string; dropped?: boolean }>
}) {
  const max = rows[0]?.value ?? 0
  return (
    <div className="space-y-0.5">
      {rows.map((r) => (
        <div key={r.key} className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)_auto] items-center gap-2 text-[11px]">
          <span className={cn("truncate", r.dropped ? "text-chrome-text/40 line-through" : "text-chrome-text/80")}>
            {r.label}
          </span>
          <span className="h-1.5 overflow-hidden rounded-sm bg-foreground/5">
            <span
              className="block h-full rounded-sm bg-rvbbit-accent/70"
              style={{ width: `${max > 0 ? Math.max((r.value / max) * 100, 1) : 0}%` }}
            />
          </span>
          <span className="tabular-nums text-chrome-text">
            {fmtRows(r.value)}
            {r.note ? <span className="ml-1.5 text-[10px] text-chrome-text/50">{r.note}</span> : null}
          </span>
        </div>
      ))}
    </div>
  )
}
