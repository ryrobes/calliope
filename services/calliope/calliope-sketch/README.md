# Calliope sketch runtime

`npm run typecheck` and `npm run build` build the isolated Excalidraw editor
into `../calliope/sketch-runtime.js`. Commit the generated runtime with source
changes; the server image ships these assets without an npm build.

## Superior Sans

Superior uses persisted font-family ID **12**. Built-in IDs retain their
meaning, including Excalifont handwriting (5). New generated text and the
editor's initial drawing tool use Superior. Saved chat fonts are preserved;
the managed Briarpatch map adopts Superior during normal reconciliation.

Excalidraw 0.18.1 does not expose custom-font registration. The small Vite
transform in `scripts/superior-font.mjs` adds Superior to its existing family,
metrics, and font-face registries without modifying `node_modules`. It covers
the font picker, canvas, text editor, and embedded SVG/PNG exports. The pinned
version and replacement counts fail the build if upstream changes the contract.
Review the transform when upgrading Excalidraw. Raw `.excalidraw` JSON using ID
12 needs this runtime; stock Excalidraw does not know our custom font. Use SVG
or PNG to share its appearance outside Calliope.

The source is the UI's `../theme/fonts/LTSuperior-Regular.otf`. To regenerate
the checked-in WOFF2 and `../superior_font_metrics.json` after replacing it:

```sh
python scripts/prepare-superior-font.py  # requires fonttools[woff]
npm run build
```

The server measures the same OTF with Pillow (including kerning), with the
generated glyph advances as a fallback. The bootstrap preloads the WOFF2
before the scene loader measures and lays out text. Do not substitute CSS
fonts while leaving the registered family or server measurements unchanged.

`../test_sketch_label_layout.py` exercises the server DSL, patched library,
no-op text editing, fixed-width labels, mixed scenes, font selection, exports,
and managed-map reconciliation using local Playwright/Chromium fixtures.

## Theme-aware ink

`src/scene-colors.ts` projects authored colors onto the active room palette.
Text targets 7:1 contrast and strokes 4.5:1; existing fills become a subtle
palette-matched tint. Images and intentionally invisible elements are untouched.
The editor uses Excalidraw's light rendering pipeline in both room modes so its
built-in inversion cannot invert these colors or embedded photos a second time.
Calliope's CSS still supplies the actual light/dark chrome.

Temporary color metadata travels with editor undo/redo. Fingerprints and save
requests restore authored colors and strip that metadata, including on preview
writes. Explicit human color edits become the new authored values. Theme changes
use a non-history scene update and never create a shared revision. PNG previews
use the displayed palette with an opaque room background for readable exports.

`test_sketch_view_mode.py` covers live theme/palette changes, contrast, no-op
switches, canonical saves, human recoloring, and undo after a theme switch.
