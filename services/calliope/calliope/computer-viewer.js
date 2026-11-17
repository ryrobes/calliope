/* Observe Kasm's public DOM states; reconnect/auth/input remain Kasm's job. */
(() => {
  const root = document.documentElement;
  const container = document.getElementById('noVNC_container');
  if (!container) return;
  const recovery = document.body.dataset.calliopeDisconnected === 'true';
  const backdrop = document.createElement('div');
  backdrop.id = 'calliope-viewer-backdrop'; backdrop.setAttribute('aria-hidden', 'true');
  // Keep Kasm's own canvases first for client code that uses broad selectors.
  document.body.append(backdrop);
  const status = document.createElement('p');
  status.id = 'calliope-viewer-status'; status.setAttribute('role', 'status');
  const label = document.createElement('span');
  const reconnect = document.createElement('button');
  reconnect.type = 'button'; reconnect.textContent = 'Reconnect'; reconnect.hidden = true;
  reconnect.addEventListener('click', () => {
    if (recovery && parent !== window) {
      parent.postMessage({type: 'calliope-computer-reconnect', manual: true}, location.origin);
    } else if (recovery) {
      location.replace('./?path=api%2Fcalliope%2Fcomputer%2Fstream%3Fview_only%3D1&resize=scale&autoconnect=true&reconnect=true');
    } else location.reload();
  });
  status.append(label, reconnect);
  document.body.append(status);
  let theme = {background: '#0d0b09', accent: '#d9a441', text: '#e8dfd2'};
  let visible = true, interactive = false, effect, pending = false, mount, failed = false;
  let state = 'loading', previousSize = '';
  const needsEffect = () => {
    if (!visible || document.hidden) return false;
    if (state !== 'connected') return true;
    const canvas = container.querySelector('canvas');
    if (!canvas) return true;
    const bounds = canvas.getBoundingClientRect();
    return container.clientWidth - bounds.width > 20 || container.clientHeight - bounds.height > 20;
  };
  const stopEffect = () => { effect?.dispose(); effect = null; };
  async function updateEffect() {
    if (!needsEffect()) { stopEffect(); return; }
    if (effect || pending || failed) return;
    pending = true;
    try {
      mount ||= (await import('/calliope/ascii-veil.js')).mountAsciiVeil;
      if (needsEffect()) effect = mount(backdrop, {...theme, intensity: 0.62});
    } catch { failed = true; /* Plain theme background remains a usable fallback. */ }
    finally { pending = false; }
  }
  function report() {
    const classes = root.classList;
    state = ['reconnecting', 'connecting', 'disconnecting', 'connected', 'disconnected']
      .find(name => classes.contains(`noVNC_${name}`)) || 'loading';
    const notice = document.getElementById('noVNC_status');
    const error = notice?.classList.contains('noVNC_open') &&
      (notice.classList.contains('noVNC_status_error') || notice.classList.contains('noVNC_status_warn'));
    const labels = {loading: 'Loading desktop…', connecting: 'Connecting…', reconnecting: 'Reconnecting…',
      disconnecting: 'Disconnecting…', connected: 'Connected', disconnected: 'Disconnected'};
    const text = error ? `${labels[state]} · ${notice.textContent.trim()}` : labels[state];
    if (label.textContent !== text) label.textContent = text;
    reconnect.hidden = state !== 'disconnected' || (!interactive && parent !== window);
    status.dataset.state = state; status.dataset.error = String(!!error);
    const canvas = container.querySelector('canvas');
    if (canvas && canvas.width >= 64 && canvas.height >= 64) {
      const size = `${canvas.width}x${canvas.height}`;
      if (size !== previousSize) {
        previousSize = size;
        parent.postMessage({type: 'calliope-computer-size', width: canvas.width, height: canvas.height}, location.origin);
      }
    }
    updateEffect();
  }
  addEventListener('message', event => {
    if (event.source !== parent || event.origin !== location.origin || event.data?.type !== 'calliope-computer-theme') return;
    const next = {...theme};
    for (const key of ['background', 'accent', 'text']) {
      const value = event.data[key];
      if (typeof value === 'string' && value.length < 200 && CSS.supports('color', value)) next[key] = value;
    }
    if (JSON.stringify(next) !== JSON.stringify(theme)) {
      theme = next;
      root.style.setProperty('--void', theme.background);
      root.style.setProperty('--amber', theme.accent);
      root.style.setProperty('--text', theme.text);
      stopEffect();
    }
    visible = event.data.visible !== false;
    interactive = event.data.interactive === true;
    report();
  });
  new MutationObserver(report).observe(root, {attributes: true, attributeFilter: ['class']});
  new MutationObserver(report).observe(container, {childList: true, subtree: true,
    attributes: true, attributeFilter: ['width', 'height', 'style']});
  const notice = document.getElementById('noVNC_status');
  if (notice) new MutationObserver(report).observe(notice, {attributes: true, childList: true, subtree: true, characterData: true});
  new ResizeObserver(report).observe(container);
  addEventListener('visibilitychange', updateEffect);
  addEventListener('pagehide', stopEffect);
  parent.postMessage({type: 'calliope-computer-ready'}, location.origin);
  report();
  if (recovery && parent !== window) parent.postMessage({type: 'calliope-computer-reconnect'}, location.origin);
})();
