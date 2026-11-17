import { spawn, spawnSync } from "node:child_process"
import { access, mkdir, mkdtemp, rm } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { _electron as electron } from "playwright-core"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const appBundle =
  process.env.DATARABBIT_APP_BUNDLE ??
  path.join(root, "dist", "mac-arm64", "Data Rabbit.app")
const executable = path.join(appBundle, "Contents", "MacOS", "Data Rabbit")
const outputDir = path.join(root, "test-results")
const screenshotPath = path.join(outputDir, "macos-smoke.png")

await access(executable)
await mkdir(outputDir, { recursive: true })
const testHome = await mkdtemp(path.join(os.tmpdir(), "datarabbit-mac-smoke-"))

let electronApp
let appUrl

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

const signatureCheck = spawnSync(
  "/usr/bin/codesign",
  ["--verify", "--deep", "--strict", appBundle],
  { encoding: "utf8" },
)
assert(
  signatureCheck.status === 0,
  `Packaged app failed strict signature verification: ${
    signatureCheck.stderr?.trim() || signatureCheck.stdout?.trim() || signatureCheck.status
  }`,
)

function launchSecondInstance() {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, [], {
      env: {
        ...process.env,
        DATARABBIT_USER_DATA_DIR: testHome,
      },
      stdio: "ignore",
    })
    const timeout = setTimeout(() => {
      child.kill("SIGTERM")
      reject(new Error("Second Data Rabbit instance did not exit promptly"))
    }, 10_000)
    child.once("error", reject)
    child.once("exit", (code, signal) => {
      clearTimeout(timeout)
      resolve({ code, signal })
    })
  })
}

function waitForChildExit(child, timeoutMs) {
  if (child.exitCode !== null) {
    return Promise.resolve({ code: child.exitCode, signal: child.signalCode })
  }
  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      child.removeListener("exit", onExit)
      resolve(null)
    }, timeoutMs)
    const onExit = (code, signal) => {
      clearTimeout(timeout)
      resolve({ code, signal })
    }
    child.once("exit", onExit)
  })
}

async function quitPackagedApp(application) {
  if (!application) return true
  const child = application.process()
  if (child.exitCode !== null) return true
  const quitCall = application
    .evaluate(({ app }) => {
      app.quit()
      return true
    })
    .catch(() => undefined)
  await Promise.race([
    quitCall,
    new Promise((resolve) => setTimeout(resolve, 1_000)),
  ])
  // Playwright keeps the Electron main-process inspector attached. Releasing
  // that connection after the app's quit sequence lets the host process exit.
  void application.close().catch(() => undefined)

  if (await waitForChildExit(child, 2_000)) return true

  child.kill("SIGKILL")
  assert(
    await waitForChildExit(child, 5_000),
    "Playwright-instrumented Data Rabbit process could not be terminated",
  )
  return false
}

async function launchDirectQuitProbe() {
  const child = spawn(executable, [], {
    env: {
      ...process.env,
      DATARABBIT_USER_DATA_DIR: testHome,
      DATARABBIT_SMOKE_AUTO_QUIT_MS: "1000",
    },
    stdio: "ignore",
  })
  const outcome = await waitForChildExit(child, 60_000)
  if (!outcome) {
    child.kill("SIGTERM")
    throw new Error("Direct Data Rabbit quit probe timed out")
  }
  return outcome
}

async function assertServerStopped(url) {
  let stopped = false
  try {
    await fetch(url, { signal: AbortSignal.timeout(2_000) })
  } catch {
    stopped = true
  }
  assert(stopped, "Bundled server was still reachable after the app quit")
}

