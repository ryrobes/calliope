import { access, chmod, copyFile, mkdir, mkdtemp, rm } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { _electron as electron } from "playwright-core"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const referenceUrl = process.env.DATARABBIT_REFERENCE_URL ?? "http://localhost:3000"
const referenceRegistry =
  process.env.DATARABBIT_REFERENCE_CONNECTIONS ??
  path.join(os.homedir(), ".config", "rvbbit-lens", "connections.json")
const appBundle =
  process.env.DATARABBIT_APP_BUNDLE ??
  path.join(root, "dist", "mac-arm64", "Data Rabbit.app")
const executable = path.join(appBundle, "Contents", "MacOS", "Data Rabbit")
const outputDir = path.join(root, "test-results")
const screenshotPath = path.join(outputDir, "rvbbit-reference-native.png")

await access(executable)
await access(referenceRegistry)
await mkdir(outputDir, { recursive: true })
const testHome = await mkdtemp(path.join(os.tmpdir(), "datarabbit-rvbbit-smoke-"))
const testConfig = path.join(testHome, "config")
await mkdir(testConfig, { recursive: true, mode: 0o700 })
const testRegistry = path.join(testConfig, "connections.json")
await copyFile(referenceRegistry, testRegistry)
await chmod(testRegistry, 0o600)

let electronApp
let appUrl

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

const diagnosticEndpoints = [
  ["finderVitals", "/api/db/finder-vitals"],
  ["pgStats", "/api/db/pg-stats"],
  ["systemHealth", "/api/db/system-health"],
  ["lockExplorer", "/api/db/lock-explorer"],
  ["mvccExplorer", "/api/db/mvcc-explorer"],
  ["pgQuerySummary", "/api/db/pg-query-summary"],
]

function sortedKeys(value) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? Object.keys(value).sort()
    : []
}

async function getJson(baseUrl, pathname) {
  const response = await fetch(new URL(pathname, baseUrl))
  const body = await response.json().catch(() => null)
  return { response, body }
}

async function runReadOnlyQuery(
  baseUrl,
  connectionId,
  sql,
  rowLimit = 100,
  database,
) {
  const response = await fetch(new URL("/api/db/query", baseUrl), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      connectionId,
      sql,
      rowLimit,
      readOnly: true,
      ...(database ? { database } : {}),
    }),
  })
  const body = await response.json().catch(() => null)
  assert(
    response.ok && body?.ok === true,
    `Read-only fixture query failed: ${body?.error ?? response.status}`,
  )
  return body.rows ?? []
}

async function collectRvbbitFixtures(baseUrl, connectionId) {
  const rows = await runReadOnlyQuery(
    baseUrl,
    connectionId,
    `SELECT
       (SELECT count(*)::int
          FROM rvbbit.operators o
         WHERE coalesce(to_jsonb(o)->>'visibility', 'public') <> 'kit') AS operator_count,
       (SELECT name
          FROM rvbbit.operators o
         WHERE coalesce(to_jsonb(o)->>'visibility', 'public') <> 'kit'
         ORDER BY name
         LIMIT 1) AS operator_name,
       (SELECT count(*)::int
          FROM rvbbit.operators
         WHERE tests IS NOT NULL
           AND jsonb_array_length(tests) > 0) AS semantic_operator_count,
       (SELECT name
          FROM rvbbit.operators o
         WHERE coalesce(to_jsonb(o)->>'visibility', 'public') <> 'kit'
           AND jsonb_array_length(coalesce(o.tests, '[]'::jsonb)) > 0
         ORDER BY name
         LIMIT 1) AS tested_operator_name,
       (SELECT jsonb_build_object(
                 'graphId', n.graph_id,
                 'kind', n.kind,
                 'label', n.label,
                 'nodeId', n.node_id
               )
          FROM rvbbit.kg_nodes n
         WHERE EXISTS (
           SELECT 1
             FROM rvbbit.kg_evidence ev
            WHERE ev.node_id = n.node_id
         )
         ORDER BY n.node_id
         LIMIT 1) AS kg_node,
       (SELECT name FROM rvbbit.metric_catalog ORDER BY name DESC LIMIT 1) AS metric_name,
       (SELECT count(*)::int FROM rvbbit.metric_catalog) AS metric_count,
       (SELECT count(*)::int
          FROM rvbbit.metric_catalog
         WHERE check_sql IS NOT NULL
           AND btrim(check_sql) <> '') AS kpi_count,
       (SELECT count(*)::int FROM rvbbit.viz_block_catalog) AS viz_block_count,
       (SELECT jsonb_build_object(
                 'name', name,
                 'version', version,
                 'title', title,
                 'intent', intent
               )
          FROM rvbbit.viz_block_catalog
         ORDER BY name
         LIMIT 1) AS viz_block,
       (SELECT count(*)::int FROM rvbbit.provider_model_catalog) AS model_count,
       (SELECT count(*)::int
          FROM rvbbit.route_overlay
         WHERE enabled) AS overlay_count,
       (SELECT count(*)::int FROM rvbbit.route_profiles) AS route_profile_count,
       (SELECT name FROM rvbbit.route_profiles ORDER BY name LIMIT 1) AS route_profile_name,
       (SELECT count(*)::int
          FROM rvbbit.route_training_queries) AS route_training_query_count,
       (SELECT jsonb_build_object(
                 'queryId', query_id::text,
                 'receipts', count(*)::int
               )
          FROM rvbbit.receipts
         WHERE query_id IS NOT NULL
         GROUP BY query_id
         ORDER BY max(invocation_at) DESC
         LIMIT 1) AS recent_query,
       (SELECT count(*)::int
          FROM rvbbit.mcp_activity
         WHERE ts >= now() - interval '7 days') AS mcp_calls_7d,
       (SELECT count(*)::int
          FROM rvbbit.receipts
         WHERE invocation_at >= now() - interval '30 days') AS receipts_30d,
       (SELECT count(*)::int
          FROM rvbbit.brain_documents
         WHERE deleted_at IS NULL) AS brain_doc_count,
       (SELECT d.title
          FROM rvbbit.brain_documents d
         WHERE d.deleted_at IS NULL
         ORDER BY d.doc_id
         LIMIT 1) AS brain_doc_title,
       (SELECT count(*)::int FROM rvbbit.backends) AS backend_count,
       (SELECT name FROM rvbbit.backends ORDER BY name LIMIT 1) AS backend_name,
       (SELECT count(*)::int FROM rvbbit.mcp_servers) AS mcp_server_count,
       (SELECT count(*)::int FROM rvbbit.mcp_gateways) AS mcp_gateway_count,
       (SELECT count(*)::int FROM rvbbit.warren_jobs) AS warren_job_count,
       (SELECT count(*)::int FROM rvbbit.warren_nodes) AS warren_node_count,
       (SELECT count(*)::int FROM rvbbit.fleet_endpoints) AS fleet_endpoint_count,
       (SELECT count(*)::int
          FROM rvbbit.python_runtimes
         WHERE name = 'fletch_data_mover'
           AND nullif(endpoint_url, '') IS NOT NULL) AS data_mover_runtime_count,
       (SELECT ((rvbbit.accelerator_runtime_status())::jsonb -> 'gpu_gqe')) AS gqe_status,
       (SELECT count(*)::int FROM hindsight.banks) AS hindsight_bank_count,
       (SELECT count(*)::int FROM hindsight.memory_units) AS hindsight_memory_count,
       (SELECT count(*)::int FROM hindsight.memory_links) AS hindsight_link_count,
       (SELECT text
          FROM hindsight.memory_units
         ORDER BY length(text), id
         LIMIT 1) AS hindsight_memory_text,
       (SELECT count(*)::int FROM rvbbit.cube_catalog) AS cube_count,
       (SELECT name FROM rvbbit.cube_catalog ORDER BY name LIMIT 1) AS cube_name,
       (SELECT count(*)::int FROM rvbbit.cube_packs_latest) AS cube_pack_count,
       (SELECT pack_key
          FROM rvbbit.cube_packs_latest
         ORDER BY pack_key
         LIMIT 1) AS cube_pack_key,
       (SELECT count(*)::int FROM rvbbit.proposals(NULL, NULL)) AS proposal_count,
       (SELECT count(*)::int
          FROM rvbbit.proposals('pending', NULL)) AS pending_proposal_count,
       (SELECT count(*)::int FROM rvbbit.alert_catalog) AS alert_count,
       (SELECT name FROM rvbbit.alert_catalog ORDER BY name LIMIT 1) AS alert_name,
       (SELECT description
          FROM rvbbit.alert_catalog
         ORDER BY name
         LIMIT 1) AS alert_description,
       (SELECT count(*)::int FROM rvbbit.kg_extraction_runs) AS kg_extraction_run_count,
       (SELECT count(*)::int FROM rvbbit.kg_merge_candidates) AS kg_merge_candidate_count,
       (SELECT count(*)::int FROM rvbbit.ml_models) AS ml_model_count,
       (SELECT name FROM rvbbit.ml_models ORDER BY name LIMIT 1) AS ml_model_name,
       (SELECT count(*)::int
          FROM rvbbit.capability_catalog
         WHERE active
           AND coalesce(
                 catalog_entry->>'catalog_visibility',
                 'public'
               ) = 'public') AS capability_count,
       (SELECT jsonb_build_object(
                 'id', id,
                 'title', title,
                 'name', name,
                 'kind', kind
               )
          FROM rvbbit.capability_catalog
         WHERE active
           AND coalesce(
                 catalog_entry->>'catalog_visibility',
                 'public'
               ) = 'public'
         ORDER BY id
         LIMIT 1) AS capability,
       EXISTS (
         SELECT 1 FROM pg_extension WHERE extname = 'pg_cron'
       ) AS pg_cron_installed,
       EXISTS (
         SELECT 1 FROM pg_available_extensions WHERE name = 'pg_cron'
       ) AS pg_cron_available,
       position(
         'pg_cron' in current_setting('shared_preload_libraries', true)
       ) > 0 AS pg_cron_preloaded,
       coalesce(
         current_setting('cron.database_name', true),
         'postgres'
       ) AS pg_cron_database`,
    1,
  )
  const fixture = rows[0] ?? {}
  const cronHomeRows = await runReadOnlyQuery(
    baseUrl,
    connectionId,
    `SELECT EXISTS (
       SELECT 1 FROM pg_extension WHERE extname = 'pg_cron'
     ) AS installed`,
    1,
    fixture.pg_cron_database || "postgres",
  )
  fixture.pg_cron_home_installed = cronHomeRows[0]?.installed === true
  const cronFixture = fixture.pg_cron_home_installed
    ? (
        await runReadOnlyQuery(
          baseUrl,
          connectionId,
          `SELECT
             (SELECT count(*)::int FROM cron.job) AS jobs,
             (SELECT jsonb_build_object(
                       'id', j.jobid,
                       'name', j.jobname,
                       'schedule', j.schedule,
                       'active', j.active,
                       'latestStatus', (
                         SELECT d.status
                           FROM cron.job_run_details d
                          WHERE d.jobid = j.jobid
                          ORDER BY d.start_time DESC
                          LIMIT 1
                       )
                     )
                FROM cron.job j
               ORDER BY j.jobname NULLS LAST, j.jobid
               LIMIT 1) AS job`,
          1,
          fixture.pg_cron_database || "postgres",
        )
      )[0] ?? {}
    : {}
  fixture.pg_cron_job_count = Number(cronFixture.jobs ?? 0)
  fixture.pg_cron_job = cronFixture.job ?? null
  assert(fixture.operator_name, "The reference has no semantic operator fixture")
  assert(fixture.tested_operator_name, "The reference has no tested semantic operator fixture")
  assert(fixture.kg_node?.label, "The reference has no knowledge-graph node fixture")
  assert(fixture.metric_name, "The reference has no metric fixture")
  assert(fixture.viz_block?.name, "The reference has no viz-block fixture")
  assert(fixture.recent_query?.queryId, "The reference has no Query Lens fixture")
  assert(fixture.brain_doc_title, "The reference has no Document Brain fixture")
  assert(fixture.backend_name, "The reference has no specialist backend fixture")
  assert(fixture.hindsight_memory_text, "The reference has no Hindsight memory fixture")
  assert(fixture.cube_name, "The reference has no cube fixture")
  assert(fixture.cube_pack_key, "The reference has no cube-pack fixture")
  assert(fixture.alert_name, "The reference has no alert fixture")
  assert(fixture.alert_description, "The reference alert fixture has no description")
  assert(fixture.capability?.title, "The reference has no capability fixture")
  return fixture
}

