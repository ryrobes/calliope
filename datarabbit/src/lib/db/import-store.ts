import "server-only"

import { createWriteStream } from "node:fs"
import { mkdir, readdir, rm, stat } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { Readable, Transform, pipeline } from "node:stream"
import type { ReadableStream as NodeWebReadableStream } from "node:stream/web"
import type { ImportConfig, ImportProgress, ImportRunResult, ImportSessionPhase, ImportSessionStatus } from "@/lib/import/types"

/**
 * Import sessions: the hand-off between `prepare`, the chunked upload, `start`
 * and `status`. Single process (local-first), so a module-level map is the
 * store; the bytes go to a staging file so a multi-GB upload never lives in
 * memory and never depends on one long HTTP request.
 *
 * Why sessions rather than a consume-once id (2026-09-20, a hosted install): a
 * 2.7 GB JSONL rode a single POST whose body outlived Node's 300 s
 * `requestTimeout`, Node answered 408, Chrome re-sent the whole body on its
 * own, and the retry found the id consumed — "Unknown or expired import id".
 * Every chunk here is a short request that refreshes the session, a retry of
 * a chunk is idempotent by offset, and the COPY runs detached from any browser
 * connection so a reload can re-attach through `status`.
 */
export interface ImportSession {
  id: string
  config: ImportConfig
  createdAt: number
  touchedAt: number
  phase: ImportSessionPhase
  bytesExpected: number | null
  bytesReceived: number
  stagePath: string
  /** A chunk is being written; a second concurrent chunk is refused. */
  writing: boolean
  progress: ImportProgress | null
  result: ImportRunResult | null
  error: string | null
  abort: AbortController | null
}

const sessions = new Map<string, ImportSession>()

/** Idle time before an unfinished upload (or an unstarted prepare) is dropped. */
const IDLE_TTL_MS = 30 * 60 * 1000
/** How long a finished session stays readable so a re-attaching window sees the outcome. */
const DONE_TTL_MS = 60 * 60 * 1000
const SWEEP_EVERY_MS = 60 * 1000

export const STAGE_DIR = process.env.RVBBIT_IMPORT_STAGE_DIR || path.join(os.tmpdir(), "rvbbit-import")

function stagePathFor(id: string): string {
  return path.join(STAGE_DIR, `${id}.part`)
}

async function removeStage(session: ImportSession): Promise<void> {
  await rm(session.stagePath, { force: true }).catch(() => undefined)
}

function isFinished(phase: ImportSessionPhase): boolean {
  return phase === "done" || phase === "failed" || phase === "cancelled"
}

async function sweep(now = Date.now()): Promise<void> {
  for (const [id, s] of sessions) {
    const ttl = isFinished(s.phase) ? DONE_TTL_MS : s.phase === "importing" ? Infinity : IDLE_TTL_MS
    if (now - s.touchedAt > ttl) {
      sessions.delete(id)
      await removeStage(s)
    }
  }
}

/** Staging files left by a previous process (a crash mid-upload) are not ours to keep. */
async function sweepOrphans(): Promise<void> {
  try {
    await mkdir(STAGE_DIR, { recursive: true })
    const now = Date.now()
    for (const name of await readdir(STAGE_DIR)) {
      if (!name.endsWith(".part")) continue
      const id = name.slice(0, -".part".length)
      if (sessions.has(id)) continue
      const full = path.join(STAGE_DIR, name)
      const st = await stat(full).catch(() => null)
      if (st && now - st.mtimeMs > IDLE_TTL_MS) await rm(full, { force: true }).catch(() => undefined)
    }
  } catch {
    /* staging dir unavailable: chunk() reports it when it matters */
  }
}

const sweeper = setInterval(() => {
  void sweep()
  void sweepOrphans()
}, SWEEP_EVERY_MS)
sweeper.unref()
void sweepOrphans()

export function createImport(config: ImportConfig, bytesExpected: number | null): string {
  const now = Date.now()
  void sweep(now)
  const id = crypto.randomUUID()
  sessions.set(id, {
    id,
    config,
    createdAt: now,
    touchedAt: now,
    phase: "uploading",
    bytesExpected: Number.isFinite(bytesExpected as number) && (bytesExpected as number) >= 0 ? bytesExpected : null,
    bytesReceived: 0,
    stagePath: stagePathFor(id),
    writing: false,
    progress: null,
    result: null,
    error: null,
    abort: null,
  })
  return id
}

export function getImport(id: string): ImportSession | undefined {
  return sessions.get(id)
}

export function statusOf(s: ImportSession): ImportSessionStatus {
  return {
    id: s.id,
    phase: s.phase,
    schema: s.config.schema,
    table: s.config.table,
    bytesExpected: s.bytesExpected,
    bytesReceived: s.bytesReceived,
    progress: s.progress,
    result: s.result,
    error: s.error,
    updatedAt: s.touchedAt,
  }
}

export class ChunkError extends Error {
  constructor(message: string, readonly status: number, readonly bytesReceived: number) {
    super(message)
  }
}

/**
 * Append one chunk at `offset`. The client sends chunks in order and retries a
 * failed one; a chunk at an offset already covered is acknowledged without
 * writing (the retry of a request whose response was lost), a chunk past the
 * end is a gap and is refused with the offset to resume from.
 */
