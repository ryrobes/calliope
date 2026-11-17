import assert from "node:assert/strict"
import yaml from "js-yaml"

import {
  defaultKnobs,
  renderManifest,
} from "../src/lib/rvbbit/capabilities.ts"

const manifest = {
  api_version: "rvbbit.capability/v1",
  kind: "runtime_sidecar",
  name: "test_brain_connector",
  title: "Test Brain Connector",
  description: "Renderer contract fixture.",
  capability_role: "brain_connector",
  source: { provider: "test" },
  runtime: {
    mode: "image",
    image: "example.invalid/test-brain-connector:latest",
    language: "brain_connector",
    port: 8080,
  },
  backend: {
    name: "test_brain_connector",
    transport: "rvbbit",
    auth_env: "TEST_CONNECTOR_TOKEN",
  },
  runtime_registration: {
    name: "test_brain_connector",
    language: "brain_connector",
    endpoint_path: "/sync",
    auth_env: "TEST_CONNECTOR_TOKEN",
  },
  brain_source: {
    label: "Test Brain",
    kind: "test_documents",
    creds_ref: "TEST_CONNECTOR_TOKEN",
    folder_prefix: "/test",
    enabled: true,
    config: { provider: "test" },
  },
  resources: { gpu: { required: false } },
  smoke: { sql: ["SELECT true;"] },
}

const rendered = renderManifest(manifest, defaultKnobs(manifest))
const staged = yaml.load(rendered.manifestYaml)

assert.equal(staged.brain_source.label, "Test Brain")
assert.equal(staged.brain_source.folder_prefix, "/test")
assert.deepEqual(staged.smoke.sql, ["SELECT true;"])
assert.equal(staged.resources.gpu.required, false)
assert.match(rendered.registerSql, /rvbbit\.register_backend\(/)
assert.match(rendered.registerSql, /rvbbit\.brain_configure_source\(/)
assert.match(rendered.registerSql, /p_label\s*=>\s*'Test Brain'/)
assert.doesNotMatch(rendered.registerSql, /register_python_runtime/)

console.log("capability renderer brain-connector contract: ok")
