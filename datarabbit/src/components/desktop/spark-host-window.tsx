"use client"

import { useCallback, useState, type ReactNode } from "react"
import { RefreshCw, Sparkles } from "@/lib/icons"
import { cn } from "@/lib/utils"
import { usePolling } from "@/lib/desktop/use-polling"
import { useWorkspaceActive } from "./workspace-active-context"
import {
  fetchSparkSnapshot,
  type SparkSnapshot,
} from "@/lib/rvbbit/sparks"

interface Props { activeConnectionId: string | null; hasRvbbit: boolean }

const stateTones: Record<string, string> = {
  live: "var(--rvbbit-accent)",
  entered: "var(--success)",
  hidden: "var(--warning)",
  expired: "var(--chrome-text)",
  retired: "var(--chrome-text)",
}

function when(value: string | null | undefined) {
  if (!value) return "—"
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })
}

function StateBadge({ value }: { value: string }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 font-mono text-[8px] uppercase tracking-[0.12em]"
      style={{ color: stateTones[value] || "var(--chrome-text)" }}
    >
      <i className="h-1.5 w-1.5 rounded-full bg-current" />
      {value}
    </span>
  )
}

function PaneTitle({ label, note }: { label: string; note?: string }) {
  return (
    <div className="sticky top-0 z-10 flex items-baseline justify-between border-b border-chrome-border bg-chrome px-4 py-2">
      <span className="font-mono text-[8px] font-semibold uppercase tracking-[0.14em] text-chrome-text/45">{label}</span>
      {note ? <span className="font-mono text-[8px] text-chrome-text/35">{note}</span> : null}
    </div>
  )
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="grid h-full place-items-center p-8 text-center text-[11px] text-chrome-text/55">{children}</div>
}

