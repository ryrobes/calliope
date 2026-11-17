import { proxyWarehouseTheme } from "@/lib/server/warehouse-theme-proxy"

export const dynamic = "force-dynamic"

export async function GET() {
  return proxyWarehouseTheme("/theme/library")
}
