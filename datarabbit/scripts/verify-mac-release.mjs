import { spawnSync } from "node:child_process"
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const electronManifest = JSON.parse(
  readFileSync(path.join(root, "electron", "package.json"), "utf8"),
)
const version = String(electronManifest.version)
const arch = process.arch
const appDir = arch === "arm64" ? "mac-arm64" : "mac"
const productName = electronManifest.build?.productName ?? "Data Rabbit"
const app = path.join(root, "dist", appDir, `${productName}.app`)
// Artifact names are de-spaced (electron/package.json artifactName), unlike
// the .app bundle which keeps the spaced productName.
const dmg = path.join(root, "dist", `DataRabbit-${version}-${arch}.dmg`)
const zip = path.join(root, "dist", `DataRabbit-${version}-${arch}.zip`)
const requireNotarized = process.env.DATARABBIT_REQUIRE_NOTARIZED === "1"

function run(command, args) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
  })
  if (result.status !== 0) {
    const detail = result.stderr?.trim() || result.stdout?.trim() || `exit ${result.status}`
    throw new Error(`${command} ${args.join(" ")} failed: ${detail}`)
  }
  return `${result.stdout ?? ""}${result.stderr ?? ""}`.trim()
}

function inspectAppBundle(appPath, label) {
  run("/usr/bin/codesign", [
    "--verify",
    "--deep",
    "--strict",
    "--verbose=2",
    appPath,
  ])

  const bundleId = run("/usr/libexec/PlistBuddy", [
    "-c",
    "Print :CFBundleIdentifier",
    path.join(appPath, "Contents", "Info.plist"),
  ])
  const bundleVersion = run("/usr/libexec/PlistBuddy", [
    "-c",
    "Print :CFBundleShortVersionString",
    path.join(appPath, "Contents", "Info.plist"),
  ])
  if (bundleId !== "ai.rvbbit.datarabbit" || bundleVersion !== version) {
    throw new Error(
      `${label} has unexpected bundle identity/version: ${bundleId} ${bundleVersion}; expected ai.rvbbit.datarabbit ${version}`,
    )
  }

  const detail = run("/usr/bin/codesign", ["-dv", "--verbose=4", appPath])
  const teamIdentifier =
    detail.match(/^TeamIdentifier=(.+)$/m)?.[1]?.trim() ?? null
  const authority =
    detail.match(/^Authority=(.+)$/m)?.[1]?.trim() ?? null
  const adHoc = /^Signature=adhoc$/m.test(detail)
  const hardenedRuntime = /flags=.*\([^)]*\bruntime\b[^)]*\)/m.test(detail)

  if (requireNotarized) {
    if (
      adHoc ||
      !teamIdentifier ||
      !authority?.startsWith("Developer ID Application:")
    ) {
      throw new Error(
        `${label} is not signed with a Developer ID Application identity`,
      )
    }
    if (!hardenedRuntime) {
      throw new Error(`${label} is not signed with the hardened runtime`)
    }
    run("/usr/sbin/spctl", [
      "--assess",
      "--verbose=4",
      "--type",
      "execute",
      appPath,
    ])
    run("/usr/bin/xcrun", ["stapler", "validate", appPath])
  }

  return {
    bundleId,
    bundleVersion,
    signature: { valid: true, adHoc, teamIdentifier, authority, hardenedRuntime },
  }
}

run("/usr/bin/hdiutil", ["verify", dmg])
run("/usr/bin/unzip", ["-tq", zip])

const stagedBundle = inspectAppBundle(app, "Staged app")
const scratch = mkdtempSync(path.join(os.tmpdir(), "datarabbit-release-verify-"))
let dmgBundle
let zipBundle

try {
  const mountPoint = path.join(scratch, "dmg")
  mkdirSync(mountPoint)
  run("/usr/bin/hdiutil", [
    "attach",
    dmg,
    "-readonly",
    "-nobrowse",
    "-mountpoint",
    mountPoint,
  ])
  try {
    dmgBundle = inspectAppBundle(
      path.join(mountPoint, `${productName}.app`),
      "DMG app",
    )
  } finally {
    run("/usr/bin/hdiutil", ["detach", mountPoint])
  }

  const zipDirectory = path.join(scratch, "zip")
  mkdirSync(zipDirectory)
  run("/usr/bin/ditto", ["-x", "-k", zip, zipDirectory])
  zipBundle = inspectAppBundle(
    path.join(zipDirectory, `${productName}.app`),
    "ZIP app",
  )
} finally {
  rmSync(scratch, { recursive: true, force: true })
}

for (const [label, candidate] of [
  ["DMG app", dmgBundle],
  ["ZIP app", zipBundle],
]) {
  if (
    candidate.signature.adHoc !== stagedBundle.signature.adHoc ||
    candidate.signature.teamIdentifier !==
      stagedBundle.signature.teamIdentifier ||
    candidate.signature.authority !== stagedBundle.signature.authority
  ) {
    throw new Error(`${label} signature identity differs from the staged app`)
  }
}

const checksumOutput = run("/usr/bin/shasum", ["-a", "256", dmg, zip])
const checksums = Object.fromEntries(
  checksumOutput.split("\n").map((line) => {
    const [hash, ...fileParts] = line.trim().split(/\s+/)
    return [path.basename(fileParts.join(" ")), hash]
  }),
)

console.log(
  JSON.stringify(
    {
      ok: true,
      version,
      arch,
      bundleId: stagedBundle.bundleId,
      signature: stagedBundle.signature,
      notarizationRequired: requireNotarized,
      archiveBundles: {
        dmg: dmgBundle.signature,
        zip: zipBundle.signature,
      },
      archives: {
        dmg: { path: dmg, sha256: checksums[path.basename(dmg)] },
        zip: { path: zip, sha256: checksums[path.basename(zip)] },
      },
    },
    null,
    2,
  ),
)
