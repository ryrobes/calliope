import "server-only"

import { randomUUID } from "node:crypto"
import { Client as GradioClient } from "@gradio/client"
import { executeQuery } from "@/lib/db/query"
import { sharesForConnection, type ColonyShare } from "./colony-store"
import { callMcpTool } from "./colony-mcp"

/**
 * Colony's claim-loop (docs/PEER_CAPABILITIES_PLAN.md P1): for every peer
 * backend THIS DataRabbit instance shares, heartbeat presence, claim
 * pending requests (rvbbit.claim_peer_request), call the local model, and
 * complete the request. Server-lifetime, not request-driven — same shape
 * as query.ts's ensureTxnSweep: a module-scope singleton per connectionId,
 * lazily started, unref()'d so it never blocks clean shutdown.
 *
 * Ported from a Python prototype (colony_runner.py) that proved the
 * mechanism against real Ollama + Gradio backends. Three shapes: openai_chat
 * (covers Ollama's own OpenAI-compatible endpoint, already the AI Providers
 * panel's own Ollama preset), gradio (the official @gradio/client — the JS
 * equivalent of the Python gradio_client this was prototyped against;
 * modern Gradio's wire protocol is queue/SSE-based, not a simple POST, so
 * hand-rolling it isn't worth it when the real client is one dependency),
 * and mcp (a locally-spawned stdio MCP server, docs/PEER_CAPABILITIES_PLAN.md
 * P4 — see colony-mcp.ts). The mcp shape's payload/response convention is
 * genuinely different from the two chat shapes (a tool name + JSON args,
 * not a prompt string), so it branches before the shared prompt-extraction
 * logic rather than going through callLocalModel.
 */

const TICK_MS = 1_000
const HEARTBEAT_MS = 5_000
// Ceiling on one local model call. Node's fetch has NO default timeout, so a
// wedged endpoint (stalled TCP, hung model) would leave the request 'claimed'
// forever: neither the success path nor the error-completion path runs, and
// the caller waits out its own poll timeout with no explanation. The MCP shape
// is already bounded by the SDK's own 60s request timeout.
const CALL_TIMEOUT_MS = 300_000

interface RunnerState {
  timer: ReturnType<typeof setInterval>
  instanceIds: Map<string, string> // backendName -> stable instance_id for this process
  lastHeartbeat: Map<string, number>
}

const RUNNERS = new Map<string, RunnerState>()

/** Postgres single-quoted literal (executeQuery has no bind params). */
function q(s: string): string {
  return `'${String(s).replace(/'/g, "''")}'`
}

async function callOpenAiChat(share: ColonyShare, prompt: string): Promise<{ content: string; model?: string }> {
  if (!share.localUrl) throw new Error(`openai_chat share '${share.backendName}' has no localUrl configured`)
  const res = await fetch(share.localUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model: share.model || "default",
      messages: [{ role: "user", content: prompt }],
      stream: false,
    }),
    signal: AbortSignal.timeout(CALL_TIMEOUT_MS),
  })
  if (!res.ok) {
    throw new Error(`local model returned HTTP ${res.status}: ${(await res.text().catch(() => "")).slice(0, 300)}`)
  }
  const body = (await res.json()) as {
    choices?: Array<{ message?: { content?: string } }>
    message?: { content?: string } // Ollama's native /api/chat shape, in case localUrl points there
  }
  const content = body.choices?.[0]?.message?.content ?? body.message?.content
  if (!content) throw new Error("local model response had no message content")
  return { content, model: share.model }
}

// Gradio's connection handshake (config + api_info fetch) is real overhead —
// worth caching per endpoint across ticks/claims rather than reconnecting
// every call, mirroring the Python prototype's own _client_cache.
const GRADIO_CLIENTS = new Map<string, Promise<GradioClient>>()

async function gradioClientFor(localUrl: string): Promise<GradioClient> {
  let pending = GRADIO_CLIENTS.get(localUrl)
  if (!pending) {
    pending = GradioClient.connect(localUrl)
    GRADIO_CLIENTS.set(localUrl, pending)
  }
  try {
    return await pending
  } catch (e) {
    GRADIO_CLIENTS.delete(localUrl)
    throw e
  }
}

/** Bound a promise that has no timeout of its own (see CALL_TIMEOUT_MS). */
function withTimeout<T>(p: Promise<T>, label: string): Promise<T> {
  return Promise.race([
    p,
    new Promise<never>((_, reject) =>
      setTimeout(
        () => reject(new Error(`${label} exceeded ${CALL_TIMEOUT_MS}ms`)),
        CALL_TIMEOUT_MS,
      ).unref(),
    ),
  ])
}

