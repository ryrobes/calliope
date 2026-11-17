"use client"

/**
 * Acceleration Observatory — the operator's single coherent view of the
 * acceleration machinery: coverage, policy freshness, end-to-end child-layout
 * freshness, worker activity, the attention queue, rebuild cost, and the
 * autopilot's own actions.  Every number comes from the extension's
 * rvbbit.accel_observatory_* views (0329), which the Callie system dashboard
 * and the autopilot read too — so this tab, that page, and the machinery agree
 * on what "current" and "fresh" mean.
 */

import { useCallback, useEffect, useMemo, useState } from "react"

import { Activity, AlertTriangle, Clock, Database, Layers, RefreshCw, Sparkles, Zap } from "@/lib/icons"
import {
  accelRebuildSql,
  accelRefreshSql,
  execAccel,
  fetchAccelObservatory,
  reconcileStaleVortexSql,
  runAutopilotActSql,
  type ObservatoryAttentionRow,
  type ObservatoryLayoutRow,
  type ObservatoryLiveRow,
  type ObservatoryRebuildCostRow,
  type ObservatorySnapshot,
  type ObservatoryTimelineRow,
} from "@/lib/rvbbit/routing"
import { cn } from "@/lib/utils"

import { fmtAgo, fmtCount, fmtMs, HBars, Metric, Panel } from "./instruments"

interface Props {
  activeConnectionId: string | null
  onOpenSql: (title: string, sql: string, run: boolean) => void
}

const BUCKET_LABEL: Record<string, string> = {
  current: "Current (clean)",
  within: "Within target",
  "1_2x": "1–2× target",
  "2_5x": "2–5× target",
  "5x_plus": "5×+ target",
  no_target: "No target",
}

function bucketColor(bucket: string): string {
  if (bucket === "current" || bucket === "within") return "var(--rvbbit-accent)"
  if (bucket === "no_target") return "color-mix(in oklab, var(--foreground) 35%, transparent)"
  return "var(--warning, #f5b446)"
}

function pct(n: number | null): string {
  return n == null ? "—" : `${n.toFixed(1)}%`
}

