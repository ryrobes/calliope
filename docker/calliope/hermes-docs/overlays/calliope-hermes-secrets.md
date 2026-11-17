---
title: Secrets in hosted Hermes configuration
kind: calliope_overlay
priority: authoritative
---

# Secrets in hosted Hermes configuration

Calliope may ask a user for a platform token, application secret, signing key,
or OAuth value through a secure Stage input. The plaintext is written directly
to the encrypted canonical RVBBIT credential store. It must not be copied into
chat, a tool result, a configuration diff, Postgres revision JSON, or a log.

Durable Hermes YAML contains a credential reference. The gateway supervisor
resolves the reference only while constructing the gateway process environment.
Diagnostic and history surfaces show the reference and credential status, never
the value.

Changing a secret creates a new credential version without rewriting unrelated
Hermes configuration. Verification should name the integration tested and the
credential reference used, but must not return authorization headers or process
environment values.
