import { NextResponse } from "next/server"
import type { ReadableStream as NodeWebReadableStream } from "node:stream/web"
import { ChunkError, appendChunk, getImport } from "@/lib/db/import-store"

export const runtime = "nodejs"
export const maxDuration = 600

/**
 * POST /api/db/import/chunk?id=<session>&offset=<bytes> — the request body is
 * one slice of the file, appended to the session's staging file. Short
 * requests, in order, each refreshing the session: a slow uplink never trips a
 * request timeout, and a retried chunk is idempotent by offset. The answer
 * always carries `bytesReceived`, which is where the client resumes from.
 */
export async function POST(req: Request) {
  const url = new URL(req.url)
  const id = url.searchParams.get("id") ?? ""
  const offset = Number(url.searchParams.get("offset"))
  const session = id ? getImport(id) : undefined
  if (!session) {
    return NextResponse.json({ ok: false, error: "Unknown or expired import id — prepare again.", bytesReceived: 0 }, { status: 404 })
  }
  if (!Number.isInteger(offset) || offset < 0) {
    return NextResponse.json({ ok: false, error: "offset must be a non-negative integer", bytesReceived: session.bytesReceived }, { status: 400 })
  }
  if (!req.body) {
    return NextResponse.json({ ok: false, error: "No request body", bytesReceived: session.bytesReceived }, { status: 400 })
  }
  const declared = req.headers.get("content-length")
  const declaredLength = declared != null && /^\d+$/.test(declared) ? Number(declared) : null
  try {
    const bytesReceived = await appendChunk(session, offset, req.body as unknown as NodeWebReadableStream<Uint8Array>, declaredLength)
    return NextResponse.json({ ok: true, bytesReceived })
  } catch (err) {
    if (err instanceof ChunkError) {
      return NextResponse.json({ ok: false, error: err.message, bytesReceived: err.bytesReceived }, { status: err.status })
    }
    return NextResponse.json({ ok: false, error: err instanceof Error ? err.message : String(err), bytesReceived: session.bytesReceived }, { status: 500 })
  }
}