export function RoutingObservatoryTab({ activeConnectionId, onOpenSql }: Props) {
  const [snap, setSnap] = useState<ObservatorySnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [hours, setHours] = useState<1 | 2 | 3>(3)
  const [busy, setBusy] = useState<string | null>(null)
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null)
  const [auto, setAuto] = useState(true)

  const load = useCallback(async () => {
    if (!activeConnectionId) return
    const res = await fetchAccelObservatory(activeConnectionId, { timelineHours: hours })
    setError(res.error ?? null)
    setSnap(res.snapshot)
    setLoaded(true)
    setRefreshing(false)
  }, [activeConnectionId, hours])

  const refresh = useCallback(async () => {
    setRefreshing(true)
    await load()
  }, [load])

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      if (cancelled || !activeConnectionId) return
      await load()
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [activeConnectionId, load])

  useEffect(() => {
    if (!auto || !activeConnectionId) return
    const id = window.setInterval(() => void load(), 30_000)
    return () => window.clearInterval(id)
  }, [auto, activeConnectionId, load])

  const act = useCallback(
    async (key: string, sql: string, label: string) => {
      if (!activeConnectionId) return
      setBusy(key)
      setToast(null)
      const res = await execAccel(activeConnectionId, sql)
      setToast(res.ok ? { ok: true, msg: label } : { ok: false, msg: res.error ?? "failed" })
      setBusy(null)
      await load()
    },
    [activeConnectionId, load],
  )

  if (!loaded || !snap) {
    return (
      <div className="grid h-full place-items-center text-[12px] text-chrome-text/60">
        <span className="inline-flex items-center gap-2">
          <RefreshCw className="h-3.5 w-3.5 animate-spin" /> loading acceleration observatory…
        </span>
      </div>
    )
  }

  if (!snap.installed) {
    return (
      <div className="grid h-full place-items-center p-6 text-center text-[12px] text-chrome-text/60">
        <div>
          <Database className="mx-auto mb-2 h-6 w-6 text-chrome-text/30" />
          The observatory views (rvbbit.accel_observatory_*) are not installed on this warehouse yet.
          <div className="mt-1 text-[11px]">Run <span className="font-mono">SELECT rvbbit.migrate()</span> on an extension that carries migration 0329.</div>
        </div>
      </div>
    )
  }

  const c = snap.coverage
  return (
    <div className="flex h-full flex-col">
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-chrome-border bg-chrome-bg/30 px-3 py-1.5 text-[11px]">
        <span className="inline-flex items-center gap-1.5 text-foreground">
          <Activity className="h-3.5 w-3.5 text-rvbbit-accent" /> Acceleration Observatory
        </span>
        <span className="text-chrome-text/50">rvbbit.accel_observatory_* · live</span>
        <span className="ml-auto inline-flex items-center gap-2">
          <label className="inline-flex items-center gap-1 text-chrome-text/70">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> auto 30s
          </label>
          <button
            type="button"
            className="rounded border border-chrome-border px-2 py-0.5 hover:bg-chrome-bg"
            onClick={() => void act("autopilot", runAutopilotActSql(), "autopilot pass requested")}
            disabled={busy != null}
            title="Run one deterministic autopilot pass now (mode must be 'act')"
          >
            <Sparkles className="mr-1 inline h-3 w-3" /> autopilot pass
          </button>
          <button
            type="button"
            className="rounded border border-chrome-border px-2 py-0.5 hover:bg-chrome-bg"
            onClick={() => onOpenSql("Observatory · coverage", "SELECT * FROM rvbbit.accel_observatory_coverage;\nSELECT * FROM rvbbit.accel_observatory_tables ORDER BY table_name;", false)}
          >
            SQL
          </button>
          <button
            type="button"
            className="rounded border border-chrome-border px-2 py-0.5 hover:bg-chrome-bg"
            onClick={() => void refresh()}
            disabled={refreshing}
          >
            <RefreshCw className={cn("mr-1 inline h-3 w-3", refreshing && "animate-spin")} /> refresh
          </button>
        </span>
      </div>

      {(error || toast) && (
        <div
          className={cn(
            "border-b border-chrome-border px-3 py-1 text-[11px]",
            error || (toast && !toast.ok) ? "text-danger" : "text-rvbbit-accent",
          )}
        >
          {error ?? toast?.msg}
        </div>
      )}

      <div className="flex-1 overflow-auto p-3">
        {/* headline */}
        {c && (
          <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-9">
            <Metric label="healthy" value={pct(c.healthyPct ?? c.coveragePct)} />
            <Metric label="eligible tables" value={fmtCount(c.eligibleTables)} />
            <Metric label="current" value={fmtCount(c.currentTables)} />
            <Metric label="dirty · within target" value={fmtCount(c.withinTargetTables)} tone="muted" />
            <Metric label="behind" value={fmtCount(c.behindTables)} tone={c.behindTables ? "danger" : undefined} />
            <Metric label="delta-capable" value={`${fmtCount(c.deltaCapableTables)} / ${fmtCount(c.eligibleTables)}`} />
            <Metric label="running now" value={fmtCount(c.runningNow)} />
            <Metric label="beyond target" value={fmtCount(c.beyondTarget)} tone={c.beyondTarget ? "danger" : "muted"} />
            <Metric
              label={`autopilot · ${c.autopilotMode ?? "?"}`}
              value={`${fmtCount(c.autopilotManaged)} managed · ${fmtCount(c.autopilotParked)} parked`}
              tone={c.autopilotMode === "act" ? undefined : "muted"}
            />
          </div>
        )}

        <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-3">
          <Panel icon={Clock} title="How far dirty tables are from target">
            <HBars
              rows={snap.buckets.map((b) => ({
                label: BUCKET_LABEL[b.bucket] ?? b.bucket,
                value: b.tables,
                valueLabel: fmtCount(b.tables),
                color: bucketColor(b.bucket),
              }))}
            />
            <div className="mt-2 text-[10px] text-chrome-text/50">
              Distance is seconds dirty ÷ target; scheduled policies use min_interval as their target. Clean tables cannot be late.
            </div>
          </Panel>
          <Panel icon={Layers} title="Readiness by strategy">
            <HBars
              rows={snap.strategies.map((s) => ({
                label: s.strategy,
                value: s.tables,
                valueLabel: `${s.current}/${s.tables} current`,
                sub: `${s.deltaCapable} delta-capable`,
              }))}
            />
          </Panel>
          <Panel icon={Zap} title="End-to-end child freshness">
            <div className="grid grid-cols-3 gap-2">
              {snap.layoutFamilies.map((f) => (
                <div key={f.family} className="rounded border border-chrome-border/60 p-2">
                  <div className="flex items-baseline justify-between">
                    <span className="text-[11px] text-foreground">{f.family}</span>
                    <span
                      className={cn(
                        "font-mono text-[13px] tabular-nums",
                        (f.freshPct ?? 100) >= 95 ? "text-rvbbit-accent" : (f.freshPct ?? 0) >= 75 ? "text-warning" : "text-danger",
                      )}
                    >
                      {pct(f.freshPct)}
                    </span>
                  </div>
                  <div className="mt-1 text-[10px] text-chrome-text/60">
                    {f.fresh}/{f.layouts} fresh · {f.synced} synced
                    {f.waitingOnParent ? ` · ${f.waitingOnParent} waiting on parent` : ""}
                  </div>
                  <div className="text-[10px] text-chrome-text/50">
                    {f.tables} tables · {f.files} files · oldest {f.oldestRefresh ? fmtAgo(f.oldestRefresh) : "—"}
                  </div>
                </div>
              ))}
              {snap.layoutFamilies.length === 0 && (
                <div className="col-span-3 text-[11px] text-chrome-text/50">No configured child layouts.</div>
              )}
            </div>
            {snap.layoutAttention.length > 0 && <LayoutAttention rows={snap.layoutAttention} />}
          </Panel>
        </div>

        <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-3">
          <Panel
            icon={Activity}
            title={`Worker activity · rolling ${hours}h`}
            className="xl:col-span-2"
            right={
              <span className="inline-flex gap-1">
                {[1, 2, 3].map((h) => (
                  <button
                    key={h}
                    type="button"
                    className={cn(
                      "rounded border px-1.5 text-[10px]",
                      hours === h ? "border-rvbbit-accent text-foreground" : "border-chrome-border text-chrome-text/60",
                    )}
                    onClick={() => setHours(h as 1 | 2 | 3)}
                  >
                    {h}h
                  </button>
                ))}
              </span>
            }
          >
            <LiveNow rows={snap.live} />
            <Timeline rows={snap.timeline} hours={hours} now={snap.fetchedAt} />
          </Panel>
          <Panel icon={Sparkles} title="Autopilot actions">
            {snap.autopilotEvents.length === 0 ? (
              <div className="text-[11px] text-chrome-text/50">
                No promote / park / reactivate actions recorded yet.
              </div>
            ) : (
              <ul className="space-y-1 text-[11px]">
                {snap.autopilotEvents.slice(0, 12).map((e, i) => (
                  <li key={i} className="flex items-baseline gap-2">
                    <span className="w-14 shrink-0 text-chrome-text/50">{fmtAgo(e.at)}</span>
                    <span
                      className={cn(
                        "shrink-0 rounded px-1 font-mono text-[10px]",
                        e.status === "ok" ? "bg-rvbbit-accent/15 text-rvbbit-accent" : "bg-warning/15 text-warning",
                      )}
                    >
                      {e.action}
                    </span>
                    <span className="truncate text-foreground">{e.tableName}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>

        <div className="mt-3">
          <Panel
            icon={AlertTriangle}
            title={`Eligible tables not current · ${snap.attention.filter((r) => !r.healthy).length} need attention · ${snap.attention.filter((r) => r.healthy).length} in flight`}
            right={
              <button
                type="button"
                className="rounded border border-chrome-border px-1.5 text-[10px] hover:bg-chrome-bg"
                onClick={() => void act("reconcile", reconcileStaleVortexSql(), "stale layout reconcile requested")}
                disabled={busy != null}
              >
                reconcile stale layouts
              </button>
            }
          >
            <AttentionTable rows={snap.attention} busy={busy} onAct={act} onOpenSql={onOpenSql} />
          </Panel>
        </div>

        <div className="mt-3">
          <Panel
            icon={Database}
            title="Most expensive full rebuilds"
            right={
              <span className="text-[10px] text-chrome-text/50">
                {snap.completedRuns ? `${fmtCount(snap.completedRuns)} completed · since ${fmtAgo(snap.windowStart)}` : "no completed full rebuilds"}
              </span>
            }
          >
            <RebuildCostTable rows={snap.rebuildCost} />
          </Panel>
        </div>
      </div>
    </div>
  )
}

function LayoutAttention({ rows }: { rows: ObservatoryLayoutRow[] }) {
  return (
    <div className="mt-2 max-h-40 overflow-auto">
      <table className="w-full border-separate border-spacing-0 text-[10px]">
        <thead className="sticky top-0 bg-chrome-bg">
          <tr className="text-left text-chrome-text/50">
            <th className="py-0.5 pr-2 font-normal">table</th>
            <th className="py-0.5 pr-2 font-normal">layout</th>
            <th className="py-0.5 pr-2 font-normal">gate</th>
            <th className="py-0.5 pr-2 font-normal">child rows</th>
            <th className="py-0.5 font-normal">refreshed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.tableName}:${r.layout}`} className="border-t border-chrome-border/40">
              <td className="py-0.5 pr-2 font-mono text-foreground">{r.tableName}</td>
              <td className="py-0.5 pr-2 font-mono text-chrome-text/70">{r.layout}</td>
              <td className={cn("py-0.5 pr-2", r.gate === "parent_stale" ? "text-warning" : "text-danger")}>
                {r.gate.replace("_", " ")}
              </td>
              <td className="py-0.5 pr-2 font-mono tabular-nums">
                {r.actualRows == null ? "—" : fmtCount(r.actualRows)} / {r.currentRows == null ? "—" : fmtCount(r.currentRows)}
              </td>
              <td className="py-0.5 text-chrome-text/60">{r.refreshedAt ? fmtAgo(r.refreshedAt) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function LiveNow({ rows }: { rows: ObservatoryLiveRow[] }) {
  if (rows.length === 0) {
    return <div className="mb-2 text-[11px] text-chrome-text/50">No active workers — idle.</div>
  }
  return (
    <div className="mb-2 grid grid-cols-1 gap-1.5 md:grid-cols-2 xl:grid-cols-4">
      {rows.map((r) => (
        <div key={r.pid} className="rounded border border-chrome-border/60 p-1.5 text-[11px]">
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate font-mono text-foreground">{r.tableName ?? `pid ${r.pid}`}</span>
            <span
              className={cn(
                "font-mono tabular-nums",
                r.implausible ? "text-danger" : r.beyondP95 ? "text-warning" : "text-rvbbit-accent",
              )}
              title={r.implausible ? "elapsed > 24h — likely a stale/orphaned live row" : undefined}
            >
              {fmtMs(r.elapsedMs)}
            </span>
          </div>
          <div className="text-[10px] text-chrome-text/50">
            {r.lane ?? "?"} · {r.action ?? "?"} · {r.state ?? ""}
            {r.p95Ms != null ? ` · P95 ${fmtMs(r.p95Ms)}` : ""}
            {r.waitEvent ? ` · wait ${r.waitEvent}` : ""}
          </div>
        </div>
      ))}
    </div>
  )
}

/** Worker-overlap timeline: one lane per concurrent slot, teal = full, amber = delta. */
function Timeline({ rows, hours, now }: { rows: ObservatoryTimelineRow[]; hours: number; now: number }) {
  const { lanes, start, end } = useMemo(() => {
    const start = now - hours * 3600_000
    const sorted = [...rows].filter((r) => r.finishedAt >= start).sort((a, b) => a.startedAt - b.startedAt)
    const laneEnds: number[] = []
    const lanes: ObservatoryTimelineRow[][] = []
    for (const r of sorted) {
      let placed = false
      for (let i = 0; i < laneEnds.length; i++) {
        if (laneEnds[i] <= r.startedAt) {
          laneEnds[i] = r.finishedAt
          lanes[i].push(r)
          placed = true
          break
        }
      }
      if (!placed) {
        laneEnds.push(r.finishedAt)
        lanes.push([r])
      }
      if (lanes.length >= 8) break
    }
    return { lanes, start, end: now }
  }, [rows, hours, now])
  const span = Math.max(1, end - start)
  if (lanes.length === 0) {
    return <div className="text-[11px] text-chrome-text/50">No finished table operations in this window.</div>
  }
  return (
    <div className="space-y-1">
      {lanes.map((lane, i) => (
        <div key={i} className="relative h-4 rounded bg-chrome-bg/40">
          <span className="absolute left-1 top-0 text-[9px] text-chrome-text/40">W{i + 1}</span>
          {lane.map((r, j) => {
            const left = ((Math.max(r.startedAt, start) - start) / span) * 100
            const width = Math.max(0.3, ((r.finishedAt - Math.max(r.startedAt, start)) / span) * 100)
            return (
              <div
                key={j}
                className="absolute top-0.5 h-3 rounded-sm"
                style={{
                  left: `${left}%`,
                  width: `${width}%`,
                  background: r.action === "full" ? "var(--rvbbit-accent)" : "var(--warning, #f5b446)",
                  opacity: r.status === "ok" ? 0.9 : 0.45,
                }}
                title={`${r.tableName} · ${r.action} · ${r.status} · ${fmtMs(r.elapsedMs)} · ${fmtCount(r.rowsWritten)} rows`}
              />
            )
          })}
        </div>
      ))}
      <div className="flex justify-between text-[9px] text-chrome-text/40">
        <span>{new Date(start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
        <span>teal = full · amber = delta · faded = not ok</span>
        <span>{new Date(end).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
      </div>
    </div>
  )
}

function AttentionTable({
  rows,
  busy,
  onAct,
  onOpenSql,
}: {
  rows: ObservatoryAttentionRow[]
  busy: string | null
  onAct: (key: string, sql: string, label: string) => void | Promise<void>
  onOpenSql: (title: string, sql: string, run: boolean) => void
}) {
  if (rows.length === 0) {
    return <div className="text-[11px] text-chrome-text/50">Every eligible table is current.</div>
  }
  return (
    <div className="max-h-72 overflow-auto">
      <table className="w-full min-w-[900px] border-separate border-spacing-0 text-[11px]">
        <thead className="sticky top-0 bg-chrome-bg">
          <tr className="text-left text-chrome-text/50">
            <th className="py-1 pr-2 font-normal">table</th>
            <th className="py-1 pr-2 font-normal">strategy</th>
            <th className="py-1 pr-2 font-normal">state</th>
            <th className="py-1 pr-2 font-normal">dirty for</th>
            <th className="py-1 pr-2 font-normal">target</th>
            <th className="py-1 pr-2 font-normal">distance</th>
            <th className="py-1 pr-2 font-normal">delta?</th>
            <th className="py-1 pr-2 font-normal">rgs / tombstones</th>
            <th className="py-1 pr-2 font-normal">reason</th>
            <th className="py-1 font-normal" />
          </tr>
        </thead>
        <tbody>
          {[...rows].sort((a, b) => Number(a.healthy) - Number(b.healthy) || a.priority - b.priority).map((r) => (
            <tr key={r.tableName} className={cn("border-t border-chrome-border/40", r.healthy && "opacity-60")}>
              <td className="py-1 pr-2 font-mono text-foreground">{r.tableName}</td>
              <td className="py-1 pr-2 text-chrome-text/70">{r.strategy}</td>
              <td className={cn("py-1 pr-2", r.state === "fold due" ? "text-warning" : "text-chrome-text/80")}>
                {r.state}
                {r.healthy ? <span className="ml-1 text-[9px] text-chrome-text/50">within target</span> : null}
              </td>
              <td className="py-1 pr-2 font-mono tabular-nums">{r.secondsDirty == null ? "—" : fmtMs(r.secondsDirty * 1000)}</td>
              <td className="py-1 pr-2 font-mono tabular-nums">{r.targetSecs == null ? "—" : fmtMs(r.targetSecs * 1000)}</td>
              <td
                className={cn(
                  "py-1 pr-2 font-mono tabular-nums",
                  r.targetDistance != null && r.targetDistance > 2 ? "text-danger" : r.targetDistance != null && r.targetDistance > 1 ? "text-warning" : "",
                )}
              >
                {r.targetDistance == null ? "—" : `${r.targetDistance.toFixed(1)}×`}
              </td>
              <td className={cn("py-1 pr-2", r.deltaCapable ? "text-rvbbit-accent" : "text-chrome-text/50")} title={r.deltaCapable ? "PK overlay ready — updates/deletes refresh as deltas" : "no primary-key overlay — updates/deletes force a full rebuild"}>
                {r.deltaCapable ? "delta" : "full only"}
              </td>
              <td className="py-1 pr-2 font-mono tabular-nums text-chrome-text/70">
                {fmtCount(r.rowGroups)} / {fmtCount(r.tombstones)}
              </td>
              <td className="max-w-[260px] truncate py-1 pr-2 text-chrome-text/60" title={r.blockedReason ?? ""}>
                {r.blockedReason ?? "—"}
              </td>
              <td className="py-1 text-right">
                <span className="inline-flex gap-1">
                  <button
                    type="button"
                    className="rounded border border-chrome-border px-1.5 text-[10px] hover:bg-chrome-bg"
                    disabled={busy != null}
                    onClick={() => void onAct(r.tableName, accelRefreshSql(r.tableName), `refresh requested: ${r.tableName}`)}
                  >
                    refresh
                  </button>
                  <button
                    type="button"
                    className="rounded border border-chrome-border px-1.5 text-[10px] hover:bg-chrome-bg"
                    disabled={busy != null}
                    onClick={() => onOpenSql(`Rebuild · ${r.tableName}`, accelRebuildSql(r.tableName), false)}
                    title="Open the full rebuild statement in a SQL tab (not executed)"
                  >
                    rebuild…
                  </button>
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function RebuildCostTable({ rows }: { rows: ObservatoryRebuildCostRow[] }) {
  if (rows.length === 0) {
    return <div className="text-[11px] text-chrome-text/50">No completed full rebuilds recorded.</div>
  }
  return (
    <div className="max-h-80 overflow-auto">
      <table className="w-full min-w-[900px] border-separate border-spacing-0 text-[11px]">
        <thead className="sticky top-0 bg-chrome-bg">
          <tr className="text-left text-chrome-text/50">
            <th className="py-1 pr-2 font-normal">#</th>
            <th className="py-1 pr-2 font-normal">table</th>
            <th className="py-1 pr-2 font-normal">avg</th>
            <th className="py-1 pr-2 font-normal">runs</th>
            <th className="py-1 pr-2 font-normal">P95</th>
            <th className="py-1 pr-2 font-normal">avg rows</th>
            <th className="py-1 pr-2 font-normal">cost / day</th>
            <th className="py-1 pr-2 font-normal">delta?</th>
            <th className="py-1 pr-2 font-normal">last</th>
            <th className="py-1 font-normal">typical trigger</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.tableName} className="border-t border-chrome-border/40">
              <td className="py-1 pr-2 text-chrome-text/50">{r.rank}</td>
              <td className="py-1 pr-2 font-mono text-foreground">{r.tableName}</td>
              <td className={cn("py-1 pr-2 font-mono tabular-nums", r.avgMs > 60_000 ? "text-warning" : "")}>{fmtMs(r.avgMs)}</td>
              <td className="py-1 pr-2 font-mono tabular-nums">{r.samples}</td>
              <td className="py-1 pr-2 font-mono tabular-nums">{r.p95Ms == null ? "—" : fmtMs(r.p95Ms)}</td>
              <td className="py-1 pr-2 font-mono tabular-nums">{fmtCount(Math.round(r.avgRows))}</td>
              <td className={cn("py-1 pr-2 font-mono tabular-nums", r.dailyCostMs > 600_000 ? "text-danger" : r.dailyCostMs > 60_000 ? "text-warning" : "")}>
                {fmtMs(r.dailyCostMs)}
              </td>
              <td className={cn("py-1 pr-2", r.deltaCapable ? "text-rvbbit-accent" : "text-chrome-text/50")}>{r.deltaCapable ? "delta" : "full only"}</td>
              <td className="py-1 pr-2 text-chrome-text/60">{r.lastFinishedAt ? fmtAgo(r.lastFinishedAt) : "—"}</td>
              <td className="max-w-[320px] truncate py-1 text-chrome-text/60" title={r.commonReason ?? ""}>
                {r.commonReason ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
