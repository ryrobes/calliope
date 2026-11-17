import { NextResponse } from "next/server"

import { getMeta, putMeta } from "@/lib/server/lens-db"

// Server-persisted one-shot UX flags (lens_meta KV). Only whitelisted keys —
// this is not a general datastore.
export const runtime = "nodejs"

const ALLOWED = new Set(["field_guide_seen"])

export async function GET(req: Request) {
  const key = new URL(req.url).searchParams.get("key") ?? ""
  if (!ALLOWED.has(key)) return NextResponse.json({ ok: false, error: "unknown key" }, { status: 400 })
  return NextResponse.json({ ok: true, value: getMeta(key) })
}

export async function PUT(req: Request) {
  const body = (await req.json().catch(() => null)) as { key?: string; value?: string } | null
  if (!body?.key || !ALLOWED.has(body.key)) {
    return NextResponse.json({ ok: false, error: "unknown key" }, { status: 400 })
  }
  putMeta(body.key, body.value ?? "1")
  return NextResponse.json({ ok: true })
}
