import { NextRequest } from "next/server"

import { proxyWarehouseAppearance } from "@/lib/server/appearance-proxy"

export const dynamic = "force-dynamic"

export async function GET(req: NextRequest) {
  return proxyWarehouseAppearance(req, "/image")
}

export async function PUT(req: NextRequest) {
  return proxyWarehouseAppearance(req, "/image")
}

export async function DELETE(req: NextRequest) {
  return proxyWarehouseAppearance(req, "/image")
}
