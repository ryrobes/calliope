import { NextResponse } from "next/server"
import { sharesForConnection } from "@/lib/server/colony-store"
import { colonyRunnerStatus, ensureColonyRunner } from "@/lib/server/colony-runner"
import { burrowSession, isBurrow } from "@/lib/server/burrow"

export const runtime = "nodejs"

export async function GET(req: Request) {
  const connectionId = new URL(req.url).searchParams.get("connectionId")
  if (!connectionId) {
    return NextResponse.json({ ok: false, error: "connectionId required" }, { status: 400 })
  }
  // Same gate the mutating colony routes use. Without it this GET was an
  // unauthenticated dump of local share config in burrow mode.
  if (isBurrow() && !(await burrowSession(req.headers.get("cookie")))) {
    return NextResponse.json({ ok: false, error: "not signed in" }, { status: 401 })
  }
  const shares = await sharesForConnection(connectionId)
  // Idempotent: a share can exist on disk (from a prior process) with no
  // runner started yet in THIS process — status is also where it resumes.
  if (shares.length > 0) ensureColonyRunner(connectionId)
  const status = colonyRunnerStatus(connectionId)
  // NEVER echo command/args/env: env is where the Share form tells people to
  // put API keys (its own placeholder is API_KEY=...). The window only reads
  // backendName/shape/localUrl/model, so the rest never leaves this process.
  const safeShares = shares.map((s) => ({
    backendName: s.backendName,
    connectionId: s.connectionId,
    shape: s.shape,
    localUrl: s.localUrl,
    model: s.model,
  }))
  return NextResponse.json({ ok: true, shares: safeShares, ...status })
}