async function callGradio(share: ColonyShare, prompt: string): Promise<{ content: string }> {
  if (!share.localUrl) throw new Error(`gradio share '${share.backendName}' has no localUrl configured`)
  const client = await withTimeout(gradioClientFor(share.localUrl), "gradio connect")
  const result = await withTimeout(client.predict("/predict", [prompt]), "gradio predict")
  const data = result.data as unknown
  const first = Array.isArray(data) ? data[0] : data
  if (first == null) throw new Error("local Gradio app returned no output")
  return { content: typeof first === "string" ? first : JSON.stringify(first) }
}

async function callLocalModel(share: ColonyShare, prompt: string): Promise<{ content: string; model?: string }> {
  return share.shape === "gradio" ? callGradio(share, prompt) : callOpenAiChat(share, prompt)
}

async function tick(
  connectionId: string,
  instanceIds: Map<string, string>,
  lastHeartbeat: Map<string, number>,
): Promise<void> {
  let shares: ColonyShare[]
  try {
    shares = await sharesForConnection(connectionId)
  } catch {
    return
  }
  if (shares.length === 0) return

  for (const share of shares) {
    let instanceId = instanceIds.get(share.backendName)
    if (!instanceId) {
      instanceId = randomUUID()
      instanceIds.set(share.backendName, instanceId)
    }

    const now = Date.now()
    const lastHb = lastHeartbeat.get(share.backendName) ?? 0
    if (now - lastHb > HEARTBEAT_MS) {
      lastHeartbeat.set(share.backendName, now)
      try {
        await executeQuery(
          connectionId,
          `SELECT rvbbit.peer_heartbeat(${q(share.backendName)}, ${q(instanceId)}, 0)`,
        )
      } catch {
        // Best-effort: a missed heartbeat just means this backend looks briefly
        // offline to other sessions; the next tick retries.
        continue
      }
    }

    try {
      const claimed = await executeQuery(
        connectionId,
        `SELECT request_id, payload FROM rvbbit.claim_peer_request(${q(share.backendName)}, ${q(instanceId)})`,
      )
      const row = claimed.rows[0] as { request_id?: string; payload?: unknown } | undefined
      if (!row?.request_id) continue

      try {
        let result: unknown
        if (share.shape === "mcp") {
          const p = row.payload as { tool?: string; args?: Record<string, unknown> } | null
          if (!p?.tool) throw new Error("mcp call payload must include a 'tool' name, e.g. {\"tool\":\"...\",\"args\":{}}")
          result = { result: await callMcpTool(share, p.tool, p.args ?? {}) }
        } else {
          const payload = row.payload as { user?: string } | null
          result = await callLocalModel(share, payload?.user ?? "")
        }
        await executeQuery(
          connectionId,
          `SELECT rvbbit.complete_peer_request(${q(row.request_id)}::uuid, ${q(JSON.stringify(result))}::jsonb, NULL)`,
        )
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e)
        await executeQuery(
          connectionId,
          `SELECT rvbbit.complete_peer_request(${q(row.request_id)}::uuid, NULL, ${q(message)})`,
        ).catch(() => {})
      }
    } catch {
      // Claim failed (e.g. transient connection issue) — retried next tick.
    }
  }
}

export function ensureColonyRunner(connectionId: string): void {
  if (RUNNERS.has(connectionId)) return
  const instanceIds = new Map<string, string>()
  const lastHeartbeat = new Map<string, number>()
  // Re-entrancy guard: tick() walks shares sequentially with awaits, so a slow
  // backend early in the list would otherwise let overlapping ticks pile up on
  // it and starve heartbeats/claims for every share behind it.
  let inFlight = false
  const timer = setInterval(() => {
    if (inFlight) return
    inFlight = true
    void tick(connectionId, instanceIds, lastHeartbeat).finally(() => {
      inFlight = false
    })
  }, TICK_MS)
  ;(timer as { unref?: () => void }).unref?.()
  RUNNERS.set(connectionId, { timer, instanceIds, lastHeartbeat })
}

export function stopColonyRunner(connectionId: string): void {
  const state = RUNNERS.get(connectionId)
  if (!state) return
  clearInterval(state.timer)
  RUNNERS.delete(connectionId)
}

export function colonyRunnerStatus(connectionId: string): { running: boolean; backends: string[] } {
  const state = RUNNERS.get(connectionId)
  return { running: !!state, backends: state ? [...state.instanceIds.keys()] : [] }
}
