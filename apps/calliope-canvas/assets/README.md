# Calliope native branding

`ai.calliope.canvas.svg` embeds the unchanged
`services/calliope/calliope/callie-avatar-day.jpg` portrait with a circular SVG
clip, matching the web avatar. It is self-contained for desktop icon loaders.

`calliope-avatar.png` is the same circular export, embedded in the shared native
header. `src/brand.rs` is compiled into desktop and Android and registers the
bundled Homemade Apple font before sign-in. The signature font is reserved for
the Calliope wordmark; UI and app typography continue to follow the user theme.

Homemade Apple comes from [Google Fonts](https://github.com/google/fonts/tree/main/apache/homemadeapple).
Its Apache 2.0 license is included in `fonts/HomemadeApple-LICENSE.txt`.

Android packages a 512 px export of this same icon inside an adaptive icon's
safe area. The portrait remains circular; the launcher chooses the outer mask.
To re-export after changing the SVG, run from the repository root:

```sh
rsvg-convert -w 512 -h 512 apps/calliope-canvas/assets/ai.calliope.canvas.svg \
  -o apps/calliope-canvas/assets/calliope-avatar.png
cp apps/calliope-canvas/assets/calliope-avatar.png \
  apps/calliope-mobile/android/app/src/main/res/drawable-nodpi/calliope_avatar.png
```

Refresh the installed Linux launcher with
`apps/calliope-canvas/install-local.sh --launcher-only`. Normal installation
without that flag also builds the release binary used by `run.sh`.
