"use client"

import { useEffect, useState } from "react"

import { Sparkles } from "@/lib/icons"

/**
 * The field guide card — shown ONCE, ever, when someone is working a
 * non-rvbbit Postgres (server-persisted flag; recallable afterward via the
 * rail icon, otherwise silent forever). Doctrine: this is the app being
 * helpful about its own capabilities, not an ad — situated copy, their
 * actual connection named, the real install commands, zero upsell energy.
 */
export function FieldGuideWindow({ connectionId }: { connectionId: string | null }) {
  const [conn, setConn] = useState<{ label?: string; database?: string } | null>(null)
  useEffect(() => {
    if (!connectionId) return
    let dead = false
    fetch("/api/db/connections")
      .then((r) => r.json())
      .then((b: { connections?: Array<{ id: string; label?: string; database?: string }> }) => {
        if (!dead) setConn(b?.connections?.find((c) => c.id === connectionId) ?? null)
      })
      .catch(() => {})
    return () => { dead = true }
  }, [connectionId])
  const connectionLabel = conn?.label ?? null
  const database = conn?.database ?? null
  const where = connectionLabel
    ? `${connectionLabel}${database ? ` · ${database}` : ""}`
    : "this connection"

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-doc-bg p-6 text-[13px] leading-relaxed text-foreground group-data-[focused=false]/window:bg-doc-bg/70">
      <div className="mb-1 flex items-center gap-2 text-[11px] uppercase tracking-widest text-chrome-text/60">
        <Sparkles className="h-3.5 w-3.5 text-rvbbit-accent" />
        the field guide · shown once, recall me from the rail
      </div>
      <h2 className="mb-3 text-[22px] font-semibold leading-tight">
        <span className="text-chrome-text">{where}</span> is running at 1×.
      </h2>
      <p className="mb-4 text-chrome-text">
        Data Rabbit works on any Postgres — you&apos;re in that mode now. When a
        database has the <code className="text-rvbbit-accent">rvbbit</code>{" "}
        extension, this same desktop lights up considerably:
      </p>

      <ul className="mb-5 space-y-2.5">
        <li>
          <b>SQL that understands meaning.</b>{" "}
          <code className="rounded bg-foreground/[0.06] px-1.5 py-0.5 text-[12px]">
            WHERE means(body, &apos;angry about billing&apos;)
          </code>{" "}
          — 49 semantic operators as plain functions, on your rows.
        </li>
        <li>
          <b>Scans that come back before you look up.</b> A columnar layer +
          engine router behind your existing tables — large aggregates
          routinely 10–100× faster, no schema changes.
        </li>
        <li>
          <b>Receipts on every AI answer.</b> Each semantic call records the
          exact model, inputs, and cost. Auditable, not vibes.
        </li>
        <li>
          <b>The assistant becomes a builder.</b>{" "}Dashboards, metrics, cubes,
          even slide decks assembled from whatever&apos;s on your desktop —
          with the numbers pinned by the database, not typed by the model.
        </li>
      </ul>

      <p className="mb-2 font-medium">
        The upgrade is additive — your existing Postgres 18, three files, one
        setting:
      </p>
      <pre className="mb-3 overflow-x-auto rounded-md border border-chrome-border/60 bg-black/30 p-3 text-[12px] leading-relaxed">
        <code>{`curl -fsSLO https://rvbbit.ai/dist/pg_rvbbit-4.1.5-pg18-amd64.tar.gz
tar xzf pg_rvbbit-4.1.5-pg18-amd64.tar.gz && sudo ./pg_rvbbit-4.1.5-pg18-amd64/install.sh
# then in psql, after setting shared_preload_libraries='pg_rvbbit' + restart:
CREATE EXTENSION pg_rvbbit;  SELECT rvbbit.migrate();`}</code>
      </pre>
      <p className="mb-5 text-chrome-text">
        The semantic operators run through a Clover gateway you host: the
        Calliope bundle includes one, or run it on its own beside your
        Postgres with your own model key. Full path:{" "}
        <a
          className="text-rvbbit-accent underline underline-offset-2"
          href="https://rvbbit.ai/existing-postgres"
          target="_blank"
          rel="noreferrer"
        >
          rvbbit.ai/existing-postgres
        </a>
        .
      </p>

      <p className="mt-auto border-t border-chrome-border/40 pt-3 text-[12px] text-chrome-text/70">
        Close this and it never interrupts again — the ✦ in the rail brings it
        back if you get curious. Data Rabbit stays fully useful either way.
      </p>
    </div>
  )
}
