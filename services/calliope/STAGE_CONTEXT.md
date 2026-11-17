# Optional Stage recall

Stage recall ranks existing notebook objects for a new human chat turn using
OpenRouter's typed Decisions API (`typesafe/jev-1.13`). It changes the automatic
Stage context supplied to Hermes, not Stage rendering, user selections, stored
objects, tools, or permission checks. It does not spawn another agent.

Default is **off**. Set `CALLIOPE_STAGE_CONTEXT_MODE=shadow` to evaluate and audit
without changing context, or `select` to use the selection. Optional comma-separated
`CALLIOPE_STAGE_CONTEXT_OWNERS` and `CALLIOPE_STAGE_CONTEXT_SESSIONS` narrow the rollout.
The owner filter actually checks the authenticated current speaker, including
shared threads. Empty filters apply to all eligible human chat turns; automatic
and specialist turns do not enter this path. Use the existing server
`OPENROUTER_API_KEY`. No production fallback model or utility slot is changed.

Specialist rounds also use assignment-specific recall when the same rollout
filters enable their owner and parent notebook. This runs once just before a new
round is submitted, including queued follow-ups. It ranks the parent Stage
against the specialist's assignment, with bounded caller notes, the current
human request, and the specialist's own last completed exchange as context.
It does not simply reuse the lead's selection.

The selected descriptors are frozen in that specialist run's
`context._stage_recall` before submission. They are added to the new handoff as
`stage_references`; existing conversation messages, system prompts, explicit
pins, references, and image attachments are untouched. No reranking occurs during
tool calls or reattachment to a running Hermes execution. A retry before
submission reuses its saved selection. New rounds receive fresh selections.
This preserves the existing message prefix for caching; the new handoff still
incurs ordinary input tokens, and provider cache hits are not guaranteed.

Specialist recall adds a bounded reference inventory to a handoff that previously
had none; unlike the lead's recall, it is not a measured token-saving change.
Small inventories avoid hosted inference. Provider/loading failures preserve the
ordinary handoff without extra Stage references; unsaved selections are not sent.
Shadow mode stores the selection without adding it to the prompt. Specialist
receipts include usage, latency, candidates, anchors, labels and selected IDs in
the run context rather than competing for the parent's per-turn receipt.

## Selection

- Look back at up to 120 same-session surfaces. Collapse older lineage revisions
  except an explicitly selected revision. Candidate retrieval keeps 12 newest
  distinct objects and fills a 32-item pool with lexical matches. A selected
  revision/latest published artifact can extend the pool by up to two entries.
- One hosted request labels candidates useful/unrelated/uncertain using fixed
  instructions. The caller supplies the new message, bounded preceding two
  exchanges, titles, artifact handles, times, bounded SQL, parsed table names,
  output column names and dataset names. Named batch results retain their own
  SQL even when MCP sends the query map as a JSON string. Older surfaces recover
  that SQL from the saved batch only when the result name matches unambiguously.
  Table extraction is bounded, best-effort metadata, not authoritative lineage.
  Image descriptors include known purpose, filename, creator, source/parent
  relationships, artifact version, and a bounded generation brief or Visual
  Intent edit instructions. Generation briefs describe requested content, not
  verified captions. No result rows, pixels or document bodies are sent to Jev.
- Keep at most eight objects, preserving the newest two substantive Stage
  objects (queries, charts, documents, etc.), the latest published
  page and explicit selection. Pair useful captures with their page when the
  capture slug identifies it. Fill remaining slots with useful evidence.
  Generic action/specialist receipts supply the recent anchors only when no
  substantive objects exist; an explicitly selected receipt always survives.
  In v5, named/described evidence takes precedence over anonymous image files
  for these recent anchors. Image-only inventories retain their recent fallback;
  anonymous images remain eligible for model selection and explicit pins.
- Fewer than nine distinct candidates skips hosted inference. Explicit object
  references, evidence pins and selected-surface context retain their existing
  separate paths. No model decision can remove those user-provided references.
- A reactive continuation reuses the initial choice, adds newly produced work,
  and refreshes existing selected IDs. No inference on every tool call/poll.
- A missing key, malformed response, provider failure or timeout restores the
  exact original recent inventory. Total ranking deadline is 2.8 seconds, HTTP
  timeout two seconds, receipt persistence separately capped at 0.7 seconds.
  DB reads use a 1.5-second statement timeout. There are no retries.

The ordinary off path still uses the original 24-item inventory. Selection is
retrieval evidence, not truth; Hermes is told this is an incomplete inventory.
Candidate identity and membership come from the authenticated thread, never the
model. The score ranks useful candidates; it is not a calibrated safety threshold.

## Observability and rollback

`rvbbit.calliope_stage_context_runs` is created by the normal server bootstrap.
Each enabled turn records candidate/selected IDs, mechanical anchor IDs,
model-labeled useful IDs, labels, actual model/provider,
provider request ID, usage/cost, context bytes/counts, elapsed time and fallback
status. It contains no API key, request text or raw provider errors. This cost
is separately auditable; it is not yet merged into Hermes' displayed turn cost.
Candidate SQL and conversation text follow the configured hosted provider route.

```sql
SELECT created_at,turn_id,receipt->>'status' AS status,
       receipt->>'before_count' AS before_items,receipt->>'after_count' AS after_items,
       receipt->>'elapsed_ms' AS milliseconds,receipt->'usage' AS usage
FROM rvbbit.calliope_stage_context_runs ORDER BY created_at DESC LIMIT 20;
```

Set mode to `off` and recreate only calliope-server to roll back selection.
Historical receipts/surfaces require no cleanup or reverse migration.

## Replay evaluation

Run a bounded private replay with `scripts/replay-stage-context.py --cases
/path/to/private/cases.jsonl --out /path/to/private/new-run`, with the OpenRouter
key in the environment. It never changes chats or sends replies. Keep private
source text/results outside the repository. A replay is a metadata replay, not a
complete reconstruction of old runtime state, and it does not measure Hermes
token savings or final-answer quality. A cheap baseline (deduplication/recency/
text matching) should remain part of the comparison. Fresh installs remain off
by default. Unit tests: `pytest services/calliope/test_stage_context.py`.

## Image metadata (v4)

`generate_image` accepts optional `title` and `purpose`. Without them, a bounded
prompt excerpt supplies the title and the purpose defaults to generated image.
The original prompt remains in `generated_images`; Stage stores a brief of up to
700 characters and the content hash. This uses no extra inference.

Image-bearing Stage objects share an `image_metadata` payload, including saved
PNG documents, page captures, and Visual Intent. Existing specialist attribution,
capture artifact/version, and Visual Intent source/parent/edit instructions are
preserved. Exact-byte copies may inherit generation metadata from an existing
surface in the same session. Renamed files qualify; re-encoded or edited images
do not acquire an inferred relationship just because they look similar.

Wide recall reads recover older generated-image prompts by their saved image IDs
in one bounded batch, without reading PNG bytes or rewriting historical records.
Known exact-byte copies can also inherit metadata on read. Legacy files with no
image ID or matching recorded hash remain filename-based; no vision captioner or
fuzzy matching is introduced. Descriptors are untrusted retrieval data and never
confer approval, authority, or a claim that a requested design was achieved.
