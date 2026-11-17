# Clover structured extraction

`rvbbit.clover_extract_json` turns text into a typed JSON object under an
explicit contract. The SQL API names the capability rather than its current
model: Clover can change or complement the extraction specialist without
breaking stored queries.

```sql
SELECT rvbbit.clover_extract_json(
  'Invoice INV-42 is due 2026-09-30. Total: USD 1,280.50.',
  '{
    "type": "object",
    "additionalProperties": false,
    "required": ["invoice_id", "due_date", "amount"],
    "properties": {
      "invoice_id": {"type": "string"},
      "due_date": {"type": ["string", "null"], "format": "date"},
      "amount": {
        "type": "object",
        "additionalProperties": false,
        "required": ["value", "currency", "unit"],
        "properties": {
          "value": {"type": "number"},
          "currency": {"type": ["string", "null"]},
          "unit": {"type": ["string", "null"]}
        }
      }
    }
  }'::jsonb
);
```

The result is an envelope, not a naked model answer:

```json
{
  "status": "ok",
  "data": {
    "invoice_id": "INV-42",
    "due_date": "2026-09-30",
    "amount": {"value": 1280.50, "currency": "USD", "unit": null}
  },
  "validation": {"valid": true, "status": "ok", "errors": []},
  "attempts": [{"route": "specialist", "valid": true, "receipt": {}}],
  "provenance": {
    "route": "specialist",
    "cache_hit": false,
    "input_hash_md5": "...",
    "contract_hash_md5": "...",
    "source_chars": 58
  }
}
```

## Execution model

The default `auto` route is intentionally layered:

1. Parse known transport or document structure deterministically before calling
   this function.
2. Run constrained schema-to-JSON extraction through the configured extraction
   specialist.
3. Validate exact types, required fields, enums, unknown properties, formats,
   ranges, and cross-field invariants in PostgreSQL.
4. If the specialist is unavailable or its output is rejected, give the text,
   contract, rejected candidate, and validation errors to the configured Clover
   generalist for one recovery attempt.
5. Cache the result only after deterministic validation succeeds. Governed
   read-only SQL may consume an existing cache entry, but it never mutates hit
   counters or persists a miss inside the analytical transaction.

Structural validity is not semantic truth. For fields involving attribution,
negation, entailment, or other ambiguity, add `semantic_rules`. The configured
generalist audits the structurally valid candidate against the source. A failed
audit triggers a corrected extraction and a second audit before the row can be
cached. This costs extra model calls, so it is explicit rather than imposed on
simple invoice IDs, dates, or other mechanically checkable fields.

Transport failure is not a negative semantic verdict. A failed audit is retried
once by default before the operation stops with `semantic_error`; recovery runs
only after an actual reviewer rejection. Every failed attempt carries its raw
error plus normalized `error_class`, `upstream_status`, `retryable`, and
`receipt_id` fields. Its durable receipt may be marked pending until Calliope
flushes the read-only receipt queue after the query.

```sql
SELECT rvbbit.clover_extract_json(
  'FYI: maintenance completed. We will notify the customer.',
  :'contract'::jsonb,
  '{
    "semantic_rules": [
      "action_requested is true only for an action explicitly requested of the recipient",
      "sender commitments do not count as recipient actions"
    ]
  }'::jsonb
);
```

Invalid contracts and policies return before any model call. Rejected candidates
are omitted by default, but their ordinary semantic receipts retain the raw
attempt audit trail. Set `include_rejected` only for a controlled review surface.

## Contract subset

Contracts use a strict, recursive JSON Schema-shaped subset. Every node requires
an explicit `type`; the root type must be exactly `object`.

Supported keywords are:

- `type`: `object`, `array`, `string`, `number`, `integer`, `boolean`, `null`, or
  an array of those types
