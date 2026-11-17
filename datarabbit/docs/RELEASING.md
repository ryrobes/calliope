# Releasing Data Rabbit for macOS

## Import a web release

Publish and tag the matching `rvbbit-lens` version, then apply that release's
web-source delta to this repository without replacing the native integration
files. Keep the root and Electron versions identical:

```bash
npm version 4.1.3 --no-git-tag-version --allow-same-version
npm --prefix electron version 4.1.3 --no-git-tag-version
npm ci
```

Run the source gates before packaging:

```bash
npm run typecheck
npm run lint:mac
npm run check:sql-viz
```

## Local candidate

Create an ad-hoc sealed arm64 candidate:

```bash
npm run dist:mac
npm run verify:mac
```

Run every packaged test against that exact candidate:

```bash
npm run test:mac:smoke
npm run test:mac:rvbbit
npm run test:mac:surfaces
npm run test:mac:persistence
npm run test:mac:write-workflows
```

The rvbbit suites require the matching web reference on port 3000. The
write-workflow suite uses only disposable PostgreSQL and SSH infrastructure.

## Developer ID release

Direct distribution requires a `Developer ID Application` certificate and
Apple notarization. Install the certificate in the login keychain, or provide a
`.p12` through `CSC_LINK` and `CSC_KEY_PASSWORD`.

Configure one notarization authentication method:

- `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, and `APPLE_TEAM_ID`;
- `APPLE_API_KEY`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER`, and
  `APPLE_TEAM_ID`; or
- `APPLE_KEYCHAIN_PROFILE` from `xcrun notarytool store-credentials`.

Then run:

```bash
npm run dist:mac:release
```

The release command fails before building if credentials are missing. It
requires signing, signs native Node modules inside the archived Next server,
enables electron-builder notarization, then verifies:

- the deep code signature;
- the Developer ID authority and Team ID;
- Gatekeeper acceptance;
- the stapled notarization ticket;
- DMG and ZIP integrity;
- the bundle identity, version, and signature of the app inside each archive;
- hardened-runtime signing on every shipped app copy; and
- SHA-256 checksums for both artifacts.

Never publish artifacts for which `npm run verify:mac:release` fails.
