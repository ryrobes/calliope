/* Temporary build activity. Deliberately outside surfaces, history and memory. */
(() => {
  'use strict';
  let session = null, epoch = 0, timer = null, controller = null, options = {}, delayed = false, etag = '';
  const cards = new Map();
  const transitions = new WeakMap();
  const transitionURL = new URL('artifact-transition.js', document.currentScript.src);
  transitionURL.search = new URL(document.currentScript.src).search;
  let transitionModule;
  const loadTransition = () => transitionModule ||= import(transitionURL.href).catch(error => {
    transitionModule = null; throw error;
  });
  const host = () => document.getElementById('stage-builds');
  const phases = {composing: 'Shaping the next edit', writing: 'Saving the draft', preview: 'Draft saved',
    checking: 'Checking the artifact', publishing: 'Publishing to the Stage', published: 'Published to the Stage',
    failed: 'This step needs another attempt', stopped: 'Build stopped'};
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  };
  const age = item => Math.max(0, Date.now() / 1000 - Number(item.updated_at || 0));
  const who = item => options.agentFor?.(item.agent_key)?.name ||
    (item.agent_key === 'calliope' ? 'Calliope' : item.agent_key || 'Agent');
  function frame(source) {
    const node = el('iframe', 'artifact-build-frame');
    node.title = 'Unfinished artifact preview; scripts and live data are paused';
    node.setAttribute('sandbox', '');
    node.setAttribute('referrerpolicy', 'no-referrer');
    node.setAttribute('tabindex', '-1');
    node.srcdoc = source;
    return node;
  }
  function renderedImage(url) {
    const node = el('img', 'artifact-build-image');
    node.alt = 'Rendered artifact draft'; if (url) node.src = url; return node;
  }
  const imageCaption = card => card.item.kind === 'a2ui'
    ? `${card.renderer === 'native' ? 'GPUI · isolated Linux layout test' : 'Web renderer'} · ${card.device || 'desktop'} · ${card.capture?.stale ? 'previous revision' : 'current revision'} · actions simulated`
    : card.imageKind === 'layout'
    ? 'Layout preview · updates as code arrives' : 'Rendered draft · updates as the artifact changes';
  function releasePreview(container) {
    const state = transitions.get(container);
    state?.cancelPending?.(); state?.finish?.(); state?.player?.dispose();
    transitions.delete(container);
  }
  function transitionPreview(container, next) {
    let state = transitions.get(container);
    if (!state) { state = {}; transitions.set(container, state); }
    // Keep the last committed frame while loading a replacement. An obsolete
    // pending frame cannot clear it or complete a later transition.
    state.cancelPending?.(); state.finish?.();
    const previous = [...container.children];
    const from = previous.findLast(node => node instanceof HTMLImageElement);
    let finished = false, stop, loadTimeout;
    const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const finish = () => {
      if (finished) return;
      finished = true; stop?.(); next.style.removeProperty('opacity');
      previous.forEach(node => node.remove());
      state.finish = null; container.dataset.transition = 'idle';
    };
    const fade = (reason = 'webgl') => {
      if (finished) return;
      stop?.(); state.player?.dispose(); state.player = null;
      if (!container.isConnected || reducedMotion() || document.hidden) { finish(); return; }
      container.dataset.transition = 'fade';
      container.dataset.transitionFallback = reason instanceof Error ? reason.message : String(reason);
      const animation = next.animate([{opacity: 0}, {opacity: 1}],
        {duration: 450, easing: 'ease-in-out', fill: 'forwards'});
      stop = () => animation.cancel();
      animation.finished.then(finish, finish);
    };
    const ready = async () => {
      if (finished) return;
      clearTimeout(loadTimeout); next.removeEventListener('load', ready);
      state.cancelPending = null; state.finish = finish;
      if (!previous.length || reducedMotion() || document.hidden) {
        finish();
        if (!reducedMotion()) loadTransition().catch(() => {});
        return;
      }
      if (!from || !(next instanceof HTMLImageElement)) { fade('html'); return; }
      try {
        const module = await loadTransition();
        if (finished) return;
        if (!container.isConnected || reducedMotion() || document.hidden) { finish(); return; }
        state.player ||= module.createPreviewTransition(container);
        container.dataset.transition = 'gl';
        delete container.dataset.transitionFallback;
        stop = state.player.play(from, next, finish, () => fade('context-lost'));
      } catch (error) { fade(error); }
    };
    const cancelPending = () => {
      if (finished) return;
      finished = true; clearTimeout(loadTimeout); next.removeEventListener('load', ready);
      next.remove(); state.cancelPending = null; container.dataset.transition = 'idle';
    };
    next.style.opacity = '0';
    if (next instanceof HTMLIFrameElement) {
      container.dataset.transition = 'loading'; state.cancelPending = cancelPending;
      next.addEventListener('load', ready, {once: true});
      // External assets in an incomplete draft may never finish loading.
      // Keep the previous frame instead of flashing an empty document.
      loadTimeout = setTimeout(cancelPending, 10000);
    }
    container.append(next);
    if (!(next instanceof HTMLIFrameElement)) ready();
  }
  async function updateDialogPreview(card) {
    const {dialog, image: current} = card;
    if (!dialog) return;
    if (card.interactive) return;
    const preview = dialog.querySelector('.artifact-build-dialog-preview');
    if (!current) {
      if (card.item.preview_html) transitionPreview(preview, frame(card.item.preview_html));
      return;
    }
    const next = renderedImage(current.getAttribute('src'));
    try { await next.decode(); } catch { return; }
    if (card.dialog !== dialog || card.image !== current) return;
    transitionPreview(preview, next);
    dialog.querySelector('p').textContent = imageCaption(card);
  }
  function openPreview(card) {
    if (!card.item.preview_html && !card.image) return;
    const dialog = el('dialog', 'artifact-build-dialog');
    const header = el('header');
    header.append(el('strong', '', card.item.filename || 'Artifact draft'));
    const close = el('button', '', 'Close'); close.type = 'button';
    close.addEventListener('click', () => dialog.close()); header.append(close);
    const preview = el('div', 'artifact-build-dialog-preview');
    dialog.append(header, el('p', '', card.image ? imageCaption(card) : 'Layout preview · full render is preparing'), preview);
    dialog.addEventListener('close', () => {
      releasePreview(preview); dialog.remove();
      if (card.dialog === dialog) card.dialog = null;
    });
    dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
    document.body.append(dialog); card.dialog = dialog; dialog.showModal();
    updateDialogPreview(card);
  }
  function openInteractive(card) {
    if (!card.item.preview_url) return;
    card.dialog?.close();
    const dialog = el('dialog', 'artifact-build-dialog artifact-app-dialog');
    const header = el('header');
    header.append(el('strong', '', card.item.filename || 'App draft'));
    const close = el('button', '', 'Close'); close.type = 'button';
    close.addEventListener('click', () => dialog.close()); header.append(close);
    const description = el('p', '', 'Interactive web preview · authorized reads · actions simulated');
    const area = el('div', 'artifact-app-interactive');
    const iframe = el('iframe'); iframe.title = 'Interactive app draft';
    const reload = el('button', '', 'Load latest draft'); reload.type = 'button';
    reload.title = 'Reload the current definition and discard unsaved preview edits';
    reload.addEventListener('click', () => {iframe.src = card.item.preview_url;});
    header.insertBefore(reload, close);
    iframe.src = card.item.preview_url;
    iframe.dataset.device = card.device || 'desktop';
    iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin');
    area.append(iframe); dialog.append(header, description, area);
    dialog.addEventListener('close', () => {dialog.remove(); if(card.dialog === dialog){card.dialog=null;card.interactive=false;}});
    document.body.append(dialog);card.dialog=dialog;card.interactive=true;dialog.showModal();
  }
  function create(item) {
    const node = el('article', 'artifact-build');
    const header = el('header', 'artifact-build-head');
    const label = el('div');
    const byline = el('div', 'artifact-build-byline');
    const title = el('strong', 'artifact-build-title');
    label.append(byline, title);
    const expand = el('button', 'artifact-build-expand', 'Preview'); expand.type = 'button';
    expand.title = 'Open the last saved draft'; expand.hidden = true;
    header.append(label, expand);
    const scene = el('div', 'artifact-build-scene');
    const preview = el('div', 'artifact-build-preview');
    const code = el('div', 'artifact-build-code');
    const minimap = el('div', 'artifact-build-minimap'); minimap.setAttribute('aria-hidden', 'true');
    code.append(el('div', 'artifact-build-code-label', 'Code'), minimap);
    const veil = el('div', 'artifact-build-caption', 'The artifact is taking shape');
    scene.append(preview, code, veil);
    const footer = el('footer', 'artifact-build-foot');
    const status = el('span'); const updated = el('small'); footer.append(status, updated);
    node.append(header, scene, footer);
    const card = {node, byline, title, expand, scene, preview, minimap, veil, status, updated, item, source: '', iframe: null};
    card.device = 'desktop'; card.renderer = 'web';
    const controls = el('div', 'artifact-app-controls'); controls.hidden = true;
    for (const [field, values] of [['device', ['desktop', 'phone']], ['renderer', ['web', 'native']]]) {
      for (const value of values) {
        const button = el('button', '', value === 'native' ? 'GPUI' : value[0].toUpperCase()+value.slice(1)); button.type='button';
        button.dataset.field=field; button.dataset.value=value;
        button.addEventListener('click', () => {card[field]=value;update(card,card.item);});
        controls.append(button);
      }
    }
    const tryApp = el('button', '', 'Try app');tryApp.type='button';
    tryApp.addEventListener('click', () => openInteractive(card));controls.append(tryApp);
    node.insertBefore(controls, scene);card.controls=controls;
    expand.addEventListener('click', () => openPreview(card));
    card.resize = new ResizeObserver(() => {
      for (const iframe of preview.querySelectorAll('iframe')) iframe.style.transform = `scale(${preview.clientWidth / 1040})`;
    });
    card.resize.observe(preview);
    return card;
  }
  function update(card, item) {
    if (item.kind === 'a2ui') {
      card.controls.hidden = false;
      for (const button of card.controls.querySelectorAll('[data-field]')) button.setAttribute('aria-pressed', String(card[button.dataset.field]===button.dataset.value));
      const capture = item.app_previews?.[`${card.renderer}-${card.device}`];card.capture=capture;
      item = {...item, render_url:capture?.url, render_kind:card.renderer};
      // Switching view retains the container dimensions, but must not leave a
      // different renderer's old image masquerading as this selection.
      const selection=`${card.renderer}-${card.device}`;
      if(card.selection!==selection){releasePreview(card.preview);card.preview.replaceChildren();card.image=null;card.renderUrl=null;card.selection=selection;}
    }
    const changed = card.item.revision !== item.revision;
    card.item = item;
    card.node.dataset.kind = item.kind || 'html';
    card.node.dataset.phase = item.phase;
    card.byline.textContent = `${who(item)} · ${item.phase === 'published' ? 'published' : item.phase === 'stopped' ? 'build stopped' : item.producer_complete ? 'draft ready for review' : 'work in progress'}`;
    card.title.textContent = item.filename || 'Building an artifact';
    card.node.setAttribute('aria-label', `${who(item)}: ${item.filename || 'artifact'} — ${phases[item.phase] || 'Building'}`);
    if (!card.image && item.preview_html && item.preview_html !== card.source) {
      card.source = item.preview_html;
      const next = frame(card.source);
      transitionPreview(card.preview, next);
      card.iframe = next;
      next.style.transform = `scale(${card.preview.clientWidth / 1040})`;
      card.node.classList.add('has-preview'); card.expand.hidden = false;
      // An expanded draft follows the same revisions as its card.
      updateDialogPreview(card);
    }
    if (item.render_url && item.render_url !== card.renderUrl) {
      card.renderUrl = item.render_url;
      const image = renderedImage();
      image.onload = async () => {
        try { await image.decode(); } catch {
          if (card.renderUrl === item.render_url) card.renderUrl = null;
          return;
        }
        if (!card.node.isConnected || card.renderUrl !== item.render_url) return;
        transitionPreview(card.preview, image);
        card.iframe = null; card.image = image; card.imageKind = item.render_kind || 'render';
        card.node.classList.add('has-preview'); card.expand.hidden = false;
        updateDialogPreview(card);
        update(card, card.item);
      };
      image.onerror = () => { if (card.renderUrl === item.render_url) card.renderUrl = null; };
      image.src = item.render_url;
    }
    if (changed || !card.minimap.childElementCount) {
      card.minimap.replaceChildren(...(item.lines || []).map((width, i) => {
        const line = el('i'); line.style.setProperty('--length', `${Math.max(4, width)}%`);
        line.style.setProperty('--tone', String(.18 + (i % 5) * .12)); return line;
      }));
      card.node.classList.remove('just-updated');
      if (changed) requestAnimationFrame(() => card.node.isConnected && card.node.classList.add('just-updated'));
    }
    const elapsed = age(item), ended = ['published', 'stopped', 'failed'].includes(item.phase);
    card.node.classList.toggle('is-quiet', elapsed > 12 || delayed || ended);
    card.status.textContent = delayed ? 'Build updates delayed' : phases[item.phase] || 'Building';
    card.updated.textContent = item.phase === 'published' ? 'Ready to explore' :
      elapsed < 5 ? 'Updated just now' : elapsed < 60 ? `Updated ${Math.floor(elapsed)}s ago` : `Updated ${Math.floor(elapsed / 60)}m ago`;
    card.veil.textContent = card.image
      ? card.imageKind === 'layout' ? imageCaption(card)
        : item.render_status === 'rendering' ? 'Last rendered draft · rendering update'
        : item.render_status === 'failed' ? 'Last rendered draft · update could not finish'
        : item.preview_kind === 'incoming' ? 'Last rendered draft · next edit arriving' : 'Rendered draft · updates as edits are saved'
      : item.render_status === 'rendering' ? item.preview_kind === 'incoming' ? 'Rendering incoming layout…' : 'Rendering saved draft…'
      : item.render_status === 'failed' ? 'Layout preview · full render could not finish'
      : card.source && item.preview_kind === 'incoming' ? 'Incoming layout · not saved yet'
      : card.source ? 'Layout preview · waiting for a full render'
      : delayed ? 'Reconnecting to build activity' : elapsed > 12 ? 'Waiting for the next edit' : 'The artifact is taking shape';
    if (item.kind==='a2ui') {
      const error=item.preview_errors?.[`${card.renderer}-${card.device}`] || item.preview_errors?.preview;
      card.veil.textContent=error
        ? `Preview update failed${card.image?' · showing the previous revision':''} · ${String(error)}`
        : card.image?imageCaption(card):'Preparing '+card.renderer+' '+card.device+' preview…';
      if(error)card.status.textContent='Preview needs attention';
    }
    card.minimap.title = `${Math.round(Number(item.received_bytes || 0) / 1024)} KB received for this edit`;
  }
  function remove(card) {
    card.resize.disconnect(); releasePreview(card.preview);
    card.dialog?.close(); card.node.remove();
  }
  function render(items) {
    const root = host(); if (!root) return;
    window.CalliopeImageWork?.update(session, items.filter(item => item.kind === 'image'));
    const keep = new Set();
    for (const item of items.filter(item => item.kind !== 'image').slice(-8)) {
      if (!item.key) continue;
      keep.add(item.key);
      let card = cards.get(item.key);
      if (!card) { card = create(item); cards.set(item.key, card); root.append(card.node); }
      update(card, item);
    }
    for (const [key, card] of cards) if (!keep.has(key)) { remove(card); cards.delete(key); }
    root.hidden = cards.size === 0;
    options.onChange?.();
  }
  async function poll(generation) {
    if (generation !== epoch || !session) return;
    try {
      if (!document.hidden) {
        const requestController = new AbortController();
        controller = requestController;
        const timeout = setTimeout(() => requestController.abort(), 12000);
        let response;
        try {
          response = await fetch(`/api/calliope/sessions/${encodeURIComponent(session)}/artifact-work`,
            {credentials: 'same-origin', cache: 'no-store', signal: requestController.signal,
              headers: etag ? {'If-None-Match': etag} : {}});
          if (generation !== epoch) return;
          if (response.status === 304) {
            delayed = false;
            for (const card of cards.values()) update(card, card.item);
            options.onChange?.();
            return;
          }
          if (!response.ok) throw new Error(`Build updates: ${response.status}`);
          const result = await response.json();
          if (generation !== epoch) return;
          delayed = false; etag = response.headers.get('etag') || '';
          render(Array.isArray(result.items) ? result.items : []);
        } finally { clearTimeout(timeout); }
      }
    } catch (error) {
      if (generation !== epoch) return;
      delayed = true;
      for (const card of cards.values()) update(card, card.item);
      options.onChange?.();
    } finally {
      if (generation === epoch && session) timer = setTimeout(() => poll(generation), 2000);
    }
  }
  window.CalliopeArtifactWork = {
    setSession(id, nextOptions = {}) {
      options = nextOptions;
      if ((id || null) === session) return;
      epoch++; session = id || null; delayed = false; etag = '';
      window.CalliopeImageWork?.update(session, []);
      clearTimeout(timer); controller?.abort();
      for (const card of cards.values()) remove(card);
      cards.clear(); if (host()) host().hidden = true;
      if (session) poll(epoch);
    },
    hasItems: () => cards.size > 0,
    activeForTurn(turnId) {
      if (delayed) return '';
      const item = [...cards.values()].map(card => card.item).reverse().find(item =>
        item.turn_id === turnId && age(item) < 12 && !['published', 'stopped', 'failed'].includes(item.phase));
      return item ? `${who(item)} · ${phases[item.phase].toLowerCase()}` : '';
    },
  };
})();
