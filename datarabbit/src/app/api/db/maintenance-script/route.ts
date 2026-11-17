import { NextResponse } from "next/server"
import { getConnection } from "@/lib/db/registry"
import {
  buildMaintenanceScript,
  type MaintenanceScriptKind,
} from "@/lib/db/system-health"

export const runtime = "nodejs"

const KINDS: MaintenanceScriptKind[] = [
  "rebuild",
  "reap-generations",
  "snapshots-retention",
  "orphaned-files",
  "vacuum-metadata",
  "install-jobs",
]

export async function GET(req: Request) {
  const url = new URL(req.url)
  const id = url.searchParams.get("connectionId")
  const kind = url.searchParams.get("kind") as MaintenanceScriptKind | null
  if (!id) return NextResponse.json({ error: "connectionId required" }, { status: 400 })
  if (!kind || !KINDS.includes(kind))
    return NextResponse.json({ error: `kind must be one of ${KINDS.join(", ")}` }, { status: 400 })
  const connection = await getConnection(id)
  if (!connection) return NextResponse.json({ error: "connection not found" }, { status: 404 })

  try {
    const result = await buildMaintenanceScript(id, kind)
    return NextResponse.json(result)
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : String(err) }, { status: 500 })
  }
}
