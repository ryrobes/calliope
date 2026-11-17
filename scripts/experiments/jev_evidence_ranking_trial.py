#!/usr/bin/env python3
"""Local-only 16/40 candidate trial. No database or pilot access.

Uses synthetic mixed-source cases and optionally replays existing frozen Brain
hits. Requires OPENROUTER_API_KEY; writes descriptions/provider replies only to
the explicitly supplied local output file. Frozen client evidence stays private.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services/calliope"))
import evidence_ranking as ranking


def synthetic_cases():
    topics = [
        ("Revenue", "Booked revenue by month, region and product."),
        ("Retention", "Student persistence by cohort and program."),
        ("Enrollment", "New student enrollments by source and week."),
        ("Marketing", "Advertising costs and attributed submissions."),
        ("Staffing", "Open staff roles and instructor availability."),
        ("Attendance", "Class attendance and absence counts."),
        ("Facilities", "Campus capacity and maintenance work."),
        ("Collections", "Payment collections, aging and balances."),
        ("Withdrawals", "Recorded withdrawal reasons, refund rules and exceptions."),
        ("Graduation", "Completion dates and certification outcomes."),
    ]
    pool = []
    for group, kind in [("knowledge", "document"), ("artifacts", "artifact"), ("data", "db_table"), ("metrics", "metric")]:
        for i, (title, summary) in enumerate(topics):
            pool.append({"id": f"{group}:{i}", "group": group, "kind": kind,
                         "title": f"{title} {kind}", "summary": summary,
                         "handle": {"doc_id": i} if kind == "document" else {},
                         "occurred_at": "2026-09-18"})
    # The correct item sits below the old four-per-source candidate cutoff.
    for label, query, target, description in [
        ("specific_policy", "What exceptions allow a full refund after a student withdraws?",
         "knowledge:8", "Current refund policy: a full refund after withdrawal is allowed for documented program cancellation or verified duplicate payment; other cases follow the attendance schedule."),
        ("joinable_table", "Which table contains invoice-level outstanding balances so I can join them to customers?",
         "data:7", "finance.open_invoices: one row per invoice, with invoice_id, customer_id, unpaid_balance and due_date. Join customer_id to crm.customers.id."),
        ("existing_metric", "Show me the number of graduates this month without building a new report.",
         "metrics:9", "Governed monthly graduate count: distinct students with completed certification in the current calendar month, with current value and a daily trend."),
    ]:
        items = [dict(i) for i in pool]
        next(i for i in items if i["id"] == target)["summary"] = description
        yield {"id": label, "query": query, "target": target, "items": items}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--frozen", type=Path)
    args = parser.parse_args()
    if not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is required")
    os.environ["CALLIOPE_EVIDENCE_RANKING_MODE"] = "select"
    cases = list(synthetic_cases())
    if args.frozen:
        for case in json.loads(args.frozen.read_text())["cases"]:
            hits = case["retrieval"]["hits"][:10]
            cases.append({"id": case["id"], "query": case["question"], "items": [
                {"id": f"brain:{h['doc_id']}:{h['chunk_idx']}", "group": "knowledge", "kind": "document",
                 "title": h["title"], "summary": h["chunk"], "source": h.get("source"),
                 "occurred_at": h.get("occurred_at"), "handle": {"doc_id": h["doc_id"]}}
                for h in hits]})
    reports = []
    provider_request = ranking.request
    for case in cases:
        for per_source in ([4, 10] if case.get("target") else [10]):
            pool, counts = [], {}
            for item in case["items"]:
                group = item["group"]
                counts[group] = counts.get(group, 0) + 1
                if counts[group] <= per_source:
                    pool.append(item)
            provider = {}
            async def capture(body, key):
                result = await provider_request(body, key)
                provider.update(result)
                return result
            ranking.request = capture
            ranked, receipt = ranking.rank(case["query"], "local-replay", pool, pool[:8])
            record = {"case": case["id"], "query": case["query"], "pool_size": len(pool),
                      "receipt": receipt, "target": case.get("target"),
                      "top_eight": [ranking.descriptor(i) for i in ranked[:8]], "provider": provider}
            reports.append(record)
            target_rank = next((i + 1 for i, r in enumerate(ranked) if r["id"] == case.get("target")), None)
            print(json.dumps({"case": case["id"], "pool": len(pool), "target_rank": target_rank, **receipt}), flush=True)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(reports, indent=2, default=str))


if __name__ == "__main__":
    main()
