import { NextResponse } from "next/server"
import { probeMcpTools } from "@/lib/server/colony-mcp"
import { burrowSession, isBurrow } from "@/lib/server/burrow"

export const runtime = "nodejs"

interface Body {
  command?: string
  args?: string[]
  env?: Record<string, string>
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as Body | null
  if (!body?.command) {
    return NextResponse.json({ ok: false, error: "command required" }, { status: 400 })
  }
  // This route spawns a caller-named local binary — the whole point of the
  // mcp shape, but only for someone entitled to configure a share. Ungated
  // (as it shipped) it is arbitrary local execution for any burrow caller.
  if (isBurrow() && !(await burrowSession(req.headers.get("cookie")))) {
    return NextResponse.json({ ok: false, error: "not signed in" }, { status: 401 })
  }
  try {
    const tools = await probeMcpTools(body.command, body.args ?? [], body.env)
    return NextResponse.json({ ok: true, tools })
  } catch (err) {
    const e = err as Error
    return NextResponse.json({ ok: false, error: e.message ?? String(err) }, { status: 200 })
  }
}
