// The Calliope login (BURROW_PLAN §5 P4). Served by lens on the unified
// origin (the ingress routes GET /login here); the form POSTs to /login,
// which the ingress routes to the warehouse AS — it owns sessions, the
// OAuth txn continuation, rate-limiting, and (in Burrow mode) verifying
// credentials against Postgres itself. Pixels here, plumbing there.
// Failures PRG back as /login?err=1 with the flow params preserved.

import type { Metadata } from "next"
import { RvbbitLogo } from "@/components/desktop/rvbbit-logo"
import { authConfig } from "@/lib/server/burrow"
import styles from "./login.module.css"

export const metadata: Metadata = {
  title: "Calliope · RVBBIT.AI",
  description: "Your company’s knowledge, put to work.",
}

const wrap: React.CSSProperties = {
  minHeight: "100dvh",
  display: "grid",
  placeItems: "center",
  padding: 24,
  background: "radial-gradient(1100px 760px at 30% -10%, color-mix(in oklch, var(--main) 16%, var(--background)) 0%, var(--secondary-background) 46%, var(--background) 100%)",
  color: "var(--foreground)",
  fontFamily: "'IBM Plex Sans', var(--font-family-sans, ui-sans-serif, sans-serif)",
}

const card: React.CSSProperties = {
  position: "relative",
  zIndex: 1,
  width: "min(400px, 100%)",
  padding: "32px 34px 28px",
  borderRadius: 14,
  border: "1px solid color-mix(in oklch, var(--border) 72%, transparent)",
  background: "color-mix(in oklch, var(--secondary-background) 86%, transparent)",
  boxShadow: "0 24px 80px color-mix(in oklch, var(--background) 76%, transparent), inset 0 1px 0 color-mix(in oklch, var(--foreground) 5%, transparent)",
  backdropFilter: "blur(18px) saturate(1.08)",
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "10px 12px",
  marginTop: 6,
  borderRadius: 9,
  border: "1px solid color-mix(in oklch, var(--border) 68%, transparent)",
  background: "color-mix(in oklch, var(--background) 74%, transparent)",
  color: "var(--foreground)",
  fontSize: 14,
  fontFamily: "'IBM Plex Sans', var(--font-family-sans, ui-sans-serif, sans-serif)",
  outline: "none",
}

const labelStyle: React.CSSProperties = {
  display: "block",
  marginTop: 16,
  fontSize: 10,
  letterSpacing: "0.12em",
  textTransform: "uppercase",
  color: "color-mix(in oklch, var(--foreground) 58%, transparent)",
  fontFamily: "'IBM Plex Mono', var(--font-family-mono, ui-monospace, monospace)",
}

const productName: React.CSSProperties = {
  color: "var(--foreground)",
  fontFamily: "'Homemade Apple', cursive",
  fontSize: "clamp(38px, 11vw, 45px)",
  fontWeight: 400,
  lineHeight: 1.22,
  letterSpacing: "-0.012em",
  textShadow: "0 4px 22px color-mix(in oklch, var(--background) 72%, transparent)",
}

const makerMark: React.CSSProperties = {
  alignSelf: "flex-end",
  marginTop: 1,
  color: "var(--main)",
  fontFamily: "'IBM Plex Mono', var(--font-family-mono, ui-monospace, monospace)",
  fontSize: 8,
  fontWeight: 700,
  lineHeight: 1,
  letterSpacing: "0.24em",
  textTransform: "uppercase",
  transform: "translateX(40px)",
}