try {
  electronApp = await electron.launch({
    executablePath: executable,
    env: {
      ...process.env,
      DATARABBIT_USER_DATA_DIR: testHome,
    },
  })

  const page = await electronApp.firstWindow({ timeout: 60_000 })
  await page.waitForLoadState("domcontentloaded")
  await page.waitForFunction(
    () => document.documentElement.dataset.datarabbitDesktop === "true",
    undefined,
    { timeout: 60_000 },
  )
  await page.locator("[data-rvbbit-boot-overlay]").waitFor({
    state: "hidden",
    timeout: 10_000,
  })

  appUrl = page.url()
  const rendererState = await page.evaluate(() => ({
    title: document.title,
    desktop: document.documentElement.dataset.datarabbitDesktop,
    platform: document.documentElement.dataset.datarabbitPlatform,
    bridge: window.dataRabbitDesktop
      ? {
          isDesktopApp: window.dataRabbitDesktop.isDesktopApp,
          platform: window.dataRabbitDesktop.platform,
          electronVersion: window.dataRabbitDesktop.electronVersion,
        }
      : null,
    menuBarPadding: getComputedStyle(
      document.querySelector("header[data-desktop-menu-bar]"),
    ).paddingLeft,
    rendererGlobals: {
      require: typeof window.require,
      process: typeof window.process,
    },
  }))

  assert(rendererState.title === "Data Rabbit", `Unexpected title: ${rendererState.title}`)
  assert(rendererState.desktop === "true", "Desktop preload marker is missing")
  assert(rendererState.platform === "darwin", `Unexpected platform: ${rendererState.platform}`)
  assert(rendererState.bridge?.isDesktopApp === true, "Secure desktop bridge is missing")
  assert(rendererState.bridge?.electronVersion.startsWith("39."), "Unexpected Electron runtime")
  assert(rendererState.menuBarPadding === "82px", "macOS traffic-light spacing was not applied")
  assert(
    rendererState.rendererGlobals.require === "undefined" &&
      rendererState.rendererGlobals.process === "undefined",
    "Node globals leaked into the renderer",
  )
  const nativeSceneSnapshot = await page.evaluate(() =>
    window.dataRabbitDesktop?.captureSceneSnapshot(),
  )
  assert(
    nativeSceneSnapshot?.startsWith("data:image/jpeg;base64,"),
    "Native Scene capture did not return a JPEG data URL",
  )
  assert(nativeSceneSnapshot.length <= 360_000, "Native Scene capture exceeded its storage cap")

  const nativeState = await electronApp.evaluate(({ app, BrowserWindow, Menu }) => ({
    userData: app.getPath("userData"),
    webPreferences: BrowserWindow.getAllWindows()[0]?.webContents.getLastWebPreferences(),
    menuActions: [
      "native-new-sql",
      "native-finder",
      "native-connections",
      "native-capabilities",
      "native-notifications",
      "native-command-palette",
      "native-scry",
      "native-tile",
      "native-scenes",
      "native-saved-views",
      "native-workspace-1",
      "native-workspace-2",
      "native-workspace-3",
      "native-workspace-4",
      "native-workspace-5",
      "native-workspace-scene",
    ].filter((id) => Boolean(Menu.getApplicationMenu()?.getMenuItemById(id))),
  }))

  assert(nativeState.userData === testHome, "Native user-data isolation is not active")
  assert(nativeState.menuActions.length === 16, "One or more native menu actions are missing")
  assert(nativeState.webPreferences?.nodeIntegration === false, "nodeIntegration must stay disabled")
  assert(nativeState.webPreferences?.contextIsolation === true, "contextIsolation must stay enabled")
  assert(nativeState.webPreferences?.sandbox === true, "Renderer sandbox must stay enabled")
  assert(nativeState.webPreferences?.webSecurity === true, "Chromium web security must stay enabled")

  const bridgePolicy = await page.evaluate(() => {
    const bridge = window.dataRabbitDesktop
    const before = bridge?.isDesktopApp
    try {
      bridge.isDesktopApp = false
    } catch {
      // Frozen bridge assignments throw in strict-mode callers.
    }
    return {
      frozen: Object.isFrozen(bridge),
      keys: Object.keys(bridge ?? {}).sort(),
      unchanged: bridge?.isDesktopApp === before,
    }
  })
  assert(
    bridgePolicy.frozen &&
      bridgePolicy.unchanged &&
      JSON.stringify(bridgePolicy.keys) ===
        JSON.stringify(
          [
            "captureSceneSnapshot",
            "electronVersion",
            "isDesktopApp",
            "onMenuAction",
            "platform",
          ].sort(),
        ),
    "Desktop preload bridge was mutable or exposed an unexpected capability",
  )

  const permissionPolicy = await page.evaluate(async () => {
    const withTimeout = (promise, label) =>
      Promise.race([
        promise,
        new Promise((resolve) => setTimeout(() => resolve(`${label}:timeout`), 5_000)),
      ])
    const notification = await withTimeout(
      Notification.requestPermission().catch((error) => error.name),
      "notification",
    )
    const camera = await withTimeout(
      navigator.mediaDevices
        .getUserMedia({ video: true })
        .then((stream) => {
          for (const track of stream.getTracks()) track.stop()
          return "granted"
        })
        .catch((error) => error.name),
      "camera",
    )
    return { notification, camera }
  })
  assert(
    permissionPolicy.notification === "denied",
    `Notification permission was not denied: ${permissionPolicy.notification}`,
  )
  assert(
    ["NotAllowedError", "NotFoundError"].includes(permissionPolicy.camera),
    `Camera capture was unexpectedly available: ${permissionPolicy.camera}`,
  )

  const windowCountBeforeBlockedPopup = electronApp.windows().length
  const blockedPopup = await page.evaluate(() => {
    const opened = window.open("file:///tmp/datarabbit-blocked-popup.html", "_blank")
    return opened === null
  })
  await page.waitForTimeout(250)
  assert(
    blockedPopup &&
      electronApp.windows().length === windowCountBeforeBlockedPopup,
    "A non-HTTP popup escaped the native window-open policy",
  )
  const originBeforeBlockedNavigation = page.url()
  await page.evaluate(() => {
    const anchor = document.createElement("a")
    anchor.href = "data:text/html,datarabbit-blocked-navigation"
    anchor.textContent = "blocked navigation"
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  })
  await page.waitForTimeout(250)
  assert(
    page.url() === originBeforeBlockedNavigation,
    "Renderer navigated away from the private application origin",
  )

  const secondInstance = await launchSecondInstance()
  assert(secondInstance.code === 0, "Second app instance did not exit cleanly")
  assert(electronApp.windows().length === 1, "Second app instance created another window")

  await electronApp.evaluate(({ Menu }) => {
    const item = Menu.getApplicationMenu()?.getMenuItemById("native-connections")
    if (!item) throw new Error("Connections menu item is missing")
    item.click()
  })
  await page.locator('[data-rvbbit-window-kind="connections"]').waitFor({
    state: "visible",
    timeout: 10_000,
  })

  const connectionWindows = await page.locator('[data-rvbbit-window-kind="connections"]').count()
  assert(connectionWindows === 1, `Expected one Connections window, found ${connectionWindows}`)
  const connectionsWindow = page.locator('[data-rvbbit-window-kind="connections"]')
  const initialWindowBox = await connectionsWindow.boundingBox()
  const titleBar = connectionsWindow.locator(":scope > div").first()
  const titleBarBox = await titleBar.boundingBox()
  assert(initialWindowBox && titleBarBox, "Connections window geometry was unavailable")
  await page.mouse.move(titleBarBox.x + Math.min(160, titleBarBox.width / 3), titleBarBox.y + 18)
  await page.mouse.down()
  await page.mouse.move(
    titleBarBox.x + Math.min(160, titleBarBox.width / 3) + 80,
    titleBarBox.y + 58,
    { steps: 6 },
  )
  await page.mouse.up()
  await page.waitForTimeout(400)
  const movedWindowBox = await connectionsWindow.boundingBox()
  assert(
    movedWindowBox &&
      movedWindowBox.x - initialWindowBox.x > 50 &&
      movedWindowBox.y - initialWindowBox.y > 25,
    "Desktop window drag did not persist its new position",
  )

  const resizeHandle = connectionsWindow.getByRole("button", {
    name: "Resize",
    exact: true,
  })
  const resizeHandleBox = await resizeHandle.boundingBox()
  assert(resizeHandleBox, "Desktop window resize handle was unavailable")
  await page.mouse.move(
    resizeHandleBox.x + resizeHandleBox.width / 2,
    resizeHandleBox.y + resizeHandleBox.height / 2,
  )
  await page.mouse.down()
  await page.mouse.move(
    resizeHandleBox.x + resizeHandleBox.width / 2 + 90,
    resizeHandleBox.y + resizeHandleBox.height / 2 + 70,
    { steps: 6 },
  )
  await page.mouse.up()
  await page.waitForTimeout(400)
  const resizedWindowBox = await connectionsWindow.boundingBox()
  assert(
    resizedWindowBox &&
      resizedWindowBox.width - movedWindowBox.width > 60 &&
      resizedWindowBox.height - movedWindowBox.height > 45,
    "Desktop window resize did not persist its new size",
  )

  await connectionsWindow.getByTitle("Minimize").click()
  await connectionsWindow.waitFor({ state: "detached", timeout: 10_000 })
  const minimizedDockButton = page
    .locator('div.fixed.bottom-3 button[title="Connections"]')
    .last()
  await minimizedDockButton.waitFor({ state: "visible", timeout: 10_000 })
  await minimizedDockButton.click()
  await connectionsWindow.waitFor({ state: "visible", timeout: 10_000 })
  const restoredWindowBox = await connectionsWindow.boundingBox()
  assert(
    restoredWindowBox &&
      Math.abs(restoredWindowBox.width - resizedWindowBox.width) < 2 &&
      Math.abs(restoredWindowBox.height - resizedWindowBox.height) < 2,
    "Restored desktop window lost its resized geometry",
  )

  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-capabilities")?.click()
  })
  const capabilitiesWindow = page.locator('[data-rvbbit-window-kind="capabilities"]')
  await capabilitiesWindow.waitFor({ state: "visible", timeout: 10_000 })
  assert(
    (await capabilitiesWindow.getAttribute("data-focused")) === "true",
    "Newly opened desktop window was not focused",
  )
  await connectionsWindow.evaluate((element) => {
    element.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }))
  })
  await page.waitForFunction(
    () =>
      document
        .querySelector('[data-rvbbit-window-kind="connections"]')
        ?.getAttribute("data-focused") === "true",
    undefined,
    { timeout: 10_000 },
  )

  const apiState = await page.evaluate(async () => {
    const [connections, homes] = await Promise.all([
      fetch("/api/db/connections").then((response) => response.json()),
      fetch("/api/lens/homes").then((response) => response.json()),
    ])
    return { connections, homes }
  })
  assert(Array.isArray(apiState.connections.connections), "Connections API did not return a list")

  await page.screenshot({ path: screenshotPath })
  const reopenedWindowPromise = electronApp.waitForEvent("window", { timeout: 30_000 })
  const closedWindowPromise = page.waitForEvent("close", { timeout: 10_000 })
  await electronApp.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()[0]?.close()
  })
  await closedWindowPromise
  assert(electronApp.windows().length === 0, "The main window did not close")
  await electronApp.evaluate(({ app }) => app.emit("activate"))
  const reopenedPage = await reopenedWindowPromise
  await reopenedPage.waitForLoadState("domcontentloaded")
  await reopenedPage.waitForFunction(
    () => document.documentElement.dataset.datarabbitDesktop === "true",
    undefined,
    { timeout: 30_000 },
  )
  assert(reopenedPage.url() === appUrl, "Dock activation did not reuse the stable server")

  const playwrightExitedWithoutSignal = await quitPackagedApp(electronApp)
  electronApp = undefined
  await assertServerStopped(appUrl)

  const directQuit = await launchDirectQuitProbe()
  assert(directQuit.code === 0, "Direct packaged app did not quit with exit code 0")
  await assertServerStopped(appUrl)

  console.log(
    JSON.stringify(
      {
        ok: true,
        appBundle,
        bundleSignatureValid: true,
        appUrl,
        screenshotPath,
        rendererState,
        nativeSceneCaptureBytes: nativeSceneSnapshot.length,
        secondInstanceRejected: true,
        dockReopenReusedServer: true,
        directQuitClean: true,
        playwrightExitedWithoutSignal,
        desktopWindowGestures: {
          drag: true,
          resize: true,
          minimizeRestore: true,
          focus: true,
        },
        nativeSecurity: {
          frozenAllowlistedBridge: true,
          cameraUnavailable: permissionPolicy.camera,
          notificationsDenied: true,
          nonHttpPopupDenied: true,
          crossOriginNavigationDenied: true,
        },
        nativeMenuActions: nativeState.menuActions,
      },
      null,
      2,
    ),
  )
} finally {
  await quitPackagedApp(electronApp)
  if (appUrl) await assertServerStopped(appUrl)

  await rm(testHome, { recursive: true, force: true })
}
