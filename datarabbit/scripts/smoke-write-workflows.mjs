import { execFile } from "node:child_process"
import { generateKeyPairSync, randomUUID } from "node:crypto"
import { constants as fsConstants } from "node:fs"
import {
  access,
  chmod,
  mkdir,
  mkdtemp,
  rm,
  writeFile,
} from "node:fs/promises"
import net from "node:net"
import os from "node:os"
import path from "node:path"
import { promisify } from "node:util"
import { fileURLToPath } from "node:url"
import { _electron as electron } from "playwright-core"
import ssh2 from "ssh2"

const execFileAsync = promisify(execFile)
const { Server: SshServer, utils: sshUtils } = ssh2

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const appBundle =
  process.env.DATARABBIT_APP_BUNDLE ??
  path.join(root, "dist", "mac-arm64", "Data Rabbit.app")
const executable = path.join(appBundle, "Contents", "MacOS", "Data Rabbit")

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function findExecutable(name, candidates) {
  const pathCandidates = (process.env.PATH ?? "")
    .split(path.delimiter)
    .filter(Boolean)
    .map((directory) => path.join(directory, name))
  for (const candidate of [...candidates, ...pathCandidates]) {
    try {
      await access(candidate, fsConstants.X_OK)
      return candidate
    } catch {
      // Keep looking.
    }
  }
  throw new Error(`${name} is required for the disposable write-workflow test`)
}

async function freePort() {
  const server = net.createServer()
  await new Promise((resolve, reject) => {
    server.once("error", reject)
    server.listen(0, "127.0.0.1", resolve)
  })
  const address = server.address()
  const port = address && typeof address === "object" ? address.port : 0
  await new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())))
  assert(port > 0, "Could not reserve a disposable loopback port")
  return port
}

function generateRsaPrivateKey(passphrase) {
  const privateKeyEncoding = {
    type: "pkcs1",
    format: "pem",
    ...(passphrase ? { cipher: "aes-256-cbc", passphrase } : {}),
  }
  return generateKeyPairSync("rsa", {
    modulusLength: 2048,
    publicKeyEncoding: { type: "pkcs1", format: "pem" },
    privateKeyEncoding,
  }).privateKey
}

function parsePrivateKey(privateKey, passphrase) {
  const parsed = sshUtils.parseKey(privateKey, passphrase)
  if (parsed instanceof Error || Array.isArray(parsed)) {
    throw parsed instanceof Error ? parsed : new Error("Unexpected multi-key SSH identity")
  }
  return parsed
}

async function startSshForwarder({ postgresPort, hostKey, identities, password }) {
  const activeClients = new Set()
  const authMethods = new Set()
  let forwardedConnections = 0

  const server = new SshServer({ hostKeys: [hostKey] }, (client) => {
    activeClients.add(client)
    client
      .on("authentication", (context) => {
        if (
          context.method === "password" &&
          context.username === "datarabbit" &&
          context.password === password
        ) {
          authMethods.add("password")
          context.accept()
          return
        }
        if (context.method === "publickey" && context.username === "datarabbit") {
          const identity = identities.find(
            (candidate) =>
              candidate.type === context.key.algo &&
              candidate.getPublicSSH().equals(context.key.data),
          )
          if (
            identity &&
            (!context.signature ||
              identity.verify(context.blob, context.signature, context.hashAlgo))
          ) {
            authMethods.add("publickey")
            context.accept()
            return
          }
        }
        context.reject()
      })
      .on("ready", () => {
        client.on("tcpip", (accept, reject, info) => {
          if (
            info.destPort !== postgresPort ||
            !["127.0.0.1", "localhost"].includes(info.destIP)
          ) {
            reject()
            return
          }
          forwardedConnections++
          const stream = accept()
          const upstream = net.connect(postgresPort, "127.0.0.1")
          const closeBoth = () => {
            stream.destroy()
            upstream.destroy()
          }
          stream.on("error", closeBoth)
          upstream.on("error", closeBoth)
          stream.on("close", () => upstream.destroy())
          upstream.on("close", () => stream.destroy())
          stream.pipe(upstream)
          upstream.pipe(stream)
        })
      })
      .on("close", () => activeClients.delete(client))
      .on("error", () => activeClients.delete(client))
  })

  const port = await freePort()
  await new Promise((resolve, reject) => {
    server.once("error", reject)
    server.listen(port, "127.0.0.1", resolve)
  })

  return {
    port,
    authMethods,
    get activeClientCount() {
      return activeClients.size
    },
    get forwardedConnections() {
      return forwardedConnections
    },
    async close() {
      for (const client of activeClients) client.end()
      await new Promise((resolve) => server.close(resolve))
    },
  }
}

async function postJson(baseUrl, pathname, body) {
  const response = await fetch(new URL(pathname, baseUrl), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  })
  return { status: response.status, body: await response.json() }
}

async function requestJson(baseUrl, pathname, init) {
  const response = await fetch(new URL(pathname, baseUrl), init)
  return { status: response.status, body: await response.json() }
}

async function runQuery(baseUrl, connectionId, sql, extra = {}) {
  const result = await postJson(baseUrl, "/api/db/query", {
    connectionId,
    sql,
    rowLimit: 100,
    ...extra,
  })
  assert(result.status === 200, `Query route returned HTTP ${result.status}`)
  return result.body
}

async function runImport(baseUrl, importId, bytes) {
  const response = await fetch(
    new URL(`/api/db/import/run?id=${encodeURIComponent(importId)}`, baseUrl),
    {
      method: "POST",
      headers: { "content-type": "text/csv" },
      body: bytes,
    },
  )
  const frames = (await response.text())
    .split("\n")
    .filter((line) => line.trim().length > 0)
    .map((line) => JSON.parse(line))
  return { status: response.status, frames }
}

