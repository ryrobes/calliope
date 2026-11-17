# One Calliope distribution

`deploy/compose.yaml` is the canonical install topology. The older files in
`docker/` remain development and existing-deployment overlays; do not add a new
shipping overlay. `release/services.json` owns build inputs, and
`calliope-release.json` owns the product version, upstream pins, runtime packs,
and database migration target.

## Building a candidate

```sh
python3 -m pip install PyYAML
python3 scripts/release/shipping.py check
scripts/release/build-calliope.sh  # All product images plus Python/MCP/DLT images
python3 scripts/release/shipping.py bundle --candidate --output /tmp/calliope-candidate
```

The candidate directory is self-contained. Follow its README, including
`configure.py`, from anywhere outside the repository. No host settings, data,
credentials or images are copied from an existing install. Candidate bundles use version tags;
they are not proof of a clean-machine boot or a released package.

## Publishing

1. Bump all product packages with `bump-calliope-version.py`. Product versions
   and the RVBBIT native extension version are separate. Change the latter only
   when needed by the extension's binding/upgrade contract.
2. Append new SQL migrations and register them in `migrations.rs`. Run
   `shipping.py schema-lock` to add their hashes. It refuses modified/deleted
   previously recorded SQL. The first lock covers 471 files through
   `0477_test_failure_details`; this records today's starting point, not proof
   that all historic deployments used these exact bytes.
3. Commit the source. Publish images with `build-calliope.sh --push` or the
   Calliope image workflow. Existing version tags cannot be overwritten; registry
   authentication/network failures do not count as permission to overwrite.
   For existing GHCR packages, grant the `ryrobes/calliope` repository Write
   under package settings → Manage Actions access. This is separate from public
   pull access, especially for runtime packages originally published by another
   repository. The workflow's `packages: write` scope alone does not grant access
   to those packages.
4. The final workflow job resolves every published image to an immutable digest
   using its registry credentials, verifies the product/runtime images' version,
   source commit and Linux amd64 platform, then independently tests anonymous access.
   It lists any private/unavailable references in `images_needing_public_access`
   and marks that bundle `awaiting-public-images`. It never changes visibility
   or includes registry credentials in the artifact.
5. Make the listed product/runtime packages public in GHCR. Rerun the bundle
   job, or use `shipping.py bundle --lock --output /tmp/calliope-VERSION`, to
   require anonymous access and mark the artifact `public-images-locked`.
   Both paths produce file checksums. The workflow
   uploads a tarball and checksum as build artifacts; it does not silently
   publish a GitHub Release or move a `latest` tag.
6. Run the empty-machine acceptance below before marking a release supported.

When visibility changes are required, rerun only the bundle job after making
them. A publication retry reuses an existing image only when its version and
source revision match exactly; it never rebuilds or overwrites that tag. A
different source revision requires a new version. A bundle
awaiting public access is ready for that handoff, not for anonymous installation.

## Flattened database baseline

The Postgres image builds its baseline from a disposable, network-isolated
cluster using the image's exact native extension. `scripts/schema/baseline.py`
runs the history once **during the image build**, captures current DDL and seed
data, and validates restoration in another empty database. The normal extension
installer and migration registry remain available for existing installations.

The baseline uses a temporary Postgres extension version to create an empty
extension shell. It restores current objects, reattaches the original extension
members, and advances to the normal engine version. Native functions, extension
ownership, grants and migration-owned objects keep their original relationships.
Catalog OIDs are rebound to object names; no customer data or credentials enter
the build. Every snapshot is tied to the native library and installer checksums.

The build fails unless the restored schema/grants, seed data and ownership match
history. Comparison uses parsed SQL and only normalizes PostgreSQL's redundant
cast aliases and associative boolean grouping. Both databases must skip covered
migrations, execute exactly one deliberately pending migration, and accept the
same forward DDL change. The manifest records each result.

`rvbbit-schema-release` installs the snapshot on empty databases, records hashes,
and rejects unknown newer migrations, modified recorded SQL, or a product
version downgrade. Legacy databases without recorded hashes are explicitly
marked `adopted-history` after their pending migrations succeed; this does not
pretend to recover the bytes of old manually edited deployments.

Before publication, `scripts/schema/test-image.py` additionally boots the real
image entrypoint with a disposable empty volume, verifies the baseline, restarts
with retained test data, exercises a pending-only upgrade, and verifies downgrade
and checksum rejection. This is a database-component gate; the complete
empty-machine product test below remains separate.

When adding migrations, update the release migration target and baseline ID as
well as appending to the schema lock. Existing databases run only missing SQL;
fresh installs consume the current baked schema.

## Empty-machine acceptance

Use the downloaded bundle and anonymous pulls, with no repository checkout,
existing Docker volumes, installed Clover service or desktop VM:

- Configure one funded OpenRouter key, administrator and origin; all required
  services and Warren's Python/MCP/DLT runtime images start from registry images.
- From another device, open the public HTTPS origin, connect an MCP client at
  `/mcp`, and connect a SQL client using the generated CA and `sslmode=verify-full`.
  Verify incoming SQL writes and rejection of incorrect credentials/certificates.
- Log in, send a chat message, delegate to a specialist, make a SQL decision and
  embedding call, and see receipts/costs in System. Verify Hindsight retain and
  recall use the same embedding profile.
- Author and preview a native app through the shipped UI Studio command; publish
  a dashboard and verify its Stage preview. Verify voice over HTTPS.
- With the computer profile, boot an empty desktop volume and complete a browser
  job using the CPU/API route. With specialists, check GLiNER and TSPulse.
- Restart everything and verify users, data, threads, model selections,
  credentials, app definitions and desktop files survive.
- Rehearse a supported image upgrade from a backed-up prior release and prove
  only new migrations run. Compare with a clean baseline install of that release.

Onboarding UI and automatic upgrade actions remain separate follow-up work.
