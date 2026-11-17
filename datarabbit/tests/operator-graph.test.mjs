import assert from "node:assert/strict"
import test from "node:test"
import { buildOperatorGraph, inputRefsOf } from "../src/lib/rvbbit/operator-graph.ts"

// The listing_verdict shape: a decision whose state array nests the photo in an
// image part, an LLM that reads the description, and SQL that reads both steps.
const look = {
  name: "look", kind: "specialist", specialist: "decisions",
  inputs: {
    model: "typesafe/jev-1.14",
    state: ["Seller's description: {{inputs.description}}",
      { type: "image_url", image_url: { url: "{{inputs.photo}}" } }],
    questions: { honest: { type: "noul", instructions: "Does the photo back it up?" } },
  },
}
const verdict = { name: "verdict", kind: "llm", user: "Claim: {{inputs.description}} Score: {{steps.look.output.answers.honest.noul}}" }
const result = { name: "result", kind: "sql", sql: "SELECT $1, $2", params: ["{{steps.look.output.answers}}", "{{steps.verdict.output}}"] }
const op = { name: "listing_verdict", arg_names: ["description", "photo"], steps: [look, verdict, result] }

const edges = (graph) => graph.edges.map((e) => `${e.from}->${e.to}`).sort()

test("an input referenced inside a nested state array still wires to its step", () => {
  assert.deepEqual(inputRefsOf(look).sort(), ["description", "photo"])
  const wired = edges(buildOperatorGraph(op))
  assert.ok(wired.includes("input-1->step-0"), "photo feeds the decision through its image part")
  assert.ok(wired.includes("input-0->step-0") && wired.includes("input-0->step-1"))
  assert.ok(wired.includes("step-0->step-1") && wired.includes("step-0->step-2") && wired.includes("step-1->step-2"))
})

test("plain string inputs keep working, and non-string scalars are ignored", () => {
  const step = { name: "s", kind: "specialist", inputs: { text: "{{ inputs.body }}", n: 3, flag: true, none: null } }
  assert.deepEqual(inputRefsOf(step), ["body"])
})
