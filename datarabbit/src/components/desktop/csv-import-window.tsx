"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { AlertTriangle, FileCsv, RefreshCw, Upload } from "@/lib/icons"
import { cn } from "@/lib/utils"
import type { CsvImportPayload } from "@/lib/desktop/types"
import type { SchemaSnapshot } from "@/lib/db/types"
import type {
  CsvDialect,
  CsvEncoding,
  DateLayout,
  ImportColumn,
  ImportConfig,
  ImportProgress,
  ImportRunResult,
  ImportSessionStatus,
  InspectResult,
  PgType,
} from "@/lib/import/types"
import { dropImportFile, peekImportFile } from "@/lib/import/file-store"
import { PG_TYPE_OPTIONS, identNeedsQuote, sanitizeIdent } from "@/lib/import/csv-infer"
import { buildCreateTableSql, includedColumns } from "@/lib/import/ddl"
import { DATE_LAYOUTS, dateLayoutLabel, normalizeToIso } from "@/lib/import/date-formats"

/** A date/timestamptz column's effective format — `hasTime` is derived from
 *  the (possibly user-changed) target type, so flipping date↔timestamptz works
 *  without mutating the stored format. Null for non-date columns. */
function effectiveDateFormat(c: ImportColumn) {
  if ((c.type !== "date" && c.type !== "timestamptz") || !c.dateFormat) return null
  return { layout: c.dateFormat.layout, hasTime: c.type === "timestamptz" }
}

interface CsvImportWindowProps {
  windowId: string
  payload: CsvImportPayload
  activeConnectionId: string | null
  schema: SchemaSnapshot | null
  onReloadSchema: () => void
  onOpenTable: (schema: string, name: string) => void
}

/** Head slice we read + send to the inspector. Bounded so a 2GB drop only
 *  ever touches this much in the browser. 2MB is enough rows for solid
 *  inference even on very wide files (e.g. ~8KB/row → ~250 rows). */
const SAMPLE_BYTES = 2 * 1024 * 1024
const RAW_LINES = 60

type Tab = "columns" | "preview" | "sql" | "raw"
type Status = "reading" | "inspecting" | "ready" | "error"
type RunPhase = "config" | "preparing" | "uploading" | "importing" | "done" | "failed"

/** One slice of the file per request. Small enough that a retry is cheap and
 *  no single request outlives any proxy or server timeout on a slow uplink;
 *  large enough that a multi-GB file is a few hundred requests, not thousands. */
const CHUNK_BYTES = 16 * 1024 * 1024
const CHUNK_ATTEMPTS = 6
const STATUS_POLL_MS = 1000

/** The import this browser is running, so a reload re-attaches (or resumes the upload). */
const ACTIVE_IMPORT_KEY = "datarabbit.import.active"
interface ActiveImport {
  id: string
  name: string
  size: number
  schema: string
  table: string
  startedAt: number
}
function readActiveImport(): ActiveImport | null {
  try {
    const raw = localStorage.getItem(ACTIVE_IMPORT_KEY)
    return raw ? (JSON.parse(raw) as ActiveImport) : null
  } catch {
    return null
  }
}
function writeActiveImport(a: ActiveImport): void {
  try {
    localStorage.setItem(ACTIVE_IMPORT_KEY, JSON.stringify(a))
  } catch {
    /* private mode: the import still runs, it just won't re-attach */
  }
}
function clearActiveImport(): void {
  try {
    localStorage.removeItem(ACTIVE_IMPORT_KEY)
  } catch {
    /* ignore */
  }
}

async function fetchImportStatus(id: string): Promise<ImportSessionStatus | null> {
  const res = await fetch(`/api/db/import/status?id=${encodeURIComponent(id)}`, { cache: "no-store" })
  const j = (await res.json().catch(() => null)) as { ok: true; status: ImportSessionStatus } | { ok: false; error: string } | null
  return j && j.ok ? j.status : null
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms)
    signal?.addEventListener("abort", () => {
      clearTimeout(t)
      reject(new DOMException("aborted", "AbortError"))
    }, { once: true })
  })
}

/**
 * Send the slice of `file` starting at `offset`; resolve with the server's
 * new byte count (the next offset). A lost ack or a dropped connection is
 * retried; the server answers every refusal with the offset to resume from.
 */
async function sendChunk(id: string, file: File, offset: number, signal: AbortSignal): Promise<number> {
  let at = offset
  let lastError = "upload failed"
  for (let attempt = 0; attempt < CHUNK_ATTEMPTS; attempt++) {
    if (attempt > 0) await sleep(Math.min(16_000, 1000 * 2 ** (attempt - 1)), signal)
    try {
      const end = Math.min(at + CHUNK_BYTES, file.size)
      const res = await fetch(`/api/db/import/chunk?id=${encodeURIComponent(id)}&offset=${at}`, {
        method: "POST",
        body: file.slice(at, end),
        signal,
      })
      const j = (await res.json().catch(() => null)) as { ok: true; bytesReceived: number } | { ok: false; error: string; bytesReceived?: number } | null
      if (j?.ok) return j.bytesReceived
      if (res.status === 404) throw new Error(j?.error ?? "Unknown or expired import id — prepare again.")
      lastError = j?.error ?? `chunk upload failed (HTTP ${res.status})`
      if (typeof j?.bytesReceived === "number") at = j.bytesReceived // resync to what the server holds
      if (res.status === 400) throw new Error(lastError)
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") throw e
      if (e instanceof Error && /expired import id|prepare again/.test(e.message)) throw e
      lastError = e instanceof Error ? e.message : String(e)
    }
  }
  throw new Error(`${lastError} (gave up after ${CHUNK_ATTEMPTS} attempts)`)
}


