import { access, chmod, copyFile, mkdir, mkdtemp, rm } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { _electron as electron } from "playwright-core"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const referenceRegistry =
  process.env.DATARABBIT_REFERENCE_CONNECTIONS ??
  path.join(os.homedir(), ".config", "rvbbit-lens", "connections.json")
const appBundle =
  process.env.DATARABBIT_APP_BUNDLE ??
  path.join(root, "dist", "mac-arm64", "Data Rabbit.app")
const executable = path.join(appBundle, "Contents", "MacOS", "Data Rabbit")
const sceneName = "Native Persistence Smoke"
const viewName = "Native Saved View"
const viewDescription = "Packaged relaunch execution proof"
const viewIconKey = "database"
const viewIconColor = "oklch(74% 0.18 295)"
const rendererDiagnostics = []

await access(executable)
await access(referenceRegistry)

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

async function launch(testHome) {
  const electronApp = await electron.launch({
    executablePath: executable,
    env: {
      ...process.env,
      DATARABBIT_USER_DATA_DIR: testHome,
      DATARABBIT_READ_ONLY_TEST: "1",
    },
  })
  const page = await electronApp.firstWindow({ timeout: 60_000 })
  page.on("pageerror", (error) => rendererDiagnostics.push(`pageerror: ${error.message}`))
  page.on("console", (message) => {
    if (message.type() === "error" || message.text().includes("[scene-snapshot]")) {
      rendererDiagnostics.push(`${message.type()}: ${message.text()}`)
    }
  })
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
  return { electronApp, page, appUrl: page.url() }
}

async function clickNativeMenu(electronApp, id) {
  await electronApp.evaluate(({ Menu }, menuId) => {
    const item = Menu.getApplicationMenu()?.getMenuItemById(menuId)
    if (!item) throw new Error(`${menuId} menu item is missing`)
    item.click()
  }, id)
}

async function closeAndAssertStopped(run) {
  await run.electronApp.close()
  let serverStopped = false
  try {
    await fetch(run.appUrl, { signal: AbortSignal.timeout(2_000) })
  } catch {
    serverStopped = true
  }
  assert(serverStopped, "Bundled server was still reachable after the app quit")
}

const testHome = await mkdtemp(path.join(os.tmpdir(), "datarabbit-persistence-"))
const testConfig = path.join(testHome, "config")
await mkdir(testConfig, { recursive: true, mode: 0o700 })
const testRegistry = path.join(testConfig, "connections.json")
await copyFile(referenceRegistry, testRegistry)
await chmod(testRegistry, 0o600)

let currentRun

