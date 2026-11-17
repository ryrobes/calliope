"use client";

import { useCallback, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  Layers,
  RefreshCw,
  Search,
  Waveform,
} from "@/lib/icons";
import { usePolling } from "@/lib/desktop/use-polling";
import { cn } from "@/lib/utils";
import {
  computeFindings,
  fetchForensicMessageBody,
  fetchForensicSession,
  fetchForensicSnapshot,
  kindColor,
  shortTool,
  type Finding,
  type ForensicCall,
  type ForensicEvent,
  type ForensicMessage,
  type ForensicSession,
  type ForensicSessionDetail,
  type ForensicSnapshot,
} from "@/lib/rvbbit/session-forensics";
import { useWorkspaceActive } from "./workspace-active-context";

interface Props {
  activeConnectionId: string | null;
  hasRvbbit: boolean;
  initialSessionId?: string | null;
}

type Tab = "timeline" | "ledger" | "findings" | "usage" | "models";

const BUDGET_CHOICES = [131_072, 200_000, 262_144, 400_000, 1_000_000];
const THRESHOLD = 0.7;
const EMPTY_DETAIL: ForensicSessionDetail = {
  calls: [],
  events: [],
  messages: [],
  usage: [],
};

function n(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function compact(value: unknown): string {
  return Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n(value));
}

function full(value: unknown): string {
  return value == null ? "—" : Intl.NumberFormat().format(n(value));
}

