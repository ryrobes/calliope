// Pre-hydration script. Loaded via <script src="/pre-hydration.js">
// in layout.tsx <head>. Two jobs:
//
//   1. Apply the saved theme (data-theme + class + colorScheme) before
//      first paint so the user never sees a flash of the wrong scheme.
//   2. Wrap console.error to swallow Next.js 16.2.6's spurious
//      "Set objects are not supported" warning — that warning
//      originates inside framework serialization (single-letter prop
//      names like `m: Set` in the payload, not application code) and
//      can't be removed without a Next upgrade.
//
// Lives in /public rather than inline in layout.tsx so React 19's
// dev-time check doesn't warn about <script> rendered through the
// React tree.
(function () {
  // A returning user's appearance lives in localStorage under the shared
  // Warehouse keys (lib/desktop/shared-appearance.ts): the colour mode, and
  // the full token set + background the desktop derived from their wallpaper.
  // Apply both before first paint so every Lens page — the login page
  // included — opens in the theme they configured instead of the default.
  var MODE_KEY = "rvbbit-warehouse-color-mode";
  var APPEARANCE_KEY = "rvbbit-warehouse-theme-v1";
  var r = document.documentElement;
  try {
    var t = localStorage.getItem(MODE_KEY);
    if (t !== "light" && t !== "dark") {
      // Pre-unification DataRabbit key, kept for one-time compatibility.
      t = localStorage.getItem("rvbbit-lens-theme") === "light" ? "light" : "dark";
    }
    r.dataset.theme = t;
    r.style.colorScheme = t;
    r.classList.toggle("dark", t === "dark");
  } catch (e) {
    // best-effort
  }
  try {
    // Two shapes can be on the volume. The desktop (Lens) writes the full
    // appearance under APPEARANCE_KEY with camelCase token keys; the Calliope
    // shell writes the viewer snapshot (rvbbit.viewer-theme.v1) under
    // SNAPSHOT_KEY with ready-made "--kebab" token names and a resolved
    // wallpaper URL. A user who set their theme in either place is a returning
    // user here, so accept both, preferring the desktop's canonical record.
    var SNAPSHOT_KEY = "rvbbit-warehouse-adaptive-theme-v1";
    var tokens = null, wallpaper = null, solid = null, snapMode = null;
    var raw = localStorage.getItem(APPEARANCE_KEY);
    var a = raw ? JSON.parse(raw) : null;
    if (!(a && a.version === 1 && a.tokens && typeof a.tokens === "object")) a = null;
    var rawSnap = localStorage.getItem(SNAPSHOT_KEY);
    var snap = rawSnap ? JSON.parse(rawSnap) : null;
    if (!(snap && snap.schema_version === "rvbbit.viewer-theme.v1" && snap.tokens && typeof snap.tokens === "object")) snap = null;
    // When both exist they are written by the same appearance change, but they
    // are not the same thing: the desktop record carries the wallpaper-derived
    // base tokens, the shell snapshot carries the mode-resolved tokens the app
    // actually paints with. The snapshot is what the user sees, so it wins.
    if (snap) a = null;
    if (a) {
      tokens = a.tokens;
      var bg = a.background || {};
      var src = a.source || {};
      if (bg.mode === "image" && src.kind === "library" && typeof (src.url || src.originalUrl) === "string") {
        wallpaper = String(src.url || src.originalUrl);
      } else if (bg.mode === "solid" && typeof bg.solidColor === "string" && bg.solidColor) {
        solid = bg.solidColor;
      }
    } else {
      if (snap) {
        tokens = snap.tokens;
        snapMode = snap.mode === "light" || snap.mode === "dark" ? snap.mode : null;
        var sbg = snap.background || {};
        if (sbg.mode === "image" && typeof sbg.wallpaper === "string" && sbg.wallpaper) {
          wallpaper = sbg.wallpaper;
        } else if (sbg.mode === "solid" && typeof sbg.solid_color === "string" && sbg.solid_color) {
          solid = sbg.solid_color;
        }
      }
    }
    if (tokens) {
      // camelCase keys use the same camel → kebab mapping as
      // lib/desktop/theme-tokens.ts tokenKey() (hump and letter→digit
      // boundaries both get a hyphen); "--kebab" keys are written as-is.
      var keys = Object.keys(tokens);
      for (var i = 0; i < keys.length; i++) {
        var v = tokens[keys[i]];
        if (typeof v !== "string" || !v) continue;
        var css = keys[i].indexOf("--") === 0 ? keys[i] : "--" + keys[i]
          .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
          .replace(/([a-zA-Z])([0-9])/g, "$1-$2")
          .toLowerCase();
        if (/^--[a-z0-9-]+$/.test(css)) r.style.setProperty(css, v);
      }
      r.dataset.appearance = "custom";
      if (snapMode && localStorage.getItem(MODE_KEY) !== "light" && localStorage.getItem(MODE_KEY) !== "dark") {
        r.dataset.theme = snapMode;
        r.style.colorScheme = snapMode;
        r.classList.toggle("dark", snapMode === "dark");
      }
      if (wallpaper) {
        r.style.setProperty("--rvbbit-wallpaper", 'url("' + wallpaper.replace(/["\\]/g, "") + '")');
      } else if (solid) {
        r.style.setProperty("--rvbbit-wallpaper-solid", solid);
      }
    }
  } catch (e) {
    // best-effort: an unreadable appearance simply means the default theme
  }
  // Swallow Next.js 16.2.6's "Set objects are not supported" warning.
  // It originates inside framework RSC serialization (single-letter
  // prop names like `P`, `c`, `q`, `m: Set` in the payload, not our
  // code) and can only be truly removed by upgrading Next.
  //
  // Next calls console.error with %c/%s formatting:
  //   args[0] = "%c%s%c Only plain objects can be passed to Client
  //             Components from Server Components. %s objects are not
  //             supported.%s"
  //   args[1] = CSS for first %c block
  //   args[2] = "Server"        (label for first %s)
  //   args[3] = CSS for second %c block
  //   args[4] = "Set"           (label for second %s — what we want
  //                              to detect)
  //   args[5] = payload object excerpt
  //
  // Match on the format-string prefix in args[0] AND "Set" as a
  // substitution arg. Narrow enough not to false-positive; covers
  // both the bare and %c-styled message shapes.
  var WARN_FRAGMENT =
    "Only plain objects can be passed to Client Components from Server Components.";
  var oe = console.error;
  console.error = function () {
    var first = arguments[0];
    if (typeof first === "string" && first.indexOf(WARN_FRAGMENT) !== -1) {
      // Is this the "Set" variant specifically? Either a substitution
      // arg literally equals "Set", or the message contains the
      // pre-substituted "Set objects are not supported" phrase.
      if (first.indexOf("Set objects are not supported") !== -1) return;
      for (var i = 1; i < arguments.length; i++) {
        if (arguments[i] === "Set") return;
      }
    }
    return oe.apply(console, arguments);
  };
  window.__rvbbitLensWrapInstalled = Date.now();

  // The console.error wrapper above only suppresses the warning from
  // `window.console`. Next.js's dev-tools error overlay captures
  // server-side errors through its own internal pipeline and renders
  // them as a "Console Error" dialog inside <nextjs-portal>'s shadow
  // DOM, never going through window.console at all. To keep the
  // dev-tools view clean of THIS specific framework-internal noise,
  // we watch the portal's shadow root and hide any dialog whose text
  // contains the Set-warning signature. Production builds don't ship
  // the dev overlay, so this is implicitly dev-only.
  var SET_DIALOG_FRAGMENT = "Set objects are not supported";
  function hideMatchingDialogs(root) {
    var dialogs = root.querySelectorAll(".error-overlay-dialog-container, [data-nextjs-dialog]");
    for (var i = 0; i < dialogs.length; i++) {
      var d = dialogs[i];
      if ((d.textContent || "").indexOf(SET_DIALOG_FRAGMENT) !== -1) {
        // Walk up to the closest positioned ancestor so we hide the
        // whole error toast, not just the inner dialog scroll. The
        // overlay backdrop is several levels above.
        var node = d;
        for (var k = 0; k < 6 && node.parentElement; k++) node = node.parentElement;
        node.style.display = "none";
      }
    }
  }
  function attachShadowSuppressor(portal) {
    var sr = portal.shadowRoot;
    if (!sr) return;
    hideMatchingDialogs(sr);
    new MutationObserver(function () {
      hideMatchingDialogs(sr);
    }).observe(sr, { childList: true, subtree: true });
  }
  function watchForPortal() {
    var portal = document.querySelector("nextjs-portal");
    if (portal) {
      attachShadowSuppressor(portal);
      return;
    }
    new MutationObserver(function (records, obs) {
      for (var r = 0; r < records.length; r++) {
        for (var n = 0; n < records[r].addedNodes.length; n++) {
          var node = records[r].addedNodes[n];
          if (node.nodeName === "NEXTJS-PORTAL") {
            attachShadowSuppressor(node);
            obs.disconnect();
            return;
          }
        }
      }
    }).observe(document.documentElement, { childList: true, subtree: true });
  }
  // The portal isn't in the DOM yet when this script runs (pre-body).
  // Wait until DOM is interactive at minimum.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watchForPortal, { once: true });
  } else {
    watchForPortal();
  }
})();
