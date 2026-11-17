# Agent result delivery and recovery

The September 28 Sonnet trial exposed three coupled failures: a short specialist
answer was wrapped in the entire UI conversation, the result spilled into a
single escaped JSON line, and recovering it required code execution that the
session could not authorize. Context compression also removed the original SQL
for a transformation, leaving the coordinator to infer its semantics from rows.

## Delivery

Delegate, continue and await now return an agent receipt: identity, status and
the latest answer. Conversation history, transcripts and Stage metadata remain
in their existing durable ledgers and UI projections. Long answers carry an
explicit `response_truncated` flag and `read_more` arguments. Fanout divides one
response budget across replies instead of multiplying the full-answer budget.

`read_calliope_specialist_result(run_id, offset, limit)` reads the exact saved
round. It requires a verified human subject and filters by run owner. It never
starts, acknowledges or continues an agent. Pages use decoded character offsets.

Hermes also exposes `read_tool_result(path, pointer, offset, limit)` in its file
toolset. It reads only the current profile's spillover directory, refuses symlink
targets and arbitrary host paths, unwraps MCP JSON, and pages text by characters.
The existing archive remains untouched. The spill recovery hint now names this
tool instead of requiring arbitrary Python. Its structured decode limit is
32 MiB; it does not change other tools' artifact/read limits.

## SQL continuity

`recall_calliope_sql` searches the existing activity ledger for this chat's SQL,
including specialist queries and workspace transformations. Receipt and batch
query names select exact statements; long SQL is paged without re-execution.
Verified subject and server-resolved chat provenance both scope the lookup.
Legacy records without verified subject/provenance are not exposed by guessing
from caller labels. The recorded outcome accompanies SQL: a successful tool
invocation alone is not proof that an approval-gated write was applied.

This recovers exact SQL after compression; it is not a new general-purpose
decision-memory system. Agent instructions point to receipts and existing
working notes rather than re-deriving lost expressions.

## Caching

Hutch applies automatic prompt caching after resolving a route to Claude on
OpenRouter, so opaque public aliases work. Existing explicit cache policies are
preserved. Other providers/models are unchanged, including overflow targets.
Set `prompt_caching: false` on an LLM definition to disable Hutch's default.
No reasoning-effort, model or compression-budget settings are changed.

The wire contract follows [OpenRouter's prompt caching documentation](https://openrouter.ai/docs/guides/best-practices/prompt-caching).
Cache hits and savings require live verification; request construction tests
alone cannot establish either.

### Conversation affinity and prefix diagnostics

For the exact configured `RVBBIT_CLOVER_OPENAI_BASE_URL`, Hermes sends an opaque
`x-hutch-cache-scope` derived from profile and logical session lineage. Compression
rotation keeps its scope; child sessions and profiles remain separate. It does
not hash changing instructions into that identity. Explicit caller cache keys or
session IDs take precedence. Tool declarations are consistently ordered without
dropping or rewriting their definitions.

After authenticating and resolving an OpenRouter route, Hutch scopes the generated
identity to the tenant and sends `session_id` plus `prompt_cache_key`. It starts
again from the original payload on overflow, so these fields are not injected
into strict/local fallback providers. The response's `x-hutch-cache-affinity`
reports `scoped`, `caller`, `none`, or `unsupported`; it contains no identity.
Cache identity does not make a changed prefix reusable. Calliope keeps stable
authoring instructions before changing business/Stage context.

Exports include `cache.json`. Usage records preserve missing provider cache
counters separately from reported zero. Metadata-only fingerprints diagnose
tool/system-prefix changes. Reported charges remain separate from hypothetical
savings and immutable pricing enrichment.

For a small live check, run inside configured Hermes:

```
python /opt/hermes/calliope/probe-prompt-cache.py \
  --models calliope-sol calliope-grok calliope-gemini \
  --output /tmp/cache-canary-unique-name
```

This makes two billable synthetic requests per selected alias, with bounded
output and timeout, through the actual Hermes transport and lifecycle telemetry.
It does not create a chat, execute tools, change saved model settings, or write
to the production run ledger. Cold cache misses are valid evidence; never infer
support or savings solely from the upstream model name.

## Validation

- `services/calliope/test_agent_result_delivery.py`: bounded receipts, lossless
  paging, owner/chat isolation, exact SQL recovery, and managed pin upgrades.
- `services/calliope/test_agent_mailbox.py`: delivery/await lifecycle and receipts.
- `docker/calliope/test-hermes-result-reader.py`: actual pinned runtime, nested
  MCP output, long lines, Unicode, file isolation, and tool availability.
- Hutch unit tests: resolved model detection, explicit policies, route isolation
  and caller message preservation.

These changes do not modify sealed benchmark baselines. Record deployed images
and configuration separately for comparisons with older trials.
