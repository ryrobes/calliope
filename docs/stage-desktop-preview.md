# Temporary desktop preview

The stacked Stage shows one read-only desktop card while a non-terminal desktop
execution job is running in the selected chat. Opening/claiming the computer,
recording, a queued run, or a shell job alone never opens the card. Desktop job
status means execution (which may include Python work or waiting for a page),
not a per-mouse-action heartbeat. Observation-only screenshot calls currently
do not trigger it.

The preview uses the existing computer status poller and authenticated VNC
viewer with `view_only=1`. The poller refreshes every three seconds while the
selected chat has agent work or owns a desktop run, otherwise every ten seconds
when the Desktop panel is hidden. Completed jobs remain visible for 15 seconds,
including short jobs that finished between polls. An unrefreshed status expires
after 20 seconds. Finish, takeover, pause, another chat, or opening the full
Desktop panel hides the preview immediately. Search, historical Stage views,
and canvas mode suppress it as well.

The card uses one Stage column (roughly one third on a wide Stage), follows the
guest aspect ratio, and participates in normal packing. Its iframe survives
ordinary Stage redraws and resizing. It is not a surface: nothing is added to
the ledger, evidence counts, timeline, or agent context.

Minimize collapses the card to its header and disconnects the preview stream.
Show restores it while activity is eligible. This preference belongs to the
chat, stored in browser session storage, and survives job changes, desktop run
changes, chat navigation, and reloads in that browser tab. A new job never
overrides the person's choice. The compact header also retires when activity
ends; the full Desktop workspace remains available at any time.

`test_computer_stage.py` exercises activity gating, short-job and stale-status
expiry, minimizing across run/chat changes, read-only viewer URLs, sidebar
suppression, iframe preservation, and responsive Stage packing in Chromium.
