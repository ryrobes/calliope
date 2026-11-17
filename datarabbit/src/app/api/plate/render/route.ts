import { NextResponse } from "next/server"
import { renderPlate } from "@/lib/server/plates"
import { burrowExecutionRole, burrowSession, isBurrow } from "@/lib/server/burrow"

export const runtime = "nodejs"

export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as {
    connectionId?: string
    plateId?: string
    params?: Record<string, unknown>
  } | null
  if (!body?.connectionId || !body?.plateId) {
    return NextResponse.json({ ok: false, error: "connectionId and plateId required" }, { status: 400 })
  }
  try {
    let role: string | undefined
    if (isBurrow()) {
      const session = await burrowSession(req.headers.get("cookie"))
      if (!session) return NextResponse.json({ ok: false, error: "not signed in" }, { status: 401 })
      role = burrowExecutionRole(session)
    }
    const rendered = await renderPlate(body.connectionId, body.plateId, body.params ?? {}, role)
    return NextResponse.json({ ok: true, ...rendered })
  } catch (e) {
    return NextResponse.json({ ok: false, error: e instanceof Error ? e.message : String(e) })
  }
}
