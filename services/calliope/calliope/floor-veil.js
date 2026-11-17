// Start the shared video renderer early, filling everything below navigation.
// Its cover scaling preserves the video's proportions and crops the excess.
(() => {
  const host = document.getElementById('floorInitialVeil');
  // The page starts with .app.is-veiled in its markup, so content never
  // flashes before this runs; every way out of here takes the class off.
  const app = host?.parentElement || document.querySelector('.app');
  if (!host) { app?.classList.remove('is-veiled', 'is-unveiling'); return; }
  let settled = false;
  let veil = null;
  let cleanupTimer = null;

  function cleanup() {
    clearTimeout(cleanupTimer);
    try { veil?.dispose(); } finally { veil = null; host.remove(); app?.classList.remove('is-veiled', 'is-unveiling'); }
    window.removeEventListener('pagehide', abandon);
  }
  function abandon() { settled = true; cleanup(); }
  function finish() {
    if (settled) return;
    settled = true;
    // Cards have rendered beneath this sibling overlay. Never wait for a video
    // frame or impose chat's minimum display time before revealing real data.
    const board = document.querySelector('.workspace-shell');
    const push = board ? Math.max(0, board.getBoundingClientRect().top - host.getBoundingClientRect().top) : 0;
    host.style.setProperty('--floor-veil-push', `${push}px`);
    host.classList.add('is-leaving');
    app?.classList.remove('is-veiled');
    app?.classList.add('is-unveiling');
    if (host.dataset.ready !== 'true' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) cleanup();
    else cleanupTimer = setTimeout(cleanup, 300);
  }
  window.floorInitialVeil = { finish };
  window.addEventListener('pagehide', abandon);
  import('/calliope/ascii-veil.js').then(({ mountAsciiVeil }) => {
    if (settled) return;
    const style = getComputedStyle(host);
    veil = mountAsciiVeil(host, {
      background: style.backgroundColor,
      accent: style.color,
      intensity: 0.62,
    });
  }).catch(abandon);
})();
