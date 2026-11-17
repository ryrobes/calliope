import "server-only"

import { Client } from "@modelcontextprotocol/sdk/client/index.js"
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js"
import type { ColonyShare } from "./colony-store"

/**
 * Colony's MCP shape (docs/PEER_CAPABILITIES_PLAN.md P4): a locally-running
 * stdio MCP server, shared the same way an Ollama/Gradio backend is —
 * except the "local model" is a subprocess speaking JSON-RPC over
 * stdin/stdout, not an HTTP endpoint.
 *
 * There is no existing code in this repo that spawns a stdio MCP
 * subprocess (rvbbit-mcp-gateway does, but it's a SEPARATE server-side
 * Python service reachable only via HTTP — the wrong shape here, since the
 * whole point is running on THIS person's own machine, not a shared
 * container that can't see their local filesystem/tools). So this module
 * is the first in-process stdio MCP client in rvbbit-lens: one client per
 * backend, spawned lazily on first claim and held for the process's
 * lifetime (mirrors the gateway's own "singleton subprocess, lazy-spawned"
 * doctrine, just local instead of shared).
 *
 * Same `next dev` caveat db/listen.ts documents for its own module-scope
 * hub map: Fast Refresh can leave a stale client/transport reference in
 * HOLDERS across a hot-reload of this module, surfacing as an odd runtime
 * error on the next claim (seen live once during development). Production
 * (no hot-reload, one process for the server's lifetime) doesn't have
 * this; in dev, restarting `next dev` clears it — same as listen.ts's own
 * "the browser just reconnects" recovery story.
 */

interface McpHolder {
  client: Client
  ready: Promise<void>
}

const HOLDERS = new Map<string, McpHolder>()

function startClient(share: Pick<ColonyShare, "backendName" | "command" | "args" | "env">): McpHolder {
  if (!share.command) {
    throw new Error(`mcp share '${share.backendName}' has no command configured`)
  }
  const client = new Client({ name: "rvbbit-lens-colony", version: "1.0.0" })
  const transport = new StdioClientTransport({
    command: share.command,
    args: share.args ?? [],
    env: share.env,
  })
  const ready = client.connect(transport).catch((e: unknown) => {
    // A failed spawn/handshake must not wedge future attempts — drop the
    // holder so the next claim retries a fresh spawn instead of replaying
    // the same rejected promise forever.
    HOLDERS.delete(share.backendName)
    throw e
  })
  return { client, ready }
}

async function ensureClient(share: Pick<ColonyShare, "backendName" | "command" | "args" | "env">): Promise<Client> {
  let holder = HOLDERS.get(share.backendName)
  if (!holder) {
    holder = startClient(share)
    HOLDERS.set(share.backendName, holder)
  }
  await holder.ready
  return holder.client
}

export async function callMcpTool(
  share: Pick<ColonyShare, "backendName" | "command" | "args" | "env">,
  toolName: string,
  args: Record<string, unknown>,
): Promise<unknown> {
  const client = await ensureClient(share)
  return client.callTool({ name: toolName, arguments: args })
}

export interface McpToolInfo {
  name: string
  description?: string
  inputSchema?: unknown
}

export async function listMcpTools(
  share: Pick<ColonyShare, "backendName" | "command" | "args" | "env">,
): Promise<McpToolInfo[]> {
  const client = await ensureClient(share)
  const result = await client.listTools()
  return result.tools.map((t) => ({ name: t.name, description: t.description, inputSchema: t.inputSchema }))
}

/**
 * One-shot tool discovery for the Share form — before committing to a
 * registration, spawn, list tools, and close immediately. Deliberately NOT
 * cached in HOLDERS: a probe is throwaway by design, so it can never leak
 * a subprocess the claim-loop doesn't know it owns.
 */
export async function probeMcpTools(command: string, args: string[], env?: Record<string, string>): Promise<McpToolInfo[]> {
  const client = new Client({ name: "rvbbit-lens-colony-probe", version: "1.0.0" })
  const transport = new StdioClientTransport({ command, args, env })
  try {
    await client.connect(transport)
    const result = await client.listTools()
    return result.tools.map((t) => ({ name: t.name, description: t.description, inputSchema: t.inputSchema }))
  } finally {
    await client.close().catch(() => {})
  }
}

export function stopMcpClient(backendName: string): void {
  const holder = HOLDERS.get(backendName)
  if (!holder) return
  HOLDERS.delete(backendName)
  holder.client.close().catch(() => {})
}
