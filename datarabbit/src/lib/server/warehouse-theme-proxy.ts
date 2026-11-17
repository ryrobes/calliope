import { NextResponse } from "next/server"

import { artifactInternalBase } from "@/lib/server/plates"

/**
 * Keep hosted appearance assets addressable by the same origin-relative URL
 * on both Calliope and DataRabbit. Shared appearance records can therefore
 * persist `/theme/...` URLs without pinning either app's public host or port.
 */
export async function proxyWarehouseTheme(path: string): Promise<NextResponse> {
  const base = artifactInternalBase()
  if (!base) {
    return NextResponse.json({ error: "Warehouse is not configured" }, { status: 503 })
  }
  try {
    const upstream = await fetch(`${base}${path}`, { cache: "no-store" })
    const headers = new Headers({
      "cache-control": upstream.headers.get("cache-control") ?? "public, max-age=300",
      "content-type": upstream.headers.get("content-type") ?? "application/octet-stream",
      "x-content-type-options": "nosniff",
    })
    return new NextResponse(upstream.body, { status: upstream.status, headers })
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Warehouse theme is unavailable" },
      { status: 502 },
    )
  }
}