- `description`
- object: `properties`, `required`, `additionalProperties` as a boolean
- array: `items`
- scalar: `enum`
- string: `minLength`, `maxLength`, `pattern`, `format` (`date` or `date-time`)
- numeric: `minimum`, `maximum`
- `x-invariants` for small cross-field rules

Unknown keywords are rejected so a caller cannot accidentally rely on a
constraint that the runtime does not enforce.

An invariant contains an `if` condition and a required `then` condition. Paths
may be dot-separated strings or arrays of path components. Operators are `eq`,
`neq`, `present`, `absent`, and `in`.

```json
{
  "x-invariants": [{
    "if": {"path": "action_requested", "op": "eq", "value": true},
    "then": {"path": "recipient_action_text", "op": "present"},
    "message": "recipient_action_text is required when action_requested is true"
  }]
}
```

Keep different meanings in different fields. For example, recipient requests and
sender commitments should not share one `action_text`, and monetary `currency`
should remain separate from physical `unit` values such as MW or MWh. Likewise,
dominant intent and the presence of an action are orthogonal fields unless the
business contract explicitly says otherwise.

## Policy

The optional third argument accepts:

- `route`: `auto` (default), `specialist`, or `generalist`
- `instructions`: extraction guidance, up to 32,768 characters
- `semantic_rules`: 1 to 32 evidence rules for generalist audit and repair;
  omitted by default to retain the specialist's fast single-call path
- `semantic_review_max_attempts`: bounded same-review attempts for retryable
  transport failures, default `2` and range `1` to `3`
- `cache`: validated-success cache, default `true`
- `include_rejected`: expose rejected model data, default `false`
- `fallback_on_error`: use the generalist after specialist failure, default `true`
- `fallback_on_rejection`: use the generalist after validation failure, default
  `true`
- `max_input_chars`: input limit, default 131,072 and maximum 1,048,576
- `max_attempts`: durable-job transport retry limit, default 3 and maximum 10

`route = 'generalist'` is useful for comparison and diagnostics. It is not a
different public contract.

`provenance.policy_hash_md5` and `provenance.cache_key_version` identify the
effective policy and cache-key contract. `validation.structural_status` and
`validation.semantic_status` keep a valid JSON shape distinct from an audited
semantic verdict. Routes describe the complete path, including
`specialist_semantic_review_error` and
`specialist_semantic_recovery_unverified` rather than reporting only the first
model used.

## Durable row jobs

For a table-sized workload, enqueue stable item IDs and process one row per
transaction:

```sql
SELECT rvbbit.clover_extract_enqueue(
  '[
    {"id":"ticket-41","text":"Please reset account 7 by Friday."},
    {"id":"ticket-42","text":"FYI: maintenance completed successfully."}
  ]'::jsonb,
  :'contract'::jsonb,
  '{"max_attempts":3}'::jsonb,
  'support-ticket-v1'
) AS job_id;

SELECT rvbbit.clover_extract_step(:'job_id'::uuid, 'worker-a');
SELECT rvbbit.clover_extract_job(:'job_id'::uuid, true);
```

Call `clover_extract_step` repeatedly until the job is complete. Multiple database
sessions can drain the same job because claims use `FOR UPDATE SKIP LOCKED`.
Successful rows commit independently, so a client timeout does not discard prior
work. Rejected rows become `review`; exhausted transport failures become `failed`.

```sql
SELECT rvbbit.clover_extract_retry(:'job_id'::uuid, ARRAY['ticket-42']);
```

The job status API returns results, validation, and provenance but deliberately
does not echo source text. The source remains in the customer database to support
resumption. Job keys are idempotent: reusing a key with different inputs,
contract, or policy raises an error instead of silently mixing runs.

## Choosing the older primitive

`rvbbit.clover_llm_extract` remains useful for informal, freeform JSON where a
plain-English shape is sufficient. Use `clover_extract_json` for production
columns, enums, obligations, amounts, dates, action flags, and any result that
must be validated, receipted, retried, or resumed.
