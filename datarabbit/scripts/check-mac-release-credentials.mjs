import { spawnSync } from "node:child_process"

function fail(message) {
  console.error(message)
  process.exit(1)
}

const identities = spawnSync(
  "/usr/bin/security",
  ["find-identity", "-v", "-p", "codesigning"],
  { encoding: "utf8" },
)
const identityText = `${identities.stdout ?? ""}\n${identities.stderr ?? ""}`
const keychainIdentity = /Developer ID Application:/i.test(identityText)
const suppliedCertificate = Boolean(process.env.CSC_LINK)

if (!keychainIdentity && !suppliedCertificate) {
  fail(
    [
      "No Developer ID Application certificate is available.",
      "Install one in the login keychain with Xcode → Settings → Accounts →",
      "Manage Certificates, or provide CSC_LINK and CSC_KEY_PASSWORD.",
    ].join(" "),
  )
}

const appleIdAuth =
  Boolean(process.env.APPLE_ID) &&
  Boolean(process.env.APPLE_APP_SPECIFIC_PASSWORD) &&
  Boolean(process.env.APPLE_TEAM_ID)
const apiKeyAuth =
  Boolean(process.env.APPLE_API_KEY) &&
  Boolean(process.env.APPLE_API_KEY_ID) &&
  Boolean(process.env.APPLE_API_ISSUER) &&
  Boolean(process.env.APPLE_TEAM_ID)
const keychainAuth = Boolean(process.env.APPLE_KEYCHAIN_PROFILE)

if (!appleIdAuth && !apiKeyAuth && !keychainAuth) {
  fail(
    [
      "No notarization credentials are configured.",
      "Provide APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD + APPLE_TEAM_ID,",
      "APPLE_API_KEY + APPLE_API_KEY_ID + APPLE_API_ISSUER + APPLE_TEAM_ID,",
      "or APPLE_KEYCHAIN_PROFILE.",
    ].join(" "),
  )
}

console.log(
  JSON.stringify(
    {
      ok: true,
      signingCertificate: keychainIdentity ? "keychain" : "CSC_LINK",
      notarization:
        appleIdAuth ? "apple-id" : apiKeyAuth ? "app-store-connect-api-key" : "keychain-profile",
    },
    null,
    2,
  ),
)
