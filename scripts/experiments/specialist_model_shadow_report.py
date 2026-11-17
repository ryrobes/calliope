#!/usr/bin/env python3
"""Read specialist shadow predictions alongside actual work; no new inference.

Run with the Calliope server environment. Export includes private task text;
keep it outside the repository. This is an admin analysis CLI, not a user API.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def collect(conn_factory, owner, days=7, limit=200, session=None):
    if not 1 <= days <= 30 or not 1 <= limit <= 1000:
        raise ValueError("Choose 1-30 days and 1-1000 runs")
    with conn_factory() as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='10s'")
        rows = conn.execute("""
            WITH recent AS (
                SELECT r.* FROM rvbbit.calliope_specialist_runs r
                 WHERE lower(r.owner_email)=lower(%s)
                   AND r.created_at >= now() - (%s * interval '1 day')
                   AND (%s::uuid IS NULL OR r.source_session_id=%s::uuid)
                 ORDER BY r.created_at DESC,r.id LIMIT %s
            )
            SELECT r.id,r.thread_id,r.source_session_id,r.source_turn_id,r.round_no,
                   s.specialist_key,s.display_name,s.profile_name,r.status,
                   r.created_at,r.started_at,r.completed_at,r.task,
                   left(r.result,3000) AS result_excerpt,left(r.error,500) AS error,
                   r.usage,r.runtime,
                   r.context->'_model_selection_shadow' AS shadow,
                   extract(epoch FROM (r.completed_at-r.started_at)) AS elapsed_seconds,
                   (SELECT count(*) FROM rvbbit.calliope_specialist_runs later
                     WHERE later.thread_id=r.thread_id AND later.round_no>r.round_no
                       AND lower(later.owner_email)=lower(%s)) AS later_rounds,
                   coalesce((SELECT jsonb_agg(jsonb_build_object(
                       'name', t.name, 'completed', t.completed, 'failed', t.failed))
                       FROM (SELECT m.payload->>'tool_name' AS name,
                           count(*) FILTER (WHERE m.kind='tool_completed') AS completed,
                           count(*) FILTER (WHERE m.kind='tool_failed') AS failed
                           FROM rvbbit.calliope_specialist_messages m WHERE m.run_id=r.id
                             AND m.kind IN ('tool_completed','tool_failed')
                           GROUP BY m.payload->>'tool_name') t), '[]'::jsonb) AS tools
              FROM recent r JOIN rvbbit.calliope_specialists s ON s.id=r.specialist_id
             ORDER BY r.created_at,r.id
            """, (owner, days, session, session, limit + 1, owner)).fetchall()
    # The extra row makes the time window's coverage limit visible.
    limited = len(rows) > limit
    rows = rows[-limit:]
    records = []
    for row in rows:
        item = dict(row)
        usage = item.get("usage") or {}
        runtime = item.get("runtime") or {}
        nested = usage.get("runtime") or {}
        item["actual_model"] = runtime.get("model") or nested.get("model") or "unknown"
        item["actual_provider"] = runtime.get("provider") or nested.get("provider") or "unknown"
        records.append(item)
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "owner": owner,
            "days": days, "session": session, "limited": limited, "runs": records,
            "summary": summarize(records),
            "limitations": [
                "Predictions are hypotheses, not measured relative model capability or savings.",
                "Completed runs are not proof of accepted work. Later rounds can be new scope, not corrections.",
                "Actual execution uses the configured model; there is no alternate-model outcome.",
                "Missing snapshots may be disabled, out of scope, at capacity, or interrupted before persistence.",
                "Pending snapshots may be in flight or interrupted; they do not imply live specialist work.",
                "No image pixels or specialist memory were inspected by the selector.",
                "Usage is preserved as reported; absent costs or cache counts remain unknown.",
            ]}


def summarize(records):
    statuses, lanes = Counter(), Counter()
    cost = 0.0
    known_cost = 0
    initial = 0
    review = []
    for r in records:
        s = r.get("shadow") or {}
        statuses[s.get("status", "not_observed")] += 1
        if int(r.get("round_no") or 1) != 1:
            continue
        initial += 1
        if s.get("status") == "classified":
            lanes[s["recommendation"]] += 1
        usage = s.get("usage") or {}
        if "cost" in usage:
            cost += float(usage["cost"])
            known_cost += 1
        if s.get("recommendation") in {"luna", "terra"}:
            # These are review flags, deliberately not an automatic quality verdict.
            flags = []
            if r.get("status") == "failed": flags.append("execution_failed")
            if r.get("later_rounds", 0): flags.append("has_followup_rounds")
            if any(t.get("failed", 0) for t in r.get("tools", [])): flags.append("tool_failures")
            if flags: review.append({"run_id": str(r["id"]), "flags": flags})
    return {"initial_assignments": initial, "observation_statuses": dict(statuses),
            "initial_candidate_lanes": dict(lanes), "known_selector_cost_usd": cost,
            "initial_receipts_with_cost": known_cost, "review_candidates": review}


def markdown(report):
    def cell(value):
        return str(value if value is not None else "unknown").replace("|", "\\|").replace("\n", " ")
    lines = ["# Specialist model shadow observations", "",
             "Configured models performed all work. Candidate lanes are experimental hypotheses.", "",
             f"Owner: {report['owner']} · last {report['days']} days · limited: {report['limited']}", "",
             "| Agent / round | Candidate | Actual model | Status | Seconds | Later rounds | Tools completed / failed | Run |",
             "| --- | --- | --- | --- | ---: | ---: | --- | --- |"]
    for r in report["runs"]:
        shadow = r.get("shadow") or {}
        lane = shadow.get("recommendation", "not observed")
        tools = "; ".join(f"{t['name']}: {t['completed']}/{t['failed']}" for t in r.get("tools", []))
        lines.append("| " + " | ".join(cell(v) for v in (
            f"{r['display_name']} / {r['round_no']}", lane, r["actual_model"], r["status"],
            r.get("elapsed_seconds"), r.get("later_rounds"), tools, r["id"])) + " |")
    lines.extend(["", "## Coverage and review flags", "", "```json", json.dumps(report["summary"], indent=2), "```", "",
                  "## Interpretation limits", "", *["- " + x for x in report["limitations"]], "",
                  "Full bounded classifier inputs, probabilities, usage, task and result excerpts are in report.json."])
    return "\n".join(lines) + "\n"


def main():
    import server
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--session")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): parser.error("Preserve the existing report; choose a new directory")
    os.umask(0o077)
    report = collect(server._conn, args.owner, args.days, args.limit, args.session)
    args.output.mkdir(parents=True, mode=0o700)
    (args.output / "report.json").write_text(json.dumps(report, default=str, ensure_ascii=False, indent=2))
    (args.output / "report.md").write_text(markdown(report))
    print(json.dumps({"runs": len(report["runs"]), "output": str(args.output), "limited": report["limited"]}))


if __name__ == "__main__":
    main()
