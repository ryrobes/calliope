import { NextResponse } from "next/server"
import { burrowExecutionRole, burrowSession, isBurrow } from "@/lib/server/burrow"

// Deployment-mode facts the client shell needs (BURROW_PLAN §5 P2). In
// Burrow the desktop is pinned to ONE database. The shell can show the
// authenticated Warehouse subject while authMode/roleScoped make the runtime
// access model explicit for diagnostics.
export async function GET(req: Request) {
  if (!isBurrow()) return NextResponse.json({ ok: true, burrow: false })
  const session = await burrowSession(req.headers.get("cookie"))
  return NextResponse.json({
    ok: true,
    burrow: true,
    sub: session?.sub ?? null,
    authMode: session?.authMode ?? null,
    roleScoped: session ? burrowExecutionRole(session) !== undefined : false,
  })
}
