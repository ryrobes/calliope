import "server-only"

import { promises as fs } from "node:fs"
import path from "node:path"
import os from "node:os"

/**
 * Local registry of peer backends THIS DataRabbit instance runs the
 * claim-loop for (colony-runner.ts). Separate from the DB-side
 * rvbbit.peer_backends row: the DB row is what every session on the shared
 * Postgres can see and call; this file is what tells THIS process where to
 * actually send a claimed request (a local URL, a model name) and is never
 * synced to the database. Same shape as db/registry.ts's connections.json —
 * single JSON file, mode 0600, atomic write via tmp+rename.
 */

const ENV_HOME = "RVBBIT_LENS_HOME"
const DEFAULT_DIR_NAME = "rvbbit-lens"
const FILE_NAME = "colony.json"

export type ColonyShape = "openai_chat" | "gradio" | "mcp"

export interface ColonyShare {
  /** Matches rvbbit.peer_backends.backend_name / rvbbit.backends.name. */
  backendName: string
  connectionId: string
  shape: ColonyShape
  /** Local endpoint this instance calls when it claims a request (openai_chat/gradio only). */
  localUrl?: string
  model?: string
  /** mcp shape only: the stdio subprocess to spawn (mirrors mcp-servers-window's own
   *  register-form fields, so a share and a locally-installed MCP server configure
   *  identically). The claim-loop holds one long-lived client per backend, lazily
   *  spawned on first claim — never re-spawned per call. */
  command?: string
  args?: string[]
  env?: Record<string, string>
}

function configDir(): string {
  const fromEnv = process.env[ENV_HOME]
  if (fromEnv && fromEnv.trim().length > 0) return fromEnv
  const xdg = process.env.XDG_CONFIG_HOME
  const base = xdg && xdg.trim().length > 0 ? xdg : path.join(os.homedir(), ".config")
  return path.join(base, DEFAULT_DIR_NAME)
}

function configPath(): string {
  return path.join(configDir(), FILE_NAME)
}

async function ensureDir(): Promise<void> {
  await fs.mkdir(configDir(), { recursive: true, mode: 0o700 })
}

export async function readShares(): Promise<ColonyShare[]> {
  try {
    const raw = await fs.readFile(configPath(), "utf-8")
    const parsed = JSON.parse(raw) as { shares?: ColonyShare[] }
    const list = parsed?.shares ?? []
    return list.filter((s) => s && typeof s.backendName === "string")
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return []
    throw err
  }
}

async function writeShares(shares: ColonyShare[]): Promise<void> {
  await ensureDir()
  const file = configPath()
  const tmp = `${file}.tmp`
  await fs.writeFile(tmp, JSON.stringify({ version: 1, shares }, null, 2), { mode: 0o600 })
  await fs.rename(tmp, file)
}

export async function upsertShare(share: ColonyShare): Promise<void> {
  const all = await readShares()
  const next = all.filter((s) => s.backendName !== share.backendName)
  next.push(share)
  await writeShares(next)
}

export async function removeShare(backendName: string): Promise<void> {
  const all = await readShares()
  await writeShares(all.filter((s) => s.backendName !== backendName))
}

export async function sharesForConnection(connectionId: string): Promise<ColonyShare[]> {
  const all = await readShares()
  return all.filter((s) => s.connectionId === connectionId)
}