async function openPaletteWindow(page, electronApp, label, kind) {
  await electronApp.evaluate(({ Menu }) => {
    const item = Menu.getApplicationMenu()?.getMenuItemById("native-command-palette")
    if (!item) throw new Error("Command Palette menu item is missing")
    item.click()
  })
  const palette = page.locator("[data-rvbbit-command-palette]")
  await palette.waitFor({ state: "visible", timeout: 10_000 })
  await palette
    .getByPlaceholder("Search tables, views, actions…")
    .fill(label)
  await palette.getByText(label, { exact: true }).click()
  const window = page.locator(`[data-rvbbit-window-kind="${kind}"]`).last()
  await window.waitFor({ state: "visible", timeout: 15_000 })
  return window
}

async function closeDesktopWindow(window) {
  await window.getByTitle("Close").click()
  await window.waitFor({ state: "hidden", timeout: 10_000 })
}

function responseShape(result) {
  return {
    status: result.response.status,
    keys: sortedKeys(result.body),
    hasError: Boolean(result.body?.error),
  }
}

const importSample = [
  "sighting_year,state_name,report_count,confirmed",
  "2022,Washington,12,true",
  "2023,Oregon,7,false",
  "2024,California,19,true",
  "",
].join("\n")

async function inspectCsvSample(baseUrl) {
  const bytes = new TextEncoder().encode(importSample)
  const url = new URL("/api/db/import/inspect", baseUrl)
  url.searchParams.set("totalBytes", String(bytes.byteLength))
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/octet-stream" },
    body: bytes,
  })
  const body = await response.json()
  return {
    status: response.status,
    ok: body.ok === true,
    dialect: body.dialect,
    columns: Array.isArray(body.columns)
      ? body.columns.map((column) => ({
          sourceName: column.sourceName,
          targetName: column.targetName,
          type: column.type,
        }))
      : [],
    sampleRows: body.sampleRows,
    estimatedRows: body.estimatedRows,
    warnings: body.warnings,
  }
}

async function collectReadOnlySurface(baseUrl, connectionId) {
  const id = encodeURIComponent(connectionId)
  const connectionsResult = await getJson(baseUrl, "/api/db/connections")
  const connection = connectionsResult.body?.connections?.find(
    (candidate) => candidate.id === connectionId,
  )
  const schemaResult = await getJson(baseUrl, `/api/db/schema?connectionId=${id}`)
  const schema = schemaResult.body ?? {}
  const extensionsResult = await getJson(baseUrl, `/api/db/extensions?connectionId=${id}`)
  const extensions = extensionsResult.body ?? {}
  const diagnostics = {}

  for (const [name, pathname] of diagnosticEndpoints) {
    diagnostics[name] = responseShape(
      await getJson(baseUrl, `${pathname}?connectionId=${id}`),
    )
  }

  let fingerprintFastPath = null
  if (typeof schema.fingerprint === "string" && schema.fingerprint.length > 0) {
    const fingerprintResult = await getJson(
      baseUrl,
      `/api/db/schema?connectionId=${id}&fp=${encodeURIComponent(schema.fingerprint)}`,
    )
    fingerprintFastPath = {
      status: fingerprintResult.response.status,
      unchanged: fingerprintResult.body?.unchanged === true,
      fingerprintMatches: fingerprintResult.body?.fingerprint === schema.fingerprint,
    }
  }

  const [homesResult, traversalResult, csvImportInspect] = await Promise.all([
    getJson(baseUrl, "/api/lens/homes"),
    getJson(
      baseUrl,
      "/api/rvbbit/capabilities?action=manifest&path=../../etc/passwd",
    ),
    inspectCsvSample(baseUrl),
  ])

  const secretKeys = ["password", "sshPrivateKey", "sshPassphrase", "sshPassword"]
  return {
    connection: {
      label: connection?.label,
      database: connection?.database,
      secretKeysPresent: secretKeys.filter((key) => key in (connection ?? {})),
      connectionStringRedacted:
        typeof connection?.connectionString !== "string" ||
        !connection.connectionString.includes("://") ||
        new URL(connection.connectionString).password === "***",
    },
    schema: {
      status: schemaResult.response.status,
      keys: sortedKeys(schema),
      currentDatabase: schema.currentDatabase,
      hasRvbbit: schema.hasRvbbit,
      rvbbitVersion: schema.rvbbitVersion,
      fingerprint: schema.fingerprint ?? null,
      schemaCount: Array.isArray(schema.schemas) ? schema.schemas.length : null,
      tableCount: Array.isArray(schema.tables) ? schema.tables.length : null,
      functionCount: Array.isArray(schema.functions) ? schema.functions.length : null,
      extensionCount: Array.isArray(schema.extensions) ? schema.extensions.length : null,
    },
    extensions: {
      status: extensionsResult.response.status,
      hasRvbbit: extensions.hasRvbbit,
      rvbbitVersion: extensions.rvbbitVersion,
      names: Array.isArray(extensions.extensions)
        ? extensions.extensions.map((extension) => extension.name).sort()
        : [],
    },
    diagnostics,
    fingerprintFastPath,
    lensHomes: responseShape(homesResult),
    capabilityTraversalGuard: responseShape(traversalResult),
    csvImportInspect,
  }
}

