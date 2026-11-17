import { NextResponse } from "next/server"
import { executeQuery } from "@/lib/db/query"
import { burrowExecutionRole, burrowSession, isBurrow } from "@/lib/server/burrow"
import { removeShare } from "@/lib/server/colony-store"
import { stopMcpClient } from "@/lib/server/colony-mcp"

export const runtime = "nodejs"

interface Body {
  connectionId?: string
  backendName?: string
}

function sqlLit(v: string): string {
  return `'${v.replace(/'/g, "''")}'`
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as Body | null
  if (!body?.connectionId || !body?.backendName) {
    return NextResponse.json({ ok: false, error: "connectionId and backendName required" }, { status: 400 })
  }
  try {
    let role: string | undefined
    if (isBurrow()) {
      const session = await burrowSession(req.headers.get("cookie"))
      if (!session) return NextResponse.json({ ok: false, error: "not signed in" }, { status: 401 })
      role = burrowExecutionRole(session)
    }
    await executeQuery(body.connectionId, `SELECT rvbbit.deregister_peer_backend(${sqlLit(body.backendName)})`, {
      role,
    })
    await removeShare(body.backendName)
    stopMcpClient(body.backendName)
    return NextResponse.json({ ok: true })
  } catch (err) {
    const e = err as Error
    return NextResponse.json({ ok: false, error: e.message ?? String(err) }, { status: 200 })
  }
}
