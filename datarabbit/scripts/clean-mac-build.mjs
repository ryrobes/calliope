import { rmSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")

// Next overlays standalone output during incremental builds instead of removing
// files that disappeared from its trace. Clear only these generated macOS
// packaging artifacts so a previous dist/ tree can never be archived again.
rmSync(path.join(root, ".next", "standalone"), { recursive: true, force: true })
rmSync(path.join(root, "build", "app-server.tar.gz"), { force: true })
