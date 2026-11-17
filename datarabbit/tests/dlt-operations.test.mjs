import assert from "node:assert/strict"
import test from "node:test"
import { fetchDltOperations, fetchWorkspaceJobRuns, controlWorkspaceJob } from "../src/lib/rvbbit/dlt-operations.ts"

function mockQueries(t, answer) {
  const queries = []
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    const body = JSON.parse(init.body)
    queries.push(body)
    return { json: async () => answer(body.sql) }
  })
  return queries
}
const ok = (rows) => ({ ok: true, rows, columns: [] })

test("SQL-only databases show configured jobs without requiring a mirror installation or a first run", async (t) => {
  const queries = mockQueries(t, (sql) => sql.includes("to_regclass")
    ? ok([{ mirror_ready: false, workspace_ready: true }])
    : ok([{ id: "00000000-0000-0000-0000-000000000001", name: "ads", enabled: true, last_status: null }]))
  const result = await fetchDltOperations("active-db")
  assert.equal(result.ready, true)
  assert.equal(result.workspaceJobs[0].name, "ads")
  assert.deepEqual(result.runs, [])
  assert.equal(queries.length, 2)
  assert.ok(queries.every((q) => q.connectionId === "active-db" && q.readOnly && q.poolLane === "meta"))
  assert.ok(!queries.some((q) => q.sql.includes("FROM rvbbit.mirror_runs")))
})

test("older mirror-only installations still load without querying missing SQL job tables", async (t) => {
  const queries = mockQueries(t, (sql) => sql.includes("to_regclass") ? ok([{ mirror_ready: true, workspace_ready: false }]) : ok([]))
  const result = await fetchDltOperations("legacy")
  assert.equal(result.ready, true)
  assert.deepEqual(result.workspaceJobs, [])
  assert.equal(queries.length, 4)
  assert.ok(!queries.slice(1).some((q) => q.sql.includes("calliope_workspace")))
})

test("one failed source leaves other source data available and reports the actual error", async (t) => {
  mockQueries(t, (sql) => sql.includes("to_regclass") ? ok([{ mirror_ready: true, workspace_ready: true }])
    : sql.includes("FROM rvbbit.mirror_runs") ? { ok: false, error: "mirror history unavailable" }
      : sql.includes("FROM rvbbit.calliope_workspace_jobs") ? ok([{ name: "ads" }]) : ok([]))
  const result = await fetchDltOperations("db")
  assert.equal(result.workspaceJobs.length, 1)
  assert.match(result.error, /mirror history unavailable/)
})

test("probe failure is exposed as an error rather than a missing installation", async (t) => {
  mockQueries(t, () => ({ ok: false, error: "permission denied" }))
  assert.match((await fetchDltOperations("db")).error, /permission denied/)
})

test("job history includes SQL receipt results and rejects invalid identifiers before query", async (t) => {
  const queries = mockQueries(t, () => ok([{ status: "complete", result: { statements: [{ rows: [{ n: 2 }] }] } }]))
  await assert.rejects(fetchWorkspaceJobRuns("db", "not-a-uuid' OR true"), /Invalid/)
  assert.equal(queries.length, 0)
  const runs = await fetchWorkspaceJobRuns("db", "00000000-0000-0000-0000-000000000001")
  assert.equal(runs[0].result.statements[0].rows[0].n, 2)
  assert.match(queries[0].sql, /LEFT JOIN rvbbit.calliope_action_runs/)
})

test("controls use the native service, preserve error messages, and never retry", async (t) => {
  let calls = 0
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls++
    assert.equal(url, "/api/rvbbit/workspace-jobs/control")
    assert.deepEqual(JSON.parse(init.body), { jobId: "job", operation: "pause" })
    return { ok: false, json: async () => ({ error: { message: "SQL job not found for your account" } }) }
  })
  await assert.rejects(controlWorkspaceJob("job", "pause"), /your account/)
  assert.equal(calls, 1)
})
