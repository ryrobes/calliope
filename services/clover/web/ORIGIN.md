# Clover Web origin

Imported on 2026-10-11 from the existing `clover-zoo/web` working tree, whose
repository HEAD was be4f9faa8896723ae4bf9730d8068bb8f9be6c18. The web directory
was not yet tracked there; this monorepo copy is the versioned release source.

Retains Firecrawl HTML extraction and anydoc (see THIRD_PARTY_NOTICES.md),
public-IP/DNS-pinned fetching, HTTP validators and the SQL Predict contract.
The self-hosted v0.3 adds an internal, bounded audio fetch for transcription,
disables environment HTTP proxies so they cannot bypass DNS pinning, and uses
container networking rather than host networking. It stores no source data.
