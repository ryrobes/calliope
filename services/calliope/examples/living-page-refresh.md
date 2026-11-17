# Living Pages: manual Hermes refresh

Pages now live in the modern Calliope UI. The Pages button opens a searchable
library; opening any Page creates/reuses its dedicated private notebook without
inference. Old `?page=UUID` links also open this notebook. The document is always
first on Stage, expanded at natural height and at most 760px wide; ordinary
artifacts pack beside it. The Page title becomes the browser tab title.

The Stage offers Refresh, Ask Callie to change this, the standing brief, current
source excerpts, revision history with access-checked bodies and sources, and
Archive. Reading a Page never refreshes it. During a refresh the prior revision
remains readable. History is read-only; unavailable source access hides the body.
A refresh initiated elsewhere links back to its originating conversation.

Floor labels Page threads with a Page chip and a distinct theme-aware accent in
kanban, grid and list. Pages rest in Standing and move to In Motion during actual
work, rather than following the recently-active chat timeout. The kind filter is
applied before pagination. Empty notebooks remain visible. Floor also offers a
Pages filter shortcut and + Page to prepare a request in its normal intake.
An existing Page suggested in the greeting opens its notebook directly and is
labeled Refresh on demand, rather than starting another conversation.

Make a Page on a completed answer prepares an editable request to Callie. New
Page in the library starts normal intake. Neither opens the old creation form.
Callie has the following private, caller-scoped tools:

- `list_calliope_pages(query)` and `read_calliope_page(page_id, version?)`.
- `create_calliope_page(title, question, standing_brief?, anchor_label?)` creates
  the definition and notebook and returns the initial evidence contract.
- `update_calliope_page(page_id, expected_version, ...)` changes the definition
  using optimistic concurrency. It does not silently regenerate the document.
- `refresh_calliope_page(page_id)` returns fresh cited evidence in the current
  trusted turn; it does not spawn another agent. Finish with `finish_page_refresh`.
- `archive_calliope_page(page_id, expected_version)` soft-archives the Page and
  notebook, preserving revisions and refusing during an active refresh.

Creation retries for the same title in one turn reuse the Page. Refresh retries
reuse that Page's run for the initiating turn, including its terminal result.
Source-turn identity is supplied by trusted Hermes transport, never model input.
The Page keeps its own notebook even when authored in another conversation. That
originating turn receives an Open Page action. Missing evidence leaves a findable
Page and a failed refresh rather than losing the definition or fabricating a body.
Browser Refresh still starts a normal owner Hermes turn in the Page notebook.

The durable Page contains its question, anchor, source filters, standing brief,
definition version, current revision and notebook link. Normal follow-up chat
receives its current readable document and standing brief as context. Content
changes must pass the cited refresh contract; a conversational answer does not
silently overwrite the document.

`read_page_refresh(run_id)` supplies a bounded snapshot of caller-visible Brain
evidence, the previous readable document, the brief and the observation date.
`finish_page_refresh(run_id, outcome, summary, body)` records one of:

- `updated`: save the complete cited Markdown as an immutable revision.
- `unchanged`: record a successful verification without creating a revision.
- `incomplete`: preserve the existing revision and record why checking failed.

The refresh turn first invokes the existing `mcp__Warehouse__get_tool_help`
through Hermes's `tool_call`. This requests the two contract schemas and sends
MCP's tool-list-change notification, refreshing Hermes's callable registry after
a service-only Warehouse deployment. Native `tool_search` / `tool_describe` alone
only inspect that registry and cannot discover tools absent from a stale copy.
The subsequent contract calls use their full `mcp__Warehouse__` names. If catalog
bootstrap still fails, the turn must stop rather than try the legacy SQL writer.

Both tools require the exact trusted initiating notebook/turn. Saving checks
source visibility, document hashes, the brief version and the base revision.
Duplicate completion cannot create another revision. A terminal turn without a
save is incomplete, not a successful refresh; read-time reconciliation recovers
missed completion hooks and abandoned launches. The existing Hermes turn broker
continues execution when the user leaves the notebook.

Work Ledger adds `page_revision` and `page_verified`. Updated Pages inherit the
operator's existing document-credit estimate on installation. Verified-current
Pages count with zero estimated minutes unless the operator configures a credit.
Failed runs earn no credit. Browser refresh prompts start with `[Page refresh]`, so the existing ledger does
not also count them as business questions. A Page created in ordinary chat retains
the initiating business question and has a separate Page-work receipt. Run IDs link to
the underlying notebook and turn receipts; these estimates are not execution time.

Source scope in this slice remains the existing caller-visible Brain corpus,
including connected tickets, documents, meetings, messages and projects. The
writer now uses normal Calliope/Hermes routing, not the legacy Clover SQL writer.
The initial evidence snapshot bounds the synthesis; insufficient coverage should
produce an incomplete outcome rather than unsupported claims. Broader source
adapters and iterative evidence expansion can follow independently.

There is no refresh-on-open, scheduler or automatic Floor topic suggestion. The
legacy modal and SQL writer remain for compatibility, but modern entry points no
longer depend on them. Migrations 0427 and 0428 ship in both the extension registry
and service compatibility bootstrap; service-only builds must include both files.

## Local validation

Run `test_page_refresh.py`, `test_page_tools.py`, `test_page_floor.py`,
`test_calliope_pages.py`, `test_living_page_stage.py`, the Floor data/browser tests
and the Stage packing/reconciliation tests with the Calliope test
Python environment. PostgreSQL cases require `IMAGE_DELIVERY_TEST_DSN` pointing
at an **empty disposable database**; the fixture creates/drops the rvbbit schema.
Floor database tests use `FLOOR_TEST_DSN` on the same disposable database; run
the suites sequentially with `PGTZ=UTC`. The browser fixture uses local Chromium with mocked APIs; no paid inference or
live install is involved.

`scripts/probe-hermes-page-tools.py` runs inside the Hermes Python environment
without inference. It checks the real Warehouse MCP endpoint, removes the Page
tools from its own temporary registry, and verifies that catalog help restores
them. It leaves the gateway process and its on-disk schema cache untouched. Use
this alongside server registration tests when shipping new contract tools.

Acceptance on a live install after deployment: create a Page around a known
project, inspect the first cited revision, save an editorial preference in its
standing brief, change a connected source, and refresh. Confirm that the new
revision preserves the preference, describes the actual change, and appears once
in the Work Ledger. Also verify an unchanged refresh and an interrupted refresh.
