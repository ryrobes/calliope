# Temporary artifact build previews

Artifact work appears separately from published Stage surfaces. No model call,
transcript message, or permanent notebook evidence is created for progress.

- Hermes observes accepted tool-argument chunks on Chat Completions, Anthropic,
  and Codex Responses. Only HTML/SVG file writes, replace/V4A patches, and live
  app/dashboard publications qualify. The client gets a byte count and bounded
  line lengths, never raw arguments or reasoning. Updates are throttled to 1.25s.
- Partial HTML arguments show a lightweight layout preview as they arrive.
  Successful writes provide bounded complete HTML; completed local patches read
  the exact tool's sandbox path independently of Hermes' read-before-write cache.
  After tool completion, tracked local artifacts are checked for content changes,
  including shell/Python edits. Nonlocal files and previously untracked shell
  writes do not currently get readback. Buffered providers may only expose a
  draft when their complete tool call arrives.
- Layout previews strip scripts, network elements, and event handlers, and run
  in an empty sandbox with restrictive CSP. Saved revisions then use the same
  server-side renderer as published artifacts, including authored JavaScript,
  CSS, external assets, charts, and map configuration. Only the resulting PNG
  reaches the browser; complete source stays on the authenticated server channel.
  This is a draft, not visual validation or publication of the application.
- Rendering is asynchronous, serialized, and coalesces edits to the newest
  revision. Each viewer has an isolated image and query cache. Queries use that
  viewer's identity and the existing read-only path, capped at 16 per render
  with a five-second statement timeout. Up to eight small successful results
  are cached for two minutes and executions are logged as artifact_draft_preview.
  Actions are disabled by the capture runtime. Saved revisions publish a first
  screenshot as usual, then a second about five seconds later from the same
  live browser page so delayed data/charts can finish without restarting queries.
  Both frames use the existing image transition; diagnostic banners are refreshed
  for the second shot. A newer edit cancels the older revision's delayed shot.
  Partial layout previews still take one shot. A failed refresh keeps the last
  rendered image; an unchanged saved revision does not trigger another pair.
- Hermes retains the latest eight build snapshots per run for SSE reconnect
  and status replay. Calliope keeps at most 32 files per session and returns at
  most eight, scoped to the authorized session and originating turn/specialist.
  Old snapshots expire from the UI after two hours; terminal work disappears,
  and published cards briefly fade after a durable Stage receipt. Snapshots
  never enter the surface ledger, timelines, or agent memory.
- Failed/stopped draft cards retire after 12 seconds even while their agent
  continues. A draft with no edits for 60 seconds is hidden once the same
  producing run has worked on a different draft. Recently edited siblings and
  separate agents remain independent; image requests never change draft focus.
  A published successor still counts as newer work after its preview retires.
  This is a display decision: files and retained snapshots are untouched, and
  a new edit to an earlier path makes its preview eligible again. A lone draft
  stays visible through long thinking or tool waits.
- The client polls every two seconds while visible, uses conditional requests,
  refreshes both the card and open expanded preview, preserves the last rendered
  image during subsequent edits, and closes previews
  when switching chats. Animation is driven by revisions, not heartbeats. Quiet
  and disconnected periods display the last actual update time.

Main-run, recovery, and specialist consumers all write the same ephemeral store.
Cancellation is determined from persisted Calliope run status. New work after a
publication can reuse the path and show another build; delayed older packets
cannot resurrect a finished card. A publish marks only cards currently in the
publishing phase; other files follow the inactivity rule above.

Validation: `test-hermes-artifact-progress.py` and existing Stage delivery tests
run against the pinned, patched Hermes source (also an image-build gate).
`test_artifact_work.py` covers session isolation, cancellation, retention and
publication ordering. `test_stage_delivery.py` exercises SSE/status replay.
`test_artifact_render.py` covers coalescing, viewer isolation, failed updates,
and actual JavaScript/CSS rendering with governed, cached query results.