const DELIMITER_OPTIONS: { value: string; label: string }[] = [
  { value: ",", label: "Comma ," },
  { value: ";", label: "Semicolon ;" },
  { value: "\t", label: "Tab ⇥" },
  { value: "|", label: "Pipe |" },
]
const ENCODING_OPTIONS: CsvEncoding[] = ["utf-8", "utf-16le", "latin1"]

/**
 * CSV importer — Phase 2: detect dialect, infer typed columns, let the user
 * rename/retype/drop columns and choose target schema + HEAP/RVBBIT, and show
 * the exact CREATE TABLE that will run. No data is written yet (Phase 3 adds
 * the streaming COPY loader behind the Import button).
 */
export function CsvImportWindow({
  windowId,
  payload,
  activeConnectionId,
  schema,
  onReloadSchema,
  onOpenTable,
}: CsvImportWindowProps) {
  const [file] = useState<File | null>(() => peekImportFile(windowId) ?? null)
  const missing = file == null
  const format = payload.format ?? "csv"
  const isJsonl = format === "jsonl"

  const [sample, setSample] = useState<Uint8Array | null>(null)
  const [status, setStatus] = useState<Status>("reading")
  const [error, setError] = useState<string | null>(null)
  const [inspect, setInspect] = useState<InspectResult | null>(null)
  const [dialect, setDialect] = useState<CsvDialect | null>(null)
  const [columns, setColumns] = useState<ImportColumn[]>([])
  const [tab, setTab] = useState<Tab>("columns")

  // Target table. The name derives from the file (e.g. "Q1 Sales.csv" →
  // "q1_sales"); the user can edit it.
  const [targetSchema, setTargetSchema] = useState(payload.defaultSchema ?? "public")
  const [tableName, setTableName] = useState(() =>
    sanitizeIdent(payload.fileName.replace(/\.[^.]+$/, ""), new Set()),
  )
  const [accessMethod, setAccessMethod] = useState<"heap" | "rvbbit">("heap")

  const schemas = schema?.schemas ?? []
  const hasRvbbit = schema?.hasRvbbit ?? false

  // ── Read the head slice once, then inspect ────────────────────────
  const doInspect = useCallback(
    async (bytes: Uint8Array, size: number, hints?: Partial<CsvDialect>) => {
      setStatus("inspecting")
      const params = new URLSearchParams({ totalBytes: String(size), format })
      if (hints) {
        for (const [k, v] of Object.entries(hints)) {
          if (v != null) params.set(k, String(v))
        }
      }
      try {
        const res = await fetch(`/api/db/import/inspect?${params.toString()}`, {
          method: "POST",
          body: bytes as BodyInit,
        })
        const json = (await res.json()) as ({ ok: true } & InspectResult) | { ok: false; error: string }
        if (!json.ok) {
          setError(json.error)
          setStatus("error")
          return
        }
        setInspect(json)
        setDialect(json.dialect)
        setColumns(json.columns)
        setStatus("ready")
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        setStatus("error")
      }
    },
    [format],
  )

  useEffect(() => {
    if (!file) return
    let cancelled = false
    file
      .slice(0, SAMPLE_BYTES)
      .arrayBuffer()
      .then((buf) => {
        if (cancelled) return
        const bytes = new Uint8Array(buf)
        setSample(bytes)
        void doInspect(bytes, file.size)
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
          setStatus("error")
        }
      })
    return () => {
      cancelled = true
    }
  }, [file, doInspect])

  useEffect(() => () => dropImportFile(windowId), [windowId])

  // ── Edits ─────────────────────────────────────────────────────────
  const changeDialect = useCallback(
    (patch: Partial<CsvDialect>) => {
      if (!dialect || !sample || !file) return
      const next = { ...dialect, ...patch }
      setDialect(next)
      void doInspect(sample, file.size, next)
    },
    [dialect, sample, file, doInspect],
  )

  const updateColumn = useCallback((index: number, patch: Partial<ImportColumn>) => {
    setColumns((cols) => cols.map((c, i) => (i === index ? { ...c, ...patch } : c)))
  }, [])

  // Changing the type keeps the date format in sync: entering date/timestamptz
  // seeds an ISO format if none was detected; leaving it drops the format.
  const changeType = useCallback((index: number, t: PgType) => {
    setColumns((cols) =>
      cols.map((c, i) => {
        if (i !== index) return c
        const isDate = t === "date" || t === "timestamptz"
        const dateFormat = !isDate
          ? undefined
          : c.dateFormat ?? { layout: "iso" as const, hasTime: t === "timestamptz" }
        return { ...c, type: t, dateFormat }
      }),
    )
  }, [])

  const changeDateLayout = useCallback((index: number, layout: DateLayout) => {
    setColumns((cols) =>
      cols.map((c, i) =>
        i === index && c.dateFormat
          ? { ...c, dateFormat: { ...c.dateFormat, layout, ambiguous: false } }
          : c,
      ),
    )
  }, [])

  const reinspect = useCallback(() => {
    if (sample && file) void doInspect(sample, file.size, dialect ?? undefined)
  }, [sample, file, dialect, doInspect])

  // ── Derived ───────────────────────────────────────────────────────
  const rawLines = useMemo(() => {
    if (!sample || !dialect) return null
    const text = decodeForRaw(sample, dialect.encoding)
    return text.split(/\r\n|\n|\r/).slice(0, RAW_LINES)
  }, [sample, dialect])

  const included = includedColumns(columns)

  const duplicateNames = useMemo(() => {
    const seen = new Map<string, number>()
    for (const c of included) seen.set(c.targetName, (seen.get(c.targetName) ?? 0) + 1)
    return new Set([...seen.entries()].filter(([, n]) => n > 1).map(([name]) => name))
  }, [included])

  const ddl = useMemo(() => {
    if (included.length === 0 || !tableName) return null
    return buildCreateTableSql({ schema: targetSchema, table: tableName, accessMethod, columns })
  }, [included.length, tableName, targetSchema, accessMethod, columns])

  // ── Run (Phase 3: create + stream-COPY) ───────────────────────────
  const [runPhase, setRunPhase] = useState<RunPhase>("config")
  const [progress, setProgress] = useState<ImportProgress | null>(null)
  const [runResult, setRunResult] = useState<ImportRunResult | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [uploaded, setUploaded] = useState(0)
  const abortRef = useRef<AbortController | null>(null)
  const followRef = useRef<{ stop: boolean } | null>(null)
  const sessionRef = useRef<string | null>(null)

  /** Poll `status` until the load finishes. Survives a reload: the mount
   *  effect below calls this for an import that was running last time. */
  const followImport = useCallback(
    async (id: string, size: number) => {
      const token = { stop: false }
      followRef.current = token
      let failures = 0
      for (;;) {
        try {
          const res = await fetch(`/api/db/import/status?id=${encodeURIComponent(id)}`, { cache: "no-store" })
          const j = (await res.json()) as { ok: true; status: ImportSessionStatus } | { ok: false; error: string }
          if (token.stop) return
          if (!j.ok) {
            setRunError(j.error)
            setRunPhase("failed")
            clearActiveImport()
            return
          }
          failures = 0
          const st = j.status
          if (st.progress) setProgress(st.progress)
          if (st.phase === "done" && st.result) {
            setRunResult(st.result)
            setProgress({ bytesRead: size || st.bytesReceived, rowsRead: st.result.rowsRead, rowsLoaded: st.result.rowsLoaded, rowsRejected: st.result.rowsRejected })
            setRunPhase("done")
            clearActiveImport()
            onReloadSchema()
            return
          }
          if (st.phase === "failed") {
            setRunError(st.error ?? "import failed")
            setRunPhase("failed")
            clearActiveImport()
            return
          }
          if (st.phase === "cancelled" || st.phase === "uploading") {
            setRunPhase("config")
            if (st.phase === "cancelled") clearActiveImport()
            return
          }
          setRunPhase("importing")
        } catch (e) {
          if (token.stop) return
          if (++failures > 30) {
            setRunError(`lost the import status: ${e instanceof Error ? e.message : String(e)}`)
            setRunPhase("failed")
            return
          }
        }
        await sleep(STATUS_POLL_MS)
        if (token.stop) return
      }
    },
    [onReloadSchema],
  )

  // Re-attach after a reload: an import that was loading (or just finished)
  // for this same file keeps showing its footer; an interrupted upload resumes
  // from the server's offset when Import is clicked again.
  useEffect(() => {
    const prior = readActiveImport()
    if (!prior || !file || prior.name !== file.name || prior.size !== file.size) return
    let gone = false
    void (async () => {
      const st = await fetchImportStatus(prior.id).catch(() => null)
      if (gone) return
      if (!st) {
        clearActiveImport()
        return
      }
      if (st.phase === "importing" || st.phase === "done" || st.phase === "failed") {
        sessionRef.current = prior.id
        setTargetSchema(st.schema)
        setTableName(st.table)
        setUploaded(prior.size)
        setRunPhase("importing")
        void followImport(prior.id, prior.size)
      } else if (st.phase === "uploading") {
        // Interrupted mid-upload: keep the target it was going to, so Import
        // resumes that session from the server's offset instead of preparing
        // a new one against a name the first attempt may already own.
        sessionRef.current = prior.id
        setTargetSchema(st.schema)
        setTableName(st.table)
        setUploaded(st.bytesReceived)
      }
    })()
    return () => {
      gone = true
      if (followRef.current) followRef.current.stop = true
    }
    // mount only: the file is fixed for the life of the window
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const importDisabledReason = !activeConnectionId
    ? "No active connection"
    : !tableName.trim()
      ? "Name the target table"
      : duplicateNames.size > 0
        ? "Resolve duplicate column names"
        : included.length === 0
          ? "Select at least one column"
          : null

  const startImport = useCallback(async () => {
    if (!file || !activeConnectionId || !dialect) return
    const config: ImportConfig = {
      format,
      connectionId: activeConnectionId,
      schema: targetSchema,
      table: tableName.trim(),
      accessMethod,
      dialect,
      columns,
    }
    setRunError(null)
    setProgress(null)
    setRunResult(null)
    setUploaded(0)
    setRunPhase("preparing")
    const ac = new AbortController()
    abortRef.current = ac
    const table = tableName.trim()
    try {
      // Resume an interrupted upload of this same file (a reload mid-upload):
      // the server still holds the bytes it got, so continue from there.
      let importId: string | null = null
      let offset = 0
      const prior = readActiveImport()
      if (prior && prior.name === file.name && prior.size === file.size && prior.schema === targetSchema && prior.table === table) {
        const st = await fetchImportStatus(prior.id).catch(() => null)
        if (st && st.phase === "uploading") {
          importId = prior.id
          offset = st.bytesReceived
        }
      }
      if (!importId) {
        const prep = await fetch("/api/db/import/prepare", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ config, bytesExpected: file.size }),
          signal: ac.signal,
        })
        const pj = (await prep.json()) as { ok: true; importId: string } | { ok: false; error: string }
        if (!pj.ok) {
          setRunError(pj.error)
          setRunPhase("failed")
          return
        }
        importId = pj.importId
      }
      sessionRef.current = importId
      writeActiveImport({ id: importId, name: file.name, size: file.size, schema: targetSchema, table, startedAt: Date.now() })

      // Upload in short requests, in order; each one refreshes the session on
      // the server and a failed one is retried from the server's offset.
      setRunPhase("uploading")
      setUploaded(offset)
      while (offset < file.size) {
        offset = await sendChunk(importId, file, offset, ac.signal)
        setUploaded(offset)
      }

      // The load runs on the server detached from this tab; follow it.
      const started = await fetch(`/api/db/import/start?id=${encodeURIComponent(importId)}`, { method: "POST", signal: ac.signal })
      const sj = (await started.json()) as { ok: true } | { ok: false; error: string }
      if (!sj.ok) {
        setRunError(sj.error)
        setRunPhase("failed")
        clearActiveImport()
        return
      }
      setRunPhase("importing")
      await followImport(importId, file.size)
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        setRunPhase("config") // user cancelled
      } else {
        setRunError(e instanceof Error ? e.message : String(e))
        setRunPhase("failed")
        clearActiveImport()
      }
    } finally {
      abortRef.current = null
    }
  }, [file, format, activeConnectionId, dialect, targetSchema, tableName, accessMethod, columns, followImport])

  const cancelImport = useCallback(() => {
    abortRef.current?.abort()
    if (followRef.current) followRef.current.stop = true
    const id = sessionRef.current ?? readActiveImport()?.id
    if (id) void fetch(`/api/db/import/cancel?id=${encodeURIComponent(id)}`, { method: "POST" }).catch(() => undefined)
    sessionRef.current = null
    clearActiveImport()
    setRunPhase("config")
    setProgress(null)
    setUploaded(0)
  }, [])
  const resetRun = useCallback(() => {
    sessionRef.current = null
    clearActiveImport()
    setRunPhase("config")
    setProgress(null)
    setRunResult(null)
    setRunError(null)
    setUploaded(0)
  }, [])

  const downloadRejects = useCallback(() => {
    if (!runResult || runResult.rejects.length === 0) return
    const esc = (s: string) => (/[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s)
    const lines = ["row,reason,sample", ...runResult.rejects.map((r) => `${r.row},${esc(r.reason)},${esc(r.sample)}`)]
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `${tableName || "import"}_rejects.csv`
    a.click()
    URL.revokeObjectURL(url)
  }, [runResult, tableName])

  // ── Render ────────────────────────────────────────────────────────
  if (missing) {
    return (
      <div className="flex h-full flex-col text-[12px] text-chrome-text">
        <Header payload={payload} estimatedRows={null} onReload={reinspect} canReload={false} />
        <MissingFile fileName={payload.fileName} />
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col text-[12px] text-chrome-text">
      <Header
        payload={payload}
        estimatedRows={inspect?.estimatedRows ?? null}
        onReload={reinspect}
        canReload={status === "ready" || status === "error"}
      />

      {!activeConnectionId ? (
        <Banner tone="warning" icon>
          No active connection — pick one to choose a target schema and import.
        </Banner>
      ) : null}
      {error ? (
        <Banner tone="warning" icon onClose={() => setError(null)}>
          {error}
        </Banner>
      ) : null}

      {status === "reading" || status === "inspecting" ? (
        <div className="grid flex-1 place-items-center text-[11px] text-chrome-text/55">
          {status === "reading" ? "reading…" : "inspecting…"}
        </div>
      ) : status === "error" ? (
        <div className="grid flex-1 place-items-center px-6 text-center text-[11px] text-chrome-text/55">
          Couldn&apos;t inspect this file. Try adjusting the format, or check the Raw view.
        </div>
      ) : (
        <>
          <TargetBar
            schemas={schemas}
            targetSchema={targetSchema}
            onSchema={setTargetSchema}
            tableName={tableName}
            onTable={setTableName}
            accessMethod={accessMethod}
            onAccessMethod={setAccessMethod}
            hasRvbbit={hasRvbbit}
          />
          {dialect ? <FormatBar dialect={dialect} onChange={changeDialect} jsonl={isJsonl} /> : null}

          <Tabs tab={tab} onTab={setTab} />

          <div className="min-h-0 flex-1 overflow-auto">
            {tab === "columns" ? (
              <ColumnsTab
                columns={columns}
                duplicateNames={duplicateNames}
                onUpdate={updateColumn}
                onChangeType={changeType}
                onChangeLayout={changeDateLayout}
              />
            ) : tab === "preview" ? (
              <PreviewTab columns={included} rows={inspect?.sampleRows ?? []} />
            ) : tab === "sql" ? (
              <SqlTab ddl={ddl} />
            ) : (
              <RawTab lines={rawLines} />
            )}
          </div>

          <RunBar
            phase={runPhase}
            progress={progress}
            result={runResult}
            error={runError}
            uploaded={uploaded}
            fileSize={file?.size ?? 0}
            estimatedRows={inspect?.estimatedRows ?? null}
            includedCount={included.length}
            duplicateCount={duplicateNames.size}
            warning={inspect?.warnings[0]}
            warningCount={inspect?.warnings.length ?? 0}
            disabledReason={importDisabledReason}
            schema={targetSchema}
            table={tableName}
            onImport={startImport}
            onCancel={cancelImport}
            onOpenTable={() => onOpenTable(targetSchema, tableName.trim())}
            onDownloadRejects={downloadRejects}
            onReset={resetRun}
          />
        </>
      )}
    </div>
  )
}

// ── Header ────────────────────────────────────────────────────────────

function Header({
  payload,
  estimatedRows,
  onReload,
  canReload,
}: {
  payload: CsvImportPayload
  estimatedRows: number | null
  onReload: () => void
  canReload: boolean
}) {
  return (
    <div className="flex flex-wrap items-center gap-2.5 border-b border-chrome-border bg-chrome-bg/40 px-3 py-1.5">
      <span className="inline-flex items-center gap-1.5 text-foreground">
        <FileCsv className="h-3.5 w-3.5 text-rvbbit-accent" />
        <span className="max-w-[240px] truncate font-medium" title={payload.fileName}>
          {payload.fileName}
        </span>
      </span>
      <span className="text-chrome-text/40">·</span>
      <span className="font-mono tabular-nums text-chrome-text/75">{fmtBytes(payload.fileSize)}</span>
      {estimatedRows != null ? (
        <>
          <span className="text-chrome-text/40">·</span>
          <span className="text-chrome-text/65">
            <span className="font-mono tabular-nums text-foreground">~{fmtCount(estimatedRows)}</span> rows
          </span>
        </>
      ) : null}
      <div className="ml-auto flex items-center gap-2">
        {canReload ? (
          <button
            type="button"
            onClick={onReload}
            title="Re-inspect"
            className="grid h-6 w-6 place-items-center rounded text-chrome-text/70 hover:bg-foreground/[0.08] hover:text-foreground"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
    </div>
  )
}

// ── Target bar ──────────────────────────────────────────────────────

function TargetBar({
  schemas,
  targetSchema,
  onSchema,
  tableName,
  onTable,
  accessMethod,
  onAccessMethod,
  hasRvbbit,
}: {
  schemas: string[]
  targetSchema: string
  onSchema: (s: string) => void
  tableName: string
  onTable: (s: string) => void
  accessMethod: "heap" | "rvbbit"
  onAccessMethod: (m: "heap" | "rvbbit") => void
  hasRvbbit: boolean
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-chrome-border/60 bg-chrome-bg/20 px-3 py-1.5">
      <label className="inline-flex items-center gap-1.5 text-[11px] text-chrome-text/65">
        <span className="uppercase tracking-wider text-chrome-text/45">schema</span>
        <select
          value={targetSchema}
          onChange={(e) => onSchema(e.target.value)}
          className="h-6 rounded border border-chrome-border bg-secondary-background px-1.5 text-[11px] text-foreground outline-none focus:ring-2 focus:ring-ring"
        >
          {schemas.length === 0 ? <option value={targetSchema}>{targetSchema}</option> : null}
          {schemas.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <span className="text-chrome-text/30">.</span>
      <input
        value={tableName}
        onChange={(e) => onTable(e.target.value)}
        placeholder="table_name"
        spellCheck={false}
        className="h-6 w-44 rounded border border-chrome-border bg-secondary-background px-2 font-mono text-[11px] text-foreground outline-none placeholder:text-chrome-text/40 focus:ring-2 focus:ring-ring"
      />
      <div className="ml-auto inline-flex overflow-hidden rounded border border-chrome-border">
        <SegBtn active={accessMethod === "heap"} onClick={() => onAccessMethod("heap")}>
          HEAP
        </SegBtn>
        <SegBtn
          active={accessMethod === "rvbbit"}
          disabled={!hasRvbbit}
          title={hasRvbbit ? "Create and register for rvbbit acceleration" : "rvbbit extension not installed on this connection"}
          onClick={() => hasRvbbit && onAccessMethod("rvbbit")}
        >
          RVBBIT
        </SegBtn>
      </div>
    </div>
  )
}

function SegBtn({
  active,
  disabled,
  onClick,
  title,
  children,
}: {
  active: boolean
  disabled?: boolean
  onClick: () => void
  title?: string
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      title={title}
      className={cn(
        "px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider transition disabled:cursor-not-allowed disabled:opacity-35",
        active
          ? "bg-rvbbit-accent/20 text-rvbbit-accent"
          : "bg-secondary-background text-chrome-text/65 hover:bg-foreground/[0.06] hover:text-foreground",
      )}
    >
      {children}
    </button>
  )
}

// ── Format bar ──────────────────────────────────────────────────────

function FormatBar({
  dialect,
  onChange,
  jsonl = false,
}: {
  dialect: CsvDialect
  onChange: (patch: Partial<CsvDialect>) => void
  jsonl?: boolean
}) {
  if (jsonl) {
    return (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-chrome-border/40 bg-chrome-bg/10 px-3 py-1 text-[10px] text-chrome-text/60">
        <span className="uppercase tracking-wider text-chrome-text/40">format</span>
        <span className="rounded border border-chrome-border px-1.5 py-0.5 font-mono text-[10px] text-foreground">JSONL</span>
        <span className="text-chrome-text/45">one JSON object per line · keys become columns · nested values become jsonb</span>
        <MiniSelect
          label="enc"
          value={dialect.encoding}
          options={ENCODING_OPTIONS.map((e) => ({ value: e, label: e }))}
          onChange={(v) => onChange({ encoding: v as CsvEncoding })}
        />
      </div>
    )
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-chrome-border/40 bg-chrome-bg/10 px-3 py-1 text-[10px] text-chrome-text/60">
      <span className="uppercase tracking-wider text-chrome-text/40">format</span>
      <MiniSelect
        label="delim"
        value={dialect.delimiter}
        options={DELIMITER_OPTIONS}
        onChange={(v) => onChange({ delimiter: v })}
      />
      <MiniSelect
        label="enc"
        value={dialect.encoding}
        options={ENCODING_OPTIONS.map((e) => ({ value: e, label: e }))}
        onChange={(v) => onChange({ encoding: v as CsvEncoding })}
      />
      <Check checked={dialect.hasHeader} onChange={(b) => onChange({ hasHeader: b })}>
        header row
      </Check>
      <Check checked={dialect.trimWhitespace} onChange={(b) => onChange({ trimWhitespace: b })}>
        trim
      </Check>
      <label className="inline-flex items-center gap-1">
        <span className="uppercase tracking-wider text-chrome-text/40">null</span>
        <input
          value={dialect.nullToken}
          onChange={(e) => onChange({ nullToken: e.target.value })}
          placeholder="(empty)"
          spellCheck={false}
          className="h-5 w-16 rounded border border-chrome-border bg-secondary-background px-1.5 font-mono text-[10px] text-foreground outline-none placeholder:text-chrome-text/35 focus:ring-1 focus:ring-ring"
        />
      </label>
    </div>
  )
}

function MiniSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (v: string) => void
}) {
  return (
    <label className="inline-flex items-center gap-1">
      <span className="uppercase tracking-wider text-chrome-text/40">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-5 rounded border border-chrome-border bg-secondary-background px-1 text-[10px] text-foreground outline-none focus:ring-1 focus:ring-ring"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function Check({
  checked,
  onChange,
  children,
}: {
  checked: boolean
  onChange: (b: boolean) => void
  children: React.ReactNode
}) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-1">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="accent-rvbbit-accent" />
      <span>{children}</span>
    </label>
  )
}