try {
  // Establish the web reference signal before launching the packaged app.
  const referenceConnectionsBody = await fetch(`${referenceUrl}/api/db/connections`).then(
    (response) => response.json(),
  )
  const referenceConnections = Array.isArray(referenceConnectionsBody.connections)
    ? referenceConnectionsBody.connections
    : []
  const referenceConnection =
    referenceConnections.find((candidate) => candidate.isDefault) ?? referenceConnections[0]
  assert(referenceConnection?.id, "The port-3000 reference has no active connection")
  const referenceRvbbit = await fetch(
    `${referenceUrl}/api/db/rvbbit-status?connectionId=${encodeURIComponent(referenceConnection.id)}`,
  ).then((response) => response.json())
  assert(referenceRvbbit.hasRvbbit === true, "The port-3000 reference did not report rvbbit")
  const referenceSurface = await collectReadOnlySurface(referenceUrl, referenceConnection.id)
  const rvbbitFixtures = await collectRvbbitFixtures(referenceUrl, referenceConnection.id)

  electronApp = await electron.launch({
    executablePath: executable,
    env: {
      ...process.env,
      DATARABBIT_USER_DATA_DIR: testHome,
      DATARABBIT_READ_ONLY_TEST: "1",
    },
  })

  const page = await electronApp.firstWindow({ timeout: 60_000 })
  await page.waitForLoadState("domcontentloaded")
  await page.waitForFunction(
    () =>
      document.documentElement.dataset.datarabbitDesktop === "true" &&
      Boolean(window.dataRabbitDesktop?.isDesktopApp),
    undefined,
    { timeout: 60_000 },
  )
  await page.locator("[data-rvbbit-boot-overlay]").waitFor({
    state: "hidden",
    timeout: 10_000,
  })
  appUrl = page.url()

  const packagedState = await page.evaluate(async () => {
    const connectionsBody = await fetch("/api/db/connections").then((response) => response.json())
    const connections = Array.isArray(connectionsBody.connections)
      ? connectionsBody.connections
      : []
    const connection = connections.find((candidate) => candidate.isDefault) ?? connections[0]
    if (!connection?.id) throw new Error("The packaged app has no active connection")

    const [rvbbit, query] = await Promise.all([
      fetch(`/api/db/rvbbit-status?connectionId=${encodeURIComponent(connection.id)}`).then(
        (response) => response.json(),
      ),
      fetch("/api/db/query", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          connectionId: connection.id,
          sql: "SELECT 1::int AS native_smoke, current_database() AS database_name",
          rowLimit: 1,
          readOnly: true,
        }),
      }).then((response) => response.json()),
    ])

    return {
      connection: {
        id: connection.id,
        label: connection.label,
        database: connection.database,
      },
      rvbbit,
      query,
    }
  })

  assert(
    packagedState.rvbbit.hasRvbbit === true,
    `Packaged app did not report rvbbit: ${packagedState.rvbbit.error ?? "no error detail"}`,
  )
  assert(
    packagedState.rvbbit.rvbbitVersion === referenceRvbbit.rvbbitVersion,
    "Packaged app and port-3000 reference reported different rvbbit versions",
  )
  assert(packagedState.query.ok === true, `Read-only query failed: ${packagedState.query.error}`)
  assert(packagedState.query.rows?.[0]?.native_smoke === 1, "Read-only query returned the wrong result")

  const packagedSurface = await collectReadOnlySurface(appUrl, packagedState.connection.id)
  assert(
    JSON.stringify(packagedSurface.schema) === JSON.stringify(referenceSurface.schema),
    "Packaged app and port-3000 reference returned different schema summaries",
  )
  assert(
    JSON.stringify(packagedSurface.extensions) === JSON.stringify(referenceSurface.extensions),
    "Packaged app and port-3000 reference returned different extension summaries",
  )
  assert(
    JSON.stringify(packagedSurface.diagnostics) === JSON.stringify(referenceSurface.diagnostics),
    "Packaged app and port-3000 reference returned different diagnostic API shapes",
  )
  assert(
    packagedSurface.csvImportInspect.ok &&
      JSON.stringify(packagedSurface.csvImportInspect) ===
        JSON.stringify(referenceSurface.csvImportInspect),
    "Packaged app and port-3000 reference returned different CSV inspection results",
  )
  assert(
    packagedSurface.fingerprintFastPath?.unchanged === true &&
      packagedSurface.fingerprintFastPath?.fingerprintMatches === true,
    "Packaged schema endpoint did not honor its fingerprint fast path",
  )
  assert(
    packagedSurface.connection.secretKeysPresent.length === 0 &&
      packagedSurface.connection.connectionStringRedacted,
    "Packaged connections API exposed a database or SSH secret",
  )
  assert(
    packagedSurface.lensHomes.status === 200 &&
      packagedSurface.lensHomes.keys.includes("homes"),
    "Packaged Lens homebase endpoint did not initialize",
  )
  assert(
    packagedSurface.capabilityTraversalGuard.status === 400 &&
      packagedSurface.capabilityTraversalGuard.hasError,
    "Packaged capability manifest route did not reject path traversal",
  )

  await page.getByRole("button", { name: "Capabilities", exact: true }).waitFor({
    state: "visible",
    timeout: 60_000,
  })

  await electronApp.evaluate(({ Menu }) => {
    const item = Menu.getApplicationMenu()?.getMenuItemById("native-capabilities")
    if (!item) throw new Error("Capabilities menu item is missing")
    item.click()
  })
  await page.locator('[data-rvbbit-window-kind="capabilities"]').waitFor({
    state: "visible",
    timeout: 10_000,
  })

  await electronApp.evaluate(({ Menu }) => {
    const item = Menu.getApplicationMenu()?.getMenuItemById("native-new-sql")
    if (!item) throw new Error("New SQL menu item is missing")
    item.click()
  })
  await page.locator('[data-rvbbit-window-kind="data"]').waitFor({
    state: "visible",
    timeout: 10_000,
  })
  const sqlWindow = page.locator('[data-rvbbit-window-kind="data"]').last()
  await sqlWindow.getByTitle("Run (⌘↩)").click()
  const singleCell = sqlWindow.locator("[data-rvbbit-single-cell]")
  await singleCell.waitFor({ state: "visible", timeout: 30_000 })
  assert(
    (await singleCell.textContent())?.includes("1"),
    "Native-menu-created SQL window did not render SELECT 1",
  )

  const existingDataWindows = await page.locator('[data-rvbbit-window-kind="data"]').count()
  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-new-sql")?.click()
  })
  await page.waitForFunction(
    (expected) =>
      document.querySelectorAll('[data-rvbbit-window-kind="data"]').length > expected,
    existingDataWindows,
    { timeout: 10_000 },
  )
  const chartSqlWindow = page.locator('[data-rvbbit-window-kind="data"]').last()
  const sqlEditor = chartSqlWindow.locator(".cm-content").first()
  await sqlEditor.click()
  await page.keyboard.press("Meta+A")
  await page.keyboard.insertText(
    "SELECT * FROM (VALUES ('Washington', 12), ('Oregon', 7), ('California', 19)) AS native_chart(state_name, report_count)",
  )
  await chartSqlWindow.getByTitle("Run (⌘↩)").click()
  const chartResult = chartSqlWindow.locator("[data-rvbbit-query-result]")
  await chartResult.waitFor({ state: "visible", timeout: 30_000 })
  assert(
    (await chartResult.textContent())?.includes("3 rows"),
    "Native SQL chart query did not return three rows",
  )
  await chartSqlWindow.getByRole("button", { name: "View", exact: true }).click()
  const renderedChart = chartSqlWindow.locator(".vega-embed svg").last()
  await renderedChart.waitFor({ state: "visible", timeout: 30_000 })
  assert(
    (await renderedChart.locator("path, rect, line, circle").count()) > 0,
    "Native SQL result did not render a Vega bar chart",
  )
  await chartSqlWindow.getByTitle("Flint Vega-Lite").click()
  await chartSqlWindow.locator(".vega-embed svg").last().waitFor({
    state: "visible",
    timeout: 30_000,
  })
  assert(
    (await chartSqlWindow.getByText("fallback", { exact: true }).count()) === 0,
    "Flint Vega-Lite fell back to the classic renderer",
  )
  await chartSqlWindow.getByTitle("Flint ECharts").click()
  const eChartsCanvas = chartSqlWindow.locator("canvas").last()
  await eChartsCanvas.waitFor({ state: "visible", timeout: 30_000 })
  await page.waitForFunction(
    (canvas) => canvas instanceof HTMLCanvasElement && canvas.width > 0 && canvas.height > 0,
    await eChartsCanvas.elementHandle(),
    { timeout: 30_000 },
  )
  assert(
    (await chartSqlWindow.getByText("fallback", { exact: true }).count()) === 0,
    "Flint ECharts fell back to the classic renderer",
  )
  await chartSqlWindow.getByTitle("Flint Chart.js").click()
  const chartJsCanvas = chartSqlWindow.locator("canvas").last()
  await chartJsCanvas.waitFor({ state: "visible", timeout: 30_000 })
  await page.waitForFunction(
    (canvas) => canvas instanceof HTMLCanvasElement && canvas.width > 0 && canvas.height > 0,
    await chartJsCanvas.elementHandle(),
    { timeout: 30_000 },
  )
  assert(
    (await chartSqlWindow.getByText("fallback", { exact: true }).count()) === 0,
    "Flint Chart.js fell back to the classic renderer",
  )

  for (const [menuId, windowKind] of [
    ["native-finder", "finder"],
    ["native-notifications", "notifications"],
    ["native-scenes", "scenes"],
    ["native-saved-views", "view-apps"],
  ]) {
    await electronApp.evaluate(({ Menu }, id) => {
      const item = Menu.getApplicationMenu()?.getMenuItemById(id)
      if (!item) throw new Error(`${id} menu item is missing`)
      item.click()
    }, menuId)
    await page.locator(`[data-rvbbit-window-kind="${windowKind}"]`).waitFor({
      state: "visible",
      timeout: 10_000,
    })
  }

  const finderWindow = page.locator('[data-rvbbit-window-kind="finder"]')
  await finderWindow.getByPlaceholder("Search objects...").fill("bigfoot_sightings")
  const dataWindowCount = await page.locator('[data-rvbbit-window-kind="data"]').count()
  await finderWindow
    .locator('[data-rvbbit-table="public.bigfoot_sightings"]')
    .first()
    .evaluate((element) => element.click())
  await page.waitForFunction(
    (expected) =>
      document.querySelectorAll('[data-rvbbit-window-kind="data"]').length > expected,
    dataWindowCount,
    { timeout: 10_000 },
  )
  const tablePreview = page.locator('[data-rvbbit-window-kind="data"]').last()
  const tableResult = tablePreview.locator("[data-rvbbit-query-result]")
  await tableResult.waitFor({ state: "visible", timeout: 30_000 })
  assert(
    (await tableResult.textContent())?.includes("rows"),
    "Finder table preview did not render a result row count",
  )

  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-command-palette")?.click()
  })
  let commandPalette = page.locator("[data-rvbbit-command-palette]")
  await commandPalette.waitFor({ state: "visible" })
  await commandPalette
    .getByPlaceholder("Search tables, views, actions…")
    .fill("Dashboards")
  await commandPalette.getByText("Dashboards", { exact: true }).click()
  const dashboardsWindow = page.locator(
    '[data-rvbbit-window-kind="dashboards"]',
  )
  await dashboardsWindow.waitFor({ state: "visible", timeout: 10_000 })
  const engineVitals = dashboardsWindow.getByRole("button", {
    name: /Engine Vitals/,
  })
  await engineVitals.waitFor({ state: "visible", timeout: 15_000 })
  await engineVitals.click()
  const dashboardFrameElement = dashboardsWindow.locator(
    'iframe[title="Engine Vitals"]',
  )
  await dashboardFrameElement.waitFor({ state: "visible", timeout: 15_000 })
  const sandbox = await dashboardFrameElement.getAttribute("sandbox")
  assert(
    sandbox === "allow-scripts" && !sandbox.includes("allow-same-origin"),
    `Published dashboard iframe had an unsafe sandbox policy: ${sandbox}`,
  )
  const dashboardFrame = dashboardsWindow.frameLocator(
    'iframe[title="Engine Vitals"]',
  )
  await dashboardFrame.locator(".card").first().waitFor({
    state: "visible",
    timeout: 30_000,
  })
  assert(
    (await dashboardFrame.locator(".card").count()) === 3 &&
      (await dashboardFrame.locator("body").textContent())?.includes("maps") &&
      (await dashboardFrame.locator("body").textContent())?.includes("texels") &&
      (await dashboardFrame.locator("body").textContent())?.includes("brushes"),
    "Published dashboard did not render all three live brokered query cards",
  )
  await dashboardsWindow.getByTitle("Close").click()

  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-command-palette")?.click()
  })
  commandPalette = page.locator("[data-rvbbit-command-palette]")
  await commandPalette.waitFor({ state: "visible" })
  await commandPalette
    .getByPlaceholder("Search tables, views, actions…")
    .fill("Plates")
  await commandPalette.getByText("Plates", { exact: true }).click()
  const platesWindow = page.locator('[data-rvbbit-window-kind="plates"]')
  await platesWindow.waitFor({ state: "visible", timeout: 10_000 })
  const crimeOverviewEntry = platesWindow.getByText("Crime Overview", {
    exact: true,
  })
  await crimeOverviewEntry.waitFor({ state: "visible", timeout: 15_000 })
  const plateCountBefore = await page
    .locator('[data-rvbbit-window-kind="plate"]')
    .count()
  await crimeOverviewEntry.click()
  await page.waitForFunction(
    (expected) =>
      document.querySelectorAll('[data-rvbbit-window-kind="plate"]').length > expected,
    plateCountBefore,
    { timeout: 10_000 },
  )
  const crimeOverview = page.locator('[data-rvbbit-window-kind="plate"]').last()
  await crimeOverview.getByText("Distinct incidents from the canonical multi-city view", {
    exact: true,
  }).waitFor({ state: "visible", timeout: 30_000 })
  await crimeOverview.getByText("Total incidents", { exact: true }).waitFor({
    state: "visible",
    timeout: 30_000,
  })
  assert(
    (await crimeOverview.locator(".plate-error").count()) === 0,
    "Crime Overview plate rendered an error",
  )
  const beforePlateNavigation = await page
    .locator('[data-rvbbit-window-kind="plate"]')
    .count()
  await crimeOverview
    .getByRole("button", { name: "Trends", exact: true })
    .click()
  await page.waitForFunction(
    (expected) =>
      document.querySelectorAll('[data-rvbbit-window-kind="plate"]').length > expected,
    beforePlateNavigation,
    { timeout: 10_000 },
  )
  const crimeTrends = page.locator('[data-rvbbit-window-kind="plate"]').last()
  await crimeTrends.getByText("Incidents by month", { exact: true }).waitFor({
    state: "visible",
    timeout: 30_000,
  })
  await crimeTrends.getByTitle("Close").click()
  await crimeOverview.getByTitle("Close").click()

  const crmLayoutTitle = platesWindow.getByText("CRM2", { exact: true })
  await crmLayoutTitle.waitFor({ state: "visible", timeout: 15_000 })
  const crmLayoutRow = crmLayoutTitle.locator("..").locator("..")
  await crmLayoutTitle.click()
  const wallBackButton = page.getByTitle("Back to the desktop")
  await wallBackButton.waitFor({ state: "visible", timeout: 15_000 })
  await page.getByText("CRM2", { exact: true }).last().waitFor({
    state: "visible",
    timeout: 15_000,
  })
  await page.getByText("Customer directory", { exact: true }).first().waitFor({
    state: "visible",
    timeout: 30_000,
  })
  await page.keyboard.press("Escape")
  await wallBackButton.waitFor({ state: "hidden", timeout: 10_000 })

  const stampedBefore = await page
    .locator('[data-rvbbit-window-kind="plate"]')
    .count()
  await crmLayoutRow.getByTitle("Stamp onto the desktop as windows").click()
  await page.waitForFunction(
    (expected) =>
      document.querySelectorAll('[data-rvbbit-window-kind="plate"]').length >=
      expected + 3,
    stampedBefore,
    { timeout: 15_000 },
  )
  const stampedPlates = page.locator('[data-rvbbit-window-kind="plate"]')
  assert(
    (await stampedPlates.count()) >= stampedBefore + 3,
    "CRM layout did not stamp all three panes as desktop windows",
  )
  while ((await stampedPlates.count()) > stampedBefore) {
    await stampedPlates.last().getByTitle("Close").click()
  }

  // Semantic operators: load the live registry, follow a real operator into
  // its graph inspector, and inspect an operator that has embedded tests.
  const operatorsWindow = await openPaletteWindow(
    page,
    electronApp,
    "Operators",
    "operators",
  )
  await operatorsWindow
    .getByText(`${rvbbitFixtures.operator_count} operators`, { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await operatorsWindow
    .getByText(`rvbbit.${rvbbitFixtures.operator_name}`, { exact: true })
    .click()
  const operatorFlowWindow = page
    .locator('[data-rvbbit-window-kind="operator-flow"]')
    .last()
  await operatorFlowWindow.waitFor({ state: "visible", timeout: 15_000 })
  await operatorFlowWindow
    .getByText(`rvbbit.${rvbbitFixtures.operator_name}`, { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 30_000 })
  await operatorFlowWindow
    .getByRole("button", { name: "run", exact: true })
    .click()
  await operatorFlowWindow
    .getByRole("button", { name: "Run operator", exact: true })
    .waitFor({ state: "visible", timeout: 15_000 })
  await closeDesktopWindow(operatorFlowWindow)
  await closeDesktopWindow(operatorsWindow)

  const semanticTestsWindow = await openPaletteWindow(
    page,
    electronApp,
    "Semantic Tests",
    "semantic-tests",
  )
  await semanticTestsWindow
    .getByText(
      new RegExp(
        `^${rvbbitFixtures.semantic_operator_count} operators · \\d+ embedded tests · \\d+ recent batteries$`,
      ),
    )
    .waitFor({ state: "visible", timeout: 30_000 })
  await semanticTestsWindow
    .getByTitle(`Inspect ${rvbbitFixtures.tested_operator_name}`)
    .click()
  await semanticTestsWindow
    .getByText("embedded tests", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  assert(
    !(await semanticTestsWindow.textContent()).includes("no embedded tests"),
    `Semantic Tests did not load definitions for ${rvbbitFixtures.tested_operator_name}`,
  )
  await closeDesktopWindow(semanticTestsWindow)

  // MCP activity is historical on the reference, so select 7d explicitly
  // instead of treating an empty 24h window as an implementation failure.
  const mcpIncomingWindow = await openPaletteWindow(
    page,
    electronApp,
    "MCP Incoming",
    "mcp-incoming",
  )
  await mcpIncomingWindow
    .getByRole("button", { name: "7d", exact: true })
    .click()
  await mcpIncomingWindow
    .getByText(new RegExp(`^${rvbbitFixtures.mcp_calls_7d} calls`))
    .waitFor({ state: "visible", timeout: 30_000 })
  await mcpIncomingWindow
    .getByText("People by surface", { exact: true })
    .waitFor({ state: "visible", timeout: 15_000 })
  const usageTimeline = mcpIncomingWindow.locator("[data-mcp-usage-timeline]")
  await usageTimeline.waitFor({ state: "visible", timeout: 15_000 })
  await usageTimeline.locator('[data-period-series="current"]').waitFor({ state: "visible" })
  const previousPeriodSeries = usageTimeline.locator('[data-period-series="previous"]')
  await previousPeriodSeries.waitFor({ state: "visible" })
  assert(
    (await previousPeriodSeries.getAttribute("stroke-dasharray")) !== null,
    "MCP Incoming prior-period series was not rendered as a comparison line",
  )
  const directMcpSurface = mcpIncomingWindow.locator('[data-mcp-surface="direct_mcp"]')
  await directMcpSurface.click()
  assert(
    (await directMcpSurface.getAttribute("aria-pressed")) === "true",
    "MCP Incoming surface callouts did not apply the shared filter",
  )
  await mcpIncomingWindow.locator('[data-mcp-surface="all"]').click()
  for (const panelTitle of ["Flow", "Activity", "People", "Tools"]) {
    await mcpIncomingWindow
      .getByText(panelTitle, { exact: true })
      .first()
      .waitFor({ state: "visible", timeout: 15_000 })
  }
  await closeDesktopWindow(mcpIncomingWindow)

  const modelSettingsWindow = await openPaletteWindow(
    page,
    electronApp,
    "Model Settings",
    "model-settings",
  )
  const availableModelsLabel = modelSettingsWindow
    .getByText(/^\d+ available models$/)
    .first()
  await availableModelsLabel.waitFor({ state: "visible", timeout: 30_000 })
  assert(
    Number.parseInt((await availableModelsLabel.textContent()) ?? "0", 10) > 0,
    "Model Settings resolved an empty model catalog",
  )
  const modelSearch = modelSettingsWindow.getByPlaceholder(
    "search operators, models, descriptions",
  )
  await modelSearch.fill(rvbbitFixtures.operator_name)
  await modelSettingsWindow
    .getByText(rvbbitFixtures.operator_name, { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 15_000 })
  await closeDesktopWindow(modelSettingsWindow)

  const routingWindow = await openPaletteWindow(
    page,
    electronApp,
    "Routing",
    "routing",
  )
  await routingWindow
    .getByText("Adaptive Routing", { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 30_000 })
  await routingWindow
    .getByRole("button", { name: "Overlay", exact: true })
    .click()
  await routingWindow
    .getByText("Routing overlay", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  const activePinsHeader = routingWindow
    .getByText("active pins", { exact: true })
    .locator("..")
  await activePinsHeader
    .getByText(String(rvbbitFixtures.overlay_count), { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await routingWindow
    .getByRole("button", { name: "Train", exact: true })
    .click()
  if (rvbbitFixtures.route_profile_count === 0) {
    await routingWindow
      .getByText(
        "No profiles yet — create one to start curating training queries.",
        { exact: true },
      )
      .waitFor({ state: "visible", timeout: 30_000 })
  } else {
    await routingWindow
      .getByText(rvbbitFixtures.route_profile_name, { exact: true })
      .first()
      .waitFor({ state: "visible", timeout: 30_000 })
  }
  await closeDesktopWindow(routingWindow)

  // Query Lens: drill from a live, receipt-backed query id into the
  // cross-surface trace without producing a new query receipt.
  const queryLensWindow = await openPaletteWindow(
    page,
    electronApp,
    "Query Lens",
    "query-lens",
  )
  const queryIdInput = queryLensWindow.getByPlaceholder("paste a query_id…")
  await queryIdInput.waitFor({ state: "visible", timeout: 30_000 })
  await queryIdInput.fill(rvbbitFixtures.recent_query.queryId)
  await queryIdInput.press("Enter")
  await queryLensWindow
    .getByText(
      new RegExp(`^${rvbbitFixtures.recent_query.queryId.slice(0, 8)}…$`),
    )
    .waitFor({ state: "visible", timeout: 30_000 })
  await queryLensWindow
    .getByText(/trace timeline/i)
    .first()
    .waitFor({ state: "visible", timeout: 30_000 })
  const queryLensText = await queryLensWindow.innerText()
  assert(
    queryLensText.includes("TRACE TIMELINE") &&
      queryLensText.includes("RECEIPTS"),
    `Query Lens did not render its trace instruments: ${queryLensText.slice(0, 700)}`,
  )
  await closeDesktopWindow(queryLensWindow)

  // Knowledge Graph: select the fixture graph, search a real node, and follow
  // it into the entity-detail surface.
  const kgBrowserWindow = await openPaletteWindow(
    page,
    electronApp,
    "Knowledge Graph",
    "kg-browser",
  )
  await kgBrowserWindow
    .getByText("Knowledge Graph", { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 30_000 })
  await kgBrowserWindow.getByTitle("Change active graph").click()
  await kgBrowserWindow
    .getByRole("button", {
      name: new RegExp(`^${rvbbitFixtures.kg_node.graphId}\\b`),
    })
    .last()
    .click()
  const kgSearch = kgBrowserWindow.getByPlaceholder("Search nodes by label…")
  await kgSearch.fill(rvbbitFixtures.kg_node.label)
  const kgSearchHit = kgBrowserWindow
    .getByText(rvbbitFixtures.kg_node.label, { exact: true })
    .last()
  await kgSearchHit.waitFor({ state: "visible", timeout: 30_000 })
  await kgSearchHit.click()
  const kgEntityWindow = page
    .locator('[data-rvbbit-window-kind="kg-entity-detail"]')
    .last()
  await kgEntityWindow.waitFor({ state: "visible", timeout: 15_000 })
  await kgEntityWindow
    .getByText(rvbbitFixtures.kg_node.label, { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 30_000 })
  await kgEntityWindow
    .getByText(rvbbitFixtures.kg_node.kind, { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 15_000 })
  await kgEntityWindow
    .getByRole("button", { name: /^Evidence/ })
    .click()
  await kgEntityWindow
    .getByText(/^ev #/)
    .first()
    .waitFor({ state: "visible", timeout: 30_000 })
  await closeDesktopWindow(kgEntityWindow)

  await kgBrowserWindow
    .getByRole("button", { name: "open dashboard →", exact: true })
    .click()
  const kgExtractionWindow = page
    .locator('[data-rvbbit-window-kind="kg-extraction-runs"]')
    .last()
  await kgExtractionWindow.waitFor({ state: "visible", timeout: 15_000 })
  if (rvbbitFixtures.kg_extraction_run_count === 0) {
    await kgExtractionWindow
      .getByText(
        "No extraction runs yet. Run rvbbit.kg_ingest_table(...) to seed.",
        { exact: true },
      )
      .waitFor({ state: "visible", timeout: 30_000 })
  } else {
    await kgExtractionWindow
      .getByText(
        `Runs · ${rvbbitFixtures.kg_extraction_run_count}`,
        { exact: true },
      )
      .waitFor({ state: "visible", timeout: 30_000 })
  }
  await closeDesktopWindow(kgExtractionWindow)

  await kgBrowserWindow
    .getByRole("button", { name: "open queue →", exact: true })
    .click()
  const kgMergeWindow = page
    .locator('[data-rvbbit-window-kind="kg-merge-review"]')
    .last()
  await kgMergeWindow.waitFor({ state: "visible", timeout: 15_000 })
  if (rvbbitFixtures.kg_merge_candidate_count === 0) {
    await kgMergeWindow
      .getByText(
        'No pending candidates. Pick a kind and click "suggest" above.',
        { exact: true },
      )
      .waitFor({ state: "visible", timeout: 30_000 })
  } else {
    await kgMergeWindow
      .getByText(
        new RegExp(`^1 / ${rvbbitFixtures.kg_merge_candidate_count}$`),
      )
      .waitFor({ state: "visible", timeout: 30_000 })
  }
  await closeDesktopWindow(kgMergeWindow)
  await closeDesktopWindow(kgBrowserWindow)

  // Document Brain: verify the system-learning source and then switch to the
  // admin corpus to prove real documents load through the native runtime.
  const brainWindow = await openPaletteWindow(
    page,
    electronApp,
    "Document Brain",
    "brain",
  )
  await brainWindow
    .getByText("RVBBIT System Learning", { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 30_000 })
  await brainWindow
    .getByText(
      new RegExp(`\\b${rvbbitFixtures.brain_doc_count} docs\\b`),
    )
    .first()
    .waitFor({ state: "visible", timeout: 15_000 })
  await brainWindow
    .getByRole("button", { name: "All docs", exact: true })
    .click()
  await brainWindow
    .getByText(rvbbitFixtures.brain_doc_title, { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await closeDesktopWindow(brainWindow)

  // Metrics and costs: traverse from a live catalog row into its bitemporal
  // inspector and wait for the metric's automatic read-only execution.
  const metricCatalogWindow = await openPaletteWindow(
    page,
    electronApp,
    "Metric Catalog",
    "metric-catalog",
  )
  await metricCatalogWindow
    .getByText(String(rvbbitFixtures.metric_count), { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await metricCatalogWindow
    .getByTitle(`Open ${rvbbitFixtures.metric_name} in the inspector`)
    .click()
  const metricInspectorWindow = page
    .locator('[data-rvbbit-window-kind="metric-inspector"]')
    .last()
  await metricInspectorWindow.waitFor({ state: "visible", timeout: 15_000 })
  await metricInspectorWindow
    .getByText(rvbbitFixtures.metric_name, { exact: true })
    .last()
    .waitFor({ state: "visible", timeout: 30_000 })
  await metricInspectorWindow
    .getByText("Resolved SQL", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await metricInspectorWindow
    .getByText(/^\d+ rows$/)
    .last()
    .waitFor({ state: "visible", timeout: 60_000 })
  await metricInspectorWindow
    .getByText("def: latest · data: latest", { exact: true })
    .waitFor({ state: "visible", timeout: 15_000 })
  await closeDesktopWindow(metricInspectorWindow)
  await closeDesktopWindow(metricCatalogWindow)

  const costsWindow = await openPaletteWindow(
    page,
    electronApp,
    "Costs",
    "costs",
  )
  await costsWindow
    .getByText(/[1-9][\d,]* receipts$/)
    .waitFor({ state: "visible", timeout: 60_000 })
  await costsWindow
    .locator(".vega-embed svg")
    .waitFor({ state: "visible", timeout: 60_000 })
  await costsWindow
    .getByText(/^receipts \(\d+ shown\)$/i)
    .waitFor({ state: "visible", timeout: 30_000 })
  await closeDesktopWindow(costsWindow)

  // Authoring surfaces stay read-only here: load existing metric/viz
  // definitions and their resolved-SQL previews, then exercise the KPI board's
  // catalog fallback without saving, running, or invoking an AI draft.
  const metricCreatorWindow = await openPaletteWindow(
    page,
    electronApp,
    "Metric Creator",
    "metric-creator",
  )
  await metricCreatorWindow
    .getByText(rvbbitFixtures.metric_name, { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 30_000 })
  await metricCreatorWindow
    .getByText(rvbbitFixtures.metric_name, { exact: true })
    .first()
    .click()
  await metricCreatorWindow
    .getByText("Resolved SQL — live preview", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  const metricResolvedEditor = metricCreatorWindow.locator(".cm-content").last()
  await metricResolvedEditor.waitFor({ state: "visible", timeout: 15_000 })
  await page.waitForFunction(
    (editor) => /\bselect\b/i.test(editor?.textContent ?? ""),
    await metricResolvedEditor.elementHandle(),
    { timeout: 30_000 },
  )
  await metricCreatorWindow
    .getByRole("button", { name: "Open in SQL", exact: true })
    .waitFor({ state: "visible", timeout: 15_000 })
  await closeDesktopWindow(metricCreatorWindow)

  const vizBlocksWindow = await openPaletteWindow(
    page,
    electronApp,
    "Viz Blocks",
    "viz-blocks",
  )
  await vizBlocksWindow
    .getByText(rvbbitFixtures.viz_block.name, { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 30_000 })
  await vizBlocksWindow
    .getByText(rvbbitFixtures.viz_block.name, { exact: true })
    .first()
    .click()
  await vizBlocksWindow
    .getByText(`v${rvbbitFixtures.viz_block.version}`, { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 30_000 })
  await vizBlocksWindow
    .getByText("Resolved SQL - Live Preview", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  const vizResolvedEditor = vizBlocksWindow.locator(".cm-content").last()
  await vizResolvedEditor.waitFor({ state: "visible", timeout: 15_000 })
  await page.waitForFunction(
    (editor) => /\bselect\b/i.test(editor?.textContent ?? ""),
    await vizResolvedEditor.elementHandle(),
    { timeout: 30_000 },
  )
  await vizBlocksWindow
    .getByRole("button", { name: "Run", exact: true })
    .waitFor({ state: "visible", timeout: 15_000 })
  await closeDesktopWindow(vizBlocksWindow)

  const metricBoardWindow = await openPaletteWindow(
    page,
    electronApp,
    "KPI Board",
    "metric-board",
  )
  await metricBoardWindow
    .getByRole("heading", { name: "KPI Board", exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  if (rvbbitFixtures.kpi_count === 0) {
    await metricBoardWindow
      .getByText("No metrics yet — define one in the Creator.", { exact: true })
      .waitFor({ state: "visible", timeout: 30_000 })
  }
  await metricBoardWindow
    .getByRole("button", { name: "KPIs only", exact: true })
    .click()
  await metricBoardWindow
    .getByRole("button", { name: "All metrics", exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await metricBoardWindow
    .getByText(rvbbitFixtures.metric_name, { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await metricBoardWindow
    .getByText(`${rvbbitFixtures.metric_count} metrics`, { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await closeDesktopWindow(metricBoardWindow)

  // The reference currently has no configured mirror jobs or Duck telemetry;
  // exercise the honest empty states without creating live-server state.
  const mirrorWindow = await openPaletteWindow(
    page,
    electronApp,
    "Temporal Mirror",
    "sync-mirror",
  )
  await mirrorWindow
    .getByText("No sync jobs yet.", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await closeDesktopWindow(mirrorWindow)

  const duckWindow = await openPaletteWindow(page, electronApp, "Duck", "duck")
  await duckWindow
    .getByText("Duck sidecars", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await duckWindow
    .getByText("No Duck sidecar queries recorded in this window.", {
      exact: true,
    })
    .waitFor({ state: "visible", timeout: 30_000 })
  await closeDesktopWindow(duckWindow)

  // Assistant identity/settings are local to this isolated native profile.
  // Exercise the OS-level dock without sending a model request to the bench.
  const assistantSettingsWindow = await openPaletteWindow(
    page,
    electronApp,
    "Assistant",
    "assistant-settings",
  )
  const assistantSettingsText = await assistantSettingsWindow.innerText()
  assert(
    ["IDENTITY", "MIND", "PERSONALITY"].every((section) =>
      assistantSettingsText.includes(section),
    ),
    `Assistant settings did not render its core controls: ${assistantSettingsText.slice(0, 500)}`,
  )
  const assistantName = "Native Rabbit"
  const assistantNameInput = assistantSettingsWindow.locator("#assistant-display-name")
  await assistantNameInput.fill(assistantName)
  await assistantNameInput.press("Enter")
  await page
    .getByTitle(`Summon ${assistantName}`)
    .waitFor({ state: "visible", timeout: 15_000 })
  await closeDesktopWindow(assistantSettingsWindow)
  await page.getByTitle(`Summon ${assistantName}`).click()
  const assistantDismiss = page.getByTitle(
    `Dismiss ${assistantName} (the thread is kept)`,
  )
  await assistantDismiss.waitFor({ state: "visible", timeout: 30_000 })
  const assistantInput = page.locator(".cm-placeholder", {
    hasText: "ask the desktop…",
  })
  await assistantInput.waitFor({ state: "visible", timeout: 30_000 })
  await page
    .getByText("Ask about the data, or ask me to put something up.", {
      exact: false,
    })
    .first()
    .waitFor({ state: "visible", timeout: 30_000 })
  await assistantDismiss.click()
  await assistantInput.waitFor({ state: "hidden", timeout: 15_000 })

  // The live bench has MCP activity but no configured server rows or ready
  // gateway. Assert that install readiness is surfaced and mutation controls
  // stay disabled.
  const mcpServersWindow = await openPaletteWindow(
    page,
    electronApp,
    "MCP",
    "mcp-servers",
  )
  await mcpServersWindow
    .getByText(`${rvbbitFixtures.mcp_server_count} servers`, { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await mcpServersWindow
    .getByText("MCP gateway runtime", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  const mcpServersText = await mcpServersWindow.innerText()
  assert(
    rvbbitFixtures.mcp_gateway_count === 0 &&
      mcpServersText.includes("MCP gateway runtime is not installed."),
    `MCP gateway empty state did not match the live registry: ${mcpServersText.slice(0, 700)}`,
  )
  assert(
    await mcpServersWindow.getByTitle("Register a new MCP server").isDisabled(),
    "MCP server registration was enabled without a ready gateway",
  )
  assert(
    await mcpServersWindow
      .getByTitle("Browse + install MCP capabilities from the catalog")
      .isDisabled(),
    "MCP catalog mutation was enabled without a ready gateway",
  )
  await closeDesktopWindow(mcpServersWindow)

  // Specialists: follow a registered backend into its live, read-only detail
  // instrument. Do not invoke its Run test action.
  const specialistsWindow = await openPaletteWindow(
    page,
    electronApp,
    "Specialists",
    "specialists",
  )
  await specialistsWindow
    .getByText(`${rvbbitFixtures.backend_count} backends`, { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await specialistsWindow
    .getByText(rvbbitFixtures.backend_name, { exact: true })
    .first()
    .click()
  const specialistDetailWindow = page
    .locator('[data-rvbbit-window-kind="specialist-detail"]')
    .last()
  await specialistDetailWindow.waitFor({ state: "visible", timeout: 15_000 })
  await specialistDetailWindow
    .getByText(rvbbitFixtures.backend_name, { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 30_000 })
  const specialistDetailText = await specialistDetailWindow.innerText()
  assert(
    specialistDetailText.includes("BACKEND") &&
      specialistDetailText.includes("LIVE TEST") &&
      (specialistDetailText.includes("RECENT CALLS") ||
        specialistDetailText.includes("no calls in the receipt log")),
    `Specialist detail did not render its core instruments: ${specialistDetailText.slice(0, 700)}`,
  )
  await specialistDetailWindow
    .getByRole("button", { name: "Run test", exact: true })
    .waitFor({ state: "visible", timeout: 15_000 })
  await closeDesktopWindow(specialistDetailWindow)
  await closeDesktopWindow(specialistsWindow)

  // Hindsight has a small but real local-schema corpus on the reference.
  // Traverse overview -> memory detail -> WebGL graph without calling recall.
  const hindsightWindow = await openPaletteWindow(
    page,
    electronApp,
    "Hindsight",
    "hindsight-memory",
  )
  await hindsightWindow
    .getByText(
      `${rvbbitFixtures.hindsight_memory_count} memories · ${rvbbitFixtures.hindsight_link_count} links`,
      { exact: true },
    )
    .waitFor({ state: "visible", timeout: 30_000 })
  assert(
    (await hindsightWindow.innerText()).includes("FOOTPRINT"),
    "Hindsight overview did not render its footprint instrument",
  )
  await hindsightWindow
    .getByRole("button", { name: "Memories", exact: true })
    .click()
  const hindsightMemoryText = hindsightWindow.getByText(
    rvbbitFixtures.hindsight_memory_text,
    { exact: true },
  )
  await hindsightMemoryText.waitFor({ state: "visible", timeout: 30_000 })
  await hindsightMemoryText.click()
  await hindsightWindow
    .getByText(/links \(\d+\)/i)
    .waitFor({ state: "visible", timeout: 30_000 })
  await hindsightWindow
    .getByRole("button", { name: "Graph", exact: true })
    .click()
  await hindsightWindow
    .locator(".kg-sigma-stage canvas")
    .first()
    .waitFor({ state: "visible", timeout: 30_000 })
  const hindsightGraphSummary = hindsightWindow
    .getByText(/^\d+n \d+e$/)
    .last()
  await hindsightGraphSummary.waitFor({ state: "visible", timeout: 30_000 })
  const hindsightGraphCounts = (await hindsightGraphSummary.innerText()).match(
    /^(\d+)n (\d+)e$/,
  )
  assert(
    Number(hindsightGraphCounts?.[1]) > 0 &&
      Number(hindsightGraphCounts?.[2]) > 0,
    `Hindsight graph did not expose non-empty topology counts: ${await hindsightGraphSummary.innerText()}`,
  )
  await closeDesktopWindow(hindsightWindow)

  // Cube and alert catalogs contain real definitions even though write-side
  // maintenance/sweeps stay out of this reference-safe test.
  const cubeCatalogWindow = await openPaletteWindow(
    page,
    electronApp,
    "Cube Catalog",
    "cube-catalog",
  )
  const cubesHeader = cubeCatalogWindow
    .getByText("Cubes", { exact: true })
    .last()
    .locator("..")
  await cubesHeader
    .getByText(String(rvbbitFixtures.cube_count), { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await cubeCatalogWindow
    .getByTitle(`Open ${rvbbitFixtures.cube_name} in the inspector`)
    .click()
  const cubeInspectorWindow = page
    .locator('[data-rvbbit-window-kind="cube-inspector"]')
    .last()
  await cubeInspectorWindow.waitFor({ state: "visible", timeout: 15_000 })
  await cubeInspectorWindow
    .getByText(rvbbitFixtures.cube_name, { exact: true })
    .last()
    .waitFor({ state: "visible", timeout: 30_000 })
  await cubeInspectorWindow
    .getByText("Definition SQL", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await cubeInspectorWindow
    .getByRole("button", { name: "Columns", exact: true })
    .click()
  await cubeInspectorWindow
    .getByText("Source", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await closeDesktopWindow(cubeInspectorWindow)
  await closeDesktopWindow(cubeCatalogWindow)

  // Cube authoring is tested as navigation and read-only catalog loading. The
  // AI proposer, pack preview/materialization, and Save controls remain
  // deliberately untouched on the shared reference.
  const cubeCreatorWindow = await openPaletteWindow(
    page,
    electronApp,
    "Cube Creator",
    "cube-creator",
  )
  await cubeCreatorWindow
    .getByRole("button", { name: "Propose", exact: true })
    .click()
  await cubeCreatorWindow
    .getByText("Describe the cube you want", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await cubeCreatorWindow
    .getByText(
      "An LLM drafts the join from the FK graph + table docs. You review and Save — nothing is created until you do.",
      { exact: true },
    )
    .waitFor({ state: "visible", timeout: 15_000 })
  await cubeCreatorWindow
    .getByRole("button", { name: "From Pack", exact: true })
    .click()
  const cubePackOption = cubeCreatorWindow.locator(
    `option[value="${rvbbitFixtures.cube_pack_key}"]`,
  )
  await cubePackOption.waitFor({ state: "attached", timeout: 30_000 })
  await cubeCreatorWindow
    .locator("select")
    .first()
    .selectOption(rvbbitFixtures.cube_pack_key)
  await cubeCreatorWindow
    .getByText(/Bindings \(\d+\)/)
    .waitFor({ state: "visible", timeout: 30_000 })
  await cubeCreatorWindow
    .getByRole("button", { name: "Preview SQL", exact: true })
    .waitFor({ state: "visible", timeout: 15_000 })
  await cubeCreatorWindow
    .getByRole("button", { name: "Create cube", exact: true })
    .waitFor({ state: "visible", timeout: 15_000 })
  await cubeCreatorWindow
    .getByText(rvbbitFixtures.cube_name, { exact: true })
    .first()
    .click()
  await cubeCreatorWindow
    .getByText(
      new RegExp(`Editing\\s+${rvbbitFixtures.cube_name}`),
    )
    .waitFor({ state: "visible", timeout: 30_000 })
  await cubeCreatorWindow
    .getByText("Live preview", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await closeDesktopWindow(cubeCreatorWindow)

  const cubeProposalsWindow = await openPaletteWindow(
    page,
    electronApp,
    "Proposals",
    "cube-proposals",
  )
  if (rvbbitFixtures.pending_proposal_count === 0) {
    await cubeProposalsWindow
      .getByText(
        "No proposals waiting. The agent's propose_cube drafts land here.",
        { exact: true },
      )
      .waitFor({ state: "visible", timeout: 30_000 })
  } else {
    await cubeProposalsWindow
      .getByText(
        `${rvbbitFixtures.pending_proposal_count} pending`,
        { exact: true },
      )
      .waitFor({ state: "visible", timeout: 30_000 })
  }
  await closeDesktopWindow(cubeProposalsWindow)

  const alertsWindow = await openPaletteWindow(
    page,
    electronApp,
    "Alerts",
    "alerts",
  )
  await alertsWindow
    .getByText("System", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await alertsWindow
    .getByText(rvbbitFixtures.alert_name, { exact: true })
    .first()
    .click()
  await alertsWindow
    .getByText(rvbbitFixtures.alert_description, { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await alertsWindow
    .getByRole("button", { name: "history ↗", exact: true })
    .waitFor({ state: "visible", timeout: 15_000 })
  await closeDesktopWindow(alertsWindow)

  // Deployment fleets are installed but currently empty. Their read-only
  // topology/readiness states must still be usable in the packaged runtime.
  const warrenWindow = await openPaletteWindow(
    page,
    electronApp,
    "Warren",
    "warren",
  )
  await warrenWindow
    .getByText(`${rvbbitFixtures.warren_node_count} nodes`, { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await warrenWindow
    .getByText(/no warren nodes registered\./i)
    .first()
    .waitFor({ state: "visible", timeout: 15_000 })
  await warrenWindow
    .getByRole("button", { name: "Jobs", exact: true })
    .click()
  await warrenWindow
    .getByText(`${rvbbitFixtures.warren_job_count} shown`, { exact: true })
    .waitFor({ state: "visible", timeout: 15_000 })
  await closeDesktopWindow(warrenWindow)

  const fleetWindow = await openPaletteWindow(
    page,
    electronApp,
    "Fleet",
    "fleet",
  )
  await fleetWindow
    .getByText("one brain, many muscles — workers earn queries by passing probes", {
      exact: true,
    })
    .waitFor({ state: "visible", timeout: 30_000 })
  assert(
    rvbbitFixtures.fleet_endpoint_count === 0,
    "The Fleet empty-state fixture unexpectedly has endpoints",
  )
  await fleetWindow
    .locator('svg[viewBox="0 0 920 380"]')
    .waitFor({ state: "visible", timeout: 15_000 })
  await fleetWindow
    .getByText("brain", { exact: true })
    .waitFor({ state: "visible", timeout: 15_000 })
  await closeDesktopWindow(fleetWindow)

  // Capability and ML authoring surfaces: inspect a real catalog pack and the
  // honest zero-model training form without installing or queueing work.
  const capabilitiesWindow = await openPaletteWindow(
    page,
    electronApp,
    "Capabilities",
    "capabilities",
  )
  await capabilitiesWindow
    .getByText(`${rvbbitFixtures.capability_count} packs`, { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  const capabilitySearch = capabilitiesWindow.getByPlaceholder(
    "Search title, model, description, operator…",
  )
  await capabilitySearch.fill(rvbbitFixtures.capability.title)
  await capabilitiesWindow
    .getByText(rvbbitFixtures.capability.title, { exact: true })
    .click()
  const capabilityDetailWindow = page
    .locator('[data-rvbbit-window-kind="capability-detail"]')
    .last()
  await capabilityDetailWindow.waitFor({ state: "visible", timeout: 15_000 })
  await capabilityDetailWindow
    .getByText(rvbbitFixtures.capability.title, { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  await capabilityDetailWindow
    .getByText(rvbbitFixtures.capability.name, { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 15_000 })
  const generatedSqlTab = capabilityDetailWindow.getByRole("button", {
    name: "Generated SQL",
    exact: true,
  })
  await generatedSqlTab.waitFor({ state: "visible", timeout: 15_000 })
  await generatedSqlTab.click()
  await capabilityDetailWindow
    .getByText("register.sql", { exact: true })
    .waitFor({ state: "visible", timeout: 15_000 })
  await closeDesktopWindow(capabilityDetailWindow)
  await closeDesktopWindow(capabilitiesWindow)

  const modelStudioWindow = await openPaletteWindow(
    page,
    electronApp,
    "Model Studio",
    "model-studio",
  )
  if (rvbbitFixtures.ml_model_count === 0) {
    await modelStudioWindow
      .getByText("No models yet. Create one →", { exact: true })
      .waitFor({ state: "visible", timeout: 30_000 })
    await modelStudioWindow
      .getByRole("button", { name: "New model from SQL", exact: true })
      .click()
    await modelStudioWindow
      .getByText("New model from SQL", { exact: true })
      .last()
      .waitFor({ state: "visible", timeout: 15_000 })
    await modelStudioWindow
      .getByRole("button", { name: "Queue training", exact: true })
      .waitFor({ state: "visible", timeout: 15_000 })
  } else {
    await modelStudioWindow
      .getByText(rvbbitFixtures.ml_model_name, { exact: true })
      .first()
      .waitFor({ state: "visible", timeout: 30_000 })
  }
  await closeDesktopWindow(modelStudioWindow)

  // Data Mover is launcher-gated by a registered Fletch runtime. When present,
  // loading status/drivers is read-only; never probe, install, dry-run, or
  // transfer. When absent, prove the unavailable action is not advertised.
  if (rvbbitFixtures.data_mover_runtime_count > 0) {
    const dataMoverWindow = await openPaletteWindow(
      page,
      electronApp,
      "Data Mover",
      "data-mover",
    )
    await dataMoverWindow
      .getByText("Fletch ADBC import/export runtime", { exact: true })
      .waitFor({ state: "visible", timeout: 30_000 })
    const dataMoverText = await dataMoverWindow.innerText()
    assert(
      ["SOURCE", "DESTINATION", "OPTIONS", "QUERY", "RUNTIME", "RESULT"].every(
        (title) => dataMoverText.includes(title),
      ),
      `Data Mover did not render its full transfer form: ${dataMoverText.slice(0, 700)}`,
    )
    await closeDesktopWindow(dataMoverWindow)
  } else {
    await electronApp.evaluate(({ Menu }) => {
      Menu.getApplicationMenu()
        ?.getMenuItemById("native-command-palette")
        ?.click()
    })
    const dataMoverPalette = page.locator("[data-rvbbit-command-palette]")
    await dataMoverPalette.waitFor({ state: "visible", timeout: 10_000 })
    await dataMoverPalette
      .getByPlaceholder("Search tables, views, actions…")
      .fill("Data Mover")
    assert(
      (await dataMoverPalette
        .getByText("Data Mover", { exact: true })
        .count()) === 0,
      "Data Mover launcher was advertised without a registered Fletch runtime",
    )
    await page.keyboard.press("Escape")
    await dataMoverPalette.waitFor({ state: "hidden", timeout: 10_000 })
  }

  // Server-level capability gates live in the menu bar. The current bench has
  // pg_cron available/preloaded but deliberately not created; GQE is configured
  // but not routable. Assert those states without creating/warming either.
  await page.getByTitle("Scheduled tasks (pg_cron)").click()
  await page
    .getByText("Scheduled Tasks", { exact: true })
    .waitFor({ state: "visible", timeout: 30_000 })
  if (!rvbbitFixtures.pg_cron_home_installed) {
    await page
      .getByText(/pg_cron is not set up/)
      .waitFor({ state: "visible", timeout: 15_000 })
    if (rvbbitFixtures.pg_cron_available && rvbbitFixtures.pg_cron_preloaded) {
      await page
        .getByRole("button", {
          name: /Create pg_cron in/,
        })
        .waitFor({ state: "visible", timeout: 15_000 })
    }
  } else {
    await page
      .getByText(
        new RegExp(`^${rvbbitFixtures.pg_cron_job_count} jobs?$`, "i"),
      )
      .waitFor({ state: "visible", timeout: 30_000 })
    if (rvbbitFixtures.pg_cron_job?.name) {
      const cronJobName = page
        .getByText(rvbbitFixtures.pg_cron_job.name, { exact: true })
        .first()
      await cronJobName.waitFor({ state: "visible", timeout: 15_000 })
      const cronJobRow = cronJobName.locator(
        "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' rounded-md ')][1]",
      )
      await cronJobRow.locator("button").first().click()
      await cronJobRow
        .getByText("recent runs", { exact: true })
        .waitFor({ state: "visible", timeout: 30_000 })
      if (rvbbitFixtures.pg_cron_job.latestStatus) {
        await cronJobRow
          .getByText(rvbbitFixtures.pg_cron_job.latestStatus, { exact: true })
          .first()
          .waitFor({ state: "visible", timeout: 30_000 })
      }
    }
  }
  await page.getByTitle("Scheduled tasks (pg_cron)").click()
  await page
    .getByText("Scheduled Tasks", { exact: true })
    .waitFor({ state: "hidden", timeout: 15_000 })

  const gqeRoutable =
    rvbbitFixtures.gqe_status?.binary_found === true &&
    rvbbitFixtures.gqe_status?.config_enabled === true &&
    rvbbitFixtures.gqe_status?.routes_available === true
  const gqeButton = page.getByRole("button", { name: "GPU Query Engine" })
  if (gqeRoutable) {
    await gqeButton.waitFor({ state: "visible", timeout: 30_000 })
  } else {
    assert(
      (await gqeButton.count()) === 0,
      "GQE menu control was visible even though the live status is not routable",
    )
  }

  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-scry")?.click()
  })
  await page.locator("[data-rvbbit-scry]").waitFor({ state: "visible", timeout: 10_000 })
  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-scry")?.click()
  })
  await page.locator("[data-rvbbit-scry]").waitFor({ state: "hidden", timeout: 10_000 })

  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-tile")?.click()
  })
  await page.locator('[data-tile-mode="true"]').waitFor({ state: "attached" })
  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-tile")?.click()
  })
  await page.locator('[data-tile-mode="false"]').waitFor({ state: "attached" })

  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-workspace-2")?.click()
  })
  await page.locator('[data-active-workspace="2"]').waitFor({ state: "attached" })
  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-workspace-1")?.click()
  })
  await page.locator('[data-active-workspace="1"]').waitFor({ state: "attached" })

  await page.screenshot({ path: screenshotPath })
  console.log(
    JSON.stringify(
      {
        ok: true,
        referenceUrl,
        packagedUrl: appUrl,
        screenshotPath,
        connection: packagedState.connection,
        rvbbitVersion: packagedState.rvbbit.rvbbitVersion,
        readOnlyQuery: packagedState.query.rows[0],
        rvbbitFixtures,
        readOnlyParity: {
          schema: packagedSurface.schema,
          extensions: packagedSurface.extensions,
          diagnostics: packagedSurface.diagnostics,
          fingerprintFastPath: packagedSurface.fingerprintFastPath,
          lensHomes: packagedSurface.lensHomes,
          capabilityTraversalGuard: packagedSurface.capabilityTraversalGuard,
          csvImportInspect: packagedSurface.csvImportInspect,
          secretsRedacted: true,
        },
        nativeMenuSurfaces: [
          "capabilities",
          "sql-editor-and-result",
          "sql-result-chart",
          "sql-chart-renderers",
          "finder",
          "finder-table-preview",
          "notifications",
          "scenes",
          "saved-views",
          "published-dashboard-query-broker",
          "plate-render-and-navigation",
          "layout-wall",
          "layout-stamping",
          "operator-registry-and-flow",
          "semantic-test-evidence",
          "mcp-incoming-activity",
          "model-catalog-settings",
          "routing-overlay",
          "routing-training-empty-state",
          "query-lens-receipt-trace",
          "knowledge-graph-entity",
          "knowledge-extraction-and-merge-empty-states",
          "document-brain-corpus",
          "metric-catalog-inspector-run",
          "metric-creator-resolved-preview",
          "viz-block-version-and-resolved-preview",
          "kpi-board-catalog-fallback",
          "cost-receipts-and-timeline",
          "temporal-mirror-empty-state",
          "duck-telemetry-empty-state",
          "assistant-identity-and-dock",
          "mcp-gateway-readiness",
          "specialist-backend-detail",
          "hindsight-memory-detail-and-graph",
          "cube-catalog-and-inspector",
          "cube-creator-propose-pack-and-edit-forms",
          "cube-proposals-inbox",
          "alert-rule-detail",
          "warren-empty-state",
          "fleet-topology-empty-state",
          "capability-pack-generated-sql",
          "model-studio-training-empty-state",
          "data-mover-availability-gate",
          "scheduler-pg-cron-job-history",
          "gqe-readiness-gate",
          "scry",
          "tile",
          "workspaces",
        ],
      },
      null,
      2,
    ),
  )
} finally {
  await electronApp?.close()

  if (appUrl) {
    let serverStopped = false
    try {
      await fetch(appUrl, { signal: AbortSignal.timeout(2_000) })
    } catch {
      serverStopped = true
    }
    assert(serverStopped, "Bundled server was still reachable after the app quit")
  }

  // This removes only the mkdtemp-created isolated copy; the reference
  // connection registry is never modified.
  await rm(testHome, { recursive: true, force: true })
}
