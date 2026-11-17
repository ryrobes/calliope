import { NextResponse } from "next/server"
import { scanForLocalModels } from "@/lib/server/colony-scan"
import { burrowSession, isBurrow } from "@/lib/server/burrow"

export const runtime = "nodejs"

export async function GET(req: Request) {
  // Probes only hardcoded loopback ports, so this is not a URL proxy — but it
  // does report what's running on the host, which is no stranger's business.
  if (isBurrow() && !(await burrowSession(req.headers.get("cookie")))) {
    return NextResponse.json({ ok: false, error: "not signed in" }, { status: 401 })
  }
  const candidates = await scanForLocalModels()
  return NextResponse.json({ ok: true, candidates })
}