function when(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function clock(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
}

function duration(ms: unknown): string {
  const value = n(ms);
  if (!value) return "—";
  if (value < 1000) return `${Math.round(value)}ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(1)}s`;
  return `${Math.floor(value / 60_000)}m ${Math.round((value % 60_000) / 1000)}s`;
}

function sessionTitle(session: ForensicSession): string {
  return (session.calliope_title || session.title || session.session_id)
    .replace(/\s+/g, " ")
    .trim();
}

const SEVERITY_COLOR: Record<Finding["severity"], string> = {
  high: "var(--danger)",
  warn: "var(--warning)",
  note: "var(--rvbbit-accent)",
};

export function SessionForensicsWindow({
  activeConnectionId,
  hasRvbbit,
  initialSessionId = null,
}: Props) {
  const workspaceActive = useWorkspaceActive();
  const [snapshot, setSnapshot] = useState<ForensicSnapshot | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(initialSessionId);
  const [detail, setDetail] = useState<ForensicSessionDetail>(EMPTY_DETAIL);
  const [detailFor, setDetailFor] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("timeline");
  const [budget, setBudget] = useState<number>(262_144);
  const [highlightCalls, setHighlightCalls] = useState<Set<number>>(new Set());
  const [highlightMessages, setHighlightMessages] = useState<Set<number>>(
    new Set(),
  );

  const reload = useCallback(async () => {
    if (!activeConnectionId || !hasRvbbit) return;
    setRefreshing(true);
    const next = await fetchForensicSnapshot(activeConnectionId);
    setRefreshing(false);
    setSnapshot(next);
  }, [activeConnectionId, hasRvbbit]);

  usePolling(reload, 30_000, {
    enabled: Boolean(activeConnectionId && hasRvbbit && workspaceActive),
    resetKey: activeConnectionId,
  });

  const sessions = useMemo(() => {
    const rows = snapshot?.sessions ?? [];
    const needle = filter.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter(
      (row) =>
        sessionTitle(row).toLowerCase().includes(needle) ||
        row.session_id.toLowerCase().includes(needle) ||
        (row.calliope_session_id ?? "").toLowerCase().includes(needle) ||
        (row.profile_name ?? "").toLowerCase().includes(needle) ||
        (row.source ?? "").toLowerCase().includes(needle),
    );
  }, [snapshot, filter]);

  const [expandedFamilies, setExpandedFamilies] = useState<Set<string>>(
    () => new Set(),
  );

  // Sidecar sessions nest under the parent that spawned them (lineage from
  // the specialist-thread canon). Families sort by their liveliest member —
  // a build whose sidecar finished after the parent's last word would
  // otherwise sink below unrelated newer sessions. Children render in spawn
  // order so an expanded family reads as the build's story. While a filter
  // is active the list stays flat so matched children are never hidden
  // under a collapsed parent.
  const sessionTree = useMemo(() => {
    const flat = filter.trim().length > 0;
    const stamp = (row: ForensicSession) =>
      new Date(row.last_activity_at || row.started_at || 0).getTime() || 0;
    const byId = new Set(sessions.map((row) => row.session_id));
    const childrenOf = new Map<string, ForensicSession[]>();
    if (!flat) {
      for (const row of sessions) {
        const parent = row.parent_hermes_session_id;
        if (parent && byId.has(parent)) {
          childrenOf.set(parent, [...(childrenOf.get(parent) ?? []), row]);
        }
      }
    }
    const tops: ForensicSession[] = [];
    for (const row of sessions) {
      const parent = row.parent_hermes_session_id;
      if (!flat && parent && byId.has(parent)) continue;
      tops.push(row);
    }
    const familyLatest = new Map<string, number>();
    for (const row of tops) {
      const kids = childrenOf.get(row.session_id) ?? [];
      familyLatest.set(
        row.session_id,
        Math.max(stamp(row), ...kids.map(stamp)),
      );
    }
    tops.sort(
      (a, b) =>
        (familyLatest.get(b.session_id) ?? 0) -
        (familyLatest.get(a.session_id) ?? 0),
    );
    const out: Array<{ row: ForensicSession; depth: number }> = [];
    for (const row of tops) {
      out.push({ row, depth: 0 });
      const kids = [...(childrenOf.get(row.session_id) ?? [])].sort(
        (a, b) =>
          (new Date(a.started_at || 0).getTime() || 0) -
          (new Date(b.started_at || 0).getTime() || 0),
      );
      const open =
        expandedFamilies.has(row.session_id) ||
        kids.some((kid) => kid.session_id === selectedId);
      if (open) for (const kid of kids) out.push({ row: kid, depth: 1 });
    }
    return out;
  }, [sessions, filter, expandedFamilies, selectedId]);

  const toggleFamily = useCallback((sessionId: string) => {
    setExpandedFamilies((prior) => {
      const next = new Set(prior);
      if (next.has(sessionId)) next.delete(sessionId);
      else next.add(sessionId);
      return next;
    });
  }, []);

  // No explicit pick yet → the newest session; derived, not synced through an effect.
  const effectiveId = selectedId ?? sessions[0]?.session_id ?? null;
  const selected = useMemo(
    () =>
      (snapshot?.sessions ?? []).find(
        (row) => row.session_id === effectiveId,
      ) ?? null,
    [snapshot, effectiveId],
  );

  const loadDetail = useCallback(async () => {
    if (!activeConnectionId || !effectiveId) return;
    const next = await fetchForensicSession(activeConnectionId, effectiveId);
    setDetail(next);
    setDetailFor(effectiveId);
  }, [activeConnectionId, effectiveId]);

  usePolling(loadDetail, 30_000, {
    enabled: Boolean(activeConnectionId && effectiveId && workspaceActive),
    resetKey: effectiveId,
  });

  const detailReady = detailFor === effectiveId;
  const findings = useMemo(
    () =>
      computeFindings({
        session: selected,
        calls: detailReady ? detail.calls : [],
        events: detailReady ? detail.events : [],
        messages: detailReady ? detail.messages : [],
        usage: detailReady ? detail.usage : [],
        contextBudget: budget,
        threshold: THRESHOLD,
      }),
    [selected, detail, detailReady, budget],
  );

  const focusFinding = useCallback((finding: Finding) => {
    setHighlightCalls(new Set(finding.callIds ?? []));
    setHighlightMessages(new Set(finding.messageIds ?? []));
    if (finding.callIds?.length) setTab("timeline");
    else if (finding.messageIds?.length) setTab("ledger");
  }, []);

  const totals = useMemo(() => {
    const rows = snapshot?.sessions ?? [];
    return {
      sessions: rows.length,
      calls: rows.reduce((sum, row) => sum + n(row.api_calls), 0),
      compactions: rows.reduce((sum, row) => sum + n(row.compactions), 0),
      replays: rows.reduce(
        (sum, row) => sum + n(row.replayed_user_messages),
        0,
      ),
      redelivery: rows.reduce(
        (sum, row) => sum + n(row.redelivery_replays),
        0,
      ),
      recurrence: rows.reduce(
        (sum, row) => sum + n(row.recurrence_replays),
        0,
      ),
      recovery: rows.reduce((sum, row) => sum + n(row.recovery_replays), 0),
      otherReplays: rows.reduce((sum, row) => sum + n(row.other_replays), 0),
      tokens: rows.reduce((sum, row) => sum + n(row.sum_input_tokens), 0),
    };
  }, [snapshot]);

  if (!hasRvbbit || !activeConnectionId)
    return (
      <Empty copy="Session Forensics needs an active RVBBIT connection." />
    );
  if (snapshot && !snapshot.ready)
    return (
      <Empty copy="The Hermes session mirror (rvbbit.hermes_*) is not installed here yet — the Calliope server creates it on its first mirror tick." />
    );

  return (
    <div className="flex h-full min-h-0 flex-col text-chrome-text">
      <header className="flex items-center gap-3 border-b border-chrome-border px-4 py-3">
        <div className="grid h-9 w-9 place-items-center border border-rvbbit-accent/35 bg-rvbbit-accent/10">
          <Waveform className="h-4 w-4 text-rvbbit-accent" />
        </div>
        <div className="min-w-0">
          <h1 className="text-[13px] font-semibold tracking-tight text-foreground">
            Session Forensics
          </h1>
          <p className="text-[10px] text-chrome-text/55">
            Every Hermes provider call, message and compaction — read from the
            rvbbit.hermes_* mirror
          </p>
        </div>
        <div className="ml-auto flex items-center gap-3 font-mono text-[9px] uppercase tracking-[0.12em] text-chrome-text/50">
          <label className="flex items-center gap-1.5">
            budget
            <select
              value={budget}
              onChange={(event) => setBudget(Number(event.target.value))}
              className="border border-chrome-border bg-transparent px-1.5 py-1 font-mono text-[9px] text-foreground"
            >
              {BUDGET_CHOICES.map((choice) => (
                <option key={choice} value={choice}>
                  {compact(choice)}
                </option>
              ))}
            </select>
            <span className="text-chrome-text/40">
              · threshold {compact(Math.round(budget * THRESHOLD))}
            </span>
          </label>
          <button
            type="button"
            onClick={() => void reload()}
            className="inline-flex items-center gap-1.5 border border-chrome-border px-2 py-1.5 text-foreground hover:border-rvbbit-accent/50"
            disabled={refreshing}
          >
            <RefreshCw
              className={cn("h-3 w-3", refreshing && "animate-spin")}
            />{" "}
            Refresh
          </button>
        </div>
      </header>

      {snapshot?.error ? (
        <div className="flex items-center gap-2 border-b border-danger/35 bg-danger/10 px-4 py-2 text-[10px] text-danger">
          <AlertTriangle className="h-3.5 w-3.5" />
          {snapshot.error}
        </div>
      ) : null}

      <div className="grid grid-cols-5 border-b border-chrome-border">
        <Readout
          label="sessions"
          value={compact(totals.sessions)}
          note="last 45 days"
        />
        <Readout
          label="provider calls"
          value={compact(totals.calls)}
          note={`${compact(totals.tokens)} prompt tokens`}
        />
        <Readout
          label="compactions"
          value={compact(totals.compactions)}
          note={
            totals.calls
              ? `${((totals.compactions / Math.max(1, totals.calls)) * 100).toFixed(0)}% of calls`
              : "—"
          }
          tone={totals.compactions ? "warn" : "ok"}
        />
        <Readout
          label="re-sent messages"
          value={compact(totals.replays)}
          note={
            totals.replays
              ? [
                  totals.redelivery ? `${compact(totals.redelivery)} re-delivery` : "",
                  totals.recurrence ? `${compact(totals.recurrence)} recurrence` : "",
                  totals.recovery ? `${compact(totals.recovery)} recovery` : "",
                  totals.otherReplays ? `${compact(totals.otherReplays)} other` : "",
                ]
                  .filter(Boolean)
                  .join(" · ")
              : "identical content re-sent"
          }
          tone={totals.redelivery + totals.otherReplays ? "warn" : "ok"}
        />
        <Readout
          label="models seen"
          value={compact(snapshot?.models.length ?? 0)}
          note="by resolved route"
        />
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(340px,0.85fr)_minmax(560px,1.6fr)] overflow-hidden">
        <section className="flex min-h-0 flex-col border-r border-chrome-border">
          <div className="border-b border-chrome-border px-3 py-2">
            <div className="flex items-center gap-2 border border-chrome-border px-2 py-1.5">
              <Search className="h-3 w-3 text-chrome-text/45" />
              <input
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder="Filter sessions…"
                className="w-full bg-transparent text-[10px] text-foreground outline-none placeholder:text-chrome-text/35"
              />
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            {sessionTree.map(({ row, depth }) => (
              <SessionRow
                key={row.session_id}
                row={row}
                depth={depth}
                familyOpen={expandedFamilies.has(row.session_id)}
                onToggleFamily={() => toggleFamily(row.session_id)}
                selected={row.session_id === effectiveId}
                budget={budget}
                onSelect={() => {
                  setSelectedId(row.session_id);
                  setHighlightCalls(new Set());
                  setHighlightMessages(new Set());
                }}
              />
            ))}
            {!sessions.length ? (
              <div className="p-8 text-center text-[11px] text-chrome-text/45">
                {snapshot ? "No sessions mirrored yet." : "Loading…"}
              </div>
            ) : null}
          </div>
        </section>

        <section className="flex min-h-0 flex-col">
          {selected ? (
            <>
              <div className="border-b border-chrome-border px-5 pt-4 pb-3">
                <div className="flex items-start gap-3">
                  <div className="min-w-0">
                    <Label>
                      {selected.source || "session"}{" "}
                      {selected.profile_name
                        ? `· ${selected.profile_name}`
                        : ""}{" "}
                      · {selected.model || "?"}
                    </Label>
                    <h2
                      className="mt-1 truncate text-[16px] font-semibold tracking-tight text-foreground"
                      title={sessionTitle(selected)}
                    >
                      {sessionTitle(selected)}
                    </h2>
                    <p className="mt-1 font-mono text-[9px] text-chrome-text/50">
                      {selected.session_id}
                      {selected.calliope_session_id
                        ? ` · calliope ${selected.calliope_session_id.slice(0, 8)}`
                        : ""}{" "}
                      · {when(selected.started_at)} →{" "}
                      {when(selected.last_activity_at)}
                    </p>
                  </div>
                  <div className="ml-auto flex shrink-0 gap-3 text-right">
                    <Mini
                      label="base"
                      value={compact(selected.base_prompt_tokens)}
                    />
                    <Mini
                      label="avg / max"
                      value={`${compact(selected.avg_input_tokens)} / ${compact(selected.max_input_tokens)}`}
                    />
                    <Mini
                      label="cache"
                      value={
                        selected.cache_pct == null
                          ? "—"
                          : `${selected.cache_pct}%`
                      }
                    />
                    <Mini
                      label="compactions"
                      value={String(selected.compactions ?? 0)}
                      tone={n(selected.compactions) ? "warn" : undefined}
                    />
                  </div>
                </div>
                <nav className="mt-3 flex gap-1 font-mono text-[9px] uppercase tracking-[0.12em]">
                  {(
                    [
                      "timeline",
                      "ledger",
                      "findings",
                      "usage",
                      "models",
                    ] as Tab[]
                  ).map((item) => (
                    <button
                      key={item}
                      type="button"
                      onClick={() => setTab(item)}
                      className={cn(
                        "border px-2.5 py-1",
                        tab === item
                          ? "border-rvbbit-accent/60 bg-rvbbit-accent/10 text-foreground"
                          : "border-chrome-border text-chrome-text/55 hover:text-foreground",
                      )}
                    >
                      {item}
                      {item === "findings" && findings.length ? (
                        <span
                          className="ml-1.5 inline-flex h-3.5 min-w-3.5 items-center justify-center rounded-full px-1 text-[8px]"
                          style={{
                            background: SEVERITY_COLOR[findings[0].severity],
                            color: "var(--background)",
                          }}
                        >
                          {findings.length}
                        </span>
                      ) : null}
                    </button>
                  ))}
                </nav>
              </div>
              {detail.error ? (
                <div className="flex items-center gap-2 border-b border-danger/35 bg-danger/10 px-4 py-2 text-[10px] text-danger">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  {detail.error}
                </div>
              ) : null}
              <div className="min-h-0 flex-1 overflow-auto">
                {!detailReady ? (
                  <div className="p-8 text-center text-[11px] text-chrome-text/45">
                    Loading the tape…
                  </div>
                ) : null}
                {detailReady && tab === "timeline" ? (
                  <Timeline
                    calls={detail.calls}
                    events={detail.events}
                    budget={budget}
                    highlight={highlightCalls}
                  />
                ) : null}
                {detailReady && tab === "ledger" ? (
                  <Ledger
                    connectionId={activeConnectionId}
                    messages={detail.messages}
                    highlight={highlightMessages}
                  />
                ) : null}
                {detailReady && tab === "findings" ? (
                  <Findings findings={findings} onFocus={focusFinding} />
                ) : null}
                {detailReady && tab === "usage" ? (
                  <Usage detail={detail} session={selected} />
                ) : null}
                {tab === "models" ? <Models snapshot={snapshot} /> : null}
              </div>
            </>
          ) : (
            <Empty copy="Select a session to read its tape." />
          )}
        </section>
      </div>
    </div>
  );
}