export function SparkHostWindow({ activeConnectionId, hasRvbbit }: Props) {
  const workspaceActive = useWorkspaceActive()
  const [snapshot, setSnapshot] = useState<SparkSnapshot | null>(null)
  const [loading, setLoading] = useState(false)

  const reload = useCallback(async () => {
    if (!activeConnectionId || !hasRvbbit) return
    setLoading(true)
    setSnapshot(await fetchSparkSnapshot(activeConnectionId))
    setLoading(false)
  }, [activeConnectionId, hasRvbbit])

  usePolling(reload, 60_000, {
    enabled: Boolean(activeConnectionId && hasRvbbit && workspaceActive),
    resetKey: activeConnectionId,
  })

  if (!activeConnectionId || !hasRvbbit)
    return <Empty>The Spark Host needs an active RVBBIT connection.</Empty>
  if (snapshot && !snapshot.ready)
    return (
      <Empty>
        rvbbit.calliope_floor_sparks is not installed here yet (migration 0396).
        Schedule generation with SELECT rvbbit.schedule_spark_host().
      </Empty>
    )

  const kinds = snapshot?.kinds ?? []
  const totals = kinds.reduce(
    (acc, row) => ({
      live: acc.live + row.live,
      entered: acc.entered + row.entered,
      hidden: acc.hidden + row.hidden,
      impressions: acc.impressions + row.impressions,
    }),
    { live: 0, entered: 0, hidden: 0, impressions: 0 },
  )
  const conversion = totals.impressions
    ? `${((totals.entered / totals.impressions) * 100).toFixed(1)}%`
    : "—"

  return (
    <div className="flex h-full flex-col bg-doc-bg/20">
      <div className="flex shrink-0 items-center gap-3 border-b border-chrome-border bg-chrome px-4 py-2">
        <Sparkles className="h-3.5 w-3.5 text-rvbbit-accent" />
        <span className="text-[11px] font-semibold text-foreground">Spark Host</span>
        <span className="font-mono text-[9px] text-chrome-text/45">
          the arrival band&apos;s substrate — doors, not plans
        </span>
        <span className="ml-auto flex items-center gap-4 font-mono text-[9px] text-chrome-text/65">
          <span>{totals.live} live</span>
          <span>{totals.entered} entered</span>
          <span>{totals.hidden} hidden</span>
          <span title="entered / impressions — conversion, not clicks">{conversion} conversion</span>
          <button
            type="button"
            onClick={() => void reload()}
            className="inline-flex items-center gap-1 rounded border border-chrome-border/70 px-1.5 py-0.5 hover:bg-foreground/[0.06]"
          >
            <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
            refresh
          </button>
        </span>
      </div>
      {snapshot?.error ? (
        <div className="border-b border-danger/30 bg-danger/10 px-4 py-1.5 text-[10px] text-danger">{snapshot.error}</div>
      ) : null}
      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[minmax(280px,0.9fr)_minmax(0,1.6fr)]">
        <div className="min-h-0 overflow-auto border-b border-chrome-border xl:border-b-0 xl:border-r">
          <PaneTitle label="By kind" />
          <table className="w-full text-left text-[10px]">
            <thead>
              <tr className="font-mono text-[8px] uppercase tracking-[0.12em] text-chrome-text/40">
                <th className="px-4 py-1.5">kind</th>
                <th className="px-2 py-1.5 text-right">live</th>
                <th className="px-2 py-1.5 text-right">entered</th>
                <th className="px-2 py-1.5 text-right">hidden</th>
                <th className="px-4 py-1.5 text-right">impressions</th>
              </tr>
            </thead>
            <tbody className="font-mono tabular-nums text-chrome-text/85">
              {kinds.map((row) => (
                <tr key={row.kind} className="border-t border-chrome-border/40">
                  <td className="px-4 py-1.5 text-foreground">{row.kind}</td>
                  <td className="px-2 py-1.5 text-right">{row.live}</td>
                  <td className="px-2 py-1.5 text-right">{row.entered}</td>
                  <td className="px-2 py-1.5 text-right">{row.hidden}</td>
                  <td className="px-4 py-1.5 text-right">{row.impressions}</td>
                </tr>
              ))}
              {!kinds.length ? (
                <tr><td colSpan={5} className="px-4 py-6 text-center text-chrome-text/50">
                  {snapshot ? "No sparks minted yet — run rvbbit.spark_host_tick()." : "Loading…"}
                </td></tr>
              ) : null}
            </tbody>
          </table>
          <PaneTitle label="By person" note="live · entered · hidden" />
          <div className="px-4 py-2 text-[10px]">
            {(snapshot?.owners ?? []).map((row) => (
              <div key={row.owner_email} className="flex items-baseline justify-between border-b border-chrome-border/30 py-1 last:border-b-0">
                <span className="min-w-0 truncate text-foreground">{row.owner_email}</span>
                <span className="ml-3 shrink-0 font-mono tabular-nums text-chrome-text/70">
                  {row.live} · {row.entered} · {row.hidden}
                </span>
              </div>
            ))}
            {!snapshot?.owners?.length ? (
              <div className="py-4 text-center text-chrome-text/50">No per-person sparks yet.</div>
            ) : null}
          </div>
        </div>
        <div className="min-h-0 overflow-auto">
          <PaneTitle label="Recent sparks" note="newest first" />
          {(snapshot?.recent ?? []).map((row) => (
            <div key={row.id} className="border-b border-chrome-border/40 px-4 py-2">
              <div className="flex items-baseline gap-2">
                <span className="rounded border border-chrome-border/60 bg-foreground/[0.03] px-1 font-mono text-[8px] uppercase tracking-wider text-chrome-text/60">{row.kind}</span>
                <strong className="min-w-0 flex-1 truncate text-[11px] font-medium text-foreground">{row.title}</strong>
                <StateBadge value={row.state} />
              </div>
              <div className="mt-0.5 flex items-baseline gap-2 font-mono text-[8px] text-chrome-text/50">
                <span className="min-w-0 truncate">{row.owner_email ?? "(audience)"}</span>
                <span>· {row.kicker}</span>
                <span>· {row.impressions} views</span>
                {row.hidden_reason ? <span className="text-warning">· “{row.hidden_reason}”</span> : null}
                <span className="ml-auto shrink-0">{when(row.created_at)}</span>
              </div>
            </div>
          ))}
          {!snapshot?.recent?.length ? (
            <Empty>{snapshot ? "Nothing minted yet." : "Loading…"}</Empty>
          ) : null}
        </div>
      </div>
    </div>
  )
}
