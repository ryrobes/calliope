import { Readable } from "node:stream"
import type { ReadableStream as NodeWebReadableStream } from "node:stream/web"
import { getConnection } from "@/lib/db/registry"
import { claimForStreamingRun, finishStreamingRun, getImport } from "@/lib/db/import-store"
import { runImport } from "@/lib/db/import-run"

export const runtime = "nodejs"
// Allow long imports in production (ignored by `next dev`).
export const maxDuration = 3600

const NDJSON = { "content-type": "application/x-ndjson", "cache-control": "no-store" }

function frame(obj: unknown): string {
  return `${JSON.stringify(obj)}\n`
}

/**
 * Single-request import: stream the file (request body) into a new table,
 * emitting newline-delimited JSON progress frames as it goes —
 * `{type:"progress",…}` repeatedly, then a terminal `{type:"done",…}` or
 * `{type:"error",…}`. Aborting the fetch cancels the COPY and rolls back.
 *
 * The window uploads in chunks now (`chunk` → `start` → `status`); this path
 * stays for small files and older clients. It shares the session, so a
 * browser's automatic re-send of a POST that was cut off is told the import
 * is already running instead of "expired", and `status` works here too.
 */
export async function POST(req: Request) {
  const id = new URL(req.url).searchParams.get("id") ?? ""
  const session = id ? getImport(id) : undefined
  if (!session) {
    return new Response(frame({ type: "error", error: "Unknown or expired import id — prepare again." }), {
      status: 400,
      headers: NDJSON,
    })
  }
  if (!req.body) {
    return new Response(frame({ type: "error", error: "No request body" }), { status: 400, headers: NDJSON })
  }
  const record = await getConnection(session.config.connectionId)
  if (!record) {
    return new Response(frame({ type: "error", error: "Unknown connection" }), { status: 400, headers: NDJSON })
  }
  const claim = claimForStreamingRun(session)
  if (!claim.ok) {
    return new Response(frame({ type: "error", error: claim.error }), { status: 409, headers: NDJSON })
  }
  const config = session.config
  const bodyStream = req.body as unknown as NodeWebReadableStream<Uint8Array>
  const encoder = new TextEncoder()
  req.signal.addEventListener("abort", () => claim.abort.abort())

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (obj: unknown) => controller.enqueue(encoder.encode(frame(obj)))
      try {
        const result = await runImport({
          record,
          config,
          body: Readable.fromWeb(bodyStream),
          signal: claim.abort.signal,
          onProgress: (p) => {
            session.progress = p
            session.touchedAt = Date.now()
            send({ type: "progress", ...p })
          },
        })
        finishStreamingRun(session, { result })
        send({ type: "done", ...result })
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        finishStreamingRun(session, { error: message, cancelled: claim.abort.signal.aborted })
        send({ type: "error", error: message })
      } finally {
        controller.close()
      }
    },
  })

  return new Response(stream, { headers: NDJSON })
}
