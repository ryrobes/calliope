// Boot veil, armed before first paint.
//
// Loaded as a classic (non-defer) script immediately after <body> opens, so
// it executes after the render-blocking stylesheets resolve but before the
// browser paints anything. calliope.js is a 1.3MB deferred bundle — by the
// time its init() runs, the empty chat shell has been on screen for a while.
// Arming the veil class here means the very first paint is the veil ground,
// and kicking the ASCII fx import here starts the three.js + mp4 fetches
// seconds earlier than init() could.
//
// calliope.js reads window.__bootVeil for the reveal handshake: it removes
// the boot-veil class once init settles (holding while the fx is playing);
// the observer below then disposes the fx after the fade. A stalled load
// offers reload without exposing a half-initialized page. CSP disallows inline
// scripts, hence this file.
(function () {
  var active = false;
  try {
    var params = new URLSearchParams(window.location.search);
    active = params.has("start")
      || params.has("session")
      || params.has("brief")
      || Boolean(sessionStorage.getItem("calliope.floorIntake.v1"));
  } catch (error) {}
  // A plain /calliope arrival is a fresh chat with nothing to restore, so it
  // paints directly (headless Stage captures load it too and never reveal).
  if (!active || !document.body) {
    window.__bootVeil = null;
    return;
  }
  document.body.classList.add("boot-veil");
  window.__bootVeil = { active: true, shownAt: performance.now() };
  // The veil must block keyboard interaction as well as pointer clicks.
  // Observe parsing: most page content has not been created at this point.
  var locked = new Map();
  var lockContent = function () {
    Array.from(document.body.children).forEach(function (node) {
      if (/^(SCRIPT|STYLE|LINK)$/.test(node.tagName)
        || node.id === "bootVeilFx" || node.id === "bootVeilRecovery") return;
      if (!locked.has(node)) { locked.set(node, node.inert); node.inert = true; }
    });
  };
  document.body.setAttribute("aria-busy", "true");
  lockContent();
  var contentWatcher = new MutationObserver(function () {
    if (document.body.classList.contains("boot-veil")) { lockContent(); return; }
    contentWatcher.disconnect();
    locked.forEach(function (wasInert, node) { node.inert = wasInert; });
    locked.clear();
    document.body.removeAttribute("aria-busy");
    document.getElementById("bootVeilRecovery")?.remove();
    window.clearTimeout(window.__bootVeil?.failsafe);
  });
  contentWatcher.observe(document.body, { childList: true, attributes: true, attributeFilter: ["class"] });
  // Slow or broken loads stay covered. Give the viewer a way to retry rather
  // than silently exposing controls whose conversation has not been restored.
  window.__bootVeil.failsafe = window.setTimeout(function () {
    if (!document.body.classList.contains("boot-veil")) return;
    var recovery = document.createElement("div");
    recovery.id = "bootVeilRecovery";
    recovery.className = "boot-veil-recovery";
    recovery.setAttribute("role", "status");
    recovery.textContent = "This is taking longer than usual. You can reload to try again.";
    var retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "Reload";
    retry.addEventListener("click", function () { window.location.reload(); });
    recovery.appendChild(retry);
    document.body.appendChild(recovery);
  }, 30000);
  try {
    var fxHost = document.createElement("div");
    fxHost.id = "bootVeilFx";
    document.body.appendChild(fxHost);
    var rootStyles = getComputedStyle(document.documentElement);
    var veilBackground = (rootStyles.getPropertyValue("--void") || "").trim() || "#0d0b09";
    var veilAccent = (rootStyles.getPropertyValue("--amber") || "").trim() || "#d9a441";
    import("/calliope/ascii-veil.js").then(function (mod) {
      if (!document.body.classList.contains("boot-veil")) { fxHost.remove(); return; }
      var veil = mod.mountAsciiVeil(fxHost, {
        src: "/calliope/calliope-avatar-busy-loop.mp4",
        background: veilBackground,
        accent: veilAccent,
        intensity: 0.62,
      });
      var cleanup = function () {
        try { veil.dispose(); } catch (error) {}
        fxHost.remove();
      };
      var watcher = new MutationObserver(function () {
        if (!document.body.classList.contains("boot-veil")) {
          watcher.disconnect();
          window.setTimeout(cleanup, 420); // let the fade finish
        }
      });
      watcher.observe(document.body, { attributes: true, attributeFilter: ["class"] });
    }).catch(function () { fxHost.remove(); });
  } catch (error) {}
})();
