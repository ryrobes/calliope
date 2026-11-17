# Session context memory

Three layers have separate jobs and triggers:

| Layer | Contents | Trigger |
| --- | --- | --- |
| Working context | Current request, recent conversation, compact findings and recovery IDs | Every model call; existing pressure/idle compaction policy |
| Session context | Original conversation, tool arguments, results and searchable chunks | Index after durable writes; retrieve when the agent needs earlier evidence |
| Long-term memory | Existing Hindsight/business memory and durable lessons | Existing lifecycle and explicit recall; no automatic promotion of session chunks |

`session_context` is bound by the running agent to its native session ID and
profile's existing SessionDB. The model cannot override session, profile, agent,
or database path. Specialists remain separate. Shared team recall would need an
explicit authorized scope; it is not implicit here.

```json
{"query":"Which time-of-day categories did we derive from witness reports?"}
{"tool_call_id":"toolu_example","pointer":"/results/bf_profile/rows/0/n"}
{"message_id":42,"offset":6000,"limit":6000}
```

Search combines complete stored text matches with dense similarity. Jev scores
the semantic shortlist for relevance; it does not synthesize replacement answers.
Responses retain source message/call IDs, timestamps, excerpts, scores, paging,
and index/judge status. Direct reads do not call inference. Text search remains
usable when embeddings or Jev are unavailable. A cold index is explicitly reported.

Historical records are untrusted data. Only user/assistant content and tool
calls/results are indexed, not hidden reasoning or media bytes. Secrets are
redacted before embedding/ranking. Native history remains authoritative for
visibility: rewound/deleted records are not served from a stale index, while
compaction archives remain eligible.

Semantic candidates are reconciled against native history before they consume a
shortlist slot or reach Jev, and checked again before being returned. A superseded
row ID may resolve to an eligible copy with the same scoped tool-call identity;
user/assistant copies must match both content and tool arguments exactly. Missing
sources and sources without an eligible copy remain hidden. This also repairs
recall from existing indexes without re-embedding duplicate compaction copies.

## Storage and configuration

`<profile home>/session-context-v1.sqlite` is profile-local. `originals` preserves
large text results before native spillover turns them into previews, independently
of spillover's 24-hour cleanup. `chunks` holds redacted overlapping text, source
record IDs, vectors, and encoder versions. `cursors` tracks ingestion. Duplicate
compaction copies do not re-embed identical chunks.

The transcript is the durable backlog. Two workers batch up to 16 chunks per
request, yielding between bounded batches. Profile, credential and accounting
contexts propagate. Indexing never awaits inference on the agent path. Failed
indexing backs off; pending work survives restart and retries on a subsequent
flush or recall. No startup-wide backfill is performed.

Defaults also apply to existing profiles without this block:

```yaml
session_context:
  enabled: true
  embedding_model: embed
  judge: true
```

The backend/credentials are the managed Hutch encoder already used by Hindsight
and the existing `decisions`/Jev endpoint. Disabling this block's `enabled` turns
off semantic indexing/ranking; archival, direct and text retrieval still work.
Encoder-version changes invalidate incompatible vectors and schedule reindexing.

This is bounded session recall, not arbitrary SQL across users' history. Search
reports `scan_limited` when its native-match cap is reached; refine the query or
read an exact ID. Previously expired spillover or upstream-truncated output cannot
be reconstructed. Existing archived transcripts can be indexed on recall.

## Compaction and measurements

Pruning and lean-tail demotion retain bounded actual findings and call IDs instead
of character-count-only stubs. Small results survive if a digest would not shrink
them. Repeated pruning recognizes retained evidence. Model-backed and deterministic
fallback summaries include recovery guidance. Main-model-first compaction and
Luna fallback remain unchanged.

SQL batch digests reserve a status, row count when supplied, truncation flag and
escaped JSON-pointer address for every named result before spending space on
errors and representative rows. Errors take priority over samples; repeated
engine/storage metadata and SQL arguments do not consume that budget. Ordinary
digests remain at 2,400 characters; batch budgets scale with their member count,
up to 12,000 characters. If even the inventory cannot fit (for example, unusually
long query names), the digest explicitly directs recall of the complete batch
instead of silently dropping members. Original results and stage data are unchanged.

Compaction checkpoints explicitly describe an earlier segment, rather than the
current state of the whole session. The summarizer receives bounded, redacted
quotes from recent real user requests plus an inventory of newer retained tool
results. Required technologies and corrections therefore remain visible even
when the small segment being summarized contains only early discovery. A
deterministic scope label is attached to both LLM and fallback summaries; absent
evidence in the segment cannot establish that an action never occurred. Tool
receipt counts establish that calls returned, not that they succeeded.

Embedding and ranking produce `session_context.embedding` and
`session_context.rerank` auxiliary ledger receipts: model, latency, sizes and usage
when supplied. Unknown prices remain unknown. Benchmark exports include scoped
`session-context-index.jsonl` coverage and `session-context-originals.jsonl` source
records alongside native tapes.

The same repair aligns both executor guards with MCP budgets (e.g. 660 seconds
plus dispatch grace for workspace SQL), reports deadline cancellation truthfully,
and marks failed workspace receipts as errors despite successful MCP transport.
