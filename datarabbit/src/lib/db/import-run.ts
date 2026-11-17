import "server-only"

import { Client } from "pg"
import { from as copyFrom } from "pg-copy-streams"
import { parse } from "csv-parse"
import { Readable, Transform, pipeline } from "node:stream"
import { once } from "node:events"
import type { ConnectionRecord } from "./types"
import { buildClientConfig } from "./pool"
import { resolveEndpoint } from "./tunnel"
import type { ImportConfig, ImportProgress, ImportRunResult, RejectRow } from "@/lib/import/types"
import { buildCopyColumnList, buildCreateTableSql, includedColumns, qualifiedName } from "@/lib/import/ddl"
import { coerceCell, toCopyCsvLine } from "@/lib/import/coerce"
import { coerceJsonValue, parseJsonlLine } from "@/lib/import/jsonl"

/** Cap rejects kept in memory (and returned for the report); the rest are counted. */
const REJECT_CAP = 1000
const PROGRESS_EVERY_MS = 120

function nodeEncoding(enc: string): BufferEncoding {
  return enc === "utf-16le" ? "utf16le" : enc === "latin1" ? "latin1" : "utf8"
}

function clipRow(record: string[]): string {
  const s = record.join(",")
  return s.length > 200 ? `${s.slice(0, 200)}…` : s
}

/**
 * Split a byte stream into complete lines (LF or CRLF), decoding as we go so a
 * multi-byte character straddling two chunks is never cut. Emits one string per
 * line; the final unterminated line is flushed at end.
 */
function lineSplitter(encoding: BufferEncoding): Transform {
  const { StringDecoder } = require("node:string_decoder") as typeof import("node:string_decoder")
  const decoder = new StringDecoder(encoding)
  let pending = ""
  return new Transform({
    readableObjectMode: true,
    transform(chunk: Buffer, _enc, cb) {
      pending += decoder.write(chunk)
      const parts = pending.split("\n")
      pending = parts.pop() ?? ""
      for (const part of parts) this.push(part.endsWith("\r") ? part.slice(0, -1) : part)
      cb()
    },
    flush(cb) {
      pending += decoder.end()
      if (pending.length > 0) this.push(pending.endsWith("\r") ? pending.slice(0, -1) : pending)
      cb()
    },
  })
}

export interface RunImportArgs {
  record: ConnectionRecord
  config: ImportConfig
  /** The raw file bytes (request body). */
  body: Readable
  signal: AbortSignal
  onProgress: (p: ImportProgress) => void
}

/**
 * Stream a CSV or JSONL file into a freshly-created table in a single transaction:
 * `BEGIN → CREATE TABLE → COPY (validated rows) → COMMIT`, on a dedicated
 * client. Each row is coerced in Node first, so COPY only sees well-formed
 * data and never aborts mid-stream; rows that don't fit are quarantined and
 * reported. Any failure (or client abort) rolls back — no orphan table.
 */
