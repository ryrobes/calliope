"use strict"
/* eslint-disable @typescript-eslint/no-require-imports */

const { app, BrowserWindow, Menu, dialog, ipcMain, session, shell } = require("electron")
const { execFile, spawn } = require("node:child_process")
const fs = require("node:fs")
const http = require("node:http")
const net = require("node:net")
const path = require("node:path")

const APP_NAME = "Data Rabbit"
const DEV_SERVER_URL = process.env.DATARABBIT_DEV_SERVER_URL || "http://127.0.0.1:3100"
const DEFAULT_PACKAGED_SERVER_PORT = 43129
const SERVER_START_TIMEOUT_MS = 60_000
const SCENE_SNAPSHOT_WIDTH = 880
const SCENE_SNAPSHOT_MAX_BYTES = 360_000

let mainWindow = null
let serverProcess = null
let serverUrl = null
let serverLogStream = null
let serverLogPath = null
let isQuitting = false

app.setName(APP_NAME)
if (process.env.DATARABBIT_USER_DATA_DIR) {
  app.setPath("userData", path.resolve(process.env.DATARABBIT_USER_DATA_DIR))
}

const hasSingleInstanceLock = app.requestSingleInstanceLock()
if (!hasSingleInstanceLock) {
  app.quit()
}

function packagedServerPort() {
  const configured = Number(process.env.DATARABBIT_SERVER_PORT)
  if (
    process.env.DATARABBIT_SERVER_PORT &&
    (!Number.isInteger(configured) || configured < 1024 || configured > 65535)
  ) {
    throw new Error("DATARABBIT_SERVER_PORT must be an integer from 1024 through 65535")
  }
  return configured || DEFAULT_PACKAGED_SERVER_PORT
}

function assertPortAvailable(port) {
  return new Promise((resolve, reject) => {
    const probe = net.createServer()
    probe.unref()
    probe.on("error", (error) => {
      reject(
        new Error(
          `Data Rabbit's local port ${port} is unavailable. ` +
            `Quit the process using it or set DATARABBIT_SERVER_PORT to another stable port.\n${String(error)}`,
        ),
      )
    })
    probe.listen(port, "127.0.0.1", () => {
      probe.close((error) => {
        if (error) reject(error)
        else resolve()
      })
    })
  })
}

function waitForServer(url) {
  const deadline = Date.now() + SERVER_START_TIMEOUT_MS

  return new Promise((resolve, reject) => {
    const poll = () => {
      if (serverProcess?.exitCode !== null) {
        reject(new Error(`The Data Rabbit server exited before startup (code ${serverProcess.exitCode}).`))
        return
      }

      const request = http.get(url, (response) => {
        response.resume()
        if ((response.statusCode || 500) < 500) {
          resolve()
          return
        }
        retry()
      })
      request.setTimeout(1_000, () => request.destroy())
      request.on("error", retry)
    }

    const retry = () => {
      if (Date.now() >= deadline) {
        reject(new Error("The Data Rabbit server did not become ready within 60 seconds."))
        return
      }
      setTimeout(poll, 250)
    }

    poll()
  })
}

function macAppPath() {
  const inheritedPath = process.env.PATH || ""
  const additions = ["/opt/homebrew/bin", "/usr/local/bin"]
  return [...additions, inheritedPath].filter(Boolean).join(path.delimiter)
}

