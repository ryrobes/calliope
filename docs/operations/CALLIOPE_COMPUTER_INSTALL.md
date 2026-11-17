# Installing Callie's Computer

The desktop is an instance component, separate from Clover's optional Qwen/Cua
model worker. Its image contains a CPU desktop, persistent Chrome, LibreOffice,
Cua Driver, the authenticated control API, and recording/file support. No GPU
is requested by its Compose service. Hosted decision/model calls still require
a configured Calliope instance.

## Runtime status

System → Overview lists **Desktop / computer**, including when it is switched
off or incompletely configured. Opening or refreshing Overview checks the
authenticated control health, the display, and the read-only Kasm viewer shell.
It never opens a run, takes control, or invokes a model. Failures identify the
connection/credential/display problem; “unavailable” does not mean “not installed.”
Callie receives the same saved observation as the user.

This is runtime evidence, not a successful installation receipt. A responding
display also does not prove a full agent task or a model provider works.

## Deployment layouts

- **Same Docker host:** `docker/docker-compose.calliope.yml --profile computer`
  uses the shared desktop definition. Ports remain on the Compose network.
- **Separate VM:** `docker/docker-compose.computer.yml` starts only the desktop.
  Bind ports 9000/6901 to its private address and permit the appliance to reach
  them. The existing Debian 12 bootstrap and GCP host-plan generator prepare a
  Docker host; neither silently provisions a complete connected desktop.

Both use `docker/Dockerfile.calliope-desktop` and preserve two named volumes:
home/profile/files and runtime/recordings. Installation does not copy an existing
desktop VM or require any existing browser profile. On a separate VM, the guest's
env file needs only `CALLIOPE_COMPUTER_BIND_ADDRESS`, the appliance's matching
`CALLIOPE_DESKTOP_VNC_PASSWORD` and `CALLIOPE_DESKTOP_CONTROL_KEY`, and
`CALLIOPE_COMPUTER_DECISION_URL` (see `docker/docker-compose.computer.yml`).

For an already initialized appliance, the same-host source path is:

```sh
python3 scripts/release/provision-computer-use-env.py --env-file /opt/rvbbit/.env
docker compose --env-file /opt/rvbbit/.env \
  -f docker/docker-compose.calliope.yml --profile computer build desktop
docker compose --env-file /opt/rvbbit/.env \
  -f docker/docker-compose.calliope.yml --profile computer up -d --no-deps desktop calliope
```

The credential helper preserves existing Computer keys. Do not point a second
desktop at an existing home/runtime pair. A Docker container is the desktop
runtime; a dedicated VM is a deployment boundary, not an inference requirement.

## Fresh-install release check

Build from the pinned upstream Kasm image, then test with empty volumes:

```sh
docker build -f docker/Dockerfile.calliope-desktop -t calliope-desktop:preflight .
python3 scripts/desktop/test-fresh-install.py \
  --image calliope-desktop:preflight --report /tmp/computer-preflight.json
```

The check creates a unique Compose project, generated credentials, loopback-only
random ports, and new volumes. It uses the shipping base Compose definition.
It verifies authenticated control, a Cua/browser job, workbook creation/readback,
screenshot bytes, finalized video segment checksums, and persistence after
container replacement. It then removes only its own containers/network/volumes.
The JSON receipt and evidence copies remain beside the requested report.
It never connects to an existing desktop, requests GPU devices, or calls a model.

Desktop is included in the image-publication CI matrix. The release build script
runs this check before pushing the desktop image. A passing source check does
not claim that an old published tag contains these changes, that registry images
are publicly pullable, or that the full Calliope appliance installs unattended.
ARM builds and automated non-GCP VM creation are separate unverified paths.
