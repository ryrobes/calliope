/**
 * Friendly display formatting for Postgres values returned by node-postgres.
 */

export interface IntervalDisplayPart {
  amount: number
  value: string
  shortUnit: string
  singularUnit: string
  pluralUnit: string
}

const INTERVAL_FIELDS = [
  ["years", "yr", "year", "years"],
  ["months", "mo", "month", "months"],
  ["days", "d", "day", "days"],
  ["hours", "h", "hour", "hours"],
  ["minutes", "m", "minute", "minutes"],
] as const
const INTERVAL_KEYS = new Set(["years", "months", "days", "hours", "minutes", "seconds", "milliseconds"])

function finiteIntervalNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null
  if (typeof value !== "string" || value.trim() === "") return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function intervalRecord(value: unknown): Record<string, unknown> | null {
  let normalized = value
  if (typeof normalized === "string") {
    const text = normalized.trim()
    if (text.length <= 4_096 && text.startsWith("{") && text.endsWith("}")) {
      try {
        normalized = JSON.parse(text) as unknown
      } catch {
        return null
      }
    } else {
      return null
    }
  }
  if (normalized == null || typeof normalized !== "object" || Array.isArray(normalized) || normalized instanceof Date) {
    return null
  }
  return normalized as Record<string, unknown>
}

function compactIntervalNumber(value: number): string {
  const rounded = Number(value.toFixed(6))
  return String(Object.is(rounded, -0) ? 0 : rounded)
}

/**
 * node-postgres parses an interval into a PostgresInterval object. Crossing the
 * API's JSON boundary intentionally strips that object's prototype and leaves
 * `{ years, months, days, hours, minutes, seconds, milliseconds }`. Convert that
 * wire shape into a small, ordered set of display tokens without guessing that
 * arbitrary object-valued (JSON/jsonb) columns are intervals.
 */
export function intervalDisplayParts(value: unknown): IntervalDisplayPart[] | null {
  const record = intervalRecord(value)
  if (!record) return null

  const keys = Object.keys(record)
  if (keys.length > 0 && !keys.some((key) => INTERVAL_KEYS.has(key))) return null

  const parts: IntervalDisplayPart[] = []
  for (const [field, shortUnit, singularUnit, pluralUnit] of INTERVAL_FIELDS) {
    const amount = finiteIntervalNumber(record[field])
    if (amount == null || amount === 0) continue
    parts.push({ amount, value: compactIntervalNumber(amount), shortUnit, singularUnit, pluralUnit })
  }

  const seconds = finiteIntervalNumber(record.seconds) ?? 0
  const milliseconds = finiteIntervalNumber(record.milliseconds) ?? 0
  if (seconds !== 0 || milliseconds !== 0) {
    if (seconds === 0 && Math.abs(milliseconds) < 1_000) {
      parts.push({
        amount: milliseconds,
        value: compactIntervalNumber(milliseconds),
        shortUnit: "ms",
        singularUnit: "millisecond",
        pluralUnit: "milliseconds",
      })
    } else {
      const amount = seconds + milliseconds / 1_000
      if (amount !== 0) {
        parts.push({
          amount,
          value: compactIntervalNumber(amount),
          shortUnit: "s",
          singularUnit: "second",
          pluralUnit: "seconds",
        })
      }
    }
  }

  // postgres-interval represents an exact zero as an empty object.
  if (parts.length === 0) {
    parts.push({ amount: 0, value: "0", shortUnit: "s", singularUnit: "second", pluralUnit: "seconds" })
  }
  return parts
}

export function formatIntervalValue(value: unknown, verbose = false): string {
  const parts = intervalDisplayParts(value)
  if (!parts) return formatCellValue(value)
  return parts
    .map((part) => {
      if (!verbose) return `${part.value}${part.shortUnit}`
      const unit = Math.abs(part.amount) === 1 ? part.singularUnit : part.pluralUnit
      return `${part.value} ${unit}`
    })
    .join(" ")
}

/** PostgreSQL compares months as 30 days when ordering intervals. */
export function intervalComparableValue(value: unknown): number | null {
  const record = intervalRecord(value)
  if (!record) return null
  const years = finiteIntervalNumber(record.years) ?? 0
  const months = finiteIntervalNumber(record.months) ?? 0
  const days = finiteIntervalNumber(record.days) ?? 0
  const hours = finiteIntervalNumber(record.hours) ?? 0
  const minutes = finiteIntervalNumber(record.minutes) ?? 0
  const seconds = finiteIntervalNumber(record.seconds) ?? 0
  const milliseconds = finiteIntervalNumber(record.milliseconds) ?? 0
  return (((((years * 12 + months) * 30 + days) * 24 + hours) * 60 + minutes) * 60 + seconds) * 1_000 + milliseconds
}

export function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return ""
  if (typeof value === "boolean") return value ? "true" : "false"
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return String(value)
    return Number.isInteger(value) ? value.toString() : value.toString()
  }
  if (typeof value === "bigint") return value.toString()
  if (value instanceof Date) return value.toISOString()
  if (Array.isArray(value)) return JSON.stringify(value)
  if (typeof value === "object") {
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

export function formatCellForCsv(value: unknown): string {
  const s = formatCellValue(value)
  if (s.includes(",") || s.includes("\n") || s.includes('"')) {
    return `"${s.replace(/"/g, '""')}"`
  }
  return s
}

export function rowsToCsv(columns: { name: string }[], rows: Record<string, unknown>[]): string {
  const header = columns.map((c) => formatCellForCsv(c.name)).join(",")
  const body = rows.map((r) => columns.map((c) => formatCellForCsv(r[c.name])).join(",")).join("\n")
  return header + "\n" + body
}
