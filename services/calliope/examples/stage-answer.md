# Stage answer primitive

`publish_answer` explicitly publishes one concise analytical takeaway to the
current chat's Stage. It uses native theme typography, spans every Stage column,
and wraps to its natural height. Ordinary replies do not automatically publish it.

Example Playbook instruction:

> After answering the analytical question and synthesizing the relevant team
> results, call `publish_answer` with one concise, evidence-supported takeaway.
> Keep essential qualifications in that sentence. Add a short caveat when useful,
> and attach the supporting Stage surface IDs when available. Keep the fuller
> explanation in chat. Do not publish interim progress or conversational replies.

Example call:

```json
{
  "answer": "September’s enrollment decline came primarily from fewer paid leads, rather than weaker conversion.",
  "caveat": "September is incomplete; comparison uses matching elapsed days."
}
```

- `answer`: plain text, up to 320 characters. Aim for one sentence.
- `caveat`: optional plain text, up to 240 characters; no Markdown or HTML styling.
- `evidence_refs`: optional list of up to eight existing supporting Stage surface
  UUIDs in this chat. Both bare UUIDs and `surface:UUID` are accepted. The renderer
  links their saved titles; arbitrary URLs and other chats' surfaces are rejected.
- `replaces_surface_id`: optional latest answer surface UUID for a revision in a
  later turn. Without this field, calls in one turn revise that turn's answer;
  a new turn starts a separate answer. Retries of the current content are reused.

The tool returns `surface_id`. Revisions are immutable; only the latest block in
each lineage appears on the Stage, with earlier wording accessible inside it.
Selected answers carry their sentence, caveat, and evidence references into the
next chat turn. Publication records an interpretation, not a guarantee of its
correctness, and does not initiate another chat turn or refresh its data.

No additional model call, file watcher, browser iframe, or scheduled task is used.