async function waitFor(predicate, timeoutMs, message) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await predicate()) return
    await delay(50)
  }
  throw new Error(message)
}

async function setSqlEditorText(page, sqlWindow, sql) {
  const editor = sqlWindow.locator(".cm-content").first()
  await editor.waitFor({ state: "visible", timeout: 10_000 })
  await editor.click()
  await page.keyboard.press("Meta+A")
  await page.keyboard.insertText(sql)
}

await access(executable, fsConstants.X_OK)
const initdb = await findExecutable("initdb", [
  "/opt/homebrew/opt/postgresql@18/bin/initdb",
  "/opt/homebrew/opt/postgresql@17/bin/initdb",
  "/usr/local/opt/postgresql@18/bin/initdb",
])
const pgCtl = await findExecutable("pg_ctl", [
  "/opt/homebrew/opt/postgresql@18/bin/pg_ctl",
  "/opt/homebrew/opt/postgresql@17/bin/pg_ctl",
  "/usr/local/opt/postgresql@18/bin/pg_ctl",
])
const openssl = await findExecutable("openssl", [
  "/opt/homebrew/bin/openssl",
  "/usr/local/bin/openssl",
  "/usr/bin/openssl",
])

const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "datarabbit-write-smoke-"))
const postgresData = path.join(temporaryRoot, "postgres")
const postgresSocket = path.join(temporaryRoot, "socket")
const postgresLog = path.join(temporaryRoot, "postgres.log")
const postgresCertificate = path.join(postgresData, "server.crt")
const postgresPrivateKey = path.join(postgresData, "server.key")
const userData = path.join(temporaryRoot, "user-data")
const encryptedKeyPath = path.join(temporaryRoot, "ssh-client-encrypted.pem")
const postgresPort = await freePort()
const appPort = await freePort()
const sshPassword = "datarabbit-smoke-password"
const keyPassphrase = "datarabbit-smoke-passphrase"

const hostKey = generateRsaPrivateKey()
const inlinePrivateKey = generateRsaPrivateKey()
const encryptedPrivateKey = generateRsaPrivateKey(keyPassphrase)
const allowedIdentities = [
  parsePrivateKey(inlinePrivateKey),
  parsePrivateKey(encryptedPrivateKey, keyPassphrase),
]

let postgresStarted = false
let sshForwarder
let electronApp
let appUrl

