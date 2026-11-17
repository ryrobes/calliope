# A case that carries the work

Open **10 Case workspace** or `/?view=case`. Harbor Dental's rooftop-unit case
links an equipment record, motor order, purchase order, service visit and work log.
These are fictional records in the existing local SQLite business desk. Lab supplies
semantic decisions and hosts the isolated Studio runner; it does not hold these
local business records.

## Try the complete slice

1. Write a handoff and open **Return visit**. Enter an appointment draft while
   the motor is pending. Reviewing/confirming it is blocked until parts are reserved.
2. Open **Delivery** in the same case. Enter a delivery reference and one usable
   motor, then **Review delivery**. The review changes no inventory.
3. **Confirm & post delivery** writes an immutable receipt through the existing
   receiving command, allocates stock to waiting orders, and refreshes readiness.
   The case remains selected. Only the receipt editor is acknowledged; notes and
   the appointment draft survive.
4. Return to the appointment, review its before/after, then confirm. The existing
   dispatch command rechecks visit version and overlaps. The case also checks
   parts readiness in the same transaction at both preview and confirmation.
5. Save the handoff when ready. Receipt, appointment and case-note saves remain
   independent. No customer/technician notification is sent by these commands.

The case starts pinned. On the native Home canvas it is a live, draggable card;
**Open workspace** returns to its case. Heading drags persist in the existing
account-scoped Home layout. **Fit**, including the return-to-canvas action, includes
case cards. Web has a live pinned-case strip. Unpinning retains the case and data.
The card is polled every five seconds in native / three seconds in web; workspace
data uses the existing revision polling. This is not websocket event delivery.

## Stable tools and adaptive support

The supporting panel watches current SQL readiness, stock, equipment, perspective
and working notes. `hvac.case-support@2` is an inline policy identifier, not a policy
registry. Jev chooses equipment/history, parts/receipts or appointment support.
Those fragments bind existing SQL projections and expose existing case commands;
no generated SQL, new permissions or external commands come from inference.

Current SQL evidence supersedes historical notes about stock. The initial policy
overweighted an old waiting note; the revised policy was checked with real waiting
and ready payloads as well as forced outcomes. A separate first branch recognizes
an explicit request to investigate equipment/customer history; the single-choice
version over-prioritized readiness even when the user asked for investigation.
The two-stage policy selected parts, scheduling and requested history correctly
in live samples (0.36–1.74 seconds before cache hits). A semantic result remains a
suggestion: command authorization always checks the transaction's current state.

Primary notes, handoff and the tool selected by the user remain stable. Receipt
arrival does not navigate to a different editor. Hold/resume freezes the current
supporting panel in this session; selecting a named panel and **Keep these
preferences** persists an explicit override across restart. Equipment/customer
history can also remain visible independently of the adaptive panel. Working
perspective changes emphasis, not authorization. Preferences and pin flags belong
to this single-operator fixture database; a multi-user rollout needs principal-
scoped preferences and the existing production action authorization layer.

`acknowledgedDraftPaths` lets a command acknowledge `/receipt`, `/appointment`, or
`/notes` and `/handoff` without consuming sibling drafts. Both hosts reconcile
unrelated server changes and retain conflicting inputs. Existing conflict controls
gate review/confirmation until resolved. Stable IDs preserve mounted inputs.
Record panes are included in the reactive baseline so decisions cannot remove them.

## Shared context with Callie

**Copy context for Callie** exports `calliope.case-context/v1`: observed timestamp,
SQL revision, case facts, evidence/source mappings, available case actions, the
semantic receipt (or explicit override), and separately labeled `unsubmittedDraft`.
Web also opens a visible, selectable copy fallback.

Native **Discuss this case** opens/reuses a Lab topic associated with this case and
places the context in its composer. It preserves an existing topic draft and does
not send automatically. A working Lab sign-in is required. Callie can discuss the
snapshot after the user sends it; she does not gain a live connection to the local
SQL store or permission to execute its commands. Context snapshots are not live
subscriptions. Automatic proposal execution is outside this slice.

## Replay and verification

`scenarios/case-return-visit.json` runs 27 steps in structural and native modes:
unsaved handoff + appointment, changing support, an external receipt, unchanged
inputs, reviewed scheduling, partial acknowledgement, saved preferences/history.

New Studio commands:

```sh
calliope-ui-studio example case /opt/data/case.json
calliope-ui-studio --session case-test start --native
calliope-ui-studio --session case-test press view-case
calliope-ui-studio --session case-test fixture_event case_delivery
calliope-ui-studio --session case-test scenario /opt/data/case-scenario.json
```

Run the full scenario on a fresh session; `case_delivery` posts the remaining
quantity on the fixed fictional PO through reviewed commands. It has no arbitrary
SQL/action arguments. In native mode it waits for the actual host revision, testing
an external update rather than replacing the renderer state. Scenario
`assert_data` checks a JSON pointer in the resolved model. Forced decisions are
labeled `studio-forced`; inspect real receipts separately.

Backend tests cover no-write previews, exact-input/expiry checks, concurrent replay,
wrong PO, stale versions, transaction rollback, renewed shortage and new technician
conflicts between preview and confirmation. Host tests cover partial draft
acknowledgement. Live web checks cover actual focus/cursor preservation through an
external receipt. Native screenshots cover 1500 and 950 pixels. Mobile lifecycle,
native accessibility and actual device use remain untested.
