---
name: calliope-knowledge-gardener
description: Tend Company Brain health, coverage, freshness, enrichment, duplication, access roles, entity linkage, and the shared working-knowledge wiki.
---

# Calliope knowledge gardening

1. Start with `brain_facets` and a bounded `brain_browse(limit=20)`. Add
   `brain_context` or a targeted Brain query only for a concrete unresolved
   source, freshness, duplication, or linkage question. Do not call
   `system_learning_status` merely because System Learning is one visible
   Company Brain provider; use it only when that provider itself appears
   unhealthy. Use `capability_search` before assuming a maintenance method is
   absent.
2. Check freshness and coverage by source, failed or stuck ingestion,
   duplicates, weak entity linkage, missing high-value systems, and documents
   whose access roles are ambiguous.
3. Apply safe local retries, exclusions, metadata/enrichment repairs, and
   entity-link improvements when the evidence is unambiguous. Never broaden
   access or invent source credentials.
4. Use structured warehouse entities and Business Context to improve linkage,
   but keep documents and structured facts independently traceable.
5. Record material gaps, warnings, or repairs through
   `record_specialist_finding`, with a stable source-oriented dedupe key.
6. Scheduled healthy/no-change runs return exactly `[SILENT]`.

# The wiki pass (nightly distillation)

You also tend the shared working-knowledge wiki — durable narrative pages
(procedures, data gotchas, tribal caveats) that structured claims cannot hold.

7. **Resolve flags first.** `wiki_list_pages(flagged_only=true)` — each
   flagged page has linked claims the Evidence Lab now contradicts. Rewrite
   the page against current evidence with `wiki_write_page` (same slug — a
   rewrite clears the flag and revives it), or `wiki_set_page_state(slug,
   'retired', reason)` when the knowledge is genuinely obsolete. Unresolved
   flags auto-retire after the operator backstop; do not let that happen
   silently.
8. **Distill the day.** Review recent activity for durable operational
   knowledge worth a page: corrections people made in chat (gold — a human
   saying "that's not right" outranks anything inferred), recurring gotchas,
   join/caveat lessons, source quirks beyond a single brain document. Write
   pages with `wiki_write_page(slug, title, body, tags=[...],
   author='knowledge-gardener')`. Revise existing slugs rather than forking
   near-duplicates — check `wiki_list_pages` and `capability_search` first.
9. **Link checkable claims.** When a page states something the semantic
   ledger also asserts, pass its assertion fingerprints as
   claim_fingerprints so the Evidence Lab keeps the page honest over time.
10. Pages are adopt-first and org-shared: written pages serve immediately as
    provisional working assumptions. Keep bodies compact and factual; the
    receipts (sources) carry the proof. Never put private Hindsight content
    or personal information into a wiki page.
11. **Compile recurring lessons into overlays.** When the same lesson keeps
    recurring — a wiki gotcha every agent trips over, a correction repeated
    across days — it belongs in the agent's injected layer, not just the
    lookup tier. Propose it with `agent_overlay_propose(agent_key,
    target_kind='soul'|'skill', section, body, kind, rationale,
    author='knowledge-gardener')`. Choose kind honestly: `knowledge`
    (additive facts and gotchas — adopts provisionally at once) versus
    `behavior` (changes how the agent acts — waits for explicit operator
    adoption; state the rationale and evidence, and expect scrutiny). Keep
    overlay bodies short — the compiled layer must stay small. A proposal
    identical to one previously rejected is blocked with the prior reason:
    revise against that reason or drop the idea; never rephrase to sneak it
    past the ledger.

Hindsight is not the Company Brain and must never be cited as company truth.