try {
  await mkdir(postgresSocket, { recursive: true })
  await mkdir(userData, { recursive: true })
  await writeFile(encryptedKeyPath, encryptedPrivateKey, { mode: 0o600 })
  await chmod(encryptedKeyPath, 0o600)

  await execFileAsync(initdb, [
    "-D",
    postgresData,
    "-A",
    "trust",
    "-U",
    "datarabbit",
    "--no-locale",
    "--encoding=UTF8",
  ])
  await execFileAsync(openssl, [
    "req",
    "-x509",
    "-newkey",
    "rsa:2048",
    "-nodes",
    "-days",
    "1",
    "-subj",
    "/CN=localhost",
    "-addext",
    "subjectAltName=IP:127.0.0.1,DNS:localhost",
    "-addext",
    "basicConstraints=critical,CA:TRUE",
    "-keyout",
    postgresPrivateKey,
    "-out",
    postgresCertificate,
  ])
  await chmod(postgresPrivateKey, 0o600)
  await execFileAsync(pgCtl, [
    "-D",
    postgresData,
    "-l",
    postgresLog,
    "-o",
    `-h 127.0.0.1 -p ${postgresPort} -k ${postgresSocket} -c ssl=on -c fsync=off -c synchronous_commit=off -c full_page_writes=off`,
    "-w",
    "-t",
    "30",
    "start",
  ])
  postgresStarted = true

  sshForwarder = await startSshForwarder({
    postgresPort,
    hostKey,
    identities: allowedIdentities,
    password: sshPassword,
  })

  electronApp = await electron.launch({
    executablePath: executable,
    env: {
      ...process.env,
      DATARABBIT_USER_DATA_DIR: userData,
      DATARABBIT_SERVER_PORT: String(appPort),
      NODE_EXTRA_CA_CERTS: postgresCertificate,
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
    timeout: 30_000,
  })
  appUrl = page.url()

  const directConnection = (
    await postJson(appUrl, "/api/db/connections", {
      label: "Disposable PostgreSQL",
      host: "127.0.0.1",
      port: postgresPort,
      database: "postgres",
      user: "datarabbit",
      sslMode: "disable",
      isDefault: true,
    })
  ).body.connection
  assert(directConnection?.id, "Disposable direct connection was not saved")

  const directTest = await postJson(appUrl, "/api/db/test", {
    connectionId: directConnection.id,
  })
  assert(directTest.body.ok === true, `Direct connection failed: ${directTest.body.error}`)

  const tlsConnection = (
    await postJson(appUrl, "/api/db/connections", {
      label: "Disposable PostgreSQL TLS",
      host: "127.0.0.1",
      port: postgresPort,
      database: "postgres",
      user: "datarabbit",
      sslMode: "require",
    })
  ).body.connection
  assert(tlsConnection?.id, "Disposable TLS connection was not saved")
  const tlsTest = await postJson(appUrl, "/api/db/test", {
    connectionId: tlsConnection.id,
  })
  assert(tlsTest.body.ok === true, `Verified TLS connection failed: ${tlsTest.body.error}`)
  const tlsState = await runQuery(
    appUrl,
    tlsConnection.id,
    "SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid()",
  )
  assert(
    tlsState.rows?.[0]?.ssl === true &&
      typeof tlsState.rows[0].version === "string" &&
      typeof tlsState.rows[0].cipher === "string",
    `PostgreSQL did not report an encrypted TLS session: ${JSON.stringify(tlsState.rows)}`,
  )

  const setup = await runQuery(
    appUrl,
    directConnection.id,
    "CREATE TABLE native_txn_smoke (id integer PRIMARY KEY, note text NOT NULL)",
  )
  assert(setup.ok === true, `Disposable table setup failed: ${setup.error}`)

  // 4.1.3 Deck publishing: exercise the new API against disposable registry
  // tables. Slide SQL must run read-only, become pinned rows in the persisted
  // manifest/HTML, and republishing the same slug must append a version.
  for (const sql of [
    "CREATE SCHEMA rvbbit",
    `CREATE TABLE rvbbit.dashboards (
       id bigserial PRIMARY KEY,
       slug text NOT NULL UNIQUE,
       name text NOT NULL,
       description text,
       status text NOT NULL,
       latest_version integer NOT NULL,
       runtime_kind text NOT NULL,
       app_kind text NOT NULL,
       manifest jsonb NOT NULL,
       updated_at timestamptz NOT NULL DEFAULT now()
     )`,
    `CREATE TABLE rvbbit.dashboard_versions (
       dashboard_id bigint NOT NULL REFERENCES rvbbit.dashboards(id),
       version integer NOT NULL,
       html text NOT NULL,
       kind text NOT NULL,
       created_by text,
       notes text,
       manifest jsonb NOT NULL,
       PRIMARY KEY (dashboard_id, version)
     )`,
  ]) {
    const result = await runQuery(appUrl, directConnection.id, sql)
    assert(result.ok === true, `Disposable deck registry setup failed: ${result.error}`)
  }

  const invalidDeck = await postJson(appUrl, "/api/deck/publish", {})
  assert(
    invalidDeck.status === 400 &&
      invalidDeck.body.error === "connectionId, name, and spec required",
    `Deck route did not reject an incomplete request: ${JSON.stringify(invalidDeck)}`,
  )
  const deckInput = {
    connectionId: directConnection.id,
    name: "Native 4.1.3 Deck Smoke",
    description: "Disposable packaged-app deck publishing verification",
    spec: {
      deck: {
        title: "Native Deck",
        slides: [
          {
            component: "TitleSlide",
            props: { title: "Pinned by Data Rabbit" },
            data: {
              sql: "SELECT 413::int AS native_value",
              bind: "value",
              shape: "rows",
              mode: "pinned",
            },
          },
        ],
      },
    },
  }
  const firstDeck = await postJson(appUrl, "/api/deck/publish", deckInput)
  assert(
    firstDeck.status === 200 &&
      firstDeck.body.ok === true &&
      firstDeck.body.slug === "native-4-1-3-deck-smoke" &&
      firstDeck.body.version === 1 &&
      firstDeck.body.pinned === 1 &&
      !firstDeck.body.pin_errors,
    `First deck publish failed: ${JSON.stringify(firstDeck)}`,
  )
  const secondDeck = await postJson(appUrl, "/api/deck/publish", {
    ...deckInput,
    description: "Republished disposable deck",
  })
  assert(
    secondDeck.status === 200 &&
      secondDeck.body.ok === true &&
      secondDeck.body.version === 2 &&
      secondDeck.body.pinned === 1,
    `Deck republish did not append v2: ${JSON.stringify(secondDeck)}`,
  )
  const deckState = await runQuery(
    appUrl,
    directConnection.id,
    `SELECT d.slug,
            d.latest_version,
            d.runtime_kind,
            d.app_kind,
            d.manifest->>'schema_version' AS schema_version,
            count(v.version)::int AS versions,
            max(v.created_by) AS created_by,
            bool_and(v.notes LIKE 'deck · 1 slides · 1 pinned') AS notes_match,
            bool_and(v.html LIKE '%name="deck-runtime" content="inline:%') AS runtime_embedded,
            bool_and(v.html LIKE '%id="deck-spec"%') AS spec_embedded,
            max(v.manifest #>> '{deck,deck,slides,0,data,rows,0,native_value}') AS pinned_value
       FROM rvbbit.dashboards d
       JOIN rvbbit.dashboard_versions v ON v.dashboard_id = d.id
      WHERE d.slug = 'native-4-1-3-deck-smoke'
      GROUP BY d.id`,
  )
  const deckRow = deckState.rows?.[0]
  assert(
    deckRow?.latest_version === 2 &&
      deckRow.runtime_kind === "html" &&
      deckRow.app_kind === "deck" &&
      deckRow.schema_version === "live_app.v0" &&
      deckRow.versions === 2 &&
      deckRow.created_by === "lens-assistant" &&
      deckRow.notes_match === true &&
      deckRow.runtime_embedded === true &&
      deckRow.spec_embedded === true &&
      deckRow.pinned_value === "413",
    `Persisted deck did not match the 4.1.3 contract: ${JSON.stringify(deckRow)}`,
  )

  const rollbackSession = randomUUID()
  const rollbackInsert = await postJson(appUrl, "/api/db/txn", {
    action: "query",
    sessionId: rollbackSession,
    connectionId: directConnection.id,
    sql: "INSERT INTO native_txn_smoke VALUES (1, 'rolled back')",
  })
  assert(rollbackInsert.body.ok === true, `Transaction insert failed: ${rollbackInsert.body.error}`)
  const insideTransaction = await postJson(appUrl, "/api/db/txn", {
    action: "query",
    sessionId: rollbackSession,
    connectionId: directConnection.id,
    sql: "SELECT count(*)::int AS count FROM native_txn_smoke",
  })
  assert(
    insideTransaction.body.rows?.[0]?.count === 1,
    "Pinned transaction could not read its uncommitted row",
  )
  const rolledBack = await postJson(appUrl, "/api/db/txn", {
    action: "rollback",
    sessionId: rollbackSession,
  })
  assert(rolledBack.body.ended === true, "Pinned transaction did not roll back")
  const afterRollback = await runQuery(
    appUrl,
    directConnection.id,
    "SELECT count(*)::int AS count FROM native_txn_smoke",
  )
  assert(afterRollback.rows?.[0]?.count === 0, "Rolled-back row remained visible")

  const commitSession = randomUUID()
  await postJson(appUrl, "/api/db/txn", {
    action: "query",
    sessionId: commitSession,
    connectionId: directConnection.id,
    sql: "INSERT INTO native_txn_smoke VALUES (2, 'committed')",
  })
  const committed = await postJson(appUrl, "/api/db/txn", {
    action: "commit",
    sessionId: commitSession,
  })
  assert(committed.body.ended === true, "Pinned transaction did not commit")
  const afterCommit = await runQuery(
    appUrl,
    directConnection.id,
    "SELECT id, note FROM native_txn_smoke ORDER BY id",
  )
  assert(
    afterCommit.rows?.length === 1 && afterCommit.rows[0].note === "committed",
    "Committed row did not persist",
  )

  await page.reload()
  await page.waitForFunction(
    () =>
      document.documentElement.dataset.datarabbitDesktop === "true" &&
      Boolean(window.dataRabbitDesktop?.isDesktopApp),
    undefined,
    { timeout: 60_000 },
  )
  await page.locator("[data-rvbbit-boot-overlay]").waitFor({
    state: "hidden",
    timeout: 30_000,
  })
  const dataWindowCount = await page
    .locator('[data-rvbbit-window-kind="data"]')
    .count()
  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-new-sql")?.click()
  })
  await page.waitForFunction(
    (expected) =>
      document.querySelectorAll('[data-rvbbit-window-kind="data"]').length > expected,
    dataWindowCount,
    { timeout: 10_000 },
  )
  const uiSqlWindow = page.locator('[data-rvbbit-window-kind="data"]').last()
  const showSqlEditor = uiSqlWindow.getByTitle("Show SQL editor")
  if ((await showSqlEditor.count()) > 0) await showSqlEditor.click()

  await setSqlEditorText(
    page,
    uiSqlWindow,
    "INSERT INTO native_txn_smoke VALUES (3, 'ui rolled back')",
  )
  const autocommitButton = uiSqlWindow.getByRole("button", {
    name: "auto",
    exact: true,
  })
  await autocommitButton.click()
  await uiSqlWindow.getByTitle("Run (⌘↩)").click()
  await uiSqlWindow.getByTitle("ROLLBACK the transaction").waitFor({
    state: "visible",
    timeout: 30_000,
  })
  await uiSqlWindow.getByTitle("ROLLBACK the transaction").click()
  const uiRollbackCheck = await runQuery(
    appUrl,
    directConnection.id,
    "SELECT count(*)::int AS count FROM native_txn_smoke WHERE id = 3",
  )
  assert(uiRollbackCheck.rows?.[0]?.count === 0, "UI rollback left its row behind")

  await setSqlEditorText(
    page,
    uiSqlWindow,
    "INSERT INTO native_txn_smoke VALUES (4, 'ui committed')",
  )
  await uiSqlWindow.getByTitle("Run (⌘↩)").click()
  await uiSqlWindow.getByTitle("COMMIT the transaction").waitFor({
    state: "visible",
    timeout: 30_000,
  })
  await uiSqlWindow.getByTitle("COMMIT the transaction").click()
  const uiCommitCheck = await runQuery(
    appUrl,
    directConnection.id,
    "SELECT note FROM native_txn_smoke WHERE id = 4",
  )
  assert(
    uiCommitCheck.rows?.[0]?.note === "ui committed",
    "UI commit did not persist its row",
  )
  await uiSqlWindow.getByRole("button", { name: "txn", exact: true }).click()

  await setSqlEditorText(page, uiSqlWindow, "SELECT * FROM native_txn_smo")
  await page.keyboard.press("Control+Space")
  const completionPopup = page.locator(".cm-tooltip-autocomplete").last()
  await completionPopup.waitFor({ state: "visible", timeout: 30_000 })
  await completionPopup
    .locator("li")
    .filter({ hasText: "native_txn_smoke" })
    .first()
    .click()
  assert(
    (await uiSqlWindow.locator(".cm-content").first().textContent())?.includes(
      "native_txn_smoke",
    ),
    "Schema-aware SQL completion did not insert the disposable table",
  )

  await setSqlEditorText(page, uiSqlWindow, "SELECT 10::int AS scrub_value")
  await uiSqlWindow.getByTitle("Run (⌘↩)").click()
  const scrubCallout = uiSqlWindow.locator(
    '[data-rvbbit-single-cell="scrub_value"]',
  )
  await scrubCallout.getByText("10", { exact: true }).waitFor({
    state: "visible",
    timeout: 30_000,
  })
  const scrubTarget = uiSqlWindow
    .locator(".cm-scrub-target")
    .filter({ hasText: "10" })
    .first()
  const scrubBox = await scrubTarget.boundingBox()
  assert(scrubBox, "Numeric SQL literal did not expose a scrub target")
  await page.mouse.move(
    scrubBox.x + scrubBox.width / 2,
    scrubBox.y + scrubBox.height / 2,
  )
  await page.mouse.down()
  await page.mouse.move(
    scrubBox.x + scrubBox.width / 2 + 36,
    scrubBox.y + scrubBox.height / 2,
    { steps: 6 },
  )
  await page.mouse.up()
  await scrubCallout.getByText("16", { exact: true }).waitFor({
    state: "visible",
    timeout: 10_000,
  })

  await setSqlEditorText(
    page,
    uiSqlWindow,
    "select * from (values (1, 'alpha'), (2, 'beta'), (3, 'gamma')) as native_rows(id, note)",
  )
  await uiSqlWindow.getByTitle("Format").click()
  await page.waitForFunction(
    () => {
      const windows = document.querySelectorAll('[data-rvbbit-window-kind="data"]')
      return windows
        .item(windows.length - 1)
        ?.querySelector(".cm-content")
        ?.textContent?.includes("SELECT")
    },
    undefined,
    { timeout: 10_000 },
  )
  await uiSqlWindow.getByTitle("Run (⌘↩)").click()
  const uiQueryResult = uiSqlWindow.locator("[data-rvbbit-query-result]")
  await uiQueryResult.waitFor({ state: "visible", timeout: 30_000 })
  assert(
    (await uiQueryResult.textContent())?.includes("3 rows"),
    "UI result grid did not report three rows",
  )

  const resultFilter = uiSqlWindow.getByPlaceholder("filter rows…")
  await resultFilter.fill("beta")
  await uiSqlWindow.getByText("1 of 3", { exact: true }).waitFor({
    state: "visible",
    timeout: 10_000,
  })
  await resultFilter.fill("")
  await uiSqlWindow.getByTitle("Show fields on the left and rows as columns").click()
  await uiSqlWindow.getByPlaceholder("filter fields or values…").waitFor({
    state: "visible",
    timeout: 10_000,
  })
  await uiSqlWindow.getByTitle("Show rows as rows").click()
  await uiSqlWindow.getByTitle("Query history").click()
  const historyMenu = page.getByRole("menu")
  await historyMenu.waitFor({ state: "visible", timeout: 10_000 })
  assert(
    /SELECT \*/.test((await historyMenu.textContent()) ?? ""),
    "Query history did not retain the formatted UI query",
  )
  await page.keyboard.press("Escape")
  const betaCell = uiSqlWindow.locator('div[title^="beta"]').first()
  await betaCell.click({ button: "right" })
  await page.getByRole("menuitem", { name: "Open row", exact: true }).click()
  const rowInspector = page.locator('[data-rvbbit-window-kind="row-inspector"]')
  await rowInspector.waitFor({
    state: "visible",
    timeout: 10_000,
  })
  await rowInspector.getByTitle("Close").click()

  const sourceBlockToken = (
    await uiSqlWindow.locator('button[title^="Block name"]').textContent()
  )?.trim()
  assert(
    sourceBlockToken?.startsWith("{") && sourceBlockToken.endsWith("}"),
    "Source SQL window did not expose its block-reference token",
  )
  const windowsBeforeReference = await page
    .locator('[data-rvbbit-window-kind="data"]')
    .count()
  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-new-sql")?.click()
  })
  await page.waitForFunction(
    (expected) =>
      document.querySelectorAll('[data-rvbbit-window-kind="data"]').length > expected,
    windowsBeforeReference,
    { timeout: 10_000 },
  )
  const referenceWindow = page.locator('[data-rvbbit-window-kind="data"]').last()
  const referenceShowEditor = referenceWindow.getByTitle("Show SQL editor")
  if ((await referenceShowEditor.count()) > 0) await referenceShowEditor.click()
  await setSqlEditorText(
    page,
    referenceWindow,
    `SELECT * FROM ${sourceBlockToken}`,
  )
  const resolvedReference = referenceWindow.locator(
    ".cm-block-ref:not(.cm-block-ref-missing)",
  )
  await resolvedReference.waitFor({ state: "visible", timeout: 10_000 })
  assert(
    (await resolvedReference.textContent()) === sourceBlockToken,
    "Cross-window SQL reference was not recognized as a live block",
  )
  await referenceWindow.getByTitle("Run (⌘↩)").click()
  await waitFor(
    async () =>
      (
        (await referenceWindow
          .locator("[data-rvbbit-query-result]")
          .textContent()) ?? ""
      ).includes("3 rows"),
    30_000,
    `Cross-window reference did not return three rows: ${
      (await referenceWindow.textContent()) ?? ""
    }`,
  )
  await referenceWindow.locator('div[title^="beta"]').waitFor({
    state: "visible",
    timeout: 10_000,
  })
  await referenceWindow.getByTitle("Close").click()

  await setSqlEditorText(
    page,
    uiSqlWindow,
    "SELECT i, 'row_' || i::text AS label FROM generate_series(1, 5000) AS series(i)",
  )
  await uiSqlWindow.getByTitle("Run (⌘↩)").click()
  await page.waitForFunction(
    () => {
      const windows = document.querySelectorAll('[data-rvbbit-window-kind="data"]')
      return windows
        .item(windows.length - 1)
        ?.querySelector("[data-rvbbit-query-result]")
        ?.textContent?.includes("5000")
    },
    undefined,
    { timeout: 30_000 },
  )
  const largeResultFilter = uiSqlWindow.getByPlaceholder("filter rows…")
  await largeResultFilter.fill("row_4999")
  await uiSqlWindow.getByText("1 of 5000", { exact: true }).waitFor({
    state: "visible",
    timeout: 10_000,
  })
  await uiSqlWindow.locator('div[title^="row_4999"]').waitFor({
    state: "visible",
    timeout: 10_000,
  })
  await largeResultFilter.fill("")

  const uiCancelMarker = `native_ui_cancel_${Date.now()}`
  await setSqlEditorText(
    page,
    uiSqlWindow,
    `SELECT pg_sleep(30) /* ${uiCancelMarker} */`,
  )
  await uiSqlWindow.getByTitle("Run (⌘↩)").click()
  const stopButton = uiSqlWindow.getByTitle(
    "Cancel the running query (pg_cancel_backend)",
  )
  await stopButton.waitFor({ state: "visible", timeout: 10_000 })
  await waitFor(
    async () =>
      ((await uiSqlWindow.textContent()) ?? "").includes("Timeout:PgSleep"),
    5_000,
    "Native SQL status did not render live pg_stat_activity wait progress",
  )
  await stopButton.click()
  await stopButton.waitFor({ state: "hidden", timeout: 10_000 })
  await waitFor(
    async () => {
      const activity = await runQuery(
        appUrl,
        directConnection.id,
        `SELECT count(*)::int AS count
           FROM pg_stat_activity
          WHERE state = 'active'
            AND pid <> pg_backend_pid()
            AND query LIKE '%${uiCancelMarker}%'`,
      )
      return activity.rows?.[0]?.count === 0
    },
    5_000,
    "UI Stop left the cancelled PostgreSQL backend active",
  )

  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-notifications")?.click()
  })
  const notificationWindow = page.locator(
    '[data-rvbbit-window-kind="notifications"]',
  )
  await notificationWindow.waitFor({ state: "visible", timeout: 10_000 })
  const notifyChannel = `native_notify_${Date.now()}`
  const notifyPayload = `packaged notification ${randomUUID()}`
  await notificationWindow
    .getByPlaceholder("Watch a channel for toasts…")
    .fill(notifyChannel)
  await notificationWindow.getByRole("button", { name: "Watch", exact: true }).click()
  await notificationWindow.getByText("Live", { exact: true }).waitFor({
    state: "visible",
    timeout: 10_000,
  })
  await notificationWindow
    .getByPlaceholder("channel", { exact: true })
    .fill(notifyChannel)
  await notificationWindow.getByPlaceholder("test payload").fill(notifyPayload)
  await notificationWindow
    .getByRole("button", { name: "Send NOTIFY", exact: true })
    .click()
  await notificationWindow.getByText(notifyPayload, { exact: true }).waitFor({
    state: "visible",
    timeout: 10_000,
  })
  const notifyToast = page.locator(".av-toast").filter({ hasText: notifyPayload })
  await notifyToast.waitFor({ state: "visible", timeout: 10_000 })
  await notificationWindow.getByTitle(`Stop watching ${notifyChannel}`).click()
  await notificationWindow.getByText("Idle", { exact: true }).waitFor({
    state: "visible",
    timeout: 10_000,
  })
  await notificationWindow.getByTitle("Close").click()

  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-finder")?.click()
  })
  const finderWindow = page.locator('[data-rvbbit-window-kind="finder"]')
  await finderWindow.waitFor({ state: "visible", timeout: 10_000 })
  await finderWindow
    .getByPlaceholder("Search objects...")
    .fill("native_txn_smoke")
  const finderTable = finderWindow.locator(
    '[data-rvbbit-table="public.native_txn_smoke"]',
  )
  await finderTable.waitFor({ state: "visible", timeout: 10_000 })
  const beforeDdlWindow = await page
    .locator('[data-rvbbit-window-kind="data"]')
    .count()
  await finderTable.click({ button: "right" })
  await page.getByRole("menuitem", { name: "View DDL", exact: true }).click()
  await page.waitForFunction(
    (expected) =>
      document.querySelectorAll('[data-rvbbit-window-kind="data"]').length > expected,
    beforeDdlWindow,
    { timeout: 10_000 },
  )
  const ddlWindow = page.locator('[data-rvbbit-window-kind="data"]').last()
  await waitFor(
    async () => {
      const text = (await ddlWindow.locator(".cm-content").textContent()) ?? ""
      return (
        text.includes('CREATE TABLE "public"."native_txn_smoke"') &&
        text.includes('"note" text NOT NULL') &&
        text.includes("PRIMARY KEY")
      )
    },
    10_000,
    "Finder View DDL did not reconstruct the disposable table",
  )
  await ddlWindow.getByTitle("Close").click()
  await finderWindow.getByTitle("Close").click()

  await electronApp.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("native-command-palette")?.click()
  })
  const commandPalette = page.locator("[data-rvbbit-command-palette]")
  await commandPalette
    .getByPlaceholder("Search tables, views, actions…")
    .fill("Postgres Admin")
  await commandPalette.getByText("Postgres Admin", { exact: true }).click()
  const adminWindow = page.locator('[data-rvbbit-window-kind="postgres-admin"]')
  await adminWindow.waitFor({ state: "visible", timeout: 10_000 })
  await adminWindow.getByText("health score", { exact: true }).waitFor({
    state: "visible",
    timeout: 15_000,
  })
  await adminWindow
    .getByRole("button", { name: "Objects", exact: true })
    .click()
  const adminObject = adminWindow
    .getByRole("button")
    .filter({ hasText: "public.native_txn_smoke" })
    .first()
  await adminObject.waitFor({ state: "visible", timeout: 15_000 })
  await adminObject.click()
  const beforeAnalyzeWindow = await page
    .locator('[data-rvbbit-window-kind="data"]')
    .count()
  await adminWindow
    .getByRole("button", { name: "Analyze template", exact: true })
    .click()
  await page.waitForFunction(
    (expected) =>
      document.querySelectorAll('[data-rvbbit-window-kind="data"]').length > expected,
    beforeAnalyzeWindow,
    { timeout: 10_000 },
  )
  const analyzeWindow = page.locator('[data-rvbbit-window-kind="data"]').last()
  assert(
    ((await analyzeWindow.locator(".cm-content").textContent()) ?? "").includes(
      "ANALYZE VERBOSE public.native_txn_smoke;",
    ),
    "Postgres Admin did not generate the expected reviewed ANALYZE statement",
  )
  await analyzeWindow.getByTitle("Run (⌘↩)").click()
  await waitFor(
    async () =>
      ((await analyzeWindow
        .locator("[data-rvbbit-query-result]")
        .textContent()) ?? "").includes("0 rows"),
    30_000,
    "Reviewed ANALYZE did not complete in the native SQL surface",
  )
  const analyzeState = await runQuery(
    appUrl,
    directConnection.id,
    `SELECT last_analyze IS NOT NULL AS analyzed
       FROM pg_stat_user_tables
      WHERE schemaname = 'public' AND relname = 'native_txn_smoke'`,
  )
  assert(
    analyzeState.rows?.[0]?.analyzed === true,
    "PostgreSQL did not record the native-admin ANALYZE operation",
  )
  await analyzeWindow.getByTitle("Close").click()
  await adminWindow.getByTitle("Close").click()

  const cancelToken = randomUUID()
  const longQueryStarted = Date.now()
  const longQuery = postJson(appUrl, "/api/db/query", {
    connectionId: directConnection.id,
    sql: "SELECT pg_sleep(30)",
    cancelToken,
    statementTimeout: 0,
  })
  let observedProgress = null
  for (let attempt = 0; attempt < 40 && !observedProgress; attempt++) {
    await delay(100)
    const progress = await postJson(appUrl, "/api/db/query-progress", {
      connectionId: directConnection.id,
    })
    if (
      progress.body.ok === true &&
      progress.body.active === true &&
      Number(progress.body.elapsedMs) >= 0
    ) {
      observedProgress = progress.body
    }
  }
  assert(observedProgress, "Query progress route never observed the running backend")
  let cancelled = false
  for (let attempt = 0; attempt < 40 && !cancelled; attempt++) {
    await delay(100)
    const cancellation = await postJson(appUrl, "/api/db/cancel", { cancelToken })
    cancelled = cancellation.body.cancelled === true
  }
  assert(cancelled, "Long-running query never registered for cancellation")
  const cancelledQuery = await longQuery
  assert(
    cancelledQuery.body.ok === false &&
      (cancelledQuery.body.code === "57014" ||
        /canceling statement/i.test(cancelledQuery.body.error ?? "")),
    "Cancelled query did not report PostgreSQL cancellation",
  )
  const cancellationDurationMs = Date.now() - longQueryStarted
  assert(cancellationDurationMs < 10_000, "Query cancellation was not prompt")

  const detachedStartedAt = Date.now()
  const detached = await postJson(appUrl, "/api/db/run-detached", {
    connectionId: directConnection.id,
    sql: "SELECT pg_sleep(0.5); INSERT INTO native_txn_smoke VALUES (5, 'detached')",
  })
  const detachedResponseMs = Date.now() - detachedStartedAt
  assert(
    detached.body.ok === true &&
      detached.body.started === true &&
      detachedResponseMs < 1_000,
    `Detached query did not acknowledge promptly: ${JSON.stringify(detached)}`,
  )
  await waitFor(
    async () => {
      const result = await runQuery(
        appUrl,
        directConnection.id,
        "SELECT note FROM native_txn_smoke WHERE id = 5",
      )
      return result.rows?.[0]?.note === "detached"
    },
    5_000,
    "Detached query did not continue to completion after its HTTP response",
  )

  const csv = [
    "event_id,event_name,event_date,score",
    "1,alpha,2024-01-02,10.5",
    "2,beta,2024-02-03,12.25",
    "bad,rejected,2024-03-04,9.0",
    "3,gamma,2024-04-05,15.75",
    "",
  ].join("\n")
  const csvBytes = new TextEncoder().encode(csv)
  const inspectUrl = new URL("/api/db/import/inspect", appUrl)
  inspectUrl.searchParams.set("totalBytes", String(csvBytes.byteLength))
  const inspected = await requestJson(appUrl, inspectUrl.pathname + inspectUrl.search, {
    method: "POST",
    headers: { "content-type": "application/octet-stream" },
    body: csvBytes,
  })
  assert(inspected.body.ok === true, `CSV inspection failed: ${inspected.body.error}`)

  const importTable = `native_import_${Date.now()}`
  const importConfig = {
    connectionId: directConnection.id,
    schema: "public",
    table: importTable,
    accessMethod: "heap",
    dialect: inspected.body.dialect,
    columns: inspected.body.columns.map((column) =>
      column.targetName === "event_id" ? { ...column, type: "integer" } : column,
    ),
  }
  const prepared = await postJson(appUrl, "/api/db/import/prepare", {
    config: importConfig,
  })
  assert(prepared.body.ok === true, `Import prepare failed: ${prepared.body.error}`)
  const imported = await runImport(appUrl, prepared.body.importId, csvBytes)
  const importDone = imported.frames.find((frame) => frame.type === "done")
  assert(imported.status === 200 && importDone, "Import stream did not emit a done frame")
  assert(
    importDone.rowsRead === 4 &&
      importDone.rowsLoaded === 3 &&
      importDone.rowsRejected === 1,
    `Unexpected import outcome: ${JSON.stringify(importDone)}`,
  )
  assert(
    imported.frames.some((frame) => frame.type === "progress"),
    "Import stream emitted no progress frame",
  )
  const importedRows = await runQuery(
    appUrl,
    directConnection.id,
    `SELECT event_id, event_name, event_date::text, score::text FROM public.${importTable} ORDER BY event_id`,
  )
  assert(
    importedRows.rows?.length === 3 &&
      importedRows.rows[0].event_name === "alpha" &&
      importedRows.rows[2].event_name === "gamma",
    "Imported table did not contain the accepted typed rows",
  )
  const duplicatePrepare = await postJson(appUrl, "/api/db/import/prepare", {
    config: importConfig,
  })
  assert(
    duplicatePrepare.body.ok === false &&
      /already exists/i.test(duplicatePrepare.body.error ?? ""),
    "Importer did not prevent overwriting an existing table",
  )

  const sshDefinitions = [
    {
      label: "SSH password",
      sshPassword,
      expectedFlag: "hasSshPassword",
    },
    {
      label: "SSH inline key",
      sshPrivateKey: inlinePrivateKey,
      expectedFlag: "hasSshPrivateKey",
    },
    {
      label: "SSH encrypted key file",
      sshKeyPath: encryptedKeyPath,
      sshPassphrase: keyPassphrase,
      expectedFlag: "hasSshPassphrase",
    },
  ]
  const sshConnections = []
  for (const definition of sshDefinitions) {
    const saved = (
      await postJson(appUrl, "/api/db/connections", {
        label: definition.label,
        host: "127.0.0.1",
        port: postgresPort,
        database: "postgres",
        user: "datarabbit",
        sslMode: "disable",
        sshEnabled: true,
        sshHost: "127.0.0.1",
        sshPort: sshForwarder.port,
        sshUser: "datarabbit",
        ...(definition.sshPassword ? { sshPassword: definition.sshPassword } : {}),
        ...(definition.sshPrivateKey
          ? { sshPrivateKey: definition.sshPrivateKey }
          : {}),
        ...(definition.sshKeyPath ? { sshKeyPath: definition.sshKeyPath } : {}),
        ...(definition.sshPassphrase
          ? { sshPassphrase: definition.sshPassphrase }
          : {}),
      })
    ).body.connection
    assert(saved?.id, `${definition.label} connection was not saved`)
    assert(
      saved[definition.expectedFlag] === true,
      `${definition.label} secret-presence flag was not preserved`,
    )
    for (const secretKey of [
      "password",
      "sshPassword",
      "sshPrivateKey",
      "sshPassphrase",
    ]) {
      assert(!(secretKey in saved), `${definition.label} exposed ${secretKey}`)
    }
    const tested = await postJson(appUrl, "/api/db/test", { connectionId: saved.id })
    assert(tested.body.ok === true, `${definition.label} failed: ${tested.body.error}`)
    const tunneledQuery = await runQuery(
      appUrl,
      saved.id,
      "SELECT 42::int AS tunnel_value",
    )
    assert(
      tunneledQuery.rows?.[0]?.tunnel_value === 42,
      `${definition.label} query did not cross the tunnel`,
    )
    sshConnections.push(saved)
  }
  assert(
    sshForwarder.authMethods.has("password") &&
      sshForwarder.authMethods.has("publickey"),
    "SSH forwarder did not observe both password and public-key authentication",
  )
  assert(
    sshForwarder.activeClientCount === 3,
    `Expected three cached SSH tunnels, found ${sshForwarder.activeClientCount}`,
  )
  const forwardsBeforeReuse = sshForwarder.forwardedConnections
  await runQuery(
    appUrl,
    sshConnections[0].id,
    "SELECT current_database() AS database_name",
  )
  assert(
    sshForwarder.activeClientCount === 3 &&
      sshForwarder.forwardedConnections >= forwardsBeforeReuse,
    "Cached SSH tunnel was not reusable",
  )

  for (const connection of sshConnections) {
    const removed = await requestJson(
      appUrl,
      `/api/db/connections/${encodeURIComponent(connection.id)}`,
      { method: "DELETE" },
    )
    assert(removed.body.ok === true, `Could not delete ${connection.label}`)
  }
  await waitFor(
    () => sshForwarder.activeClientCount === 0,
    5_000,
    "Deleting SSH connections did not dispose their live tunnels",
  )
  const removedTls = await requestJson(
    appUrl,
    `/api/db/connections/${encodeURIComponent(tlsConnection.id)}`,
    { method: "DELETE" },
  )
  assert(removedTls.body.ok === true, "Could not delete the disposable TLS connection")

  console.log(
    JSON.stringify(
      {
        ok: true,
        appBundle,
        postgresVersion: directTest.body.serverVersion,
        directConnection: true,
        verifiedTls: {
          enabled: true,
          version: tlsState.rows[0].version,
          cipher: tlsState.rows[0].cipher,
          disposedOnDelete: true,
        },
        transactionRollback: true,
        transactionCommit: true,
        deckPublishing: {
          routeValidation: true,
          pinnedQueries: 1,
          appendedVersions: 2,
          persistedRuntime: "inline",
        },
        nativeSqlUi: {
          transactionRollback: true,
          transactionCommit: true,
          formatting: true,
          schemaCompletion: true,
          literalScrubber: true,
          blockReferences: true,
          filtering: true,
          transposition: true,
          virtualizedRows: 5_000,
          rowInspector: true,
          history: true,
          cancellation: true,
        },
        notifications: {
          liveSubscription: true,
          uiSend: true,
          history: true,
          toast: true,
          unsubscribe: true,
        },
        finderAndAdmin: {
          schemaSearch: true,
          reconstructedDdl: true,
          healthOverview: true,
          objectInspection: true,
          reviewedAnalyzeExecuted: true,
        },
        queryCancellationMs: cancellationDurationMs,
        queryExecution: {
          liveProgress: {
            elapsedMs: Number(observedProgress.elapsedMs),
            wait: observedProgress.wait,
          },
          detachedResponseMs,
          detachedCompletedAfterResponse: true,
        },
        import: {
          rowsRead: importDone.rowsRead,
          rowsLoaded: importDone.rowsLoaded,
          rowsRejected: importDone.rowsRejected,
          progressFrames: imported.frames.filter((frame) => frame.type === "progress")
            .length,
          overwriteGuard: true,
        },
        ssh: {
          password: true,
          inlineKey: true,
          encryptedKeyFileAndPassphrase: true,
          forwardedConnections: sshForwarder.forwardedConnections,
          tunnelsDisposedOnDelete: true,
        },
      },
      null,
      2,
    ),
  )
} finally {
  await electronApp?.close().catch(() => undefined)

  if (appUrl) {
    let stopped = false
    try {
      await fetch(appUrl, { signal: AbortSignal.timeout(2_000) })
    } catch {
      stopped = true
    }
    assert(stopped, "Packaged server remained reachable after the write test")
  }

  await sshForwarder?.close().catch(() => undefined)
  if (postgresStarted) {
    await execFileAsync(pgCtl, [
      "-D",
      postgresData,
      "-m",
      "fast",
      "-w",
      "-t",
      "30",
      "stop",
    ]).catch(() => undefined)
  }
  await rm(temporaryRoot, { recursive: true, force: true })
}
