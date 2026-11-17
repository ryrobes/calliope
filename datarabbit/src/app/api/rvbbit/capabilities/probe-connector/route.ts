import { NextResponse } from "next/server"
import { executeQuery } from "@/lib/db/query"

export const runtime = "nodejs"

interface ProbeConnectorBody {
  connectionId?: string
  backendName?: string
}

function sqlLit(value: string): string {
  return `'${value.replace(/'/g, "''")}'`
}

function parseManifest(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  if (typeof value !== "string" || !value.trim()) return null
  try {
    const parsed = JSON.parse(value) as unknown
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

function healthPath(manifest: Record<string, unknown> | null): string {
  const runtimeBlock = manifest?.runtime
  if (!runtimeBlock || typeof runtimeBlock !== "object" || Array.isArray(runtimeBlock)) {
    return "/health"
  }
  const value = (runtimeBlock as Record<string, unknown>).health_path
  const path = typeof value === "string" && value.trim() ? value.trim() : "/health"
  return path.startsWith("/") ? path : `/${path}`
}

function healthEndpoint(endpoint: string, path: string): string {
  const url = new URL(endpoint)
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`unsupported connector URL scheme ${url.protocol}`)
  }
  url.pathname = path
  url.search = ""
  url.hash = ""
  return url.toString()
}

function outputShape(value: unknown): { outputType: string; outputSize: number } {
  if (value == null) return { outputType: "null", outputSize: 0 }
  if (Array.isArray(value)) return { outputType: "array", outputSize: value.length }
  if (typeof value === "object") {
    return { outputType: "object", outputSize: Object.keys(value).length }
  }
  const rendered = String(value)
  return { outputType: typeof value, outputSize: rendered.length }
}

function responseError(value: unknown, status: number): string {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const obj = value as Record<string, unknown>
    if (obj.detail != null) return String(obj.detail)
    if (obj.error != null) return String(obj.error)
  }
  return `connector health returned HTTP ${status}`
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as ProbeConnectorBody | null
  const connectionId = body?.connectionId?.trim() ?? ""
  const backendName = body?.backendName?.trim() ?? ""
  if (!connectionId || !backendName) {
    return NextResponse.json(
      { ok: false, error: "connectionId and backendName are required" },
      { status: 400 },
    )
  }
  if (backendName.length > 128 || !/^[a-zA-Z0-9_.-]+$/.test(backendName)) {
    return NextResponse.json({ ok: false, error: "invalid connector backend name" }, { status: 400 })
  }

  const started = Date.now()
  try {
    const result = await executeQuery(
      connectionId,
      `SELECT endpoint_url, auth_header_env, install_manifest
         FROM rvbbit.backends
        WHERE name = ${sqlLit(backendName)}
        LIMIT 1`,
      { rowLimit: 1, poolLane: "meta" },
    )
    const row = result.rows?.[0]
    const endpoint = typeof row?.endpoint_url === "string" ? row.endpoint_url.trim() : ""
    if (!endpoint) {
      return NextResponse.json({
        ok: false,
        backend: backendName,
        targetKind: "connector",
        latency_ms: Date.now() - started,
        error: "connector backend is not registered",
      })
    }

    const url = healthEndpoint(endpoint, healthPath(parseManifest(row.install_manifest)))
    const headers: Record<string, string> = { accept: "application/json" }
    const authEnv = typeof row.auth_header_env === "string" ? row.auth_header_env.trim() : ""
    const token = authEnv ? process.env[authEnv]?.trim() : ""
    if (token) headers.authorization = `Bearer ${token}`

    const response = await fetch(url, {
      method: "GET",
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    })
    const text = await response.text()
    let output: unknown = text
    try {
      output = text ? JSON.parse(text) : null
    } catch {
      // Preserve non-JSON health responses for diagnostics.
    }
    const declaredOk =
      !output || typeof output !== "object" || Array.isArray(output)
        ? true
        : (output as Record<string, unknown>).ok !== false
    const ok = response.ok && declaredOk
    const shape = outputShape(output)
    return NextResponse.json({
      ok,
      backend: backendName,
      targetKind: "connector",
      transport: "connector_health",
      endpoint: url,
      latency_ms: Date.now() - started,
      output,
      ...shape,
      error: ok ? undefined : responseError(output, response.status),
    })
  } catch (e) {
    return NextResponse.json({
      ok: false,
      backend: backendName,
      targetKind: "connector",
      latency_ms: Date.now() - started,
      error: e instanceof Error ? e.message : String(e),
    })
  }
}
