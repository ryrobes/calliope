import { NextResponse } from "next/server"
import { runPlateAction } from "@/lib/server/plates"
import { burrowExecutionRole, burrowSession, isBurrow } from "@/lib/server/burrow"

export const runtime = "nodejs"

export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as {
    connectionId?: string
    plateId?: string
    action?: string
    args?: Record<string, unknown>
  } | null
  if (!body?.connectionId || !body?.plateId || !body?.action) {
    return NextResponse.json(
      { ok: false, error: "connectionId, plateId, action required" },
      { status: 400 },
    )
  }
  let role: string | undefined
  if (isBurrow()) {
    const session = await burrowSession(req.headers.get("cookie"))
    if (!session) return NextResponse.json({ ok: false, error: "not signed in" }, { status: 401 })
    role = burrowExecutionRole(session)
  }
  const result = await runPlateAction(body.connectionId, body.plateId, body.action, body.args ?? {}, role)
  return NextResponse.json(result)
}
