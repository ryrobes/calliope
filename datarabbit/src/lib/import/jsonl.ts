/**
 * JSONL (newline-delimited JSON) import: inspect a head sample and coerce
 * one parsed line into COPY-ready values. Pure (no server-only / db deps) so
 * it runs on either side and can be exercised without a database.
 *
 * Shape rules, kept deliberately simple for a first pass:
 *  - one JSON object per line; blank lines are skipped; a line that is not an
 *    object (array, scalar, broken JSON) is a quarantined row, never a crash;
 *  - columns are the top-level keys in order of first appearance;
 *  - a key whose values are objects or arrays becomes a `jsonb` column;
 *    scalars infer like CSV cells (boolean / integer / bigint / numeric /
 *    double precision / date / timestamptz / text), and a key that mixes
 *    scalars with objects is `jsonb` too;
 *  - missing keys are NULL; keys not in the column list are ignored.
 * Numbers beyond 2^53 lose precision in JSON.parse — a bigint column fed such
 * values is flagged in the inspect warnings.
 */

import type { CsvDialect, ImportColumn, InspectResult, PgType } from "./types"
import { detectEncoding, decodeSample } from "./csv-dialect"
import { inferColumn, sanitizeIdent, boolToken, INT_RE, FLOAT_RE, fitsInt4, fitsInt8 } from "./csv-infer"
import { normalizeToIso } from "./date-formats"

const PREVIEW_ROWS = 100
const SAMPLE_CELLS = 8
const MAX_SAFE = 2 ** 53

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue }

export type ParsedLine =
  | { ok: true; row: Record<string, JsonValue> }
  | { ok: false; reason: string }

/** Parse one line; only a JSON object counts as a row. */
export function parseJsonlLine(line: string): ParsedLine {
  const text = line.replace(/^﻿/, "").trim()
  if (!text) return { ok: false, reason: "blank line" }
  let value: unknown
  try {
    value = JSON.parse(text)
  } catch (e) {
    return { ok: false, reason: `invalid JSON (${e instanceof Error ? e.message.slice(0, 60) : "parse error"})` }
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return { ok: false, reason: Array.isArray(value) ? "line is a JSON array, not an object" : "line is not a JSON object" }
  }
  return { ok: true, row: value as Record<string, JsonValue> }
}

/** Display form of a JSON value for previews and sample cells. */
export function displayJson(v: JsonValue | undefined): string {
  if (v === undefined || v === null) return ""
  if (typeof v === "string") return v
  return JSON.stringify(v)
}

function scalarKind(v: JsonValue): "null" | "boolean" | "number" | "string" | "object" {
  if (v === null) return "null"
  if (typeof v === "boolean") return "boolean"
  if (typeof v === "number") return "number"
  if (typeof v === "string") return "string"
  return "object"
}

/** Infer a column type from the JSON values seen for one key. */
export function inferJsonColumn(values: JsonValue[]): { type: PgType; dateFormat?: ImportColumn["dateFormat"]; precisionRisk: boolean } {
  const present = values.filter((v) => v !== null && v !== undefined)
  if (present.length === 0) return { type: "text", precisionRisk: false }
  const kinds = new Set(present.map(scalarKind))
  if (kinds.has("object")) return { type: "jsonb", precisionRisk: false }
  if (kinds.size > 1) {
    // mixed scalars: numbers and strings side by side read best as text
    return { type: "text", precisionRisk: false }
  }
  const kind = [...kinds][0]
  if (kind === "boolean") return { type: "boolean", precisionRisk: false }
  if (kind === "number") {
    const nums = present as number[]
    const precisionRisk = nums.some((n) => Math.abs(n) >= MAX_SAFE)
    if (nums.every((n) => Number.isInteger(n))) {
      if (nums.every((n) => n >= -2147483648 && n <= 2147483647)) return { type: "integer", precisionRisk }
      if (nums.every((n) => Math.abs(n) < MAX_SAFE)) return { type: "bigint", precisionRisk }
      return { type: "numeric", precisionRisk }
    }
    if (nums.every(Number.isFinite)) return { type: "double precision", precisionRisk }
    return { type: "text", precisionRisk }
  }
  // strings: dates and timestamps are worth typing; digits in strings stay text (JSON said string)
  const strings = present as string[]
  const inferred = inferColumn(strings)
  if (inferred.type === "date" || inferred.type === "timestamptz") {
    return { type: inferred.type, dateFormat: inferred.dateFormat, precisionRisk: false }
  }
  return { type: "text", precisionRisk: false }
}