async function startBundledServer() {
  if (serverProcess && serverProcess.exitCode === null && serverUrl) {
    return serverUrl
  }

  const serverRoot = await ensureBundledServer()
  const serverEntry = path.join(serverRoot, "server.js")
  if (!fs.existsSync(serverEntry)) {
    throw new Error(`Packaged server is missing: ${serverEntry}`)
  }

  // Chromium storage is origin-scoped, including the port. A stable private
  // origin is therefore required for localStorage/IndexedDB-backed Scenes,
  // Saved Views, wallpapers, and desktop state to survive app relaunches.
  const port = packagedServerPort()
  await assertPortAvailable(port)
  const userData = app.getPath("userData")
  const logsDir = path.join(userData, "logs")
  fs.mkdirSync(logsDir, { recursive: true })
  serverLogPath = path.join(logsDir, "server.log")
  serverLogStream = fs.createWriteStream(serverLogPath, { flags: "a" })
  serverLogStream.write(`\n[${new Date().toISOString()}] Starting Data Rabbit ${app.getVersion()}\n`)

  const env = {
    ...process.env,
    ELECTRON_RUN_AS_NODE: "1",
    HOSTNAME: "127.0.0.1",
    PORT: String(port),
    NODE_ENV: "production",
    NEXT_TELEMETRY_DISABLED: "1",
    PATH: macAppPath(),
    RVBBIT_LENS_HOME: path.join(userData, "config"),
    RVBBIT_LOCAL_WORK_ROOT: path.join(userData, "workspaces"),
    LENS_DATA_DIR: path.join(userData, "lens-data"),
  }

  serverProcess = spawn(process.execPath, [serverEntry], {
    cwd: serverRoot,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  })
  serverProcess.stdout.pipe(serverLogStream, { end: false })
  serverProcess.stderr.pipe(serverLogStream, { end: false })
  serverProcess.once("exit", (code, signal) => {
    serverLogStream?.write(
      `[${new Date().toISOString()}] Server exited (code=${String(code)}, signal=${String(signal)})\n`,
    )
    if (!isQuitting && mainWindow && !mainWindow.isDestroyed()) {
      void dialog.showMessageBox(mainWindow, {
        type: "error",
        title: "Data Rabbit server stopped",
        message: "The local Data Rabbit server stopped unexpectedly.",
        detail: serverLogPath ? `Diagnostic log: ${serverLogPath}` : undefined,
      })
    }
    serverProcess = null
    serverUrl = null
  })

  const url = `http://127.0.0.1:${port}`
  await waitForServer(url)
  serverUrl = url
  return url
}

function execFileAsync(executable, args) {
  return new Promise((resolve, reject) => {
    execFile(executable, args, (error) => {
      if (error) reject(error)
      else resolve()
    })
  })
}

async function ensureBundledServer() {
  const archivePath = path.join(process.resourcesPath, "app-server.tar.gz")
  if (!fs.existsSync(archivePath)) {
    throw new Error(`Packaged server archive is missing: ${archivePath}`)
  }

  const runtimeParent = path.join(app.getPath("userData"), "runtime")
  const runtimeRoot = path.join(runtimeParent, app.getVersion())
  const serverEntry = path.join(runtimeRoot, "server.js")
  const markerPath = path.join(runtimeRoot, ".archive-size")
  const archiveSize = String(fs.statSync(archivePath).size)

  if (
    fs.existsSync(serverEntry) &&
    fs.existsSync(markerPath) &&
    fs.readFileSync(markerPath, "utf8") === archiveSize
  ) {
    return runtimeRoot
  }

  fs.mkdirSync(runtimeParent, { recursive: true })
  const temporaryRoot = path.join(runtimeParent, `${app.getVersion()}.tmp-${process.pid}`)
  fs.rmSync(temporaryRoot, { recursive: true, force: true })
  fs.mkdirSync(temporaryRoot, { recursive: true })

  try {
    await execFileAsync("/usr/bin/tar", ["-xzf", archivePath, "-C", temporaryRoot])
    if (!fs.existsSync(path.join(temporaryRoot, "server.js"))) {
      throw new Error("The packaged server archive did not contain server.js")
    }
    fs.writeFileSync(path.join(temporaryRoot, ".archive-size"), archiveSize)
    fs.rmSync(runtimeRoot, { recursive: true, force: true })
    fs.renameSync(temporaryRoot, runtimeRoot)
  } catch (error) {
    fs.rmSync(temporaryRoot, { recursive: true, force: true })
    throw error
  }

  return runtimeRoot
}

