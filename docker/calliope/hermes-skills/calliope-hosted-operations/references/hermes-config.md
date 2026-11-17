# Hermes messaging and gateway configuration

Use this for Hermes platforms, profiles, gateway behavior, plugins, raw Hermes
cron, or another setting stored in Hermes configuration.

1. Search `rvbbit.hermes_docs_search()` with the administrator's intent. Read
   both the pinned upstream instructions and any `calliope_overlay` result.
2. Keep customer MCP servers out of Hermes. A request to connect a business
   service routes to the RVBBIT MCP registry; a request to chat with Calliope
   through Telegram, Discord, Slack, or another transport routes here.
3. Collect secret values only through a secure Stage input. Durable candidate
   YAML contains only environment references where Hermes supports them. The
   secret-binding ledger maps those names—or native Hermes messaging variable
   names such as `TELEGRAM_BOT_TOKEN`—to canonical credential references. No
   plaintext value belongs in YAML, revision history, chat, or tool output.
4. Generate a complete candidate from the current active revision. Never write
   fragments directly into the live file.
5. Validate with the pinned Hermes structure validator and the Calliope hosted
   invariant validator. Show errors and useful warnings with exact paths.
6. Apply through the Hermes configuration controller. It records the candidate,
   atomically replaces the file, restarts only the gateway, verifies health and
   a Warehouse round trip, and rolls back without agent involvement on failure.
7. Report the active revision and verification result. Do not claim success from
   a YAML parse alone.

The protected hosted invariants are the Clover model, authenticated API server,
Hindsight provider, multiplexed specialist profiles, and the two first-party
Warehouse MCP bridges. These constraints keep Calliope connected; they are not
a whitelist of permitted Hermes features.