export interface InspectJsonlInput {
  bytes: Uint8Array
  totalBytes: number
  hints?: Partial<CsvDialect>
}

/** Inspect a bounded head sample of a JSONL file. */
export function inspectJsonl({ bytes, totalBytes, hints }: InspectJsonlInput): InspectResult {
  const warnings: string[] = []
  const sampledBytes = bytes.length
  const truncated = sampledBytes < totalBytes
  const detected = detectEncoding(bytes)
  const encoding = hints?.encoding ?? detected.encoding
  if (detected.bomLength > 0) warnings.push(`Stripped a ${encoding} byte-order mark.`)
  const dialect: CsvDialect = {
    delimiter: ",",
    quote: '"',
    hasHeader: true,
    encoding,
    nullToken: "",
    trimWhitespace: false,
  }

  let text = decodeSample(bytes, encoding)
  const allLines = text.split(/\r\n|\n|\r/)
  // a truncated sample almost always ends mid-line: drop the partial tail
  const lines = truncated && allLines.length > 1 ? allLines.slice(0, -1) : allLines
  text = ""

  const rows: Record<string, JsonValue>[] = []
  let badLines = 0
  let firstBad: string | null = null
  let nonEmpty = 0
  for (const line of lines) {
    if (!line.trim()) continue
    nonEmpty++
    const parsed = parseJsonlLine(line)
    if (!parsed.ok) {
      badLines++
      if (!firstBad) firstBad = parsed.reason
      continue
    }
    rows.push(parsed.row)
  }
  if (badLines > 0) {
    warnings.push(`${badLines} sampled line${badLines === 1 ? "" : "s"} could not be read as a JSON object (${firstBad}); those rows will be quarantined.`)
  }

  // keys in order of first appearance
  const keys: string[] = []
  const seen = new Set<string>()
  for (const row of rows) {
    for (const k of Object.keys(row)) {
      if (!seen.has(k)) {
        seen.add(k)
        keys.push(k)
      }
    }
  }
  if (keys.length === 0) {
    return {
      format: "jsonl",
      dialect,
      columns: [],
      sampleRows: [],
      totalBytes,
      sampledBytes,
      estimatedRows: 0,
      warnings: [...warnings, "No JSON objects with keys found in the sample."],
    }
  }

  const usedNames = new Set<string>()
  let renamed = 0
  let precisionRisks: string[] = []
  const columns: ImportColumn[] = keys.map((key, i) => {
    const targetName = sanitizeIdent(key, usedNames)
    if (targetName !== key) renamed++
    const values = rows.map((r) => (key in r ? r[key] : null))
    const { type, dateFormat, precisionRisk } = inferJsonColumn(values)
    if (precisionRisk) precisionRisks.push(key)
    const sampleValues = values
      .filter((v) => v !== null && v !== undefined)
      .slice(0, SAMPLE_CELLS)
      .map((v) => displayJson(v))
    return {
      sourceIndex: i,
      sourceKey: key,
      sourceName: key,
      targetName,
      type,
      inferredType: type,
      dateFormat,
      nullable: true,
      include: true,
      sampleValues,
    }
  })
  if (renamed > 0) warnings.push(`${renamed} key name${renamed === 1 ? " was" : "s were"} adjusted to be SQL-safe.`)
  if (precisionRisks.length > 0) {
    warnings.push(`Numbers beyond 2^53 in ${precisionRisks.join(", ")} lose precision when parsed as JSON; consider importing them as text.`)
  }
  const sparse = keys.filter((k) => rows.filter((r) => k in r).length < rows.length * 0.5)
  if (sparse.length > 0 && rows.length >= 4) {
    warnings.push(`${sparse.length} key${sparse.length === 1 ? " appears" : "s appear"} in fewer than half the sampled rows (NULL where absent).`)
  }

  const sampleRows = rows.slice(0, PREVIEW_ROWS).map((r) => keys.map((k) => displayJson(k in r ? r[k] : null)))

  let estimatedRows: number | null
  if (!truncated) estimatedRows = rows.length
  else if (nonEmpty > 0) estimatedRows = Math.max(0, Math.round(totalBytes / (sampledBytes / nonEmpty)))
  else estimatedRows = null

  return { format: "jsonl", dialect, columns, sampleRows, totalBytes, sampledBytes, estimatedRows, warnings }
}

