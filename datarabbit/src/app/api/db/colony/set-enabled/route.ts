import { NextResponse } from "next/server"
import { executeQuery } from "@/lib/db/query"
import { burrowExecutionRole, burrowSession, isBurrow } from "@/lib/server/burrow"

export const runtime = "nodejs"

interface Body {
  connectionId?: string
  backendName?: string
  enabled?: boolean
}

function sqlLit(v: string): string {
  return `'${v.replace(/'/g, "''")}'`
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as Body | null
  if (!body?.connectionId || !body?.backendName || typeof body.enabled !== "boolean") {
    return NextResponse.json(
      { ok: false, error: "connectionId, backendName, and enabled required" },
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
    await executeQuery(
      body.connectionId,
      `SELECT rvbbit.set_peer_backend_enabled(${sqlLit(body.backendName)}, ${body.enabled ? "true" : "false"})`,
      { role },
    )
    return NextResponse.json({ ok: true })
  } catch (err) {
    const e = err as Error
    return NextResponse.json({ ok: false, error: e.message ?? String(err) }, { status: 200 })
  }
}
