import assert from "node:assert/strict"
import test from "node:test"

import {
  isBackendConnectorCapability,
  isBackendConnectorManifest,
  joinCatalogToInstalled,
  probeConnector,
} from "../src/lib/rvbbit/capabilities.ts"

const connectorManifest = {
  api_version: "rvbbit.capability/v1",
  kind: "runtime_sidecar",
  name: "google_meet_brain_connector",
  runtime: { language: "brain_connector", health_path: "/health" },
  backend: { name: "gmeet_connector" },
  runtime_registration: { name: "gmeet_connector", language: "brain_connector" },
}

const connectorCatalog = {
  id: "integrations/google-meet-brain",
  kind: "runtime_sidecar",
  backend_name: "gmeet_connector",
  runtime_name: "gmeet_connector",
  runtime_language: "brain_connector",
  manifest: connectorManifest,
}

test("Brain connector sidecars join through rvbbit.backends", () => {
  const backend = {
    name: "gmeet_connector",
    n_calls: 0,
    n_errors: 0,
    deployment_callable: true,
    deployment_serving_status: "ready",
  }
  const joined = joinCatalogToInstalled([connectorCatalog], [backend], [])

  assert.equal(isBackendConnectorCapability(connectorCatalog), true)
  assert.equal(isBackendConnectorManifest(connectorManifest), true)
  assert.equal(joined.entries[0].installed, backend)
  assert.equal(joined.entries[0].installedRuntime, null)
  assert.equal(joined.entries[0].flags.registered, true)
})

test("ordinary runtime sidecars continue to join through runtime registries", () => {
  const catalog = {
    id: "data/fletch-data-mover",
    kind: "runtime_sidecar",
    backend_name: null,
    runtime_name: "fletch_data_mover",
    runtime_language: "data_mover",
  }
  const runtime = { name: "fletch_data_mover", status: "ready" }
  const joined = joinCatalogToInstalled([catalog], [], [runtime])

  assert.equal(isBackendConnectorCapability(catalog), false)
  assert.equal(joined.entries[0].installed, null)
  assert.equal(joined.entries[0].installedRuntime, runtime)
  assert.equal(joined.entries[0].flags.runtimeReady, true)
})

test("connector probes use the server-side health proxy", async () => {
  const originalFetch = globalThis.fetch
  let request
  globalThis.fetch = async (url, init) => {
    request = { url, init }
    return new Response(
      JSON.stringify({
        ok: true,
        backend: "gmeet_connector",
        targetKind: "connector",
        transport: "connector_health",
        latency_ms: 12,
        output: { ok: true },
        outputType: "object",
        outputSize: 1,
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    )
  }
  try {
    const result = await probeConnector("pilot", connectorManifest)
    assert.equal(request.url, "/api/rvbbit/capabilities/probe-connector")
    assert.deepEqual(JSON.parse(request.init.body), {
      connectionId: "pilot",
      backendName: "gmeet_connector",
    })
    assert.equal(result.ok, true)
    assert.equal(result.targetKind, "connector")
  } finally {
    globalThis.fetch = originalFetch
  }
})
