import { NextResponse } from "next/server"
import { getImport, statusOf } from "@/lib/db/import-store"

export const runtime = "nodejs"

/** GET /api/db/import/status?id=<session> — phase, bytes staged, rows loaded, outcome. */
export async function GET(req: Request) {
  const id = new URL(req.url).searchParams.get("id") ?? ""
  const session = id ? getImport(id) : undefined
  if (!session) {
    return NextResponse.json({ ok: false, error: "Unknown or expired import id — prepare again." }, { status: 404, headers: { "cache-control": "no-store" } })
  }
  return NextResponse.json({ ok: true, status: statusOf(session) }, { headers: { "cache-control": "no-store" } })
}