export type CoerceJsonResult = { value: string | null } | { reject: string }

/** Coerce one JSON value to a COPY-ready string for its column (or a rejection reason). */
export function coerceJsonValue(value: JsonValue | undefined, col: ImportColumn): CoerceJsonResult {
  if (value === undefined || value === null) {
    return col.nullable ? { value: null } : { reject: `${col.targetName}: null in a NOT NULL column` }
  }
  const kind = scalarKind(value)
  if (typeof value === "string" && value.includes(" ")) return { reject: `${col.targetName}: contains a NUL byte` }
  switch (col.type) {
    case "jsonb":
      return { value: JSON.stringify(value) }
    case "text":
      return { value: kind === "string" ? (value as string) : JSON.stringify(value) }
    case "boolean": {
      if (kind === "boolean") return { value: value ? "t" : "f" }
      if (kind === "string") {
        const b = boolToken((value as string).trim())
        if (b) return { value: b }
      }
      if (kind === "number" && (value === 0 || value === 1)) return { value: value === 1 ? "t" : "f" }
      return { reject: `${col.targetName}: not a boolean (${clip(displayJson(value))})` }
    }
    case "integer":
    case "bigint":
    case "numeric":
    case "double precision": {
      if (kind === "object") return { reject: `${col.targetName}: object where a number was expected` }
      const text = kind === "number" ? numberLiteral(value as number) : (value as string).trim()
      if (text === null) return { reject: `${col.targetName}: number out of range` }
      if (col.type === "integer") {
        return INT_RE.test(text) && fitsInt4(text) ? { value: text } : { reject: `${col.targetName}: not a 32-bit integer (${clip(text)})` }
      }
      if (col.type === "bigint") {
        return INT_RE.test(text) && fitsInt8(text) ? { value: text } : { reject: `${col.targetName}: not a 64-bit integer (${clip(text)})` }
      }
      if (!(INT_RE.test(text) || FLOAT_RE.test(text))) return { reject: `${col.targetName}: not numeric (${clip(text)})` }
      if (col.type === "double precision" && !Number.isFinite(Number(text))) return { reject: `${col.targetName}: number out of range (${clip(text)})` }
      return { value: text }
    }
    case "date":
    case "timestamptz": {
      if (kind !== "string") return { reject: `${col.targetName}: ${col.type} must be a string (${clip(displayJson(value))})` }
      const layout = col.dateFormat?.layout ?? "iso"
      const iso = normalizeToIso((value as string).trim(), { layout, hasTime: col.type === "timestamptz" })
      return iso ? { value: iso } : { reject: `${col.targetName}: not a valid ${col.type} as ${layout} (${clip(value as string)})` }
    }
    default:
      return { value: displayJson(value) }
  }
}

/** A JS number as a Postgres literal: integers without exponent, no NaN/Infinity. */
function numberLiteral(n: number): string | null {
  if (!Number.isFinite(n)) return null
  if (Number.isInteger(n) && Math.abs(n) < 1e21) return n.toLocaleString("en-US", { useGrouping: false, maximumFractionDigits: 0 })
  return String(n)
}

function clip(s: string): string {
  return s.length > 40 ? `"${s.slice(0, 40)}…"` : `"${s}"`
}
