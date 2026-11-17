# Generated specialist readiness

A generated definition is separate from a runnable Hermes profile. Migration
0412 prevents registration from advertising an agent before its profile exists.

- Definitions remain in `generated_specialists` while the roster entry is
  `provisioning`. They are absent from delegation and chat autocomplete.
- The Hermes supervisor checks for missing generated profiles every minute.
  It waits for an explicitly idle gateway, materializes profiles, reconciles
  the multiplex allowlist, and reloads through its verified apply/rollback path.
  Resolved credential bindings survive that reload. It checks idle again after
  materialization; this is an idle check, not an atomic admission/drain barrier.
- The factory probes each generated profile's authenticated sessions endpoint
  every 30 seconds between crafting passes. Only a successful response marks
  the profile ready and publishes the roster entry. This probe uses no inference
  and does not create sessions.
- A confirmed unconfigured-profile 404 returns the agent to provisioning.
  Network failures do not disable previously verified agents. Manual pauses and
  disables are preserved. Pending definitions are not repeatedly minted.

Deploy migration 0412 before the updated Calliope server and Hermes images.
The migration is independent of 0411. Existing generated agents briefly enter
provisioning until checked. No definitions, versions, memories, or threads are
deleted. The additive PostgreSQL image embeds the migration for future installs.
