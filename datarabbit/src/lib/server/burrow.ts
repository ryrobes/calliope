import { artifactInternalBase } from "@/lib/server/plates"

// Burrow mode (rvbbit-sql/docs/BURROW_PLAN.md): one database, one door.
// Warehouse owns the wh_session cookie and may authenticate with Postgres,
// Google, or the hosted shared account. Lens INTROSPECTS that same session
// (no JWT secret sharing, just a cookie round-trip on the unified origin).
// In pg auth mode the returned subject is also the execution role; in shared
// hosted mode it remains the human identity while SQL uses the pinned service
// connection, matching Calliope's access model.

export function isBurrow(): boolean {
  return (process.env.RVBBIT_MODE ?? "").toLowerCase() === "burrow"
}

/** Where whoami lives server-side (compose-internal warehouse URL). */
function authBase(): string {
  return (process.env.RVBBIT_AUTH_INTERNAL ?? "").replace(/\/+$/, "") || artifactInternalBase()
}

export type AuthConfig = { mode: string; google: boolean; password: boolean }

/** Which sign-in affordances the warehouse wants drawn. We render its login
 * page, so it — not our env — is the authority on what's configured. Falls
 * back to password-only: showing a Google button that 404s is worse than
 * omitting one that would have worked. */
export async function authConfig(): Promise<AuthConfig> {
  const base = authBase()
  const fallback: AuthConfig = { mode: "shared", google: false, password: true }
  if (!base) return fallback
  try {
    const r = await fetch(`${base}/auth/config`, { cache: "no-store" })
    if (!r.ok) return fallback
    const j = (await r.json()) as Partial<AuthConfig>
    return {
      mode: typeof j.mode === "string" ? j.mode : "shared",
      google: j.google === true,
      password: j.password !== false,
    }
  } catch {
    return fallback
  }
}

// Tiny positive cache: whoami is one HTTP hop, but the query API is hot.
// Keyed on the raw cookie value; entries live briefly so revocation and
// expiry stay meaningful.
const CACHE_TTL_MS = 60_000
export interface BurrowSession {
  sub: string
  authMode: string
  identity?: string
  mapped?: boolean
  via?: string
}

const sessionCache = new Map<string, { session: BurrowSession; until: number }>()

/** Resolve an authenticated Warehouse session from a Cookie header. */
export async function burrowSession(cookieHeader: string | null): Promise<BurrowSession | null> {
  if (!isBurrow() || !cookieHeader) return null
  const m = /(?:^|;\s*)wh_session=([^;]+)/.exec(cookieHeader)
  if (!m) return null
  const key = m[1]
  const hit = sessionCache.get(key)
  if (hit && hit.until > Date.now()) return hit.session
  const base = authBase()
  if (!base) return null
  try {
    const res = await fetch(`${base}/auth/whoami`, {
      headers: { cookie: `wh_session=${key}` },
      cache: "no-store",
    })
    if (!res.ok) {
      sessionCache.delete(key)
      return null
    }
    const body = (await res.json()) as {
      ok: boolean
      sub?: string
      mode?: string
      identity?: string
      mapped?: boolean
      via?: string
    }
    if (!body.ok || !body.sub) return null
    // Older pg-only Warehouse builds did not include mode. Defaulting those
    // sessions to pg preserves the original SET LOCAL ROLE enforcement.
    const session: BurrowSession = {
      sub: body.sub,
      authMode: typeof body.mode === "string" && body.mode ? body.mode : "pg",
      identity: body.identity,
      mapped: body.mapped,
      via: body.via,
    }
    if (sessionCache.size > 500) sessionCache.clear()
    sessionCache.set(key, { session, until: Date.now() + CACHE_TTL_MS })
    return session
  } catch {
    return null
  }
}

/**
 * PostgreSQL role to apply for this authenticated session. Hosted/shared auth
 * intentionally has no per-user SET ROLE: all appliance users have the same
 * database visibility as Calliope. pg auth retains the classic GRANT/RLS wall.
 */
export function burrowExecutionRole(session: BurrowSession): string | undefined {
  return session.authMode === "shared" ? undefined : session.sub
}
