"use strict"
/* eslint-disable @typescript-eslint/no-require-imports */

const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")
const { execFileSync } = require("node:child_process")
const plist = require("plist")

function collectNativeBinaries(directory) {
  const binaries = []
  const pending = [directory]

  while (pending.length > 0) {
    const current = pending.pop()
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const entryPath = path.join(current, entry.name)
      if (entry.isDirectory()) {
        pending.push(entryPath)
        continue
      }
      if (!entry.isFile()) continue

      const extension = path.extname(entry.name).toLowerCase()
      const executable = (fs.statSync(entryPath).mode & 0o111) !== 0
      if (!executable && ![".node", ".dylib", ".so"].includes(extension)) {
        continue
      }

      const kind = execFileSync("/usr/bin/file", ["-b", entryPath], {
        encoding: "utf8",
      })
      if (kind.includes("Mach-O")) binaries.push(entryPath)
    }
  }

  return binaries.sort()
}

function developerIdIdentity() {
  const identities = execFileSync(
    "/usr/bin/security",
    ["find-identity", "-v", "-p", "codesigning"],
    { encoding: "utf8" },
  )
  return (
    identities.match(/"([^"]*Developer ID Application:[^"]*)"/)?.[1] ?? null
  )
}

function signEmbeddedServer(archivePath) {
  if (!fs.existsSync(archivePath)) return

  const temporaryDirectory = fs.mkdtempSync(
    path.join(os.tmpdir(), "datarabbit-server-sign-"),
  )
  const replacementArchive = `${archivePath}.signed`

  try {
    execFileSync(
      "/usr/bin/tar",
      ["-xzf", archivePath, "-C", temporaryDirectory],
      { stdio: "inherit" },
    )

    const binaries = collectNativeBinaries(temporaryDirectory)
    const identity = developerIdIdentity()

    for (const binary of binaries) {
      const signingArguments = ["--force"]
      if (identity) {
        signingArguments.push("--timestamp", "--options", "runtime")
      }
      signingArguments.push("--sign", identity ?? "-", binary)
      execFileSync("/usr/bin/codesign", signingArguments, { stdio: "inherit" })
      execFileSync(
        "/usr/bin/codesign",
        ["--verify", "--strict", "--verbose=2", binary],
        { stdio: "inherit" },
      )
    }

    fs.rmSync(replacementArchive, { force: true })
    execFileSync(
      "/usr/bin/tar",
      ["-czf", replacementArchive, "-C", temporaryDirectory, "."],
      { stdio: "inherit" },
    )
    fs.renameSync(replacementArchive, archivePath)

    console.log(
      `signed ${binaries.length} embedded native server binaries with ${
        identity ?? "an ad-hoc identity"
      }`,
    )
  } finally {
    fs.rmSync(replacementArchive, { force: true })
    fs.rmSync(temporaryDirectory, { recursive: true, force: true })
  }
}

/**
 * Electron's base Info.plist enables broad ATS and includes unused device
 * permission descriptions. Tighten the generated bundle after electron-builder
 * has merged its defaults, but before signing and disk-image creation.
 */
module.exports = async function tightenMacBundle(context) {
  if (context.electronPlatformName !== "darwin") return

  const appName = context.packager.appInfo.productFilename
  const appPath = path.join(context.appOutDir, `${appName}.app`)
  const infoPath = path.join(appPath, "Contents", "Info.plist")
  const serverArchivePath = path.join(
    appPath,
    "Contents",
    "Resources",
    "app-server.tar.gz",
  )
  const info = plist.parse(fs.readFileSync(infoPath, "utf8"))

  // electron-builder signs ordinary app resources but cannot discover native
  // Node modules after the standalone Next server has been compressed. Apple
  // expands nested archives during notarization, so sign those Mach-O files
  // before the outer app is signed and submitted.
  signEmbeddedServer(serverArchivePath)

  info.NSAppTransportSecurity = {
    NSAllowsArbitraryLoads: false,
    NSAllowsLocalNetworking: true,
    NSExceptionDomains: {
      "127.0.0.1": {
        NSIncludesSubdomains: false,
        NSTemporaryExceptionAllowsInsecureHTTPLoads: true,
      },
      localhost: {
        NSIncludesSubdomains: false,
        NSTemporaryExceptionAllowsInsecureHTTPLoads: true,
      },
    },
  }
  delete info.NSCameraUsageDescription
  delete info.NSBluetoothAlwaysUsageDescription
  delete info.NSBluetoothPeripheralUsageDescription

  fs.writeFileSync(infoPath, plist.build(info))

  // electron-builder leaves the bundle root unsigned when no Developer ID is
  // available, while Electron's nested executable retains a resource-sealing
  // signature. That combination launches locally but fails `codesign --verify`
  // because the outer bundle has no CodeResources. Seal local builds ad hoc;
  // when a Developer ID is configured, electron-builder's later signing stage
  // replaces this signature with the distributable hardened-runtime signature.
  execFileSync("codesign", ["--force", "--deep", "--sign", "-", appPath], {
    stdio: "inherit",
  })
}
