import { createReadStream } from "node:fs"
import { NextResponse } from "next/server"
import { getConnection } from "@/lib/db/registry"
import { getImport, startImport, statusOf } from "@/lib/db/import-store"
import { runImport } from "@/lib/db/import-run"

export const runtime = "nodejs"

/**
 * POST /api/db/import/start?id=<session> — every chunk is staged; create the
 * table and COPY from the staging file. Returns at once: the load runs
 * detached from this request (and from the browser), and `status` follows it.
 */
export async function POST(req: Request) {
  const id = new URL(req.url).searchParams.get("id") ?? ""
  const session = id ? getImport(id) : undefined
  if (!session) {
    return NextResponse.json({ ok: false, error: "Unknown or expired import id — prepare again." }, { status: 404 })
  }
  const record = await getConnection(session.config.connectionId)
  if (!record) {
    return NextResponse.json({ ok: false, error: "Unknown connection" }, { status: 400 })
  }
  const started = startImport(session, ({ signal, onProgress, stagePath }) =>
    runImport({ record, config: session.config, body: createReadStream(stagePath), signal, onProgress }),
  )
  if (!started.ok) {
    return NextResponse.json({ ok: false, error: started.error, status: statusOf(session) }, { status: 409 })
  }
  return NextResponse.json({ ok: true, status: statusOf(session) })
}
