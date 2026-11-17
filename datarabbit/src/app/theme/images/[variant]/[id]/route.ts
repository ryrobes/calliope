import { NextResponse } from "next/server"

import { proxyWarehouseTheme } from "@/lib/server/warehouse-theme-proxy"

const THEME_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,160}\.webp$/

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ variant: string; id: string }> },
) {
  const { variant, id } = await params
  if (!new Set(["thumb", "full"]).has(variant) || !THEME_ID.test(id)) {
    return NextResponse.json({ error: "Theme image not found" }, { status: 404 })
  }
  return proxyWarehouseTheme(`/theme/images/${variant}/${id}`)
}
