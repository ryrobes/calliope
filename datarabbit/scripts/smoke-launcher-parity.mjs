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

await access(executable)
await access(referenceRegistry)

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

async function referencePanelIds() {
  const connectionsBody = await fetch(`${referenceUrl}/api/db/connections`).then(
    (response) => response.json(),
  )
  const connections = Array.isArray(connectionsBody.connections)
    ? connectionsBody.connections
    : []
  const connection = connections.find((candidate) => candidate.isDefault) ?? connections[0]
  assert(connection?.id, "The port-3000 reference has no active connection")

  const query = await fetch(`${referenceUrl}/api/db/query`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      connectionId: connection.id,
      sql: "SELECT id, label, folder FROM rvbbit.desktop_panels ORDER BY id",
      rowLimit: 250,
      readOnly: true,
    }),
  }).then((response) => response.json())
  assert(query.ok === true, `Could not read the reference panel registry: ${query.error}`)
  return {
    connection,
    ids: query.rows.map((row) => String(row.id)).sort(),
  }
}

async function activateWindowLauncher(page, launcher, id, context) {
  const before = await page.locator("[data-rvbbit-window]").count()
  await launcher.evaluate((element) => element.click())
  await page.waitForFunction(
    (expected) => document.querySelectorAll("[data-rvbbit-window]").length > expected,
    before,
    { timeout: 10_000 },
  )

  const focused = page.locator('[data-rvbbit-window][data-focused="true"]')
  await focused.waitFor({ state: "visible", timeout: 10_000 })
  const kind = await focused.getAttribute("data-rvbbit-window-kind")
  assert(kind, `${context} launcher ${id} did not focus a typed desktop window`)

  // Leave enough time for the mounted surface to start its initial read before
  // closing it. This catches render-time faults without retaining dozens of
  // heavyweight windows for the rest of the sweep.
  await page.waitForTimeout(175)
  const closeButton = focused.getByTitle("Close")
  await closeButton.evaluate((element) => element.click())
  await page.waitForFunction(
    (expected) => document.querySelectorAll("[data-rvbbit-window]").length === expected,
    before,
    { timeout: 10_000 },
  )
  return kind
}

const reference = await referencePanelIds()
const testHome = await mkdtemp(path.join(os.tmpdir(), "datarabbit-launcher-parity-"))
const testConfig = path.join(testHome, "config")
await mkdir(testConfig, { recursive: true, mode: 0o700 })
const testRegistry = path.join(testConfig, "connections.json")
await copyFile(referenceRegistry, testRegistry)
await chmod(testRegistry, 0o600)

let electronApp
let appUrl
const pageErrors = []

try {
  electronApp = await electron.launch({
    executablePath: executable,
    env: {
      ...process.env,
      DATARABBIT_USER_DATA_DIR: testHome,
      DATARABBIT_READ_ONLY_TEST: "1",
    },
  })

  const page = await electronApp.firstWindow({ timeout: 60_000 })
  page.on("pageerror", (error) => pageErrors.push(error.message))
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
  await page.getByRole("button", { name: "Capabilities", exact: true }).waitFor({
    state: "visible",
    timeout: 60_000,
  })
  appUrl = page.url()

  const observed = []
  const directIds = await page
    .locator('[data-rvbbit-launcher-id]:not([data-rvbbit-launcher-id^="folder:"])')
    .evaluateAll((elements) =>
      elements.map((element) => element.getAttribute("data-rvbbit-launcher-id")).filter(Boolean),
    )

  for (const id of directIds) {
    const launcher = page.locator(`[data-rvbbit-launcher-id="${id}"]`).first()
    const kind = await activateWindowLauncher(page, launcher, id, "desktop")
    observed.push({ id, kind, folder: null })
  }

  const folderIds = await page
    .locator('[data-rvbbit-launcher-id^="folder:"]')
    .evaluateAll((elements) =>
      elements.map((element) => element.getAttribute("data-rvbbit-launcher-id")).filter(Boolean),
    )

  for (const folderLauncherId of folderIds) {
    const folderId = folderLauncherId.slice("folder:".length)
    const before = await page.locator("[data-rvbbit-window]").count()
    await page
      .locator(`[data-rvbbit-launcher-id="${folderLauncherId}"]`)
      .evaluate((element) => element.click())
    await page.waitForFunction(
      (expected) => document.querySelectorAll("[data-rvbbit-window]").length > expected,
      before,
      { timeout: 10_000 },
    )

    const folderWindow = page.locator(
      `[data-rvbbit-window-kind="folder"]:has([data-rvbbit-folder-id="${folderId}"])`,
    )
    await folderWindow.waitFor({ state: "visible", timeout: 10_000 })
    const folderWindowId = await folderWindow.getAttribute("data-rvbbit-window-id")
    assert(folderWindowId, `Folder ${folderId} has no window id`)
    const itemIds = await folderWindow
      .locator("[data-rvbbit-launcher-id]")
      .evaluateAll((elements) =>
        elements.map((element) => element.getAttribute("data-rvbbit-launcher-id")).filter(Boolean),
      )

    for (const id of itemIds) {
      const stableFolder = page.locator(`[data-rvbbit-window-id="${folderWindowId}"]`)
      const launcher = stableFolder.locator(`[data-rvbbit-launcher-id="${id}"]`)
      const kind = await activateWindowLauncher(page, launcher, id, `folder ${folderId}`)
      observed.push({ id, kind, folder: folderId })
    }

    const stableFolder = page.locator(`[data-rvbbit-window-id="${folderWindowId}"]`)
    await stableFolder.getByTitle("Close").evaluate((element) => element.click())
    await page.waitForFunction(
      (expected) => document.querySelectorAll("[data-rvbbit-window]").length === expected,
      before,
      { timeout: 10_000 },
    )
  }

  const observedIds = observed.map((surface) => surface.id).sort()
  const missing = reference.ids.filter((id) => !observedIds.includes(id))
  const extra = observedIds.filter((id) => !reference.ids.includes(id))
  assert(
    missing.length === 0,
    `Packaged app is missing reference launchers: ${missing.join(", ")}`,
  )
  assert(observed.length >= 50, `Expected at least 50 feature launchers, found ${observed.length}`)
  assert(pageErrors.length === 0, `Renderer errors during launcher sweep: ${pageErrors.join(" | ")}`)

  console.log(
    JSON.stringify(
      {
        ok: true,
        referenceUrl,
        packagedUrl: appUrl,
        connection: {
          label: reference.connection.label,
          database: reference.connection.database,
        },
        referencePanelCount: reference.ids.length,
        packagedLauncherCount: observed.length,
        packagedExtrasBeyondReferenceRegistry: extra,
        surfaces: observed,
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

  await rm(testHome, { recursive: true, force: true })
}
