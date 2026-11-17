// Seed the loading screen before font, runtime, or sketch-data waits. Prefer
// the live room; a standalone tab can use the same cached adaptive snapshot.
(() => {
  const root = document.documentElement;
  const keys = ["--void", "--panel", "--panel-raised", "--bone", "--bone-bright",
    "--fog", "--dim", "--line", "--line-hot", "--amber", "--jade", "--danger",
    "--warning", "--success", "--main", "--rvbbit-accent"];
  let mode = new URLSearchParams(location.search).get("theme");
  let tokens = {};
  try {
    const cached = JSON.parse(localStorage.getItem("rvbbit-warehouse-adaptive-theme-v1") || "null");
    tokens = cached?.tokens || {};
    mode = mode || cached?.color_mode || cached?.mode;
  } catch {}
  try {
    if (window.parent !== window) {
      const parentRoot = window.parent.document.documentElement;
      const styles = window.parent.getComputedStyle(parentRoot);
      const live = Object.fromEntries(keys.map(key => [key, styles.getPropertyValue(key).trim()]).filter(([, value]) => value));
      if (Object.keys(live).length) tokens = live;
      mode = parentRoot.dataset.warehouseColorMode || parentRoot.dataset.theme || mode;
      root.dataset.embedded = "true";
    }
  } catch {} // Cross-origin embedding still has cached or default colors.
  root.dataset.theme = mode === "light" || mode === "dark" ? mode
    : window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
  keys.forEach(key => {
    const value = String(tokens[key] || "").trim();
    if (value && value.length <= 240 && !/[<>{};]|url\s*\(/i.test(value)) root.style.setProperty(key, value);
  });
})();

// Excalidraw reads this global while its packaged runtime initializes. Keep its
// fonts same-origin and authenticated rather than reaching out to a public CDN.
window.EXCALIDRAW_ASSET_PATH = "/calliope/sketch-assets/";

// The runtime measures text once at mount and never recomputes when fonts
// finish loading, so a font race bakes narrower fallback metrics into every
// element and labels clip permanently. Await the Excalifont subsets
// (bounded at 3s; fail-open if a hashed filename rotates with a runtime
// upgrade) and only then inject the runtime script. This file owns the
// injection because the page's CSP forbids inline scripts.
const CALLIOPE_SKETCH_ASSET_VERSION = (() => {
  try {
    return new URL(document.currentScript.src).searchParams.get("v") || "";
  } catch {
    return "";
  }
})();
(async () => {
  const version = CALLIOPE_SKETCH_ASSET_VERSION;
  // The server injects the subset hashes it actually ships (body
  // data-excalifont), so runtime upgrades that rotate or ADD subsets can
  // never reopen the race. The static list is only a fallback for pages
  // rendered without the placeholder.
  const injected = String(document.body?.dataset?.excalifont || "")
    .split(",")
    .filter((hash) => /^[0-9a-f]{32}$/.test(hash));
  const hashes = injected.length ? injected : [
    "349fac6ca4700ffec595a7150a0d1e1d", "3f2c5db56cc93c5a6873b1361d730c16",
    "41b173a47b57366892116a575a43e2b6", "623ccf21b21ef6b3a0d87738f77eb071",
    "a88b72a24fb54c9f94e3b5fdaa7481c9", "b9dcf9d2e50a1eaf42fc664b50a3fd0d",
    "be310b9bcd4f1a43f571c46df7809174",
  ];
  try {
    const faces = hashes.map((hash) => new FontFace(
      "Excalifont",
      `url(/calliope/sketch-assets/fonts/Excalifont/Excalifont-Regular-${hash}.woff2)`,
    ));
    faces.push(new FontFace("Superior",
      "url(/calliope/sketch-assets/fonts/Superior/Superior-Regular.woff2)"));
    await Promise.race([
      Promise.allSettled(faces.map(async (face) => {
        try {
          await face.load();
          document.fonts.add(face);
        } catch {}
      })),
      new Promise((resolve) => setTimeout(resolve, 3000)),
    ]);
  } catch {}
  const script = document.createElement("script");
  script.src = `/calliope/sketch-runtime.js?v=${encodeURIComponent(version)}`;
  document.body.appendChild(script);
})();
