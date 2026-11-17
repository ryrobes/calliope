import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const standaloneRoot = path.join(root, ".next", "standalone");

if (!existsSync(standaloneRoot)) {
  process.exit(0);
}

const copies = [
  {
    label: "static assets",
    source: path.join(root, ".next", "static"),
    target: path.join(standaloneRoot, ".next", "static"),
  },
  {
    label: "public assets",
    source: path.join(root, "public"),
    target: path.join(standaloneRoot, "public"),
  },
];

for (const copy of copies) {
  if (!existsSync(copy.source)) continue;
  mkdirSync(path.dirname(copy.target), { recursive: true });
  rmSync(copy.target, { recursive: true, force: true });
  cpSync(copy.source, copy.target, { recursive: true });
  console.log(`synced standalone ${copy.label}`);
}

// electron-builder deliberately filters nested node_modules directories from
// ordinary resource file sets. Preserve the complete standalone server as one
// archive instead; the native main process extracts it into the versioned
// Application Support runtime on first launch.
const buildDir = path.join(root, "build");
const archivePath = path.join(buildDir, "app-server.tar.gz");
mkdirSync(buildDir, { recursive: true });
rmSync(archivePath, { force: true });

const archive = spawnSync(
  "/usr/bin/tar",
  ["-czf", archivePath, "-C", standaloneRoot, "."],
  { stdio: "inherit" },
);
if (archive.error) throw archive.error;
if (archive.status !== 0) {
  throw new Error(`failed to archive standalone server (tar exit ${archive.status})`);
}
console.log("archived standalone server for the macOS bundle");
