# Browser and image delivery

These fixes require rebuilding **calliope-hermes** and **calliope-server**,
plus the updated Compose healthcheck/environment hint. No database migration
is needed. They do not change model selections or the dashboard runtime contract.

## Browser availability

The Hermes image includes full Chromium and browser-use 0.13.10. The supervisor
prefers the image binary, retains support for old Playwright installations,
and restarts Chromium if its CDP endpoint disappears. Compose health requires
both the gateway and CDP. A fresh data volume no longer needs a browser download.

Run the smoke test against a built image as the normal Hermes uid:

```sh
docker run --rm --user 10000:10000 \
  --tmpfs /opt/data:rw,uid=10000,gid=10000 \
  -e HOME=/opt/data -e HERMES_HOME=/opt/data -e PYTHONPATH=/opt/hermes \
  --entrypoint python -i calliope-hermes:browser-image-stage-fix - \
  < docker/calliope/test-hermes-browser-smoke.py
```

This exercises real browser_exec against a local fixture, kills Chromium,
and checks recovery. It does not contact a provider or customer service.

## Image completion

Warehouse and Specialist allow generate_image 660 seconds; ordinary
tools retain their 90-second budget. The HTTP read budget accommodates the
long image call, including the Hermes helper lane's 480-second turn.

For a trusted Calliope session or specialist, the server records the originating
turn before generation and commits the image and Stage surface together. A lost
MCP response does not lose a successfully completed image. Existing notebook
surface-count reconciliation discovers late attachments. This does not resume
a generation interrupted by a server process/container restart.

Unlinked/headless callers still receive a stored image URL; the server never
guesses a destination from their most recent chat. Deleting the originating
turn prevents attachment and does not redirect the image to another turn.

## File publication

- Explicit Markdown file links and MEDIA attachments in the assistant's reply publish.
- Files referenced under `/opt/data/outputs`, `/opt/data/exports`, or a profile's
  corresponding directories publish automatically, as do native generated images.
- Bare paths in tool receipts, errors, tests, and assistant diagnostics do not
  publish arbitrary working files. Keep tests/manifests outside outputs/exports.
- Existing explicit tool-produced Stage surfaces and remote generated images
  retain their behavior. Existing Stage history is not deleted.

The generated-image delivery tests use `IMAGE_DELIVERY_TEST_DSN` pointing at an
empty disposable PostgreSQL database. Never point them at an appliance database.
