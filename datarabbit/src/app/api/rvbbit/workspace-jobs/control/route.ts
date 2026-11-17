import { NextRequest, NextResponse } from "next/server"
import { artifactInternalBase } from "@/lib/server/plates"

export const dynamic = "force-dynamic"

export async function POST(req: NextRequest) {
  const base = artifactInternalBase()
  const session = req.cookies.get("wh_session")?.value
  if (!session) return NextResponse.json({ error: { message: "Sign in to Calliope to control SQL jobs." } }, { status: 401 })
  if (!base) return NextResponse.json({ error: { message: "Calliope job controls are not configured on this DataRabbit server." } }, { status: 503 })
  try {
    const body = await req.json()
    if (typeof body?.jobId !== "string" || !/^[0-9a-f-]{36}$/i.test(body.jobId)
      || !["pause", "resume", "run_now"].includes(body.operation)) {
      return NextResponse.json({ error: { message: "Valid jobId and operation required" } }, { status: 400 })
    }
    const headers = new Headers({ "content-type": "application/json", cookie: `wh_session=${session}` })
    const key = process.env.WAREHOUSE_MCP_KEY
    if (key) headers.set("authorization", `Bearer ${key}`)
    const response = await fetch(`${base}/api/calliope/workspace-jobs/control`, {
      method: "POST", headers, cache: "no-store",
      body: JSON.stringify({ jobId: body.jobId, operation: body.operation }),
      signal: AbortSignal.timeout(20_000),
    })
    return new NextResponse(response.body, { status: response.status, headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
      "cache-control": "private, no-store", "x-content-type-options": "nosniff",
    } })
  } catch {
    // Do not automatically retry a control request after an uncertain response.
    return NextResponse.json({ error: { message: "Could not confirm the job control. Refresh its status before trying again." } }, { status: 502 })
  }
}