export async function runImport({ record, config, body, signal, onProgress }: RunImportArgs): Promise<ImportRunResult> {
  const cols = includedColumns(config.columns)
  if (cols.length === 0) throw new Error("no columns selected")
  const start = Date.now()

  const endpoint = await resolveEndpoint(record)
  const client = new Client(buildClientConfig(endpoint, { statementTimeout: 0, applicationName: "rvbbit-lens-import" }))
  await client.connect()

  let rowsRead = 0
  let rowsLoaded = 0
  let rowsRejected = 0
  let bytesRead = 0
  let recordIndex = 0
  let lastProgressAt = 0
  const rejects: RejectRow[] = []

  const emit = (force = false) => {
    const now = Date.now()
    if (force || now - lastProgressAt >= PROGRESS_EVERY_MS) {
      lastProgressAt = now
      onProgress({ bytesRead, rowsRead, rowsLoaded, rowsRejected })
    }
  }

  // Count raw bytes as they flow into the parser (matches file.size for %).
  const counter = new Transform({
    transform(chunk: Buffer, _enc, cb) {
      bytesRead += chunk.length
      cb(null, chunk)
    },
  })
  const jsonl = config.format === "jsonl"
  // The CSV parser yields string[] records; the JSONL splitter yields raw lines.
  const parser: Transform = jsonl
    ? lineSplitter(nodeEncoding(config.dialect.encoding))
    : parse({
        delimiter: config.dialect.delimiter,
        quote: config.dialect.quote,
        relax_column_count: true,
        relax_quotes: true,
        skip_empty_lines: true,
        bom: true,
        encoding: nodeEncoding(config.dialect.encoding),
      })

  const onAbort = () => body.destroy(new Error("import aborted by client"))
  signal.addEventListener("abort", onAbort)

  let copyStream: ReturnType<typeof copyFrom> | null = null
  try {
    await client.query("BEGIN")
    await client.query(buildCreateTableSql(config))

    const copySql = `COPY ${qualifiedName(config.schema, config.table)} ${buildCopyColumnList(
      config.columns,
    )} FROM STDIN WITH (FORMAT csv, NULL '')`
    copyStream = client.query(copyFrom(copySql))
    // COPY can reject a row *asynchronously* — a value Node accepted but Postgres'
    // input function rejects (float8 overflow, NUL byte, year-zero date). Without a
    // persistent 'error' listener that surfaces as an uncaughtException that kills
    // the process before ROLLBACK runs. Route it into the async iterator below by
    // destroying the parser with the same error so the `for await` rejects.
    copyStream.on("error", (e: Error) => { parser.destroy(e) })

    // pipeline (not raw pipe) propagates a body.destroy()/abort downstream so the
    // `for await` actually rejects and unwinds. A plain `pipe` neither forwards the
    // source error nor listens for it — on cancel/tab-close the iterator would hang
    // forever, stranding the import inside an open transaction holding locks.
    pipeline(body, counter, parser, () => {
      /* errors surface through the async iterator (parser is destroyed); this
         callback just absorbs the pipeline result and guarantees teardown. */
    })

    for await (const record of parser as AsyncIterable<string[] | string>) {
      if (signal.aborted) throw new Error("import aborted by client")
      recordIndex++
      if (!jsonl && config.dialect.hasHeader && recordIndex === 1) continue // skip header row
      if (jsonl && typeof record === "string" && !record.trim()) continue     // blank line
      rowsRead++

      const out: (string | null)[] = []
      let reject: string | null = null
      let sample: string
      if (jsonl) {
        const line = String(record)
        sample = line.length > 200 ? `${line.slice(0, 200)}…` : line
        const parsed = parseJsonlLine(line)
        if (!parsed.ok) {
          reject = parsed.reason
        } else {
          for (const col of cols) {
            const key = col.sourceKey ?? col.sourceName
            const r = coerceJsonValue(parsed.row[key], col)
            if ("reject" in r) {
              reject = r.reject
              break
            }
            out.push(r.value)
          }
        }
      } else {
        const cells = record as string[]
        sample = clipRow(cells)
        for (const col of cols) {
          const r = coerceCell(cells[col.sourceIndex] ?? "", col, config.dialect)
          if ("reject" in r) {
            reject = r.reject
            break
          }
          out.push(r.value)
        }
      }

      if (reject) {
        rowsRejected++
        if (rejects.length < REJECT_CAP) rejects.push({ row: rowsRead, reason: reject, sample })
        emit()
        continue
      }

      if (!copyStream.write(toCopyCsvLine(out))) await once(copyStream, "drain")
      rowsLoaded++
      emit()
    }

    copyStream.end()
    await once(copyStream, "finish")
    await client.query("COMMIT")
    emit(true)

    return {
      rowsRead,
      rowsLoaded,
      rowsRejected,
      durationMs: Date.now() - start,
      rejects,
      rejectsTruncated: rowsRejected > rejects.length,
    }
  } catch (err) {
    body.destroy()
    copyStream?.destroy()
    try {
      await client.query("ROLLBACK")
    } catch {
      /* connection may already be unusable */
    }
    throw err
  } finally {
    signal.removeEventListener("abort", onAbort)
    await client.end().catch(() => {})
  }
}
