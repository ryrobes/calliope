# Calliope Files runtime

SVAR React File Manager 2.6.0, React 18, and Vite are pinned by package-lock.json.
Run `npm ci && npm run build` here; commit the generated `../calliope/files-runtime.js`
and CSS with source changes. The app serves these assets itself and loads them
only when Files opens. No SVAR server or external fonts are required.

SVAR supplies navigation, selection, search, views, keyboard shortcuts, menus and
file-operation dialogs. The adapter intercepts mutations before local changes,
waits for Calliope's authenticated API, then refreshes actual desktop state.
Polling runs every five seconds while the modal is open and the tab is visible.
Failures refresh state and remain visible instead of reporting optimistic success.

The main view shows current working files. Uploaded originals are a separate
read-only view; guest upload receipts preserve original associations through
UI rename/move/delete. Out-of-band moves by desktop applications cannot reliably
preserve that association, but originals always remain in Uploaded originals.
Delete moves working files to `/var/lib/calliope-desktop/shared-uploads/trash/<session>/<id>`;
`receipt.json` records the former path and `content` holds the file/folder.
Trash is retained for manual recovery, with no automatic purge or restore UI yet.

The MIT license for SVAR is in SVAR-LICENSE.txt. Icons are the SVAR wx-icons asset,
vendored from https://cdn.svar.dev/fonts/wxi/wx-icons.css and its versioned woff2
(font query revision 5c1d2b451b95eb4894fd97b9270b27ad). The CSS uses the local
`/calliope/files-icons.woff2` route. Themes use Calliope tokens and existing fonts.
