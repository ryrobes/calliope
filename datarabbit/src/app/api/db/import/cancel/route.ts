import { NextResponse } from "next/server"
import { cancelImport, getImport, statusOf } from "@/lib/db/import-store"

export const runtime = "nodejs"

/** POST /api/db/import/cancel?id=<session> — abort a running load (rolls back) or drop a staged upload. */
export async function POST(req: Request) {
  const id = new URL(req.url).searchParams.get("id") ?? ""
  const session = id ? getImport(id) : undefined
  if (!session) {
    return NextResponse.json({ ok: true, gone: true })
  }
  await cancelImport(session)
  return NextResponse.json({ ok: true, status: statusOf(session) })
}
