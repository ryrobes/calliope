"use client";

/**
 * Session Forensics — the SQL side of the DataRabbit window over the Hermes
 * session mirror (rvbbit.hermes_* / migration 0334, filled by Calliope's
 * hermes_mirror worker). Everything here is a plain read of those views;
 * the detectors are deterministic and run client-side over what was read,
 * so the same numbers can be reproduced with SQL by Calliope herself.
 */

import type { QueryResultColumn } from "@/lib/db/types";

interface QueryOk {
  ok: true;
  columns: QueryResultColumn[];
  rows: Record<string, unknown>[];
}
interface QueryErr {
  ok: false;
  error: string;
}

async function runQuery(
  connectionId: string,
  sql: string,
  rowLimit = 1000,
): Promise<QueryOk | QueryErr> {
  try {
    const response = await fetch("/api/db/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ connectionId, sql, rowLimit, readOnly: true }),
    });
    return (await response.json()) as QueryOk | QueryErr;
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

const SESSION_ID_RE = /^[A-Za-z0-9_.:-]{1,200}$/;

function literal(value: string): string {
  return `'${value.replace(/'/g, "''")}'`;
}

export interface ForensicSession {
  session_id: string;
  calliope_session_id: string | null;
  calliope_title: string | null;
  title: string | null;
  source: string | null;
  profile_name: string | null;
  model: string | null;
  started_at: string | null;
  last_activity_at: string | null;
  compression_ineffective_count: number | null;
  api_calls: number | null;
  hops: number | null;
  base_prompt_tokens: number | null;
  avg_input_tokens: number | null;
  max_input_tokens: number | null;
  sum_input_tokens: number | null;
  sum_output_tokens: number | null;
  cache_pct: number | null;
  compactions: number | null;
  compaction_blocked: number | null;
  truncated_results: number | null;
  largest_tool_result_chars: number | null;
  rows_total: number | null;
  rows_active: number | null;
  rows_compacted: number | null;
  user_messages: number | null;
  parent_hermes_session_id: string | null;
  lineage_specialist: string | null;
  child_sessions: number | null;
  child_api_calls: number | null;
  child_input_tokens: number | null;
  child_output_tokens: number | null;
  child_compactions: number | null;
  child_replays: number | null;
  replayed_user_messages: number | null;
  versioned_user_rows: number | null;
  redelivery_replays: number | null;
  recurrence_replays: number | null;
  recovery_replays: number | null;
  other_replays: number | null;
  tool_result_chars: number | null;
  largest_payload_chars: number | null;
  main_input_tokens: number | null;
  aux_input_tokens: number | null;
  aux_calls: Record<string, number> | null;
}

export interface ForensicCall {
  id: number;
  ts: string;
  call_no: number | null;
  opens_turn: boolean;
  model: string | null;
  route_model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_pct: number | null;
  input_delta: number | null;
  latency_ms: number | null;
}

export interface ForensicEvent {
  id: number;
  ts: string;
  kind: string;
  details: Record<string, unknown>;
}

export interface ForensicMessage {
  id: number;
  ts: string;
  role: string;
  kind: string;
  tool_name: string | null;
  content_chars: number;
  tool_arg_chars: number;
  payload_chars: number;
  tool_call_names: string[] | null;
  finish_reason: string | null;
  active: boolean;
  compacted: boolean;
  same_content_seen: number | null;
}

export interface ForensicUsage {
  task: string;
  model: string;
  api_call_count: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  reasoning_tokens: number;
}

export interface ForensicModelRow {
  model: string;
  sessions: number;
  calls: number;
  hops: number;
  avg_input: number | null;
  max_input: number | null;
  cache_pct: number | null;
  avg_latency_ms: number | null;
  sum_output: number | null;
}

export interface ForensicSnapshot {
  ready: boolean;
  sessions: ForensicSession[];
  models: ForensicModelRow[];
  error?: string;
  fetchedAt: number;
}

export interface ForensicSessionDetail {
  calls: ForensicCall[];
  events: ForensicEvent[];
  messages: ForensicMessage[];
  usage: ForensicUsage[];
  error?: string;
}

const SESSIONS_SQL = `
SELECT o.session_id, o.calliope_session_id, o.calliope_title, o.title, o.source, o.profile_name, o.model,
       o.started_at, o.last_activity_at, o.compression_ineffective_count,
       o.api_calls, o.hops, o.base_prompt_tokens, o.avg_input_tokens, o.max_input_tokens,
       o.sum_input_tokens, o.sum_output_tokens, o.cache_pct, o.compactions, o.compaction_blocked,
       o.truncated_results, o.largest_tool_result_chars, o.rows_total, o.rows_active, o.rows_compacted,
       o.user_messages, o.replayed_user_messages, o.versioned_user_rows, o.redelivery_replays,
       o.recurrence_replays, o.recovery_replays, o.other_replays, o.tool_result_chars, o.largest_payload_chars,
       o.main_input_tokens, o.aux_input_tokens, o.aux_calls,
       l.parent_hermes_session_id, l.specialist_key AS lineage_specialist,
       f.child_sessions, f.child_api_calls, f.child_input_tokens,
       f.child_output_tokens, f.child_compactions, f.child_replays
  FROM rvbbit.hermes_session_overview o
  LEFT JOIN rvbbit.hermes_session_lineage l ON l.child_hermes_session_id = o.session_id
  LEFT JOIN rvbbit.hermes_session_family f ON f.session_id = o.session_id
 WHERE coalesce(o.last_activity_at, o.started_at) > now() - interval '45 days'
 ORDER BY coalesce(o.last_activity_at, o.started_at) DESC
 LIMIT 300`;

const MODELS_SQL = `
SELECT coalesce(route_provider, provider, '?') || ' / ' || coalesce(route_model, model, '?') AS model,
       count(DISTINCT session_id)                                           AS sessions,
       count(*)                                                             AS calls,
       count(*) FILTER (WHERE call_no = 1)                                  AS hops,
       round(avg(input_tokens))                                             AS avg_input,
       max(input_tokens)                                                    AS max_input,
       round(100.0 * sum(coalesce(cache_read_tokens, 0)) / nullif(sum(input_tokens), 0), 1) AS cache_pct,
       round(avg(latency_ms))                                               AS avg_latency_ms,
       sum(output_tokens)                                                   AS sum_output
  FROM rvbbit.hermes_api_calls
 WHERE ts > now() - interval '45 days'
 GROUP BY 1
 ORDER BY calls DESC
 LIMIT 40`;

export async function fetchForensicSnapshot(
  connectionId: string,
): Promise<ForensicSnapshot> {
  const fetchedAt = Date.now();
  const probe = await runQuery(
    connectionId,
    "SELECT to_regclass('rvbbit.hermes_session_overview') IS NOT NULL AS ready",
    1,
  );
  if (!probe.ok)
    return {
      ready: false,
      sessions: [],
      models: [],
      error: probe.error,
      fetchedAt,
    };
  if (!probe.rows[0]?.ready)
    return { ready: false, sessions: [], models: [], fetchedAt };
  const [sessions, models] = await Promise.all([
    runQuery(connectionId, SESSIONS_SQL, 300),
    runQuery(connectionId, MODELS_SQL, 40),
  ]);
  const failure = [sessions, models].find((result) => !result.ok) as
    QueryErr | undefined;
  return {
    ready: true,
    sessions: sessions.ok
      ? (sessions.rows as unknown as ForensicSession[])
      : [],
    models: models.ok ? (models.rows as unknown as ForensicModelRow[]) : [],
    error: failure?.error,
    fetchedAt,
  };
}

export async function fetchForensicSession(
  connectionId: string,
  sessionId: string,
): Promise<ForensicSessionDetail> {
  if (!SESSION_ID_RE.test(sessionId)) {
    return {
      calls: [],
      events: [],
      messages: [],
      usage: [],
      error: "invalid session id",
    };
  }
  const sid = literal(sessionId);
  const [calls, events, messages, usage] = await Promise.all([
    runQuery(
      connectionId,
      `
      SELECT id, ts, call_no, opens_turn, model, route_model, input_tokens, output_tokens,
             cache_read_tokens, cache_pct, input_delta, latency_ms
        FROM rvbbit.hermes_session_calls WHERE session_id = ${sid} ORDER BY ts, id LIMIT 2000`,
      2000,
    ),
    runQuery(
      connectionId,
      `
      SELECT id, ts, kind, details FROM rvbbit.hermes_context_events
       WHERE session_id = ${sid} ORDER BY ts, id LIMIT 4000`,
      4000,
    ),
    runQuery(
      connectionId,
      `
      SELECT id, ts, role, kind, tool_name, content_chars, tool_arg_chars, payload_chars,
             tool_call_names, finish_reason, active, compacted, same_content_seen
        FROM rvbbit.hermes_message_ledger WHERE session_id = ${sid} ORDER BY id LIMIT 5000`,
      5000,
    ),
    runQuery(
      connectionId,
      `
      SELECT task, model, api_call_count, input_tokens, output_tokens, cache_read_tokens, reasoning_tokens
        FROM rvbbit.hermes_model_usage WHERE session_id = ${sid} ORDER BY task, model LIMIT 50`,
      50,
    ),
  ]);
  const failure = [calls, events, messages, usage].find(
    (result) => !result.ok,
  ) as QueryErr | undefined;
  return {
    calls: calls.ok ? (calls.rows as unknown as ForensicCall[]) : [],
    events: events.ok ? (events.rows as unknown as ForensicEvent[]) : [],
    messages: messages.ok
      ? (messages.rows as unknown as ForensicMessage[])
      : [],
    usage: usage.ok ? (usage.rows as unknown as ForensicUsage[]) : [],
    error: failure?.error,
  };
}

export async function fetchForensicMessageBody(
  connectionId: string,
  messageId: number,
): Promise<{ content: string | null; tool_calls: unknown; error?: string }> {
  const id = Math.trunc(Number(messageId));
  if (!Number.isFinite(id) || id <= 0)
    return { content: null, tool_calls: null, error: "invalid message id" };
  const result = await runQuery(
    connectionId,
    `
    SELECT left(content, 60000) AS content, tool_calls, content_chars
      FROM rvbbit.hermes_messages WHERE id = ${id}`,
    1,
  );
  if (!result.ok)
    return { content: null, tool_calls: null, error: result.error };
  const row = result.rows[0];
  return {
    content: (row?.content as string | null) ?? null,
    tool_calls: row?.tool_calls ?? null,
  };
}

// ── Deterministic detectors ─────────────────────────────────────────────────
// Each finding is a plain, reproducible statement about the tape; nothing
// here needs a model. Severity: "high" is spend or correctness that needs a
// decision, "note" is worth a glance.

export type FindingSeverity = "high" | "warn" | "note";

export interface Finding {
  code: string;
  severity: FindingSeverity;
  title: string;
  detail: string;
  /** ids of hermes_api_calls / hermes_messages this finding points at (for highlighting) */
  callIds?: number[];
  messageIds?: number[];
}

export interface DetectorInput {
  session: ForensicSession | null;
  calls: ForensicCall[];
  events: ForensicEvent[];
  messages: ForensicMessage[];
  usage: ForensicUsage[];
  /** working budget the compaction ratios hang off (model.context_length) */
  contextBudget: number;
  /** compression threshold ratio (compression.threshold) */
  threshold: number;
}

const fmt = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(Number(value))
    ? "—"
    : Intl.NumberFormat().format(Math.round(Number(value)));

export function computeFindings(input: DetectorInput): Finding[] {
  const { calls, events, messages, usage } = input;
  const findings: Finding[] = [];
  const thresholdTokens = Math.round(input.contextBudget * input.threshold);

  // 1. Fixed base vs threshold — the smallest turn-opening call is the base.
  const openers = calls.filter((c) => c.opens_turn && c.input_tokens != null);
  if (openers.length) {
    const base = Math.min(...openers.map((c) => Number(c.input_tokens)));
    const share = thresholdTokens ? base / thresholdTokens : 0;
    if (share >= 0.6) {
      findings.push({
        code: "BASE_OVERHEAD",
        severity: share >= 0.85 ? "high" : "warn",
        title: `Fixed base is ${fmt(base)} tokens — ${Math.round(share * 100)}% of the ${fmt(thresholdTokens)} compaction threshold`,
        detail:
          "The first call of a turn (history 0 or right after compaction) is mostly system prompt, visible tools, per-turn instructions and memory recall. Above ~40% of the threshold, ordinary tool results push every turn into compaction. Raise model.context_length or shrink what is injected per turn.",
        callIds: openers
          .filter((c) => Number(c.input_tokens) === base)
          .map((c) => c.id),
      });
    }
  }

  // 2. Compaction churn: compactions per user turn, replays, ineffective.
  const compactions = events.filter((e) => e.kind === "compression_done");
  const userTurns = messages.filter((m) => m.kind === "user").length;
  const distinctUserTurns = new Set(
    messages.filter((m) => m.kind === "user").map((m) => m.id),
  ).size;
  const replays = Math.max(0, input.session?.replayed_user_messages ?? 0);
  const replaySpecies = [
    [input.session?.redelivery_replays, "thread re-deliveries"],
    [input.session?.recurrence_replays, "recurring skill invocations"],
    [input.session?.recovery_replays, "continue-after-cut retries"],
    [input.session?.other_replays, "other re-sends"],
  ]
    .filter(([count]) => Number(count) > 0)
    .map(([count, label]) => `${count} ${label}`)
    .join(", ");
  if (compactions.length && userTurns) {
    const perTurn = compactions.length / Math.max(1, userTurns - replays);
    if (perTurn >= 0.5) {
      findings.push({
        code: "COMPACTION_CHURN",
        severity: perTurn >= 1 ? "high" : "warn",
        title: `${compactions.length} compactions for ${Math.max(1, userTurns - replays)} user turns (${perTurn.toFixed(1)} per turn)`,
        detail: replays
          ? `Re-sent user content here is machinery (${replaySpecies}), so per-turn math excludes it. Compaction this frequent usually means the base is too large for the budget, not that the conversation is long.`
          : "Compaction this frequent usually means the base is too large for the budget, not that the conversation is long.",
      });
    }
  }
  // Redelivery is the costly replay species: the collaboration transport
  // re-ships the full thread context every round.
  const redelivery = Math.max(0, input.session?.redelivery_replays ?? 0);
  if (redelivery >= 3) {
    findings.push({
      code: "THREAD_REDELIVERY",
      severity: redelivery >= 8 ? "warn" : "note",
      title: `The collaboration transport re-delivered identical thread context ${redelivery}×`,
      detail:
        "Each specialist round re-ships the whole thread as one user message, so context grows with the square of the round count. Cache absorbs some of it; a delta or rolling-window transport removes it.",
    });
  }
  if ((input.session?.compression_ineffective_count ?? 0) > 0) {
    findings.push({
      code: "COMPACTION_INEFFECTIVE",
      severity: "warn",
      title: `Compaction was marked ineffective ${input.session?.compression_ineffective_count}× — Hermes suspended it and ran over the threshold`,
      detail:
        "Two consecutive compactions recovered too little (the summary + protected tail is nearly as big as what was compacted). The model then keeps running at the full size until the anti-thrash window passes.",
    });
  }
  void distinctUserTurns;

  // 3. Prompt jumps between consecutive calls, attributed to what landed in between.
  const jumps = calls
    .map((c, index) => ({ c, delta: Number(c.input_delta ?? 0), index }))
    .filter(({ delta }) => delta >= 20_000)
    .sort((a, b) => b.delta - a.delta);
  if (jumps.length) {
    const top = jumps[0];
    const between = landedBefore(messages, calls, top.index);
    findings.push({
      code: "PROMPT_JUMPS",
      severity: top.delta >= 40_000 ? "warn" : "note",
      title: `${jumps.length} call(s) grew the prompt by ≥20k tokens; largest +${fmt(top.delta)} on call #${top.c.call_no}`,
      detail: between.length
        ? `What landed just before it: ${between
            .slice(0, 4)
            .map(
              (m) =>
                `${m.kind}${m.tool_name ? ` ${shortTool(m.tool_name)}` : ""} (${fmt(m.payload_chars)} chars)`,
            )
            .join(", ")}.`
        : "No mirrored message sits between the two calls (an injected payload, an image, or a re-read of the tape).",
      callIds: jumps.slice(0, 5).map(({ c }) => c.id),
      messageIds: between.slice(0, 4).map((m) => m.id),
    });
  }

  // 4. Cache prefix breaks: cache % dropping ≥25 points call to call.
  const breaks: ForensicCall[] = [];
  for (let index = 1; index < calls.length; index += 1) {
    const prev = calls[index - 1];
    const cur = calls[index];
    if (
      prev.cache_pct != null &&
      cur.cache_pct != null &&
      Number(prev.cache_pct) - Number(cur.cache_pct) >= 25 &&
      Number(cur.input_tokens ?? 0) > 20_000
    ) {
      breaks.push(cur);
    }
  }
  if (breaks.length) {
    findings.push({
      code: "CACHE_BREAKS",
      severity: "note",
      title: `${breaks.length} call(s) lost ≥25 points of cache hit versus the previous call`,
      detail:
        "A prefix stopped matching: something before the conversation changed (instructions, tools, compaction rewrite) or the provider evicted it. Cheap to look at, expensive to ignore at 100k+ prompts.",
      callIds: breaks.map((c) => c.id),
    });
  }

  // 5. Huge payloads and rewrites.
  const bigResults = messages
    .filter((m) => m.role === "tool" && m.payload_chars >= 30_000)
    .sort((a, b) => b.payload_chars - a.payload_chars);
  if (bigResults.length) {
    findings.push({
      code: "LARGE_TOOL_RESULTS",
      severity: bigResults[0].payload_chars >= 100_000 ? "warn" : "note",
      title: `${bigResults.length} tool result(s) ≥30k chars; largest ${fmt(bigResults[0].payload_chars)} (${shortTool(bigResults[0].tool_name)})`,
      detail:
        "Big results are fine when they are read once and pruned; the same 45k-char read repeated every turn is the pattern to break (edit in place, read part='html', or narrow the tool's payload).",
      messageIds: bigResults.slice(0, 8).map((m) => m.id),
    });
  }
  const rewrites = messages.filter(
    (m) => m.kind === "tool_call" && m.tool_arg_chars >= 8_000,
  );
  if (rewrites.length) {
    findings.push({
      code: "WHOLE_DOCUMENT_WRITES",
      severity: rewrites.length >= 3 ? "warn" : "note",
      title: `${rewrites.length} tool call(s) carried ≥8k chars of arguments (whole-document writes)`,
      detail: `Retyping a document is where unrelated sections vanish silently. Revisions should be edits (update_live_app edits=[…]) against a fresh part='html' read. Tools: ${
        uniq(rewrites.flatMap((m) => m.tool_call_names ?? []))
          .slice(0, 4)
          .join(", ") || "?"
      }.`,
      messageIds: rewrites.map((m) => m.id),
    });
  }
  const dupes = messages.filter(
    (m) => (m.same_content_seen ?? 0) >= 2 && m.payload_chars >= 2000,
  );
  if (dupes.length) {
    const groups = new Map<string, ForensicMessage[]>();
    for (const m of dupes) {
      const key = `${m.tool_name ?? m.kind}:${m.payload_chars}`;
      groups.set(key, [...(groups.get(key) ?? []), m]);
    }
    const repeated = [...groups.values()].filter((g) => g.length >= 2);
    if (repeated.length) {
      findings.push({
        code: "REPEATED_PAYLOADS",
        severity: "note",
        title: `${repeated.length} identical payload(s) appear more than once (${repeated.reduce((s, g) => s + g.length, 0)} rows)`,
        detail:
          "Re-reads after compaction are legitimate; identical user messages are replays. Either way each copy is paid for again.",
        messageIds: repeated
          .flat()
          .map((m) => m.id)
          .slice(0, 12),
      });
    }
  }

  // 6. Truncated results / length-cut outputs.
  const truncated = events.filter((e) => e.kind === "tool_result_truncated");
  if (truncated.length) {
    findings.push({
      code: "TRUNCATED_RESULTS",
      severity: "note",
      title: `${truncated.length} tool result(s) were inline-truncated by Hermes`,
      detail: truncated
        .slice(0, 3)
        .map(
          (e) =>
            `${shortTool(String(e.details?.tool ?? "?"))} ${fmt(Number(e.details?.chars ?? 0))} chars`,
        )
        .join(" · "),
    });
  }
  const lengthCut = messages.filter((m) => m.finish_reason === "length");
  if (lengthCut.length) {
    findings.push({
      code: "OUTPUT_LENGTH_CUT",
      severity: "high",
      title: `${lengthCut.length} response(s) hit the output token cap (finish_reason=length)`,
      detail:
        "A whole-document write that gets cut mid-stream is the worst case: the tool call is invalid or the artifact incomplete. Raise max_tokens or move to edits.",
      messageIds: lengthCut.map((m) => m.id),
    });
  }

  // 7. Auxiliary spend (curator / vision / compression / title) as a share.
  const mainIn = usage
    .filter((u) => !u.task)
    .reduce((s, u) => s + Number(u.input_tokens || 0), 0);
  const auxIn = usage
    .filter((u) => u.task)
    .reduce((s, u) => s + Number(u.input_tokens || 0), 0);
  if (mainIn && auxIn / mainIn >= 0.25) {
    const parts = usage
      .filter((u) => u.task)
      .map((u) => `${u.task} ${fmt(u.input_tokens)}`)
      .join(" · ");
    findings.push({
      code: "AUXILIARY_SPEND",
      severity: auxIn / mainIn >= 1 ? "warn" : "note",
      title: `Auxiliary passes cost ${Math.round((auxIn / mainIn) * 100)}% of the main turn's input tokens`,
      detail: `${parts}. Compression and vision are per-turn overheads; the skill curator replays the whole history (now off by default on the appliance).`,
    });
  }
  const curatorTurns = messages.filter(
    (m) => m.kind === "skill_curator_prompt",
  );
  if (curatorTurns.length) {
    findings.push({
      code: "SKILL_CURATOR",
      severity: "note",
      title: `${curatorTurns.length} background skill-curator pass(es) ran in this session`,
      detail:
        "Each one forks the agent over the uncompacted history. Disabled by skills.creation_nudge_interval=0 on the appliance since 2026-08-17.",
      messageIds: curatorTurns.map((m) => m.id),
    });
  }

  const order: Record<FindingSeverity, number> = { high: 0, warn: 1, note: 2 };
  return findings.sort((a, b) => order[a.severity] - order[b.severity]);
}

function landedBefore(
  messages: ForensicMessage[],
  calls: ForensicCall[],
  callIndex: number,
): ForensicMessage[] {
  const cur = calls[callIndex];
  const prev = calls[callIndex - 1];
  if (!cur || !prev) return [];
  const start = Date.parse(prev.ts);
  const end = Date.parse(cur.ts);
  return messages
    .filter((m) => {
      const t = Date.parse(m.ts);
      return t > start && t <= end && m.payload_chars > 0;
    })
    .sort((a, b) => b.payload_chars - a.payload_chars);
}

export function shortTool(name: string | null | undefined): string {
  if (!name) return "?";
  return name.replace(/^mcp__.*?__/, "");
}

function uniq<T>(values: T[]): T[] {
  return [...new Set(values)];
}

export const KIND_COLORS: Record<string, string> = {
  user: "var(--rvbbit-accent)",
  assistant_text: "var(--viz-5, #65c6a7)",
  tool_call: "var(--viz-2, #6ba7ff)",
  mcp_result: "var(--viz-3, #b68cff)",
  tool_result: "var(--viz-4, #d9a441)",
  tool_describe: "var(--chrome-text)",
  compaction_summary: "var(--warning)",
  visual_self_check: "var(--viz-1, #ff8fa3)",
  skill_curator_prompt: "var(--danger)",
};

export function kindColor(kind: string): string {
  return KIND_COLORS[kind] || "var(--chrome-text)";
}
