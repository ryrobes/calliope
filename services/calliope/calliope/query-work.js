/* Live shadows of saved receipts. No queries, surfaces or history are created here. */
(() => {
  'use strict';
  const SQL_TOOLS = new Set(['run_sql', 'run_sql_multi', 'calliope_sheet_query']);
  const isSql = name => SQL_TOOLS.has(String(name || '').split(/__|[./]/).at(-1));
  const callKey = (scope, id) => id ? `${scope}:${id}` : '';
  const text = value => {
    if (value == null) return '—';
    return (typeof value === 'object' ? JSON.stringify(value) : String(value)).slice(0, 180);
  };

  function receipt(surface) {
    if (surface.kind !== 'query' || !surface.id) return null;
    const data = surface.payload || {}, source = surface.source || {};
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const raw = data.columns || (rows[0] && !Array.isArray(rows[0]) ? Object.keys(rows[0]) : []);
    const columns = (Array.isArray(raw) ? raw : Object.keys(raw)).slice(0, 4).map((field, index) => ({
      name: typeof field === 'string' ? field : field?.name || field?.column_name || field?.column || `Column ${index + 1}`,
      index,
    }));
    const failure = data.error || (data.status === 'error' ? data.message || 'Query failed' : '');
    return {
      key: `receipt:${surface.id}`, surfaceId: surface.id, turnId: String(surface.turn_id || ''),
      scope: String(source.specialist_run_id || surface.turn_id || ''),
      call: callKey(source.specialist_run_id || surface.turn_id, source.receipt_call_id || surface.tool_call_id),
      agent: source.specialist_key || 'calliope',
      title: surface.title || source.exploration?.intent || 'Query result',
      intent: source.exploration?.intent || '',
      status: failure ? 'failed' : 'complete', error: text(failure || ''),
      columns: columns.map(column => column.name),
      rows: rows.slice(0, 3).map(row => columns.map(column => text(Array.isArray(row) ? row[column.index] : row?.[column.name]))),
      rowCount: data.row_count ?? rows.length, elapsed: data.elapsed_ms, engine: data.engine,
      at: Date.parse(surface.created_at) || 0,
    };
  }

  const MAX_AGE = 90000, RETIRE_DELAY = 1800;
  // Run identity owns lifetime. Replayed or overflowed receipts never return,
  // and timestamps from the server preserve age across refreshes.
  class RollingQueries {
    constructor() { this.reset(null); }
    reset(session) {
      this.session = session; this.items = new Map(); this.replaced = new Set();
      this.retired = new Set(); this.visible = []; this.sequence = 0; this.delayed = false;
    }
    sync(snapshot, now = Date.now()) {
      if (snapshot.session !== this.session) this.reset(snapshot.session);
      this.delayed = Boolean(snapshot.delayed);
      if (!this.session) return;
      const runs = snapshot.runs || {};
      const put = item => {
        const previous = this.items.get(item.key);
        if (this.retired.has(item.key) || (!previous && runs[item.scope] !== 'running')) return;
        const at = previous?.at || item.at || now;
        const completed = item.status !== 'running';
        const completedAt = completed ? previous?.completedAt ?? item.completedAt ?? (previous ? now : Math.min(at, now)) : null;
        if (completedAt !== null && now - completedAt >= MAX_AGE) {
          this.retired.add(item.key); this.items.delete(item.key); return;
        }
        this.items.set(item.key, {...item, at, completedAt, started: item.started || previous?.started || at,
          endAt: previous?.endAt ?? null, sequence: previous?.sequence ?? item.sequence ?? ++this.sequence});
      };
      const receipts = [...(snapshot.surfaces || [])].sort((a, b) =>
        (Date.parse(a.created_at) || 0) - (Date.parse(b.created_at) || 0) || Number(a.ordinal || 0) - Number(b.ordinal || 0))
        .map(receipt).filter(Boolean);
      const callCounts = new Map();
      for (const item of receipts) if (item.call) callCounts.set(item.call, (callCounts.get(item.call) || 0) + 1);
      for (const item of receipts) {
        if (item.call) this.replaced.add(item.call);
        // A query finishing updates its existing place in the row. Replacing
        // its timestamp with receipt time made parallel calls swap positions.
        const pending = this.items.get(`call:${item.call}`);
        if (pending && callCounts.get(item.call) === 1) {
          item.at = pending.at; item.sequence = pending.sequence; item.completedAt = now;
          this.visible = this.visible.map(key => key === pending.key ? item.key : key);
        }
        put(item);
      }
      for (const task of snapshot.tasks || []) {
        if (!isSql(task.name) || !task.call || this.replaced.has(task.call)) continue;
        put({key: `call:${task.call}`, call: task.call, scope: task.scope, agent: task.agent || 'calliope',
          title: task.title || 'Reading data', status: task.status, error: task.error || '',
          started: task.started, at: task.started || now, columns: [], rows: []});
      }
      for (const [key, item] of this.items) {
        if (!item.surfaceId && this.replaced.has(item.call)) { this.items.delete(key); continue; }
        if (runs[item.scope] === 'ended' && item.endAt === null) item.endAt = now;
      }
    }
    view(now = Date.now(), held = false, capacity = 6) {
      capacity = Math.max(1, Math.floor(capacity));
      for (const [key, item] of this.items) {
        if ((item.endAt !== null && now - item.endAt >= RETIRE_DELAY)
          || (item.completedAt !== null && now - item.completedAt >= MAX_AGE)) {
          this.retired.add(key); this.items.delete(key);
        }
      }
      const candidates = [...this.items.values()].sort((a, b) => b.at - a.at || b.sequence - a.sequence);
      const retained = held ? this.visible.filter(key => this.items.has(key)) : [];
      const keys = [...new Set([...retained, ...candidates.map(item => item.key)])].slice(0, capacity);
      if (!held) for (const item of candidates) if (!keys.includes(item.key)) {
        this.retired.add(item.key); this.items.delete(item.key);
      }
      this.visible = keys;
      const items = keys.map(key => this.items.get(key));
      return {items, total: items.length, running: items.filter(item => item.status === 'running' && item.endAt === null).length,
        delayed: this.delayed};
    }
  }

  if (typeof module !== 'undefined') module.exports = {RollingQueries, receipt, isSql, callKey};
  if (typeof window === 'undefined') return;
  const store = new RollingQueries(), cards = new Map();
  let options = {}, timer = null, collapse = null, hovered = false, focused = false, suppressed = false;
  let flightLayer = null, nextDeparture = 0, flightsEnd = 0;
  const flights = new Set(), filingReceipts = new Set();
  let evidenceKey = '';
  const visibleReceiptIds = (includeFiling = false) => suppressed ? [] : [...new Set([
    ...[...cards.values()].map(card => card.item.surfaceId),
    ...[...flights].map(flight => flight.surfaceId),
    ...(includeFiling ? filingReceipts : []),
  ].filter(Boolean))];
  function notifyEvidence() {
    const key = JSON.stringify([store.session, visibleReceiptIds().sort()]);
    if (key === evidenceKey) return;
    evidenceKey = key; options.onEvidenceChange?.();
  }
  const motion = () => !document.hidden && !matchMedia('(prefers-reduced-motion: reduce)').matches;
  const host = () => document.getElementById('stage-queries');
  const el = (tag, className, value) => {
    const node = document.createElement(tag); node.className = className || '';
    if (value != null) node.textContent = value; return node;
  };
  function clearFlights() {
    for (const flight of [...flights]) flight.cancel();
    flightLayer?.remove(); flightLayer = null; nextDeparture = flightsEnd = 0;
  }
  function tuckIntoEvidence(card, box) {
    const viewport = host().closest('.stage-scroll');
    if (!viewport) return false;
    const bounds = viewport.getBoundingClientRect();
    const clip = {left: Math.max(0, bounds.left), top: Math.max(0, bounds.top),
      right: Math.min(innerWidth, bounds.right), bottom: Math.min(innerHeight, bounds.bottom)};
    if (box.bottom <= clip.top || box.top >= clip.bottom || box.right <= clip.left || box.left >= clip.right) return false;
    const ledgers = [...viewport.querySelectorAll('[data-zen-ledger]')];
    const ledger = ledgers.find(node => node.dataset.zenLedger === card.item.turnId) || ledgers[0];
    const target = ledger?.querySelector('.evidence-ledger-head');
    const targetBox = target?.getBoundingClientRect();
    const visible = targetBox && targetBox.bottom > clip.top && targetBox.top < clip.bottom
      && targetBox.right > clip.left && targetBox.left < clip.right;
    const targetX = visible ? Math.max(clip.left + 20, Math.min(clip.right - 20, targetBox.left + targetBox.width / 2))
      : Math.max(clip.left + 20, Math.min(clip.right - 20, box.left + box.width / 2));
    const targetY = visible ? Math.max(clip.top + 12, Math.min(clip.bottom - 12, targetBox.top + targetBox.height / 2)) : clip.bottom + 40;
    if (!flightLayer) {
      flightLayer = el('div', 'query-work query-work-flight-layer');
      flightLayer.setAttribute('aria-hidden', 'true'); flightLayer.inert = true;
      const style = getComputedStyle(host());
      for (const name of style) if (name.startsWith('--workspace-')) flightLayer.style.setProperty(name, style.getPropertyValue(name));
      document.body.append(flightLayer);
    }
    Object.assign(flightLayer.style, {left: `${clip.left}px`, top: `${clip.top}px`,
      width: `${clip.right-clip.left}px`, height: `${clip.bottom-clip.top}px`});
    const wrapper = el('div', 'query-work-flight');
    wrapper.dataset.destination = visible ? 'evidence' : 'below';
    Object.assign(wrapper.style, {left: `${box.left-clip.left}px`, top: `${box.top-clip.top}px`, width: `${box.width}px`, height: `${box.height}px`});
    const token = el('span', 'query-work-token');
    token.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14h18M9 9v11M15 9v11"/></svg>';
    const node = card.node;
    node.classList.add('is-leaving'); node.setAttribute('aria-hidden', 'true'); node.inert = true;
    Object.assign(node.style, {width: `${box.width}px`, height: `${box.height}px`});
    wrapper.append(node, token); flightLayer.append(wrapper);
    const now = performance.now(), delay = Math.max(0, nextDeparture - now), duration = 780;
    nextDeparture = now + delay + 95; flightsEnd = Math.max(flightsEnd, now + delay + duration);
    const dx = targetX - (box.left + box.width / 2), dy = targetY - (box.top + box.height / 2);
    const movement = wrapper.animate([
      {transform: 'translate(0,0)', opacity: 1, offset: 0},
      {transform: 'translate(0,4px)', opacity: 1, offset: .28},
      {transform: `translate(${dx}px,${dy}px)`, opacity: 1, offset: .88},
      {transform: `translate(${dx}px,${dy}px) scale(.4)`, opacity: 0, offset: 1},
    ], {duration, delay, easing: 'cubic-bezier(.4,0,.2,1)', fill: 'both'});
    const shrink = node.animate([{transform: 'scale(1)', opacity: 1}, {transform: 'scale(.16)', opacity: 0}],
      {duration: 270, delay, easing: 'ease-in-out', fill: 'both'});
    const icon = token.animate([{opacity: 0, transform: 'translate(-50%,-50%) scale(.5)'},
      {opacity: 1, transform: 'translate(-50%,-50%) scale(1)'}], {duration: 180, delay: delay + 100, fill: 'both'});
    const cleanup = () => {
      wrapper.remove(); flights.delete(flight);
      if (!flights.size) { flightLayer?.remove(); flightLayer = null; filingReceipts.clear(); }
      notifyEvidence();
    };
    const flight = {surfaceId: card.item.surfaceId, cancel: () => { movement.cancel(); shrink.cancel(); icon.cancel(); cleanup(); }};
    flights.add(flight); filingReceipts.add(card.item.surfaceId);
    movement.finished.then(() => {
      if (visible && target.isConnected && motion()) target.animate(
        [{filter: 'brightness(1)'}, {filter: 'brightness(1.6)'}, {filter: 'brightness(1)'}], {duration: 450});
      cleanup();
    }, cleanup);
    return true;
  }
  function remove(card, animate = false) {
    window.CalliopeThinkingOrbs?.unmount(card.orb);
    const node = card.node, grid = node.parentElement;
    const box = node.getBoundingClientRect(), origin = grid.getBoundingClientRect();
    card.motion?.cancel();
    if (!animate || !motion()) { card.node.remove(); return; }
    if (card.item.surfaceId && tuckIntoEvidence(card, box)) return;
    node.classList.add('is-leaving'); node.setAttribute('aria-hidden', 'true'); card.open.tabIndex = -1;
    Object.assign(node.style, {position: 'absolute', left: `${box.left-origin.left}px`, top: `${box.top-origin.top}px`, width: `${box.width}px`});
    node.animate([{opacity: 1, transform: 'none'}, {opacity: 0, transform: 'translateY(16px) scale(.93)'}],
      {duration: 280, easing: 'ease-in', fill: 'forwards'}).finished.then(() => node.remove(), () => node.remove());
  }
  function create(item) {
    const node = el('article', 'query-shadow'); node.dataset.queryShadow = item.key;
    const header = el('header'), identity = el('span', 'query-shadow-agent');
    const avatar = el('span', 'query-shadow-avatar'), name = el('span'); identity.append(avatar, name);
    const status = el('small', 'query-shadow-state'); header.append(identity, status);
    const title = el('strong', 'query-shadow-title');
    const preview = el('div', 'query-shadow-preview');
    const waiting = el('div', 'query-shadow-waiting');
    const orb = el('canvas'); orb.dataset.thinkingOrbTint = 'theme';
    const detail = el('span'); waiting.append(orb, detail);
    const footer = el('footer'), meta = el('span'), open = el('button', 'query-shadow-open', 'Explore ↗');
    open.type = 'button'; footer.append(meta, open); node.append(header, title, preview, waiting, footer);
    const card = {node, avatar, name, status, title, preview, waiting, orb, detail, meta, open, item};
    open.addEventListener('click', () => options.open?.(card.item.surfaceId));
    return card;
  }
  function update(card, item, view) {
    card.item = item;
    const agent = options.agentFor?.(item.agent);
    card.name.textContent = agent?.name || (item.agent === 'calliope' ? 'Calliope' : item.agent);
    const avatar = options.avatar?.(item.agent) || '';
    if (card.avatar._markup !== avatar) { card.avatar.innerHTML = avatar; card.avatar._markup = avatar; }
    card.title.textContent = item.title; card.title.title = item.intent || item.title;
    card.node.dataset.status = item.status;
    const ended = item.endAt !== null;
    card.node.classList.toggle('is-settled', ended);
    const running = item.status === 'running' && !ended && !view.delayed;
    card.status.textContent = ended ? (item.surfaceId ? 'In Evidence' : 'Work finished') : item.status === 'running'
      ? view.delayed ? 'Updates delayed' : `Running · ${Math.max(0, Math.floor((Date.now() - item.started) / 1000))}s`
      : item.status === 'failed' ? 'Needs another attempt' : 'Result received';
    card.open.hidden = !item.surfaceId;
    card.open.setAttribute('aria-label', `Explore ${item.title} in Data`);
    const meta = [item.rowCount != null ? `${Number(item.rowCount).toLocaleString()} ${Number(item.rowCount) === 1 ? 'row' : 'rows'}` : '',
      item.elapsed != null ? `${Number(item.elapsed).toLocaleString()} ms` : '', options.engineLabel?.(item.engine) || ''].filter(Boolean);
    card.meta.textContent = meta.join(' · ');
    const signature = JSON.stringify([item.columns, item.rows, item.error, item.status]);
    if (card.signature !== signature) {
      card.signature = signature; card.preview.replaceChildren();
      if (item.status === 'failed') {
        card.preview.append(el('p', 'query-shadow-error', item.error || 'This query could not finish.'));
      } else if (item.surfaceId && item.columns.length) {
        if (item.rows.length === 1 && item.columns.length === 1) {
          const single = el('div', 'query-shadow-value');
          single.append(el('b', '', item.rows[0][0]), el('small', '', item.columns[0])); card.preview.append(single);
        } else {
          const table = el('table'), head = el('thead'), headRow = el('tr'), body = el('tbody');
          for (const name of item.columns) headRow.append(el('th', '', name)); head.append(headRow);
          for (const row of item.rows) {
            const tr = el('tr'); for (const value of row) { const td = el('td', '', value); td.title = value; tr.append(td); } body.append(tr);
          }
          table.append(head, body); card.preview.append(table);
          if (!item.rows.length) card.preview.append(el('p', 'query-shadow-empty', 'No rows returned'));
        }
      } else if (item.surfaceId) card.preview.append(el('p', 'query-shadow-empty', 'Result saved · open in Data to inspect'));
    }
    card.preview.hidden = !item.surfaceId && item.status !== 'failed';
    card.waiting.hidden = !card.preview.hidden;
    card.detail.textContent = ended ? 'Work finished' : view.delayed ? 'Waiting for activity updates'
      : running ? 'Reading local data' : 'Query finished · awaiting its receipt';
    card.orb.hidden = !running;
    const animate = running && !suppressed && !document.hidden;
    if (animate && !card.animating) window.CalliopeThinkingOrbs?.mount(card.orb, 'working', 42);
    if (!animate && card.animating) window.CalliopeThinkingOrbs?.unmount(card.orb);
    card.animating = animate;
  }
  function render() {
    const root = host(); if (!root) return;
    if (!root._ready) {
      const header = el('header', 'query-work-head');
      header.append(el('span', '', 'Exploring data'), el('small', 'query-work-count'));
      root.append(header, el('div', 'query-work-grid'));
      root.addEventListener('pointerenter', () => { hovered = true; });
      root.addEventListener('pointerleave', () => { hovered = false; render(); });
      root.addEventListener('focusin', () => { focused = true; });
      root.addEventListener('focusout', () => { queueMicrotask(() => { focused = root.contains(document.activeElement); render(); }); });
      root._ready = true;
      const resize = new ResizeObserver(() => render());
      resize.observe(root.parentElement);
    }
    if (suppressed) {
      clearFlights();
      root.hidden = true; collapse?.cancel(); collapse = null;
      for (const card of cards.values()) { window.CalliopeThinkingOrbs?.unmount(card.orb); card.animating = false; }
      clearTimeout(timer); notifyEvidence(); options.onChange?.(); return;
    }
    if (store.items.size) root.hidden = false;
    const before = new Map([...cards.values()].map(card => [card.node, {
      left: card.node.getBoundingClientRect().left,
      opacity: getComputedStyle(card.node).opacity,
    }]));
    const width = Math.max(1, root.clientWidth || root.parentElement.clientWidth - 40);
    const capacity = Math.max(1, Math.floor((width + 10) / 226));
    root.style.setProperty('--query-width', `${Math.min(260, (width - (capacity - 1) * 10) / capacity)}px`);
    const view = store.view(Date.now(), hovered || focused, capacity);
    const keep = new Set(view.items.map(item => item.key));
    const grid = root.querySelector('.query-work-grid');
    // Reuse the pending card for its receipt, rather than animating the same
    // query out and in again. Native IDs are already scoped by producing run.
    for (const item of view.items) if (item.surfaceId && item.call && !cards.has(item.key)) {
      const pendingKey = `call:${item.call}`, pending = cards.get(pendingKey);
      if (pending) {
        cards.delete(pendingKey); cards.set(item.key, pending);
        pending.node.dataset.queryShadow = item.key;
      }
    }
    const retiring = [...cards].filter(([key]) => !keep.has(key))
      .sort((a, b) => b[1].node.offsetLeft - a[1].node.offsetLeft);
    for (const [key, card] of retiring) { remove(card, !suppressed); cards.delete(key); }
    let cursor = grid.firstElementChild;
    for (const item of view.items) {
      let card = cards.get(item.key);
      if (!card) { card = create(item); cards.set(item.key, card); }
      update(card, item, view);
      if (card.node !== cursor) grid.insertBefore(card.node, cursor);
      cursor = card.node.nextElementSibling;
    }
    if (view.items.length && !suppressed) {
      collapse?.cancel(); collapse = null; root.hidden = false;
      for (const card of cards.values()) {
        const previous = before.get(card.node);
        const left = card.node.offsetLeft, width = card.node.offsetWidth;
        // Layout coordinates exclude the in-flight transform. Polls must not
        // repeatedly animate from an already translated "destination".
        const moved = left !== card.left || width !== card.width;
        card.left = left; card.width = width;
        if (!motion()) { card.motion?.cancel(); continue; }
        if (previous && !moved) continue;
        card.motion?.cancel();
        const dx = previous ? previous.left - card.node.getBoundingClientRect().left : -32;
        if (motion() && Math.abs(dx) > 1) {
          card.motion = card.node.animate([{transform: `translateX(${dx}px)`, opacity: previous?.opacity ?? 0},
            {transform: 'none', opacity: 1}], {duration: 340, easing: 'cubic-bezier(.2,.7,.2,1)'});
        }
      }
    } else if (suppressed || !motion()) {
      collapse?.cancel(); collapse = null; root.hidden = true;
    } else if (!root.hidden && !collapse) {
      collapse = root.animate([{height: `${root.offsetHeight}px`, opacity: 1}, {height: '0px', opacity: 0}],
        {duration: 340, delay: Math.max(140, flightsEnd - performance.now()), easing: 'ease-in-out', fill: 'forwards'});
      const current = collapse;
      current.finished.then(() => {
        if (collapse === current) { root.hidden = true; collapse = null; current.cancel(); options.onChange?.(); }
      }, () => {});
    }
    const count = root.querySelector('.query-work-count');
    notifyEvidence();
    count.textContent = `${view.running ? `${view.running} running · ` : ''}${view.total} recent${hovered || focused ? ' · paused' : ''}`;
    options.onChange?.();
    clearTimeout(timer);
    if (cards.size || store.items.size) timer = setTimeout(render, 1000);
  }
  window.CalliopeQueryWork = {
    sync(snapshot, nextOptions = {}) {
      options = nextOptions; suppressed = Boolean(snapshot.suppressed);
      if (snapshot.session !== store.session) {
        clearFlights();
        for (const card of cards.values()) remove(card);
        cards.clear(); hovered = false; focused = false;
        collapse?.cancel(); collapse = null;
        host()?.querySelectorAll('.is-leaving').forEach(node => node.remove());
        if (host()) host().hidden = true;
      }
      store.sync(snapshot); render();
    },
    hasItems: () => cards.size > 0 && !suppressed,
    visibleReceiptIds,
  };
  document.addEventListener('visibilitychange', () => { if (document.hidden) clearFlights(); });
  matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', event => { if (event.matches) clearFlights(); });
})();
