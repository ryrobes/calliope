import os from "node:os"
import { NextResponse } from "next/server"
import { executeQuery } from "@/lib/db/query"
import { burrowExecutionRole, burrowSession, isBurrow } from "@/lib/server/burrow"
import { upsertShare, type ColonyShape } from "@/lib/server/colony-store"
import { ensureColonyRunner } from "@/lib/server/colony-runner"
import { stopMcpClient } from "@/lib/server/colony-mcp"

export const runtime = "nodejs"

interface Body {
  connectionId?: string
  backendName?: string
  kind?: string
  scopeRole?: string
  description?: string
  localUrl?: string
  model?: string
  shape?: ColonyShape
  command?: string
  args?: string[]
  env?: Record<string, string>
}

function sqlLit(v: string): string {
  return `'${v.replace(/'/g, "''")}'`
}

const VALID_SHAPES: ColonyShape[] = ["openai_chat", "gradio", "mcp"]

export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as Body | null
  const shape: ColonyShape = VALID_SHAPES.includes(body?.shape as ColonyShape) ? (body!.shape as ColonyShape) : "openai_chat"
  const missingLocation = shape === "mcp" ? !body?.command : !body?.localUrl
  if (!body?.connectionId || !body?.backendName || !body?.kind || !body?.scopeRole || missingLocation) {
    return NextResponse.json(
      {
        ok: false,
        error:
          shape === "mcp"
            ? "connectionId, backendName, kind, scopeRole, and command are required"
            : "connectionId, backendName, kind, scopeRole, and localUrl are required",
      },
      { status: 400 },
    )
  }
  try {
    let role: string | undefined
    if (isBurrow()) {
      const session = await burrowSession(req.headers.get("cookie"))
      if (!session) return NextResponse.json({ ok: false, error: "not signed in" }, { status: 401 })
      role = burrowExecutionRole(session)
    }

    // Two registrations, matching how peer_queue.rs expects them to line up:
    // rvbbit.backends (the generic catalog every transport shares) and
    // rvbbit.peer_backends (Colony's own scope/presence table), same name.
    const endpoint = `peer://${body.backendName}`
    await executeQuery(
      body.connectionId,
      `SELECT rvbbit.register_backend(
         backend_name => ${sqlLit(body.backendName)},
         backend_endpoint => ${sqlLit(endpoint)},
         backend_transport => 'peer_queue',
         backend_timeout_ms => 60000,
         backend_description => ${sqlLit(body.description || "Colony peer backend")}
       )`,
      { role },
    )
    // p_client_host: this machine's self-reported hostname, so the Fleet
    // topology can name the peer node by the box that's actually answering
    // (named-arg call — the migration-0218 signature keeps it last/optional).
    await executeQuery(
      body.connectionId,
      `SELECT rvbbit.register_peer_backend(
         ${sqlLit(body.backendName)}, ${sqlLit(body.kind)}, ${sqlLit(body.scopeRole)},
         ${sqlLit(shape)}, ${sqlLit(body.description || "")},
         p_client_host => ${sqlLit(os.hostname())}
       )`,
      { role },
    )

    // Re-registering an existing name replaces its config, but the mcp holder
    // is cached by backend name and would keep talking to the OLD subprocess
    // (silently ignoring an edited command/args/env) until process restart.
    stopMcpClient(body.backendName)

    await upsertShare({
      backendName: body.backendName,
      connectionId: body.connectionId,
      shape,
      localUrl: body.localUrl,
      model: body.model,
      command: body.command,
      args: body.args,
      env: body.env,
    })
    ensureColonyRunner(body.connectionId)

    return NextResponse.json({ ok: true })
  } catch (err) {
    const e = err as Error
    return NextResponse.json({ ok: false, error: e.message ?? String(err) }, { status: 200 })
  }
}
