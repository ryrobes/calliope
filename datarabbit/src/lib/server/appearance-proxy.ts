import { NextRequest, NextResponse } from "next/server"

import { artifactInternalBase } from "@/lib/server/plates"

export async function proxyWarehouseAppearance(
  req: NextRequest,
  suffix = "",
): Promise<NextResponse> {
  const base = artifactInternalBase()
  if (!base) {
    return NextResponse.json(
      { error: { code: "APPEARANCE_UNAVAILABLE", message: "Warehouse is not configured" } },
      { status: 503 },
    )
  }
  const headers = new Headers()
  const key = process.env.WAREHOUSE_MCP_KEY ?? ""
  const warehouseSession = req.cookies.get("wh_session")?.value ?? ""
  if (key) headers.set("authorization", `Bearer ${key}`)
  if (warehouseSession) headers.set("cookie", `wh_session=${warehouseSession}`)
  for (const name of ["content-type", "x-rvbbit-filename"]) {
    const value = req.headers.get(name)
    if (value) headers.set(name, value)
  }
  const hasBody = !["GET", "HEAD", "DELETE"].includes(req.method)
  try {
    const upstream = await fetch(`${base}/api/calliope/appearance${suffix}`, {
      method: req.method,
      headers,
      body: hasBody ? await req.arrayBuffer() : undefined,
      cache: "no-store",
    })
    const responseHeaders = new Headers({
      "cache-control": "private, no-store",
      "content-type": upstream.headers.get("content-type") ?? "application/json",
      "x-content-type-options": "nosniff",
    })
    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    })
  } catch (error) {
    return NextResponse.json(
      {
        error: {
          code: "APPEARANCE_UNAVAILABLE",
          message: error instanceof Error ? error.message : "Warehouse appearance is unavailable",
        },
      },
      { status: 502 },
    )
  }
}