export async function appendChunk(
  s: ImportSession,
  offset: number,
  body: NodeWebReadableStream<Uint8Array>,
  declaredLength: number | null,
): Promise<number> {
  if (s.phase !== "uploading") throw new ChunkError(`Import is ${s.phase}; not accepting data`, 409, s.bytesReceived)
  if (s.writing) throw new ChunkError("Another chunk is still being written", 409, s.bytesReceived)
  if (offset > s.bytesReceived) throw new ChunkError(`Gap: have ${s.bytesReceived} bytes, chunk starts at ${offset}`, 409, s.bytesReceived)
  const source = Readable.fromWeb(body)
  if (offset < s.bytesReceived) {
    // Already have it (a retried chunk whose ack was lost). Drain and ack.
    if (declaredLength != null && offset + declaredLength > s.bytesReceived) {
      throw new ChunkError(`Chunk at ${offset} overlaps the end (${s.bytesReceived}); resume from there`, 409, s.bytesReceived)
    }
    source.resume()
    await new Promise<void>((resolve) => source.once("end", resolve).once("error", () => resolve()))
    s.touchedAt = Date.now()
    return s.bytesReceived
  }
  s.writing = true
  let written = 0
  const counter = new Transform({
    transform(chunk: Buffer, _enc, cb) {
      written += chunk.length
      cb(null, chunk)
    },
  })
  try {
    await mkdir(STAGE_DIR, { recursive: true })
    await new Promise<void>((resolve, reject) => {
      pipeline(source, counter, createWriteStream(s.stagePath, { flags: "a" }), (err) => (err ? reject(err) : resolve()))
    })
    if (declaredLength != null && written !== declaredLength) {
      // The connection dropped mid-chunk: the file now holds a partial chunk.
      // Truncate back to the last good offset so the client's retry lines up.
      await truncateStage(s.stagePath, s.bytesReceived)
      throw new ChunkError(`Chunk truncated (${written} of ${declaredLength} bytes); resume from ${s.bytesReceived}`, 409, s.bytesReceived)
    }
    s.bytesReceived += written
    s.touchedAt = Date.now()
    return s.bytesReceived
  } catch (err) {
    if (err instanceof ChunkError) throw err
    await truncateStage(s.stagePath, s.bytesReceived)
    throw new ChunkError(err instanceof Error ? err.message : String(err), 500, s.bytesReceived)
  } finally {
    s.writing = false
  }
}

async function truncateStage(file: string, length: number): Promise<void> {
  const { truncate } = await import("node:fs/promises")
  await truncate(file, length).catch(() => undefined)
}

export interface Runner {
  (args: { signal: AbortSignal; onProgress: (p: ImportProgress) => void; stagePath: string }): Promise<ImportRunResult>
}

/**
 * Move the session to `importing` and run the load detached from the request
 * that asked for it. `status` is how the browser follows along, and how a
 * reloaded window re-attaches.
 */
export function startImport(s: ImportSession, run: Runner): { ok: true } | { ok: false; error: string } {
  if (s.phase !== "uploading") return { ok: false, error: `Import is already ${s.phase}` }
  if (s.writing) return { ok: false, error: "A chunk is still being written" }
  if (s.bytesExpected != null && s.bytesReceived !== s.bytesExpected) {
    return { ok: false, error: `Upload incomplete: ${s.bytesReceived} of ${s.bytesExpected} bytes` }
  }
  if (s.bytesReceived === 0) return { ok: false, error: "Nothing uploaded" }
  const ac = new AbortController()
  s.abort = ac
  s.phase = "importing"
  s.touchedAt = Date.now()
  s.progress = { bytesRead: 0, rowsRead: 0, rowsLoaded: 0, rowsRejected: 0 }
  void run({
    signal: ac.signal,
    stagePath: s.stagePath,
    onProgress: (p) => {
      s.progress = p
      s.touchedAt = Date.now()
    },
  })
    .then((result) => {
      s.result = result
      s.progress = { bytesRead: s.bytesReceived, rowsRead: result.rowsRead, rowsLoaded: result.rowsLoaded, rowsRejected: result.rowsRejected }
      s.phase = "done"
    })
    .catch((err: unknown) => {
      s.error = err instanceof Error ? err.message : String(err)
      s.phase = ac.signal.aborted ? "cancelled" : "failed"
    })
    .finally(() => {
      s.abort = null
      s.touchedAt = Date.now()
      void removeStage(s)
    })
  return { ok: true }
}

/**
 * The legacy single-request path (`run` with the file as the body) shares the
 * session so `status` and a browser retry behave: a second POST for an id
 * that already started is told so instead of "expired".
 */
export function claimForStreamingRun(s: ImportSession): { ok: true; abort: AbortController } | { ok: false; error: string } {
  if (s.phase !== "uploading") return { ok: false, error: `Import ${s.id} is already ${s.phase}; follow it from the import window instead of re-sending` }
  if (s.bytesReceived > 0) return { ok: false, error: "This import is being uploaded in chunks; call start instead" }
  const ac = new AbortController()
  s.abort = ac
  s.phase = "importing"
  s.touchedAt = Date.now()
  return { ok: true, abort: ac }
}

export function finishStreamingRun(s: ImportSession, outcome: { result?: ImportRunResult; error?: string; cancelled?: boolean }): void {
  if (outcome.result) {
    s.result = outcome.result
    s.phase = "done"
  } else {
    s.error = outcome.error ?? "import failed"
    s.phase = outcome.cancelled ? "cancelled" : "failed"
  }
  s.abort = null
  s.touchedAt = Date.now()
}

export async function cancelImport(s: ImportSession): Promise<void> {
  if (s.phase === "importing" && s.abort) {
    s.abort.abort()
    return // the runner's rejection moves the phase to cancelled
  }
  if (s.phase === "uploading") {
    s.phase = "cancelled"
    s.error = "cancelled before the load started"
    s.touchedAt = Date.now()
    await removeStage(s)
  }
}