// ── Tabs ────────────────────────────────────────────────────────────

function Tabs({ tab, onTab }: { tab: Tab; onTab: (t: Tab) => void }) {
  const items: { key: Tab; label: string }[] = [
    { key: "columns", label: "Columns" },
    { key: "preview", label: "Preview" },
    { key: "sql", label: "SQL" },
    { key: "raw", label: "Raw" },
  ]
  return (
    <div className="flex items-center gap-px border-b border-chrome-border bg-chrome-bg/20 px-2">
      {items.map((it) => (
        <button
          key={it.key}
          type="button"
          onClick={() => onTab(it.key)}
          className={cn(
            "border-b-2 px-3 py-1.5 text-[11px] font-medium uppercase tracking-wider transition",
            tab === it.key
              ? "border-rvbbit-accent text-rvbbit-accent"
              : "border-transparent text-chrome-text/65 hover:text-foreground",
          )}
        >
          {it.label}
        </button>
      ))}
    </div>
  )
}

// ── Columns tab ─────────────────────────────────────────────────────

function ColumnsTab({
  columns,
  duplicateNames,
  onUpdate,
  onChangeType,
  onChangeLayout,
}: {
  columns: ImportColumn[]
  duplicateNames: Set<string>
  onUpdate: (index: number, patch: Partial<ImportColumn>) => void
  onChangeType: (index: number, type: PgType) => void
  onChangeLayout: (index: number, layout: DateLayout) => void
}) {
  return (
    <div className="divide-y divide-chrome-border/30">
      {columns.map((c, i) => {
        const dup = c.include && duplicateNames.has(c.targetName)
        const quoted = c.include && identNeedsQuote(c.targetName)
        const isDate = c.type === "date" || c.type === "timestamptz"
        return (
          <div
            key={c.sourceIndex}
            className={cn("flex items-center gap-2 px-3 py-1.5", !c.include && "opacity-45")}
          >
            <input
              type="checkbox"
              checked={c.include}
              onChange={(e) => onUpdate(i, { include: e.target.checked })}
              title={c.include ? "Exclude column" : "Include column"}
              className="accent-rvbbit-accent"
            />
            <span className="w-28 shrink-0 truncate text-[10px] text-chrome-text/45" title={c.sourceName}>
              {c.sourceName}
            </span>
            <span className="text-chrome-text/30">→</span>
            <input
              value={c.targetName}
              disabled={!c.include}
              onChange={(e) => onUpdate(i, { targetName: e.target.value })}
              spellCheck={false}
              className={cn(
                "h-6 w-36 rounded border bg-secondary-background px-2 font-mono text-[11px] text-foreground outline-none focus:ring-2 focus:ring-ring",
                dup ? "border-danger/70" : quoted ? "border-warning/60" : "border-chrome-border",
              )}
              title={
                dup
                  ? "Duplicate column name"
                  : quoted
                    ? "Will be quoted in SQL (not a bare identifier)"
                    : undefined
              }
            />
            <select
              value={c.type}
              disabled={!c.include}
              onChange={(e) => onChangeType(i, e.target.value as PgType)}
              className={cn(
                "h-6 rounded border border-chrome-border bg-secondary-background px-1.5 font-mono text-[10px] outline-none focus:ring-2 focus:ring-ring",
                c.type === c.inferredType ? "text-foreground" : "text-rvbbit-accent",
              )}
              title={c.type === c.inferredType ? "Inferred type" : `Overridden (inferred: ${c.inferredType})`}
            >
              {PG_TYPE_OPTIONS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            {isDate && c.dateFormat ? (
              <select
                value={c.dateFormat.layout}
                disabled={!c.include}
                onChange={(e) => onChangeLayout(i, e.target.value as DateLayout)}
                className={cn(
                  "h-6 rounded border bg-secondary-background px-1 text-[10px] outline-none focus:ring-2 focus:ring-ring",
                  c.dateFormat.ambiguous ? "border-warning/70 text-warning" : "border-chrome-border text-chrome-text/70",
                )}
                title={
                  c.dateFormat.ambiguous
                    ? "Ambiguous M/D vs D/M order — every sampled day was ≤ 12. Verify this is right."
                    : "Source date format (normalized to ISO on import)"
                }
              >
                {DATE_LAYOUTS.map((l) => (
                  <option key={l} value={l}>
                    {dateLayoutLabel(l)}
                  </option>
                ))}
              </select>
            ) : null}
            <Check checked={c.nullable} onChange={(b) => onUpdate(i, { nullable: b })}>
              <span className="text-[10px] text-chrome-text/55">null</span>
            </Check>
            <span className="ml-auto max-w-[150px] truncate text-[10px] text-chrome-text/35" title={c.sampleValues.join(" · ")}>
              {c.sampleValues.slice(0, 3).join(" · ")}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ── Preview tab ─────────────────────────────────────────────────────

function PreviewTab({ columns, rows }: { columns: ImportColumn[]; rows: string[][] }) {
  if (columns.length === 0) return <EmptyHint label="No columns selected." />
  if (rows.length === 0) return <EmptyHint label="No sample rows." />
  return (
    <table className="w-full border-collapse text-[11px]">
      <thead className="sticky top-0 bg-chrome-bg/95 text-[9px] uppercase tracking-wider text-chrome-text/50 backdrop-blur">
        <tr>
          {columns.map((c) => (
            <th key={c.sourceIndex} className="border-b border-chrome-border px-2 py-1 text-left font-medium">
              <div className="font-mono normal-case tracking-normal text-foreground">{c.targetName}</div>
              <div className="font-mono lowercase text-chrome-text/40">{c.type}</div>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, ri) => (
          <tr key={ri} className="border-b border-chrome-border/20 hover:bg-foreground/[0.03]">
            {columns.map((c) => (
              <PreviewCell key={c.sourceIndex} column={c} raw={r[c.sourceIndex]} />
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** One preview cell. Date/timestamp columns show the *normalized* ISO value
 *  (what actually gets stored), accent-tinted; a value that won't parse under
 *  the chosen format is shown raw + danger-tinted (it'd be quarantined). */
function PreviewCell({ column, raw }: { column: ImportColumn; raw: string | undefined }) {
  if (raw === "" || raw == null) {
    return <td className="px-2 py-1 font-mono text-chrome-text/30">∅</td>
  }
  const fmt = effectiveDateFormat(column)
  if (fmt && fmt.layout !== "iso") {
    const iso = normalizeToIso(raw, fmt)
    if (iso) {
      return (
        <td className="max-w-[220px] truncate px-2 py-1 font-mono text-rvbbit-accent" title={`${raw} → ${iso}`}>
          {iso}
        </td>
      )
    }
    return (
      <td className="max-w-[220px] truncate px-2 py-1 font-mono text-danger" title={`Won't parse as ${fmt.layout} — this row would be quarantined`}>
        {raw}
      </td>
    )
  }
  return (
    <td className="max-w-[220px] truncate px-2 py-1 font-mono text-foreground/85" title={raw}>
      {raw}
    </td>
  )
}

// ── SQL tab ─────────────────────────────────────────────────────────

function SqlTab({ ddl }: { ddl: string | null }) {
  if (!ddl) return <EmptyHint label="Set a table name and keep at least one column." />
  return (
    <pre className="whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed text-foreground/90">
      {ddl}
    </pre>
  )
}

// ── Raw tab ─────────────────────────────────────────────────────────

function RawTab({ lines }: { lines: string[] | null }) {
  if (!lines) return <EmptyHint label="No sample." />
  return (
    <pre className="px-3 py-2 font-mono text-[11px] leading-relaxed text-foreground/90">
      {lines.map((line, i) => (
        <div key={i} className="flex gap-3 whitespace-pre">
          <span className="select-none text-right text-chrome-text/30" style={{ minWidth: "2.5ch" }}>
            {i + 1}
          </span>
          <span className="break-all">{line || " "}</span>
        </div>
      ))}
    </pre>
  )
}

// ── Footer + shared ─────────────────────────────────────────────────

function RunBar({
  phase,
  progress,
  result,
  error,
  uploaded,
  fileSize,
  estimatedRows,
  includedCount,
  duplicateCount,
  warning,
  warningCount,
  disabledReason,
  schema,
  table,
  onImport,
  onCancel,
  onOpenTable,
  onDownloadRejects,
  onReset,
}: {
  phase: RunPhase
  progress: ImportProgress | null
  result: ImportRunResult | null
  error: string | null
  uploaded: number
  fileSize: number
  estimatedRows: number | null
  includedCount: number
  duplicateCount: number
  warning: string | undefined
  warningCount: number
  disabledReason: string | null
  schema: string
  table: string
  onImport: () => void
  onCancel: () => void
  onOpenTable: () => void
  onDownloadRejects: () => void
  onReset: () => void
}) {
  const base = "flex items-center gap-2 border-t border-chrome-border bg-chrome-bg/40 px-3 py-1.5 text-[11px]"

  if (phase === "importing" || phase === "preparing" || phase === "uploading") {
    // Two bars in one: the upload (bytes this browser has sent) and then the
    // load (bytes the server has read into the table). The upload is what a
    // slow uplink spends its time on; it used to sit at 0% until it finished.
    const uploading = phase === "uploading"
    const pct =
      fileSize > 0
        ? uploading
          ? Math.min(100, Math.round((uploaded / fileSize) * 100))
          : progress
            ? Math.min(100, Math.round((progress.bytesRead / fileSize) * 100))
            : 0
        : 0
    return (
      <div className={cn(base, "text-chrome-text/70")}>
        <div className="relative h-1.5 w-28 overflow-hidden rounded-full bg-foreground/[0.08]">
          <div
            className={cn("absolute inset-y-0 left-0 rounded-full transition-[width]", uploading ? "bg-rvbbit-accent/55" : "bg-rvbbit-accent")}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="font-mono tabular-nums text-foreground">{pct}%</span>
        {phase === "preparing" ? (
          <span>preparing…</span>
        ) : uploading ? (
          <span className="font-mono tabular-nums text-chrome-text/60">
            {fmtBytes(uploaded)} of {fmtBytes(fileSize)} uploaded
          </span>
        ) : progress ? (
          <span className="font-mono tabular-nums text-chrome-text/60">
            {fmtCount(progress.rowsLoaded)} loaded
            {progress.rowsRejected > 0 ? ` · ${fmtCount(progress.rowsRejected)} rejected` : ""}
          </span>
        ) : (
          <span>loading…</span>
        )}
        <button
          type="button"
          onClick={onCancel}
          className="ml-auto rounded border border-chrome-border px-2 py-0.5 text-[10px] text-chrome-text/70 hover:bg-foreground/[0.08] hover:text-foreground"
        >
          Cancel
        </button>
      </div>
    )
  }

  if (phase === "done" && result) {
    return (
      <div className={cn(base, "flex-wrap text-chrome-text/70")}>
        <span className="inline-flex items-center gap-1 text-success">✓ Loaded {fmtCount(result.rowsLoaded)} rows</span>
        {result.rowsRejected > 0 ? (
          <span className="text-warning">· {fmtCount(result.rowsRejected)} rejected</span>
        ) : null}
        <span className="text-chrome-text/45">· {(result.durationMs / 1000).toFixed(1)}s</span>
        <div className="ml-auto flex items-center gap-1.5">
          {result.rejects.length > 0 ? (
            <button type="button" onClick={onDownloadRejects} className="rounded border border-chrome-border px-2 py-0.5 text-[10px] text-chrome-text/70 hover:bg-foreground/[0.08] hover:text-foreground">
              Rejects{result.rejectsTruncated ? " (sample)" : ""}
            </button>
          ) : null}
          <button type="button" onClick={onReset} className="rounded border border-chrome-border px-2 py-0.5 text-[10px] text-chrome-text/70 hover:bg-foreground/[0.08] hover:text-foreground">
            Import another
          </button>
          <button type="button" onClick={onOpenTable} className="rounded bg-rvbbit-accent/15 px-2 py-0.5 text-[10px] font-medium text-rvbbit-accent hover:bg-rvbbit-accent/25">
            Open table →
          </button>
        </div>
      </div>
    )
  }

  if (phase === "failed") {
    return (
      <div className={cn(base, "text-danger")}>
        <AlertTriangle className="h-3 w-3 shrink-0" />
        <span className="truncate" title={error ?? undefined}>{error ?? "Import failed"}</span>
        <button type="button" onClick={onReset} className="ml-auto rounded border border-danger/50 px-2 py-0.5 text-[10px] text-danger hover:bg-danger/10">
          Back
        </button>
      </div>
    )
  }

  // config phase — summary + the Import button
  return (
    <div className={cn(base, "text-chrome-text/55")}>
      {duplicateCount > 0 ? (
        <span className="text-danger">
          {duplicateCount} duplicate column name{duplicateCount === 1 ? "" : "s"}
        </span>
      ) : warning ? (
        <span className="inline-flex items-center gap-1 text-chrome-text/60" title={warning}>
          <AlertTriangle className="h-3 w-3 text-warning" />
          {warning}
          {warningCount > 1 ? ` (+${warningCount - 1})` : ""}
        </span>
      ) : (
        <span className="font-mono tabular-nums">
          {includedCount} col{includedCount === 1 ? "" : "s"}
        </span>
      )}
      <div className="ml-auto flex items-center gap-2">
        <span className="font-mono tabular-nums text-chrome-text/45">
          {schema}.{table || "…"}
        </span>
        <button
          type="button"
          onClick={onImport}
          disabled={disabledReason != null}
          title={disabledReason ?? undefined}
          className="rounded bg-rvbbit-accent/15 px-2.5 py-1 text-[11px] font-medium text-rvbbit-accent hover:bg-rvbbit-accent/25 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Import{estimatedRows != null ? ` ~${fmtCount(estimatedRows)} rows` : ""} →
        </button>
      </div>
    </div>
  )
}

function Banner({
  tone,
  icon,
  onClose,
  children,
}: {
  tone: "warning"
  icon?: boolean
  onClose?: () => void
  children: React.ReactNode
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-1.5 border-b px-3 py-1.5 text-[11px]",
        tone === "warning" && "border-warning/40 bg-warning/10 text-warning",
      )}
    >
      {icon ? <AlertTriangle className="mt-px h-3 w-3 shrink-0" /> : null}
      <span className="break-words">{children}</span>
      {onClose ? (
        <button type="button" onClick={onClose} className="ml-auto text-warning/70 hover:text-warning">
          ✕
        </button>
      ) : null}
    </div>
  )
}

function EmptyHint({ label }: { label: string }) {
  return <div className="grid h-full place-items-center px-6 text-center text-[11px] text-chrome-text/45">{label}</div>
}

function MissingFile({ fileName }: { fileName: string }) {
  return (
    <div className="grid flex-1 place-items-center px-6 text-center">
      <div className="max-w-sm space-y-2">
        <Upload className="mx-auto h-8 w-8 text-chrome-text/30" />
        <div className="text-[12px] text-foreground">{fileName} is no longer in memory</div>
        <div className="text-[11px] text-chrome-text/55">
          A dropped file can&apos;t survive a page reload. Drop the file onto the desktop again to
          re-open the importer.
        </div>
      </div>
    </div>
  )
}

// ── utils ───────────────────────────────────────────────────────────

/** Decode the head sample for the Raw view (mirrors the server's decode). */
function decodeForRaw(bytes: Uint8Array, encoding: CsvEncoding): string {
  try {
    return new TextDecoder(encoding, { fatal: false, ignoreBOM: false }).decode(bytes)
  } catch {
    return new TextDecoder("utf-8", { fatal: false }).decode(bytes)
  }
}

function fmtBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—"
  if (n < 1024) return `${n} B`
  const units = ["KB", "MB", "GB", "TB"]
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`
}

function fmtCount(n: number): string {
  if (!Number.isFinite(n)) return "—"
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`
  return `${(n / 1_000_000).toFixed(1)}M`
}