function SessionRow({
  row,
  selected,
  budget,
  onSelect,
  depth = 0,
  familyOpen = false,
  onToggleFamily,
}: {
  row: ForensicSession;
  selected: boolean;
  budget: number;
  onSelect: () => void;
  depth?: number;
  familyOpen?: boolean;
  onToggleFamily?: () => void;
}) {
  const threshold = budget * THRESHOLD;
  const baseShare = row.base_prompt_tokens
    ? n(row.base_prompt_tokens) / threshold
    : 0;
  const maxShare = row.max_input_tokens
    ? Math.min(1.6, n(row.max_input_tokens) / threshold)
    : 0;
  // Recurrence/recovery re-sends are expected machinery; only re-delivery
  // (and unclassified) replays mark a session as worth a second look.
  const hot =
    n(row.compactions) > 0 ||
    n(row.redelivery_replays) + n(row.other_replays) > 0;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "relative block w-full border-b border-chrome-border/60 px-4 py-3 text-left hover:bg-foreground/[0.025] focus-visible:outline-none",
        selected && "bg-rvbbit-accent/[0.07]",
        depth > 0 && "pl-8",
      )}
    >
      <i
        className="absolute inset-y-0 left-0 w-[3px]"
        style={{
          background: hot ? "var(--warning)" : "var(--success)",
          opacity: selected ? 0.9 : 0.45,
        }}
      />
      <span className="flex items-baseline gap-2">
        <strong className="min-w-0 flex-1 truncate text-[11px] font-medium text-foreground">
          {sessionTitle(row)}
        </strong>
        <small className="shrink-0 font-mono text-[8px] tabular-nums text-chrome-text/45">
          {when(row.last_activity_at || row.started_at)}
        </small>
      </span>
      <span className="mt-1 block truncate font-mono text-[8px] text-chrome-text/45">
        {depth > 0 ? "└ " : ""}
        {depth > 0 && row.lineage_specialist
          ? `${row.lineage_specialist} · `
          : ""}
        {row.source}
        {row.profile_name ? ` · ${row.profile_name}` : ""} ·{" "}
        {row.api_calls ?? 0} calls · {row.hops ?? 0} hops ·{" "}
        {compact(row.sum_input_tokens)} tok
        {n(row.child_sessions)
          ? ` · family ${compact(n(row.sum_input_tokens) + n(row.child_input_tokens))} tok`
          : ""}
      </span>
      <span
        className="mt-2 block h-1.5 overflow-hidden bg-foreground/[0.04]"
        title={`base ${full(row.base_prompt_tokens)} · max ${full(row.max_input_tokens)} · threshold ${full(Math.round(threshold))}`}
      >
        <i
          className="block h-full"
          style={{
            width: `${Math.min(100, maxShare * 62.5)}%`,
            background:
              "color-mix(in oklch, var(--viz-2, #6ba7ff) 55%, transparent)",
          }}
        />
        <i
          className="-mt-1.5 block h-full"
          style={{
            width: `${Math.min(100, baseShare * 62.5)}%`,
            background: "var(--rvbbit-accent)",
          }}
        />
      </span>
      <span className="mt-1.5 flex flex-wrap gap-1.5">
        {n(row.child_sessions) && onToggleFamily ? (
          <span
            role="button"
            tabIndex={0}
            onClick={(event) => {
              event.stopPropagation();
              onToggleFamily();
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.stopPropagation();
                onToggleFamily();
              }
            }}
            title={
              `${row.child_sessions} sidecar session(s) · ` +
              `${full(row.child_input_tokens)} in / ${full(row.child_output_tokens)} out across ${row.child_api_calls} calls. ` +
              "Family totals count Hermes sessions only (Hindsight/embedder work is billed elsewhere)."
            }
            className="cursor-pointer"
          >
            <Chip color="var(--rvbbit-accent)">
              {familyOpen ? "▾" : "▸"} {row.child_sessions} sidecar
              {n(row.child_sessions) === 1 ? "" : "s"} ·{" "}
              {compact(row.child_input_tokens)} tok
            </Chip>
          </span>
        ) : null}
        {n(row.compactions) ? (
          <Chip color="var(--warning)">
            {row.compactions} compaction{n(row.compactions) === 1 ? "" : "s"}
          </Chip>
        ) : null}
        {n(row.redelivery_replays) + n(row.other_replays) ? (
          <Chip color="var(--warning)">
            {n(row.redelivery_replays) + n(row.other_replays)} re-delivered
          </Chip>
        ) : null}
        {n(row.compression_ineffective_count) ? (
          <Chip color="var(--danger)">
            ineffective ×{row.compression_ineffective_count}
          </Chip>
        ) : null}
        {n(row.aux_input_tokens) &&
        n(row.main_input_tokens) &&
        n(row.aux_input_tokens) / n(row.main_input_tokens) >= 0.25 ? (
          <Chip color="var(--viz-3, #b68cff)">
            aux{" "}
            {Math.round(
              (n(row.aux_input_tokens) / n(row.main_input_tokens)) * 100,
            )}
            %
          </Chip>
        ) : null}
        {!hot && !n(row.compression_ineffective_count) ? (
          <Chip color="var(--success)">clean</Chip>
        ) : null}
      </span>
    </button>
  );
}

