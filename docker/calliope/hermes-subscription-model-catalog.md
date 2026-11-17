# Subscription model discovery backport

Reviewed upstream Hermes revision: `d9a97ae517774b11a263124fa92812393ab906c2`
([source](https://github.com/NousResearch/hermes-agent/tree/d9a97ae517774b11a263124fa92812393ab906c2)).

`hermes-subscription-model-catalog.patch` carries the shared upstream catalog
version negotiation (`99.0.0` first, `0.0.0` on empty/non-200 responses), GPT-6
Sol/Luna picker fallbacks and compatibility entries, plus Codex context and effort
metadata for GPT-6 Sol/Luna and GPT-6.1 Sol. Live model IDs pass through without
a static allowlist; only provider-hidden entries are excluded. Both the picker
and context-window probe use the same catalog request. Upstream code lives in
`hermes_cli/codex_models.py`, `agent/model_metadata.py`, and
`agent/reasoning_effort.py`. The large-context variants remain opt-in and live
maximums still cap them. Astra still requires account-scoped discovery.

Do not replace those upstream files wholesale: newer revisions also change HTTP,
credential routing, caching, and imports which require a broader Hermes upgrade.
This patch preserves the pinned runtime and the existing Calliope overlays.

The Claude plugin is no longer backported file by file: the whole plugin is
vendored at the revision recorded in `hermes-plugins/source.json` (0.3.3 from
NousResearch/hermes-official-plugins), which includes this discovery. It accepts
new resolved model IDs from the installed CLI, preserves its explicit `[1m]`
choices and usage-credit notes, and bounds unfamiliar plain routes to 200K.

Refresh follows Calliope -> supervisor -> Hermes `/api/model/options?refresh=true`.
Codex asks its account catalog, then uses local/default compatibility entries if
needed. Claude uses the CLI initialize handshake, merged with pinned entries.
A refreshed inventory is not proof that an account can invoke every fallback
model; the provider enforces access when a request is made.