// Google's mark, inlined — a login page must not phone a third party to render.
const gMark = (
  <svg viewBox="0 0 48 48" width={17} height={17} aria-hidden style={{ display: "block" }}>
    <path fill="#4285F4" d="M45.1 24.5c0-1.6-.1-2.8-.4-4H24v7.3h12.1c-.2 2-1.6 5-4.5 7l-.1.3 6.6 5 .4.1c4.2-3.9 6.6-9.6 6.6-15.7" />
    <path fill="#34A853" d="M24 46c6 0 11-2 14.6-5.4l-7-5.4c-1.8 1.3-4.3 2.2-7.6 2.2-5.8 0-10.7-3.8-12.5-9.1l-.3.1-6.8 5.3-.1.3C8 41.1 15.4 46 24 46" />
    <path fill="#FBBC05" d="M11.5 28.3c-.5-1.4-.8-2.9-.8-4.3s.3-3 .7-4.3v-.4l-6.9-5.4-.2.1C2.8 16.8 2 20.3 2 24s.8 7.2 2.3 10.3z" />
    <path fill="#EB4335" d="M24 9.9c4.1 0 6.9 1.8 8.5 3.3l6.2-6C34.9 3.7 30 1.5 24 1.5 15.4 1.5 8 6.4 4.3 13.6l7.1 5.5C13.3 13.8 18.2 9.9 24 9.9" />
  </svg>
)

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  const sp = await searchParams
  const txn = typeof sp.txn === "string" ? sp.txn : ""
  const next = typeof sp.next === "string" ? sp.next : "/"
  const err = sp.err != null
  const oauth = txn !== ""
  // The warehouse owns auth; ask it what to draw rather than duplicating its
  // env here. Google carries txn/next so a federated login resumes the same
  // flow (Claude connector, or the dashboard the user was reaching for).
  const cfg = await authConfig()
  const googleHref = `/auth/google/start?${new URLSearchParams(txn ? { txn } : { next }).toString()}`
  return (
    <div style={wrap} className={styles.wrap}>
      {/* A returning user's saved appearance (pre-hydration.js) tints these: the palette's
          main colour drives the slow aurora, and their wallpaper, when they have one, sits
          dimmed underneath. Purely decorative; reduced-motion users get a still version. */}
      <div className={styles.backdrop} aria-hidden>
        <div className={styles.wallpaper} />
        <div className={`${styles.blob} ${styles.blobA}`} />
        <div className={`${styles.blob} ${styles.blobB}`} />
        <div className={`${styles.blob} ${styles.blobC}`} />
      </div>
      <form method="post" action="/login" style={card} className={styles.card}>
        <div style={{ position: "relative", textAlign: "center" }}>
          <RvbbitLogo style={{ position: "absolute", top: 14, left: 0, width: 31, height: 16, color: "var(--main)", filter: "drop-shadow(0 0 9px color-mix(in oklch, var(--main) 48%, transparent))" }} />
          <div style={{ minWidth: 0 }}>
            <div style={{ display: "inline-flex", flexDirection: "column", alignItems: "flex-end" }}>
              <div style={productName} className={styles.wordmark}>Calliope</div>
              <div style={makerMark}>by RVBBIT.AI</div>
            </div>
            <div style={{ marginTop: 10, color: "var(--foreground)", fontSize: 15, fontWeight: 500, letterSpacing: "-0.01em", lineHeight: 1.45 }}>
              Your company&apos;s knowledge, put to work.
            </div>
            {oauth ? (
              <div style={{ marginTop: 7, color: "color-mix(in oklch, var(--foreground) 48%, transparent)", fontFamily: "'IBM Plex Mono', var(--font-family-mono, ui-monospace, monospace)", fontSize: 8, fontWeight: 600, letterSpacing: "0.08em", lineHeight: 1.45, textTransform: "uppercase" }}>
                Authorize Claude to work with your company knowledge.
              </div>
            ) : null}
          </div>
        </div>

        {txn ? <input type="hidden" name="txn" value={txn} /> : <input type="hidden" name="next" value={next} />}

        {cfg.google ? (
          <a
            href={googleHref}
            style={{
              display: "flex", alignItems: "center", justifyContent: "center", gap: 9,
              width: "100%", marginTop: 22, padding: "11px 0", borderRadius: 10,
              border: "1px solid color-mix(in oklch, var(--border) 70%, transparent)", background: "color-mix(in oklch, var(--foreground) 94%, transparent)",
              color: "var(--background)", fontSize: 14, fontWeight: 600, textDecoration: "none",
            }}
          >
            {gMark}
            {oauth ? "Authorize with Google" : "Sign in with Google"}
          </a>
        ) : null}

        {cfg.google && cfg.password ? (
          <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "18px 0 0",
                        color: "color-mix(in oklch, var(--foreground) 36%, transparent)", fontSize: 10, letterSpacing: "0.12em" }}>
            <span style={{ flex: 1, height: 1, background: "color-mix(in oklch, var(--border) 52%, transparent)" }} />
            OR
            <span style={{ flex: 1, height: 1, background: "color-mix(in oklch, var(--border) 52%, transparent)" }} />
          </div>
        ) : null}

        {cfg.password ? (
          <>
            <label style={labelStyle}>
              Username
              <input name="email" autoComplete="username" autoFocus required style={inputStyle} />
            </label>
            <label style={labelStyle}>
              Password
              <input name="password" type="password" autoComplete="current-password" required style={inputStyle} />
            </label>
          </>
        ) : null}

        {err ? (
          <div style={{ marginTop: 14, fontSize: 12, color: "#e06c5e" }}>
            That didn&apos;t work — check your username and password.
          </div>
        ) : null}

        {cfg.password ? (
          <button
            type="submit"
            style={{
              width: "100%", marginTop: 22, padding: "11px 0", borderRadius: 10,
              border: "1px solid color-mix(in oklch, var(--main) 62%, var(--border))", background: "color-mix(in oklch, var(--main) 14%, transparent)",
              color: "var(--main)", fontSize: 14, fontWeight: 600, fontFamily: "inherit", cursor: "pointer",
            }}
          >
            {oauth ? "Authorize" : "Sign in"}
          </button>
        ) : null}

        <div style={{ marginTop: 18, fontSize: 10, textAlign: "center", color: "color-mix(in oklch, var(--foreground) 36%, transparent)" }}>
          Secure access to your company workspace.
        </div>
      </form>
    </div>
  )
}