// ── Timeline ────────────────────────────────────────────────────────────────

function Timeline({
  calls,
  events,
  budget,
  highlight,
}: {
  calls: ForensicCall[];
  events: ForensicEvent[];
  budget: number;
  highlight: Set<number>;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const threshold = budget * THRESHOLD;
  const maxTokens = Math.max(
    threshold * 1.05,
    ...calls.map((c) => n(c.input_tokens)),
  );
  const width = Math.max(600, calls.length * 14 + 40);
  const height = 200;
  const pad = { l: 8, r: 8, t: 8, b: 20 };
  const plotH = height - pad.t - pad.b;
  const bar = calls.length ? (width - pad.l - pad.r) / calls.length : 0;
  const y = (tokens: number) => pad.t + plotH - (tokens / maxTokens) * plotH;
  const compactionTimes = events
    .filter((e) => e.kind === "compression_done")
    .map((e) => Date.parse(e.ts));
  const compactionIndex = (index: number): boolean => {
    const cur = Date.parse(calls[index]?.ts ?? "");
    const prev = index > 0 ? Date.parse(calls[index - 1].ts) : -Infinity;
    return compactionTimes.some((t) => t > prev && t <= cur);
  };
  const hovered = hover != null ? calls[hover] : null;
  return (
    <div className="p-4">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <Label>Prompt per provider call</Label>
          <p className="mt-1 text-[10px] text-chrome-text/50">
            Bar = prompt tokens; the darker part was served from cache. Ticks
            mark a compaction before that call; ◆ opens a turn (call #1).
          </p>
        </div>
        <div className="flex gap-3 font-mono text-[8px] text-chrome-text/50">
          <span>
            <i
              className="mr-1 inline-block h-2 w-2"
              style={{ background: "var(--viz-2, #6ba7ff)" }}
            />
            uncached
          </span>
          <span>
            <i
              className="mr-1 inline-block h-2 w-2"
              style={{ background: "var(--rvbbit-accent)" }}
            />
            cached
          </span>
          <span>
            <i className="mr-1 inline-block h-2 w-3 border-t border-dashed border-warning align-middle" />
            threshold
          </span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <svg width={width} height={height} className="block" role="img">
          <line
            x1={pad.l}
            x2={width - pad.r}
            y1={y(threshold)}
            y2={y(threshold)}
            stroke="var(--warning)"
            strokeDasharray="4 3"
            strokeWidth={1}
          />
          <text
            x={width - pad.r - 2}
            y={y(threshold) - 3}
            textAnchor="end"
            fontSize={8}
            fill="var(--warning)"
            fontFamily="monospace"
          >
            threshold {compact(threshold)}
          </text>
          {calls.map((c, index) => {
            const tokens = n(c.input_tokens);
            const cached = Math.min(tokens, n(c.cache_read_tokens));
            const x = pad.l + index * bar;
            const w = Math.max(2, bar - 2);
            const isHighlight = highlight.has(c.id);
            const dim = highlight.size > 0 && !isHighlight;
            return (
              <g
                key={c.id}
                onMouseEnter={() => setHover(index)}
                onMouseLeave={() => setHover(null)}
                opacity={dim ? 0.35 : 1}
              >
                <rect
                  x={x}
                  y={y(tokens)}
                  width={w}
                  height={Math.max(1, pad.t + plotH - y(tokens))}
                  fill="var(--viz-2, #6ba7ff)"
                  fillOpacity={0.55}
                />
                <rect
                  x={x}
                  y={y(cached)}
                  width={w}
                  height={Math.max(0, pad.t + plotH - y(cached))}
                  fill="var(--rvbbit-accent)"
                  fillOpacity={0.9}
                />
                {isHighlight ? (
                  <rect
                    x={x - 1}
                    y={y(tokens) - 1}
                    width={w + 2}
                    height={Math.max(1, pad.t + plotH - y(tokens)) + 2}
                    fill="none"
                    stroke="var(--danger)"
                    strokeWidth={1.5}
                  />
                ) : null}
                {c.opens_turn ? (
                  <text
                    x={x + w / 2}
                    y={height - 6}
                    textAnchor="middle"
                    fontSize={8}
                    fill="var(--chrome-text)"
                  >
                    ◆
                  </text>
                ) : null}
                {compactionIndex(index) ? (
                  <line
                    x1={x + w / 2}
                    x2={x + w / 2}
                    y1={pad.t}
                    y2={pad.t + 8}
                    stroke="var(--warning)"
                    strokeWidth={2}
                  />
                ) : null}
                <rect
                  x={x}
                  y={pad.t}
                  width={w}
                  height={plotH}
                  fill="transparent"
                />
              </g>
            );
          })}
        </svg>
      </div>
      <div className="mt-2 h-8 font-mono text-[9px] text-chrome-text/60">
        {hovered ? (
          <span>
            {clock(hovered.ts)} · call #{hovered.call_no} ·{" "}
            <b className="text-foreground">{full(hovered.input_tokens)}</b> in (
            {hovered.cache_pct ?? 0}% cached) · {full(hovered.output_tokens)}{" "}
            out · Δ{" "}
            {hovered.input_delta == null
              ? "—"
              : `${n(hovered.input_delta) >= 0 ? "+" : ""}${full(hovered.input_delta)}`}{" "}
            · {duration(hovered.latency_ms)}
            {hovered.route_model ? ` · ${hovered.route_model}` : ""}
          </span>
        ) : (
          <span className="text-chrome-text/35">hover a bar</span>
        )}
      </div>
      <table className="mt-3 w-full border-t border-chrome-border font-mono text-[9px] tabular-nums">
        <thead className="text-left text-[8px] uppercase tracking-[0.12em] text-chrome-text/45">
          <tr>
            <th className="py-1.5 pr-2">time</th>
            <th className="pr-2">#</th>
            <th className="pr-2 text-right">in</th>
            <th className="pr-2 text-right">cached</th>
            <th className="pr-2 text-right">Δ</th>
            <th className="pr-2 text-right">out</th>
            <th className="pr-2 text-right">latency</th>
            <th>route</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((c, index) => (
            <tr
              key={c.id}
              className={cn(
                "border-t border-chrome-border/40",
                highlight.has(c.id) && "bg-danger/10",
                c.opens_turn && "text-foreground",
              )}
            >
              <td className="py-1 pr-2 text-chrome-text/60">
                {clock(c.ts)}
                {compactionIndex(index) ? (
                  <span
                    className="ml-1 text-warning"
                    title="compaction before this call"
                  >
                    ⟳
                  </span>
                ) : null}
              </td>
              <td className="pr-2">
                {c.opens_turn ? "◆ " : ""}
                {c.call_no}
              </td>
              <td className="pr-2 text-right">{full(c.input_tokens)}</td>
              <td className="pr-2 text-right text-chrome-text/60">
                {c.cache_pct == null ? "—" : `${c.cache_pct}%`}
              </td>
              <td
                className={cn(
                  "pr-2 text-right",
                  n(c.input_delta) >= 20_000 && "text-warning",
                  n(c.input_delta) < 0 && "text-success",
                )}
              >
                {c.input_delta == null
                  ? "—"
                  : `${n(c.input_delta) >= 0 ? "+" : ""}${full(c.input_delta)}`}
              </td>
              <td className="pr-2 text-right text-chrome-text/60">
                {full(c.output_tokens)}
              </td>
              <td className="pr-2 text-right text-chrome-text/60">
                {duration(c.latency_ms)}
              </td>
              <td className="truncate text-chrome-text/50">
                {c.route_model || c.model || ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!calls.length ? (
        <div className="p-8 text-center text-[10px] text-chrome-text/45">
          No provider calls mirrored for this session (agent.log lines land
          within a couple of minutes).
        </div>
      ) : null}
    </div>
  );
}

// ── Ledger ──────────────────────────────────────────────────────────────────

function Ledger({
  connectionId,
  messages,
  highlight,
}: {
  connectionId: string;
  messages: ForensicMessage[];
  highlight: Set<number>;
}) {
  const [openId, setOpenId] = useState<number | null>(null);
  const [body, setBody] = useState<{
    id: number;
    content: string | null;
    tool_calls: unknown;
    error?: string;
  } | null>(null);
  const [showInactive, setShowInactive] = useState(true);
  const maxPayload = Math.max(1, ...messages.map((m) => m.payload_chars));
  const kinds = useMemo(() => {
    const counts = new Map<string, number>();
    messages.forEach((m) => counts.set(m.kind, (counts.get(m.kind) ?? 0) + 1));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [messages]);
  const visible = showInactive ? messages : messages.filter((m) => m.active);

  const open = useCallback(
    async (id: number) => {
      if (openId === id) {
        setOpenId(null);
        return;
      }
      setOpenId(id);
      setBody(null);
      const next = await fetchForensicMessageBody(connectionId, id);
      setBody({ id, ...next });
    },
    [connectionId, openId],
  );

  return (
    <div>
      <div className="sticky top-0 z-10 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-chrome-border bg-chrome-bg/40 px-4 py-2 backdrop-blur">
        {kinds.map(([kind, count]) => (
          <span
            key={kind}
            className="inline-flex items-center gap-1.5 font-mono text-[8px] text-chrome-text/60"
          >
            <i
              className="h-1.5 w-1.5 rounded-full"
              style={{ background: kindColor(kind) }}
            />
            {kind} {count}
          </span>
        ))}
        <label className="ml-auto flex items-center gap-1.5 font-mono text-[8px] uppercase tracking-wide text-chrome-text/50">
          <input
            type="checkbox"
            checked={showInactive}
            onChange={(event) => setShowInactive(event.target.checked)}
          />
          show compacted
        </label>
      </div>
      <div className="divide-y divide-chrome-border/40">
        {visible.map((m) => {
          const isOpen = openId === m.id;
          const hi = highlight.has(m.id);
          return (
            <div
              key={m.id}
              className={cn(hi && "bg-danger/10", !m.active && "opacity-60")}
            >
              <button
                type="button"
                onClick={() => void open(m.id)}
                className="grid w-full grid-cols-[52px_minmax(0,1fr)_minmax(140px,0.7fr)_84px] items-center gap-3 px-4 py-2 text-left hover:bg-foreground/[0.025] focus-visible:outline-none"
              >
                <span className="font-mono text-[8px] tabular-nums text-chrome-text/45">
                  {clock(m.ts)}
                </span>
                <span className="min-w-0">
                  <span className="flex items-center gap-2">
                    <i
                      className="h-1.5 w-1.5 shrink-0 rounded-full"
                      style={{ background: kindColor(m.kind) }}
                    />
                    <span className="truncate font-mono text-[10px] text-foreground">
                      {m.kind}
                      {m.tool_name ? ` · ${shortTool(m.tool_name)}` : ""}
                      {m.tool_call_names?.length
                        ? ` → ${m.tool_call_names.map(shortTool).join(", ")}`
                        : ""}
                    </span>
                  </span>
                  <span className="mt-1 flex flex-wrap gap-1.5">
                    {m.compacted ? (
                      <Chip color="var(--warning)">compacted</Chip>
                    ) : null}
                    {!m.active ? (
                      <Chip color="var(--chrome-text)">inactive</Chip>
                    ) : null}
                    {(m.same_content_seen ?? 0) >= 2 &&
                    m.payload_chars >= 2000 ? (
                      <Chip color="var(--viz-3, #b68cff)">
                        ×{m.same_content_seen} identical
                      </Chip>
                    ) : null}
                    {m.finish_reason &&
                    m.finish_reason !== "stop" &&
                    m.finish_reason !== "tool_calls" ? (
                      <Chip color="var(--danger)">{m.finish_reason}</Chip>
                    ) : null}
                  </span>
                </span>
                <span className="relative h-3 bg-foreground/[0.035]">
                  <i
                    className="absolute inset-y-0 left-0"
                    style={{
                      width: `${Math.max(1, (m.payload_chars / maxPayload) * 100)}%`,
                      background: `color-mix(in oklch, ${kindColor(m.kind)} 70%, transparent)`,
                    }}
                  />
                </span>
                <span className="text-right font-mono text-[9px] tabular-nums text-chrome-text/60">
                  {full(m.payload_chars)}
                  <small className="ml-0.5 text-chrome-text/35">ch</small>
                </span>
              </button>
              {isOpen ? (
                <div className="border-t border-chrome-border/40 bg-foreground/[0.02] px-4 py-3">
                  {!body || body.id !== m.id ? (
                    <div className="text-[10px] text-chrome-text/45">
                      Loading…
                    </div>
                  ) : body.error ? (
                    <div className="text-[10px] text-danger">{body.error}</div>
                  ) : (
                    <>
                      {body.tool_calls ? (
                        <pre className="mb-2 max-h-64 overflow-auto whitespace-pre-wrap break-all border border-chrome-border/60 bg-background/40 p-2 font-mono text-[9px] leading-relaxed text-foreground/85">
                          {JSON.stringify(body.tool_calls, null, 1).slice(
                            0,
                            20000,
                          )}
                        </pre>
                      ) : null}
                      {body.content ? (
                        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words border border-chrome-border/60 bg-background/40 p-2 font-mono text-[9px] leading-relaxed text-foreground/85">
                          {body.content}
                        </pre>
                      ) : null}
                      {!body.content && !body.tool_calls ? (
                        <div className="text-[10px] text-chrome-text/45">
                          (empty)
                        </div>
                      ) : null}
                    </>
                  )}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
      {!messages.length ? (
        <div className="p-8 text-center text-[10px] text-chrome-text/45">
          No messages mirrored for this session.
        </div>
      ) : null}
    </div>
  );
}

// ── Findings ────────────────────────────────────────────────────────────────

function Findings({
  findings,
  onFocus,
}: {
  findings: Finding[];
  onFocus: (finding: Finding) => void;
}) {
  if (!findings.length)
    return (
      <div className="p-8 text-center text-[11px] text-chrome-text/45">
        Nothing deterministic to flag on this tape.
      </div>
    );
  return (
    <div className="divide-y divide-chrome-border/50">
      {findings.map((finding) => (
        <button
          key={finding.code}
          type="button"
          onClick={() => onFocus(finding)}
          className="block w-full px-5 py-4 text-left hover:bg-foreground/[0.025] focus-visible:outline-none"
        >
          <span className="flex items-center gap-2">
            <Chip color={SEVERITY_COLOR[finding.severity]}>
              {finding.severity}
            </Chip>
            <span className="font-mono text-[8px] uppercase tracking-wide text-chrome-text/45">
              {finding.code}
            </span>
          </span>
          <strong className="mt-1.5 block text-[12px] font-medium text-foreground">
            {finding.title}
          </strong>
          <p className="mt-1 text-[10px] leading-relaxed text-chrome-text/60">
            {finding.detail}
          </p>
          {finding.callIds?.length || finding.messageIds?.length ? (
            <small className="mt-1.5 block font-mono text-[8px] text-rvbbit-accent/80">
              click to highlight in the{" "}
              {finding.callIds?.length ? "timeline" : "ledger"}
            </small>
          ) : null}
        </button>
      ))}
    </div>
  );
}

// ── Usage / Models ──────────────────────────────────────────────────────────

function Usage({
  detail,
  session,
}: {
  detail: ForensicSessionDetail;
  session: ForensicSession;
}) {
  const rows = detail.usage;
  const total = rows.reduce((sum, row) => sum + n(row.input_tokens), 0);
  return (
    <div className="p-5">
      <Label>Tokens by task</Label>
      <p className="mt-1 text-[10px] text-chrome-text/50">
        Hermes&apos; own per-session accounting: the main turn versus its
        auxiliary passes (compression summaries, vision descriptions of
        screenshots, title generation, approvals, skill curator).
      </p>
      <div className="mt-3 flex h-2.5 overflow-hidden bg-foreground/[0.04]">
        {rows.map((row) => (
          <span
            key={`${row.task}:${row.model}`}
            title={`${row.task || "main"}: ${full(row.input_tokens)}`}
            style={{
              width: `${(n(row.input_tokens) / Math.max(1, total)) * 100}%`,
              background: taskColor(row.task),
            }}
          />
        ))}
      </div>
      <table className="mt-3 w-full font-mono text-[9px] tabular-nums">
        <thead className="text-left text-[8px] uppercase tracking-[0.12em] text-chrome-text/45">
          <tr>
            <th className="py-1.5">task</th>
            <th>model</th>
            <th className="text-right">calls</th>
            <th className="text-right">input</th>
            <th className="text-right">cached</th>
            <th className="text-right">output</th>
            <th className="text-right">reasoning</th>
            <th className="text-right">share</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={`${row.task}:${row.model}`}
              className="border-t border-chrome-border/40"
            >
              <td className="py-1.5">
                <i
                  className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full"
                  style={{ background: taskColor(row.task) }}
                />
                {row.task || "main"}
              </td>
              <td className="text-chrome-text/60">{row.model}</td>
              <td className="text-right">{row.api_call_count}</td>
              <td className="text-right">{full(row.input_tokens)}</td>
              <td className="text-right text-chrome-text/60">
                {full(row.cache_read_tokens)}
              </td>
              <td className="text-right">{full(row.output_tokens)}</td>
              <td className="text-right text-chrome-text/60">
                {full(row.reasoning_tokens)}
              </td>
              <td className="text-right">
                {total
                  ? `${Math.round((n(row.input_tokens) / total) * 100)}%`
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-6 grid grid-cols-4 gap-3">
        <Mini
          label="transcript rows"
          value={`${session.rows_active ?? 0} active / ${session.rows_total ?? 0}`}
        />
        <Mini
          label="compacted rows"
          value={String(session.rows_compacted ?? 0)}
        />
        <Mini
          label="tool result chars"
          value={compact(session.tool_result_chars)}
        />
        <Mini
          label="largest payload"
          value={compact(session.largest_payload_chars)}
        />
      </div>
    </div>
  );
}

function Models({ snapshot }: { snapshot: ForensicSnapshot | null }) {
  const rows = snapshot?.models ?? [];
  return (
    <div className="p-5">
      <Label>Models the agents called · last 45 days</Label>
      <p className="mt-1 text-[10px] text-chrome-text/50">
        Grouped by the provider/model on each API call (Hermes&apos; `custom`
        provider is the Clover/Hutch route, where `calliope` is the endpoint&apos;s
        own alias for its current model). Same task class across rows is the
        Pareto view.
      </p>
      <table className="mt-3 w-full font-mono text-[9px] tabular-nums">
        <thead className="text-left text-[8px] uppercase tracking-[0.12em] text-chrome-text/45">
          <tr>
            <th className="py-1.5">model</th>
            <th className="text-right">sessions</th>
            <th className="text-right">calls</th>
            <th className="text-right">hops</th>
            <th className="text-right">avg prompt</th>
            <th className="text-right">max prompt</th>
            <th className="text-right">cache</th>
            <th className="text-right">latency</th>
            <th className="text-right">output</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.model} className="border-t border-chrome-border/40">
              <td className="py-1.5 text-foreground">{row.model}</td>
              <td className="text-right">{row.sessions}</td>
              <td className="text-right">{row.calls}</td>
              <td className="text-right">{row.hops}</td>
              <td className="text-right">{full(row.avg_input)}</td>
              <td className="text-right">{full(row.max_input)}</td>
              <td className="text-right text-chrome-text/60">
                {row.cache_pct == null ? "—" : `${row.cache_pct}%`}
              </td>
              <td className="text-right text-chrome-text/60">
                {duration(row.avg_latency_ms)}
              </td>
              <td className="text-right">{compact(row.sum_output)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length ? (
        <div className="p-8 text-center text-[10px] text-chrome-text/45">
          No provider calls mirrored yet.
        </div>
      ) : null}
    </div>
  );
}

function taskColor(task: string): string {
  return (
    (
      {
        "": "var(--rvbbit-accent)",
        compression: "var(--warning)",
        vision: "var(--viz-3, #b68cff)",
        title_generation: "var(--viz-5, #65c6a7)",
        approval: "var(--viz-2, #6ba7ff)",
      } as Record<string, string>
    )[task] || "var(--chrome-text)"
  );
}

// ── bits ────────────────────────────────────────────────────────────────────

function Readout({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note: string;
  tone?: "warn" | "ok";
}) {
  return (
    <div className="border-r border-chrome-border px-4 py-3 last:border-r-0">
      <Label>{label}</Label>
      <div
        className={cn(
          "mt-1 font-mono text-[21px] tabular-nums text-foreground",
          tone === "warn" && "text-warning",
          tone === "ok" && "text-success",
        )}
      >
        {value}
      </div>
      <div className="mt-0.5 text-[9px] text-chrome-text/45">{note}</div>
    </div>
  );
}
function Label({ children }: { children: ReactNode }) {
  return (
    <div className="font-mono text-[8px] font-semibold uppercase tracking-[0.14em] text-chrome-text/50">
      {children}
    </div>
  );
}
function Mini({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "warn";
}) {
  return (
    <div>
      <Label>{label}</Label>
      <div
        className={cn(
          "mt-1 font-mono text-[13px] tabular-nums text-foreground",
          tone === "warn" && "text-warning",
        )}
      >
        {value}
      </div>
    </div>
  );
}
function Chip({ children, color }: { children: ReactNode; color: string }) {
  return (
    <span
      className="inline-flex items-center border px-1.5 py-0.5 font-mono text-[8px] uppercase tracking-wide"
      style={{
        color,
        borderColor: `color-mix(in oklch, ${color} 42%, var(--chrome-border))`,
      }}
    >
      {children}
    </span>
  );
}
function Empty({ copy }: { copy: string }) {
  return (
    <div className="grid h-full place-items-center p-8 text-center">
      <div>
        <Layers className="mx-auto mb-3 h-8 w-8 text-chrome-text/25" />
        <p className="text-[11px] text-chrome-text/55">{copy}</p>
      </div>
    </div>
  );
}
