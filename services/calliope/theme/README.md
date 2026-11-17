# Warehouse appearance assets

`warehouse-theme.src.js` is the readable source. `warehouse-theme.js` is its
checked-in browser bundle, including `node-vibrant`; the Warehouse image has no
JavaScript build step at runtime.

`adaptive-artifact.js` is intentionally dependency-free and unbundled. It is
loaded by every hosted artifact shim but exits before touching the DOM unless
that immutable artifact manifest names a Design Profile whose
`behavior.theme_source` is `viewer`. Direct pages read the sanitized
`rvbbit-warehouse-adaptive-theme-v1` snapshot; sandboxed Calliope frames receive
the same shape through the validated parent bridge.

From the Calliope repository root, with the in-tree DataRabbit dependencies
installed:

```bash
NODE_PATH="$PWD/datarabbit/node_modules" \
  npx --yes esbuild@0.25.8 \
  services/calliope/theme/warehouse-theme.src.js \
  --bundle --platform=browser --format=iife --target=es2022 --minify \
  --outfile=services/calliope/theme/warehouse-theme.js
```

The 103 images under `images/` are intentionally standalone derivatives of the
Lens wallpaper library, not a live mirror. Full images fit within 1920×1080 and
thumbnail tiles are center-cropped to 420×260; both are stripped WebP files.
This keeps the copied library under 30 MB instead of shipping the roughly
171 MB source set. `VIBRANT-LICENSE` covers the bundled extractor.

Adding a JPG to `backgrounds/` and `auth.BACKGROUNDS` only exposes it to the
login backdrop. The appearance picker discovers paired WebP files under
`theme/images/full` and `theme/images/thumb`. Add both when adding a wallpaper:

```bash
# Run from services/calliope; replace NAME with the wallpaper slug.
magick backgrounds/NAME.jpg -auto-orient -resize '1920x1080>' -strip -quality 82 theme/images/full/NAME.webp
magick backgrounds/NAME.jpg -auto-orient -resize '420x260^' -gravity center -extent 420x260 -strip -quality 82 theme/images/thumb/NAME.webp
```

`test_login_backgrounds_are_also_available_in_the_appearance_picker` catches
missing picker assets, accepting the legacy `-high` names for existing scenes.
The picker catalog is discovered at runtime; no JavaScript rebuild is required.

## Workspace appearance defaults

Admins can open **Workspace settings → Appearance** from the account menu.
The shared library picker saves a wallpaper, its derived palette/tokens, background
style, and default light/dark mode to `rvbbit.calliope_workspace_settings`.
Personal wallpaper choices take precedence; a nullable personal color mode inherits
independently. “Use workspace default” clears only the personal appearance.
Shared defaults are resolved on reads and are never copied into personal rows.
Browser storage is a display cache; hydration does not write preferences.

Migration `0416_workspace_appearance` adds the shared setting and nullable personal
color mode. Server startup also ensures this schema for a server-only update.
The matching DataRabbit change sends mode-only edits without copying the effective
wallpaper into a personal override. The first version selects bundled library
wallpapers; personal uploaded images remain private to their owner.