function sendMenuAction(action) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("datarabbit:menu-action", action)
  }
}

ipcMain.handle("datarabbit:capture-scene", async (event) => {
  if (
    !mainWindow ||
    mainWindow.isDestroyed() ||
    event.sender !== mainWindow.webContents
  ) {
    return null
  }

  try {
    const image = await mainWindow.webContents.capturePage()
    const { width } = image.getSize()
    const thumbnail =
      width > SCENE_SNAPSHOT_WIDTH
        ? image.resize({ width: SCENE_SNAPSHOT_WIDTH, quality: "good" })
        : image
    for (const quality of [70, 50]) {
      const jpeg = thumbnail.toJPEG(quality)
      if (jpeg.byteLength <= SCENE_SNAPSHOT_MAX_BYTES) {
        return `data:image/jpeg;base64,${jpeg.toString("base64")}`
      }
    }
  } catch (error) {
    serverLogStream?.write(
      `[${new Date().toISOString()}] Scene capture failed: ${String(error)}\n`,
    )
  }
  return null
})

function showAbout() {
  void dialog.showMessageBox(mainWindow || undefined, {
    type: "info",
    title: `About ${APP_NAME}`,
    message: APP_NAME,
    detail: `Version ${app.getVersion()}\nLocal-first SQL desktop for Postgres + rvbbit`,
    buttons: ["OK"],
  })
}

function installApplicationMenu() {
  const workspaceItems = ["1", "2", "3", "4", "5"].map((number) => ({
    id: `native-workspace-${number}`,
    label: `Workspace ${number}`,
    accelerator: `Alt+${number}`,
    click: () => sendMenuAction(`workspace-${number}`),
  }))

  const template = [
    {
      label: APP_NAME,
      submenu: [
        { label: `About ${APP_NAME}`, click: showAbout },
        { type: "separator" },
        { role: "services" },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    {
      label: "File",
      submenu: [
        { id: "native-new-sql", label: "New SQL Window", accelerator: "CmdOrCtrl+N", click: () => sendMenuAction("new-sql") },
        { id: "native-finder", label: "Open Finder", accelerator: "CmdOrCtrl+F", click: () => sendMenuAction("finder") },
        { type: "separator" },
        { role: "close" },
      ],
    },
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "pasteAndMatchStyle" },
        { role: "delete" },
        { role: "selectAll" },
      ],
    },
    {
      label: "Database",
      submenu: [
        { id: "native-connections", label: "Connections", click: () => sendMenuAction("connections") },
        { id: "native-capabilities", label: "Capabilities", click: () => sendMenuAction("capabilities") },
        { id: "native-notifications", label: "Notifications", click: () => sendMenuAction("notifications") },
      ],
    },
    {
      label: "Desktop",
      submenu: [
        { id: "native-command-palette", label: "Command Palette", accelerator: "CmdOrCtrl+P", click: () => sendMenuAction("command-palette") },
        { id: "native-scry", label: "Scry", accelerator: "CmdOrCtrl+K", click: () => sendMenuAction("scry") },
        { id: "native-tile", label: "Tile Windows", accelerator: "Alt+T", click: () => sendMenuAction("tile") },
        { type: "separator" },
        { id: "native-scenes", label: "Scenes", click: () => sendMenuAction("scenes") },
        { id: "native-saved-views", label: "Saved Views", click: () => sendMenuAction("saved-views") },
        { type: "separator" },
        ...workspaceItems,
        {
          id: "native-workspace-scene",
          label: "Scene Workspace",
          accelerator: "Alt+6",
          click: () => sendMenuAction("workspace-scene"),
        },
      ],
    },
    {
      label: "View",
      submenu: [
        ...(app.isPackaged
          ? []
          : [
              { role: "reload" },
              { role: "forceReload" },
              { role: "toggleDevTools" },
              { type: "separator" },
            ]),
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    { role: "windowMenu" },
    {
      role: "help",
      submenu: [
        {
          label: "Data Rabbit Documentation",
          click: () => void shell.openExternal("https://rvbbit.ai/docs/data-rabbit"),
        },
        ...(serverLogPath
          ? [
              {
                label: "Show Server Log",
                click: () => shell.showItemInFolder(serverLogPath),
              },
            ]
          : []),
      ],
    },
  ]

  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

async function createWindow() {
  const appUrl = app.isPackaged ? await startBundledServer() : DEV_SERVER_URL

  mainWindow = new BrowserWindow({
    title: APP_NAME,
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    show: false,
    backgroundColor: "#0b0b0a",
    titleBarStyle: "hiddenInset",
    trafficLightPosition: { x: 14, y: 9 },
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  })

  const allowedOrigin = new URL(appUrl).origin
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url)
    return { action: "deny" }
  })
  mainWindow.webContents.on("will-navigate", (event, url) => {
    try {
      if (new URL(url).origin === allowedOrigin) return
    } catch {
      // Non-URL navigation targets are never allowed out of the renderer.
    }
    event.preventDefault()
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url)
  })
  mainWindow.webContents.on("will-attach-webview", (event) => event.preventDefault())

  mainWindow.once("ready-to-show", () => mainWindow?.show())
  mainWindow.on("closed", () => {
    mainWindow = null
  })

  await mainWindow.loadURL(appUrl)
}