try {
  currentRun = await launch(testHome)
  await clickNativeMenu(currentRun.electronApp, "native-new-sql")
  const sqlWindow = currentRun.page.locator('[data-rvbbit-window-kind="data"]').last()
  await sqlWindow.waitFor({ state: "visible", timeout: 10_000 })
  await sqlWindow.getByTitle("Run (⌘↩)").click()
  await sqlWindow.locator("[data-rvbbit-single-cell]").waitFor({
    state: "visible",
    timeout: 30_000,
  })

  const sceneMenu = currentRun.page
    .locator('header[data-desktop-menu-bar] button[title^="Scenes"]')
    .first()
  await sceneMenu.click()
  await currentRun.page.getByPlaceholder("Save this desktop as…").fill(sceneName)
  await currentRun.page.getByTitle("Save as a new Scene").click()
  try {
    await currentRun.page.waitForFunction(
      (expectedName) => {
        const raw = window.localStorage.getItem("rvbbit-lens.scenes.v1")
        if (!raw) return false
        try {
          const store = JSON.parse(raw)
          return store.scenes?.some((scene) => scene.name === expectedName)
        } catch {
          return false
        }
      },
      sceneName,
      { timeout: 60_000 },
    )
  } catch (error) {
    const state = await currentRun.page.evaluate(() => ({
      activeWorkspace: document.querySelector("[data-active-workspace]")?.getAttribute("data-active-workspace"),
      sceneStore: window.localStorage.getItem("rvbbit-lens.scenes.v1"),
      menuTitles: [...document.querySelectorAll("header button[title]")].map((button) =>
        button.getAttribute("title"),
      ),
    }))
    throw new Error(
      `Scene save did not commit. State: ${JSON.stringify(state)}. Renderer: ${rendererDiagnostics.join(" | ")}`,
      { cause: error },
    )
  }

  await clickNativeMenu(currentRun.electronApp, "native-saved-views")
  const initialViewsWindow = currentRun.page.locator('[data-rvbbit-window-kind="view-apps"]')
  await initialViewsWindow.waitFor({ state: "visible", timeout: 10_000 })
  await initialViewsWindow.getByRole("button", { name: "New" }).click()
  const builder = currentRun.page.locator('[data-rvbbit-window-kind="view-app-builder"]')
  await builder.waitFor({ state: "visible", timeout: 10_000 })
  await builder.getByPlaceholder("Daily orders").fill(viewName)
  await builder.locator("textarea").fill(viewDescription)
  await builder.getByTitle("Database").click()
  await builder.getByTitle("violet").click()
  await builder.getByRole("button", { name: "Create app" }).click()
  await builder.getByRole("button", { name: "Saved" }).waitFor({
    state: "visible",
    timeout: 10_000,
  })
  await currentRun.page.waitForFunction(
    ({ name, description, iconKey, iconColor }) => {
      try {
        const apps = JSON.parse(
          window.localStorage.getItem("rvbbit-lens.view-apps.v1") ?? "[]",
        )
        return apps.some(
          (app) =>
            app.name === name &&
            app.description === description &&
            app.iconKey === iconKey &&
            app.iconColor === iconColor &&
            app.sql.trim() === "SELECT 1;" &&
            typeof app.connectionId === "string",
        )
      } catch {
        return false
      }
    },
    {
      name: viewName,
      description: viewDescription,
      iconKey: viewIconKey,
      iconColor: viewIconColor,
    },
    { timeout: 10_000 },
  )

  await closeAndAssertStopped(currentRun)
  currentRun = undefined

  currentRun = await launch(testHome)
  await clickNativeMenu(currentRun.electronApp, "native-scenes")
  const scenesWindow = currentRun.page.locator('[data-rvbbit-window-kind="scenes"]')
  await scenesWindow.getByText(sceneName, { exact: true }).waitFor({
    state: "visible",
    timeout: 10_000,
  })

  await clickNativeMenu(currentRun.electronApp, "native-saved-views")
  const viewsWindow = currentRun.page.locator('[data-rvbbit-window-kind="view-apps"]')
  await viewsWindow.getByText(viewName, { exact: true }).waitFor({
    state: "visible",
    timeout: 10_000,
  })
  await viewsWindow.getByText(viewName, { exact: true }).click()
  const restoredView = currentRun.page.locator('[data-rvbbit-window-kind="data"]').last()
  await restoredView.waitFor({ state: "visible", timeout: 10_000 })
  const restoredCallout = restoredView.locator("[data-rvbbit-single-cell]")
  await restoredCallout.waitFor({ state: "visible", timeout: 30_000 })
  assert(
    (await restoredCallout.textContent())?.includes("1"),
    "Persisted Saved View did not execute its SELECT 1 query after relaunch",
  )

  console.log(
    JSON.stringify(
      {
        ok: true,
        appBundle,
        userDataIsolation: true,
        scenePersistedAcrossRelaunch: sceneName,
        savedViewPersistedAcrossRelaunch: viewName,
        savedViewReopened: true,
        savedViewExecutedAfterRelaunch: true,
        savedViewMetadataPersisted: {
          description: true,
          connection: true,
          customIcon: viewIconKey,
          customColor: "violet",
        },
      },
      null,
      2,
    ),
  )
} finally {
  if (currentRun) await currentRun.electronApp.close()
  await rm(testHome, { recursive: true, force: true })
}
