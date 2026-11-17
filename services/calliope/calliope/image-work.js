/* Temporary image requests, reconciled with exact saved receipt identities. */
(() => {
  'use strict';
  let session = null, items = [], signature = '', changed = false;
  const bindings = new Map(), shown = new Set();
  const escape = value => String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const key = item => `image-work:${item.key}`;
  function ratio(item) {
    const parts = String(item.aspect_ratio || '').match(/^(\d+(?:\.\d+)?)[:x/](\d+(?:\.\d+)?)$/);
    const value = parts && Number(parts[1]) / Number(parts[2]);
    return Number.isFinite(value) && value >= .33 && value <= 3 ? value : 1.5;
  }
  const status = item => item.phase === 'failed' ? 'Image generation failed'
    : item.phase === 'stopped' ? 'Image generation stopped'
    : item.phase === 'image_ready' ? 'Preparing image' : 'Creating image';
  const placeholder = item => `<div class="image-work-placeholder" style="--image-work-ratio:${ratio(item)}">
    <span class="image-work-status"><i aria-hidden="true"></i>${escape(status(item))}</span>
    <p>${escape(item.prompt || 'An image is taking shape.')}</p></div>`;
  window.CalliopeImageWork = {
    update(id, next) {
      if (session !== id) { session = id; items = []; bindings.clear(); shown.clear(); signature = ''; changed = true; }
      const nextSignature = JSON.stringify(next);
      if (nextSignature !== signature) { signature = nextSignature; items = next; changed = true; }
    },
    takeChanged() { const value = changed; changed = false; return value; },
    hasItems() { return items.some(item => ![...bindings.values()].some(binding => binding.item.key === item.key)); },
    pending(surfaces) {
      for (const item of items) {
        const matches = surfaces.filter(surface => {
          const source = surface.source || {};
          return (surface.kind === 'image' || /^image\//.test(surface.payload?.mime_type || ''))
            && String(source.specialist_run_id || surface.turn_id) === String(item.agent_run_id)
            && String(source.receipt_call_id || surface.tool_call_id || '') === item.call_id;
        });
        // One pending card becomes the first result; additional batch images
        // remain ordinary saved cards, each with its own durable identity.
        if (matches.length && ![...bindings.values()].some(binding => binding.item.key === item.key)) {
          bindings.set(matches[0].id, {item, animate: shown.has(item.key), done: false});
        }
      }
      const completed = new Set([...bindings.values()].map(binding => binding.item.key));
      return items.filter(item => !completed.has(item.key));
    },
    markup(item, agent) {
      shown.add(item.key);
      return `<article class="surface kind-image image-work-card" data-stage-key="${escape(key(item))}" aria-label="${escape(status(item))}">
        <header class="surface-head"><div class="surface-titles"><h3>${escape(item.title || 'Image in progress')}</h3><p>${escape(agent?.name || item.agent_key || 'Calliope')} · ${escape(status(item).toLowerCase())}</p></div></header>
        <div class="surface-body">${placeholder(item)}</div></article>`;
    },
    keyFor(surface) { const binding = bindings.get(surface.id); return binding ? key(binding.item) : ''; },
    wrap(surface, body) {
      const binding = bindings.get(surface.id);
      if (!binding?.animate) return body;
      return `<div class="image-work-reveal" data-image-work-result="${escape(surface.id)}" style="--image-work-ratio:${ratio(binding.item)}">
        <div class="image-work-result">${body}</div><div class="image-work-cover" aria-hidden="true">${placeholder(binding.item)}</div></div>`;
    },
    hydrate(root) {
      for (const node of root.querySelectorAll('[data-image-work-result]')) {
        const binding = bindings.get(node.dataset.imageWorkResult), img = node.querySelector('img');
        if (!binding || !img || node._imageWorkReady) continue;
        node._imageWorkReady = true;
        const reveal = async () => {
          try { await img.decode(); } catch { /* Existing image fallback handles failed downloads. */ }
          if (!node.isConnected || bindings.get(node.dataset.imageWorkResult) !== binding) return;
          if (img.naturalWidth && img.naturalHeight) node.style.setProperty('--image-work-ratio', img.naturalWidth / img.naturalHeight);
          const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
          if (binding.done || reduced) { node.classList.add('is-ready'); node.dataset.state = 'complete'; binding.done = true; return; }
          requestAnimationFrame(() => requestAnimationFrame(() => {
            if (!node.isConnected) return;
            node.classList.add('is-ready'); node.dataset.state = 'revealing'; binding.done = true;
            setTimeout(() => { if (node.isConnected) node.dataset.state = 'complete'; }, 760);
          }));
        };
        node.dataset.state = 'loading';
        if (img.complete) void reveal();
        else { img.addEventListener('load', reveal, {once:true}); img.addEventListener('error', reveal, {once:true}); }
      }
    },
  };
})();