function stopBundledServer() {
  if (serverProcess && serverProcess.exitCode === null) {
    serverProcess.kill("SIGTERM")
  }
  serverProcess = null
  serverUrl = null
  serverLogStream?.end()
  serverLogStream = null
}

function appendLifecycleLog(message) {
  if (!serverLogPath) return
  try {
    fs.appendFileSync(
      serverLogPath,
      `[${new Date().toISOString()}] Lifecycle: ${message}\n`,
    )
  } catch {
    // Lifecycle diagnostics must never interfere with quitting.
  }
}

app.on("second-instance", () => {
  if (!mainWindow) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
})

app.on("before-quit", () => {
  appendLifecycleLog(`before-quit (${BrowserWindow.getAllWindows().length} windows)`)
  isQuitting = true
  stopBundledServer()
  // A renderer can occasionally remain alive after a Dock-close/reopen cycle,
  // even though app.quit() has begun and the bundled server is already gone.
  // Once the user has explicitly chosen Quit, do not leave a headless Electron
  // process behind.
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) window.destroy()
  }
  appendLifecycleLog(`windows destroyed (${BrowserWindow.getAllWindows().length} remain)`)
  app.exit(0)
})

app.on("will-quit", () => appendLifecycleLog("will-quit"))
app.on("quit", () => appendLifecycleLog("quit"))

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit()
})

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    void createWindow().catch(showStartupError)
  }
})

function showStartupError(error) {
  const detail = error instanceof Error ? error.stack || error.message : String(error)
  void dialog
    .showMessageBox({
      type: "error",
      title: `${APP_NAME} could not start`,
      message: `${APP_NAME} could not start its local server.`,
      detail: serverLogPath ? `${detail}\n\nDiagnostic log: ${serverLogPath}` : detail,
    })
    .finally(() => app.quit())
}

if (hasSingleInstanceLock) {
  app.whenReady().then(async () => {
    session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback, details) => {
      const audioRequested = permission === "media" && details.mediaTypes?.includes("audio")
      callback(Boolean(audioRequested))
    })

    try {
      await createWindow()
      installApplicationMenu()
      if (process.env.DATARABBIT_SMOKE_AUTO_QUIT_MS) {
        const delay = Number(process.env.DATARABBIT_SMOKE_AUTO_QUIT_MS)
        if (!Number.isInteger(delay) || delay < 250 || delay > 30_000) {
          throw new Error(
            "DATARABBIT_SMOKE_AUTO_QUIT_MS must be an integer from 250 through 30000",
          )
        }
        setTimeout(() => app.quit(), delay)
      }
    } catch (error) {
      showStartupError(error)
    }
  })
}
