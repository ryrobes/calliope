/* Arrangements: a user-composed grid of this chat's Stage pieces.

   Cells sit on a 60px lattice, never overlap, and render exactly as placed.
   Blocks are content only: the title bar and handles appear on hover and
   selection. Every change autosaves as a revision of an `arrangement` surface;
   placed cards keep living on the normal Stage. The module owns the DOM of
   #stage-arrange; the Stage renderer only moves that host into a slot. */
(() => {
  'use strict';
  const UNIT = 60, MAX_COLUMNS = 64, MAX_ROWS = 400, SLACK_ROWS = 6, STORAGE = 'rvbbit-calliope-arrangement-v1';
  const MEMBER_KINDS = new Set(['artifact', 'chart', 'query', 'board', 'metric', 'map', 'answer', 'document', 'image',
    'find', 'cube', 'value_system', 'selection', 'evidence', 'email']);
  // Blocks can shrink to two cells each way; a stat or a note to one row.
  const MIN = {stat: [2, 1], artifact: [2, 2], board: [2, 2], map: [2, 2], chart: [2, 2], query: [2, 2], metric: [2, 2], answer: [2, 2],
    document: [2, 2], image: [2, 2], find: [2, 2], cube: [2, 2], value_system: [2, 2], selection: [2, 2], evidence: [2, 2],
    email: [2, 2], text: [2, 1], value: [2, 1], grid: [2, 2]};
  const SIZE = {stat: [3, 2], artifact: [12, 8], board: [12, 8], map: [8, 6], chart: [8, 5], query: [10, 5], metric: [4, 2], answer: [6, 4],
    document: [6, 5], image: [6, 5], find: [8, 4], cube: [10, 6], value_system: [8, 5], selection: [6, 3], evidence: [6, 3],
    email: [6, 4], text: [6, 1], value: [3, 2], grid: [8, 5]};
  const GLYPH = {artifact: '▣', chart: '◫', query: '≣', board: '▦', metric: '№', map: '◎', answer: '¶', document: '▤',
    image: '▧', find: '⌕', cube: '⬚', value_system: '⚖', selection: '◧', evidence: '◈', email: '✉', text: 'T', value: '№', grid: '▦'};
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const clean = (v, limit = 180) => String(v ?? '').replace(/\s+/g, ' ').trim().slice(0, limit);
  const time = s => Date.parse(s?.created_at) || 0;
  const newer = (a, b) => time(b) - time(a) || Number(b.ordinal || 0) - Number(a.ordinal || 0) || String(b.id).localeCompare(String(a.id));
  // One line on what changed between two revisions' payloads, for the
  // history slider: "added Sales, moved 2, resized 1".
  function describeChange(before, after) {
    const name = c => c.title || c.member_title || c.label || c.heading || (c.kind === 'text' ? 'a note' : c.kind === 'stat' ? 'a stat' : c.member_kind || 'a block');
    const cellsOf = p => Array.isArray(p?.cells) ? p.cells : [];
    if (!before) { const n = cellsOf(after).length; return n ? `created with ${n} block${n === 1 ? '' : 's'}` : 'created empty'; }
    const prev = new Map(cellsOf(before).map(c => [c.id, c])), next = new Map(cellsOf(after).map(c => [c.id, c]));
    const parts = [], added = [], removed = [];
    let moved = 0, resized = 0, retitled = 0, edited = 0, repinned = 0, reviewed = 0;
    for (const [id, c] of next) {
      const p = prev.get(id);
      if (!p) { added.push(name(c)); continue; }
      if (p.x !== c.x || p.y !== c.y) moved++;
      if (p.w !== c.w || p.h !== c.h) resized++;
      if ((p.title || '') !== (c.title || '') || (p.label || '') !== (c.label || '')) retitled++;
      if ((p.heading || '') !== (c.heading || '') || (p.body || '') !== (c.body || '') || (p.format || '') !== (c.format || '') || (p.column || '') !== (c.column || '')
        || JSON.stringify(p.params || {}) !== JSON.stringify(c.params || {})) edited++;
      if ((p.version || 'latest') !== (c.version || 'latest')) repinned++;
      if ((p.view || '') !== (c.view || '')) reviewed++;
    }
    for (const [id, c] of prev) if (!next.has(id)) removed.push(name(c));
    const few = list => list.length <= 2 ? list.join(' and ') : `${list.length} blocks`;
    if (added.length) parts.push(`added ${few(added)}`);
    if (removed.length) parts.push(`removed ${few(removed)}`);
    if (moved) parts.push(`moved ${moved}`);
    if (resized) parts.push(`resized ${resized}`);
    if (retitled) parts.push(`renamed ${retitled}`);
    if (edited) parts.push(`edited ${edited}`);
    if (repinned) parts.push(`repinned ${repinned}`);
    if (reviewed) parts.push(`switched ${reviewed} view${reviewed === 1 ? '' : 's'}`);
    if ((before.title || '') !== (after.title || '')) parts.push(`titled “${String(after.title || '').slice(0, 40)}”`);
    if ((before.width || 0) !== (after.width || 0)) parts.push(`width ${before.width}→${after.width}`);
    return parts.join(', ') || 'no layout change';
  }
  const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  let cellCounter = 0;
  const cellId = () => `c${Date.now().toString(36)}${(cellCounter++).toString(36)}`;

  function create({host, control, scroll, api, renderSurface, renderValue, hydrate, onChange, onSaved, canEdit: canEditOption, toast, focusSurface, viewer, subjects, values: valuesApi = null, page = '', onTimeTravel, railHost = null}) {
    // `railHost`: an element outside the Stage (the chat column's Library tab)
    // that holds the library, so the sheet keeps the whole Stage to itself.
    // `page`: the lineage this host shows as a standalone page (always on,
    // read-only, no rail or tools); empty for the editor inside the Stage.
    if (page) host.dataset.page = '1';
    // History: every autosave is a revision surface in the lineage. With a
    // cutoff set, the sheet shows the revision current at that moment, read
    // only, and unpinned members resolve as they stood then.
    let cutoff = null, viewing = null; // viewing: {surface, payload, at}
    const canEdit = () => !viewing && Boolean(canEditOption?.());
    let session = '', surfaces = [], turns = [], library = [];
    let arrangements = new Map(); // lineage → latest arrangement surface
    let active = false, currentLineage = null;
    let draft = null, base = null; // draft: working payload; base: saved surface it derives from
    let past = [], future = [], dirty = false, saveTimer = 0, saving = null, saveError = '';
    let mark = ''; // the draft as last committed, so undo restores what came before a change
    let selected = new Set(), drag = null, railOpen = true, railQuery = '', railKinds = new Set(), unplacedOnly = false;
    let menuOpen = false, wanted = null; // a remembered or linked choice not yet loadable
    // Subjects: the library's grouping, decided server-side (Jev or rules) and
    // corrected by people. Keyed by surface id and by lineage, since a lineage
    // shares one subject across its versions.
    let groupBy = 'subject', subjectMap = new Map(), subjectList = [], subjectSpend = {decisions: 0, cost: 0}, subjectPending = 0;
    let subjectMode = '', subjectBusy = null, subjectError = '', subjectSession = '', collapsed = new Set(), railDrop = null;
    let popover = null; // the open revision or stat picker, one at a time
    // Values: numbers the team's dashboards already show. The library lists
    // them across every artifact the viewer may open (a persistent section,
    // searched with the same box); a placed one is read live for whoever looks.
    let valueCatalog = {items: [], total: 0, catalog: 0, q: null, at: 0, loading: null, error: ''}, valueTimer = 0;
    const valueData = new Map(); // block key → {status, value, slices, …, at}
    // The section is grouped by source dashboard: closed until opened, and all
    // open while the filter box narrows them (a closed one stays closed).
    const dashOpen = new Set(), dashShut = new Set();
    // The "value" and "grid" chips narrow the section to one shape, like the
    // filter box narrows it by words; either one opens the matching dashboards.
    const valuesFiltering = () => Boolean(railQuery.trim()) || railKinds.has('value') || railKinds.has('grid');
    const VALUE_FRESH = 5 * 60 * 1000, CATALOG_FRESH = 2 * 60 * 1000;
    const isGrid = c => c?.kind === 'value' && c.shape === 'grid';
    const valueKey = c => `${c.shape === 'grid' ? 'grid' : 'value'}|${c.artifact}|${c.object}|${JSON.stringify(c.params || {})}`;
    const valueOf = cell => valueData.get(valueKey(cell)) || null;
    function ensureValues() {
      if (!valuesApi?.read || !active) return;
      const want = new Map();
      for (const c of cells()) {
        if (c.kind !== 'value') continue;
        const key = valueKey(c), have = valueData.get(key);
        if (have && (have.status === 'loading' || have.refreshing || Date.now() - (have.at || 0) < VALUE_FRESH)) continue;
        if (want.size < 40) want.set(key, c);
      }
      if (!want.size) return;
      for (const key of want.keys()) { const have = valueData.get(key); if (have) have.refreshing = true; else valueData.set(key, {status: 'loading', at: Date.now()}); }
      const asked = [...want].map(([key, c]) => ({key, shape: c.shape === 'grid' ? 'grid' : 'value', artifact: c.artifact, object: c.object, from_version: c.from_version, source_label: c.source_label, measure: c.measure, params: c.params || {}}));
      Promise.resolve(valuesApi.read(asked))
        .then(result => { for (const key of want.keys()) valueData.set(key, {...(result?.values?.[key] || {status: 'error', message: 'No answer for this value'}), at: Date.now()}); })
        .catch(err => { for (const key of want.keys()) valueData.set(key, {status: 'error', message: err?.message || 'This value could not be read', at: Date.now()}); })
        .finally(() => { if (active) paintSheet(); });
    }
    function loadValues() {
      if (!valuesApi?.search || page || !active) return;
      const q = railQuery.trim();
      if (valueCatalog.loading === q) return;
      if (valueCatalog.q === q && Date.now() - valueCatalog.at < CATALOG_FRESH) return;
      clearTimeout(valueTimer);
      valueCatalog.loading = q;
      valueTimer = setTimeout(async () => {
        try {
          const r = await valuesApi.search(q);
          if (railQuery.trim() === q) valueCatalog = {items: Array.isArray(r?.values) ? r.values : [], total: Number(r?.total) || 0, catalog: Number(r?.catalog) || 0,
            kinds: r?.kinds && typeof r.kinds === 'object' ? r.kinds : {value: Number(r?.catalog) || 0, grid: 0}, q, at: Date.now(), loading: null, error: ''};
        } catch (err) {
          if (railQuery.trim() === q) valueCatalog = {...valueCatalog, q, at: Date.now(), loading: null, error: err?.message || 'Dashboard values are unavailable'};
        } finally {
          if (valueCatalog.loading === q) valueCatalog.loading = null;
          paintRail();
        }
      }, valueCatalog.q === null ? 0 : 220);
    }
    const FORMATS = ['auto', 'number', 'compact', 'percent', 'currency', 'text'];
    const FORMAT_GLYPH = {auto: '#', number: '1,0', compact: '1k', percent: '%', currency: '$', text: 'Aa'};
    host.className = 'stage-arrange';
    host.innerHTML = `${railHost ? '' : '<aside class="arrange-rail" aria-label="Stage library"></aside>'}<div class="arrange-work"><div class="arrange-toolbar"></div><div class="arrange-scroll"><div class="arrange-sheet" tabindex="0" aria-label="Arrangement"></div></div></div>`;
    const preview = document.createElement('aside'); preview.className = 'arrange-preview'; preview.hidden = true; preview.setAttribute('aria-live', 'polite'); host.appendChild(preview);
    if (railHost) railHost.innerHTML = '<aside class="arrange-rail arrange-rail-shelf" aria-label="Stage library"></aside>';
    const rail = (railHost || host).querySelector('.arrange-rail'), toolbar = host.querySelector('.arrange-toolbar');
    // A press that may become a drag keeps the whole page from selecting text
    // until the pointer lifts, wherever it travels (chat, rail, header).
    let releaseSelection = null;
    function guardSelection() {
      if (releaseSelection) return;
      const block = e => e.preventDefault();
      releaseSelection = () => {
        document.removeEventListener('selectstart', block, true);
        window.removeEventListener('pointerup', releaseSelection, true);
        window.removeEventListener('pointercancel', releaseSelection, true);
        document.documentElement.classList.remove('arrange-no-select');
        releaseSelection = null;
      };
      document.addEventListener('selectstart', block, true);
      window.addEventListener('pointerup', releaseSelection, true);
      window.addEventListener('pointercancel', releaseSelection, true);
      document.documentElement.classList.add('arrange-no-select');
      window.getSelection?.()?.removeAllRanges();
    }
    const setDragging = on => { host.classList.toggle('is-dragging', on); rail.classList.toggle('is-dragging', on); if (on) { guardSelection(); previewSurface(null); } };

    // ── preview: the hovered library piece, rendered where the Stage shows
    // its questions (upper right, clear of the chat column's tabs) ─────────
    let previewId = null, previewTimer = 0;
    function captureImage(surface) {
      if (surface.kind === 'image') return surface.payload?.image_url || null;
      const capture = surfaces.find(s => s.kind === 'image' && s.payload?.source_surface_id === surface.id && s.payload?.image_url)
        || (surface.artifact_slug && surface.artifact_version != null
          ? surfaces.find(s => s.kind === 'image' && s.artifact_slug === surface.artifact_slug && s.artifact_version === surface.artifact_version && s.payload?.image_url) : null);
      return capture?.payload?.image_url || null;
    }
    function previewSurface(id, {now = false} = {}) {
      clearTimeout(previewTimer);
      if (!id) {
        previewId = null;
        if (!preview.hidden) { preview.hidden = true; preview.innerHTML = ''; }
        return;
      }
      if (id === previewId && !preview.hidden) return;
      previewTimer = setTimeout(() => {
        const s = surfaces.find(x => x.id === id); if (!s || !active) return;
        previewId = id;
        const [w, h] = SIZE[s.kind] || [6, 4];
        const turn = turns.find(t => String(t.id) === String(s.turn_id));
        const image = s.kind === 'artifact' ? captureImage(s) : null;
        const body = image ? `<img class="arrange-preview-capture" src="${esc(image)}" alt="">` : renderSurface(s, {view: 'table'});
        preview.innerHTML = `<header><b title="${esc(s.title || s.kind)}">${esc(s.title || s.kind)}</b><span>${esc(s.kind)}${s.artifact_version ? ` · v${esc(s.artifact_version)}` : ''}${s.created_at ? ` · ${esc(when(s.created_at))}` : ''}${turn?.user_message ? ` · ${esc(clean(turn.user_message, 48))}` : ''}</span></header>
          <div class="arrange-cell-body arrange-preview-body" style="aspect-ratio:${w} / ${h}">${body}</div>`;
        // The same top edge the Stage's questions use: the Stage scroller's.
        preview.style.top = `${Math.max(12, Math.ceil(((scroll || sheetScroll).getBoundingClientRect?.().top ?? 0) + 12))}px`;
        preview.hidden = false;
        // Outside the Stage's lazy-frame observer, an artifact frame is woken by hand.
        for (const frame of preview.querySelectorAll('iframe[data-artifact-src]')) if (!frame.getAttribute('src')) frame.src = frame.dataset.artifactSrc;
        hydrate?.(preview);
      }, now ? 0 : 180);
    }
    // A library value or grid previews like a Stage piece: the live number or
    // the first rows of the grid, read for this viewer, above what is known
    // about it (measure, description, columns, slices).
    const PREVIEW_ROWS = 12;
    const previewCell = v => ({kind: 'value', shape: v.shape === 'grid' ? 'grid' : undefined, artifact: v.artifact, object: v.object,
      from_version: v.version || undefined, source_label: v.label, measure: v.measure || undefined, label: v.label, format: 'auto'});
    function paintValuePreview(v) {
      const grid = v.shape === 'grid', cell = previewCell(v), data = valueOf(cell);
      const list = items => items.map(sl => `<li><span>${esc(sl.label || sl.name)}</span><b>${esc(Array.isArray(sl.value) ? sl.value.join(', ') : sl.value ?? '')}</b></li>`).join('');
      const slices = list(data?.slices || v.slices || []);
      const columns = grid ? (v.columns || []).map(c => `<li><span>${esc(c.label || c.name)}</span><b>${esc(c.role === 'measure' ? (c.measure || 'measure') : 'breakdown')}</b></li>`).join('') : '';
      const headline = grid ? [(v.measures || []).join(', '), v.grain].filter(Boolean).join(' · ') : [v.measure, v.unit].filter(Boolean).join(' · ');
      const total = Number(data?.row_count) || (data?.rows || []).length;
      const shown = grid && data?.status === 'ready' ? {...data, rows: (data.rows || []).slice(0, PREVIEW_ROWS), truncated: false} : data;
      const live = renderValue ? renderValue(cell, shown) : '';
      const rowsNote = grid && data?.status === 'ready' ? `<p class="arrange-preview-rows">${total > PREVIEW_ROWS ? `First ${PREVIEW_ROWS} of ${total}${data.truncated ? '+' : ''} rows` : `${total} row${total === 1 ? '' : 's'}`}</p>` : '';
      preview.innerHTML = `<header><b title="${esc(v.label)}">${esc(v.label)}</b><span>${grid ? 'grid' : 'value'} · ${esc(v.artifact_name)}${v.owner ? ` · ${esc(v.owner)}` : ''}</span></header>
        <div class="arrange-preview-live ${grid ? 'is-grid' : 'is-value'}">${live}</div>${rowsNote}
        <div class="arrange-value-preview">${headline ? `<p class="arrange-value-measure">${esc(headline)}</p>` : ''}
          ${v.description ? `<p>${esc(v.description)}</p>` : ''}${columns ? `<ul>${columns}</ul>` : ''}${slices ? `<ul>${slices}</ul>` : ''}</div>`;
    }
    function readPreviewValue(v) {
      if (!valuesApi?.read) return;
      const cell = previewCell(v), key = valueKey(cell), have = valueData.get(key);
      if (have && (have.status === 'loading' || (have.status === 'ready' && Date.now() - (have.at || 0) < VALUE_FRESH))) return;
      valueData.set(key, {status: 'loading', at: Date.now()});
      Promise.resolve(valuesApi.read([{key, shape: cell.shape === 'grid' ? 'grid' : 'value', artifact: cell.artifact, object: cell.object,
        from_version: cell.from_version, source_label: cell.source_label, measure: cell.measure, params: {}}]))
        .then(result => valueData.set(key, {...(result?.values?.[key] || {status: 'error', message: 'No answer for this value'}), at: Date.now()}))
        .catch(err => valueData.set(key, {status: 'error', message: err?.message || 'This could not be read', at: Date.now()}))
        .finally(() => { if (previewId === `value:${v.key}` && !preview.hidden) paintValuePreview(v); });
    }
    function previewValue(key, {now = false} = {}) {
      clearTimeout(previewTimer);
      if (`value:${key}` === previewId && !preview.hidden) return;
      previewTimer = setTimeout(() => {
        const v = valueCatalog.items.find(x => x.key === key); if (!v || !active) return;
        previewId = `value:${key}`;
        readPreviewValue(v);
        paintValuePreview(v);
        preview.style.top = `${Math.max(12, Math.ceil(((scroll || sheetScroll).getBoundingClientRect?.().top ?? 0) + 12))}px`;
        preview.hidden = false;
      }, now ? 0 : 180);
    }
    rail.addEventListener('pointerover', e => {
      const tile = e.target.closest?.('[data-library-surface], [data-library-value]');
      if (!tile || drag) return;
      if (tile.dataset.libraryValue) previewValue(tile.dataset.libraryValue); else previewSurface(tile.dataset.librarySurface);
    });
    rail.addEventListener('pointerleave', () => previewSurface(null));
    rail.addEventListener('focusin', e => { const tile = e.target.closest?.('[data-library-surface]'); if (tile) previewSurface(tile.dataset.librarySurface); });
    rail.addEventListener('pointerout', e => { if (e.target.closest?.('[data-library-value]') && !e.relatedTarget?.closest?.('[data-library-value]')) previewSurface(null); });
    preview.addEventListener('pointerenter', () => clearTimeout(previewTimer));
    preview.addEventListener('pointerleave', () => previewSurface(null));
    const sheet = host.querySelector('.arrange-sheet'), sheetScroll = host.querySelector('.arrange-scroll');

    // ── persistence of the viewer's choice ────────────────────────────────
    const storageKey = () => `${STORAGE}:${session}`;
    function remember() {
      if (page) return; // a page remembers nothing and never rewrites its address
      try { localStorage.setItem(storageKey(), JSON.stringify({active, lineage: currentLineage, rail: railOpen, group: groupBy})); } catch {}
      const url = new URL(location.href);
      if (active && currentLineage) url.searchParams.set('arrangement', currentLineage.replace(/^arrangement:/, ''));
      else url.searchParams.delete('arrangement');
      if (url.href !== location.href) history.replaceState(history.state, '', url);
    }
    function recall() {
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(storageKey()) || 'null'); } catch {}
      const fromUrl = new URLSearchParams(location.search).get('arrangement');
      if (fromUrl) return {active: true, lineage: `arrangement:${fromUrl}`, rail: saved?.rail ?? true, group: saved?.group, fromUrl: true};
      return saved || {active: false, lineage: null, rail: true};
    }

    // ── subjects ──────────────────────────────────────────────────────────
    function subjectFor(s) {
      return subjectMap.get(s.id) || s.presentation?.subject || (s.lineage_key && subjectMap.get(`lineage:${s.lineage_key}`)) || null;
    }
    function absorbSubjects(result) {
      if (!result || typeof result !== 'object') return;
      for (const [id, subject] of Object.entries(result.surfaces || {})) {
        if (!subject?.label) continue;
        subjectMap.set(id, subject);
        const s = surfaces.find(x => x.id === id); if (s?.lineage_key) subjectMap.set(`lineage:${s.lineage_key}`, subject);
      }
      if (Array.isArray(result.subjects)) subjectList = result.subjects;
      if (result.spend) subjectSpend = result.spend;
      if (result.pending != null) subjectPending = Number(result.pending) || 0;
      if (result.mode) subjectMode = result.mode;
    }
    function seedSubjects() {
      for (const s of surfaces) {
        const subject = s.presentation?.subject;
        if (subject?.label && !subjectMap.has(s.id)) { subjectMap.set(s.id, subject); if (s.lineage_key && !subjectMap.has(`lineage:${s.lineage_key}`)) subjectMap.set(`lineage:${s.lineage_key}`, subject); }
      }
    }
    function resetSubjects() {
      subjectMap = new Map(); subjectList = []; subjectSpend = {decisions: 0, cost: 0}; subjectPending = 0;
      subjectMode = ''; subjectError = ''; subjectSession = ''; collapsed = new Set();
    }
    const lackingSubjects = () => library.filter(s => MEMBER_KINDS.has(s.kind) && !subjectFor(s));
    // The rail asks the server for what it lacks: the chat's current state
    // once per session, then one assign call per batch until nothing waits.
    // Spend therefore happens only in chats where someone opens the library.
    function syncSubjects() {
      if (!subjects || !active || !railOpen || !session || subjectBusy || groupBy !== 'subject') return;
      const first = subjectSession !== session;
      if (!first && (!lackingSubjects().length || !canEdit?.() || subjectMode === 'off')) return;
      const mine = session; subjectSession = session; subjectError = '';
      subjectBusy = (async () => {
        try {
          if (first) absorbSubjects(await subjects.load(mine));
          for (let round = 0; round < 40 && mine === session && lackingSubjects().length && canEdit?.() && subjectMode !== 'off'; round++) {
            paintRail();
            const result = await subjects.assign(mine);
            if (mine !== session) return;
            absorbSubjects(result);
            if (!result?.assigned) break;
          }
        } catch (err) {
          subjectError = err?.message || 'Grouping unavailable';
        } finally {
          subjectBusy = null;
          if (mine === session) paintRail();
        }
      })();
    }
    function correctSubjects(body) {
      if (!subjects || !session) return Promise.resolve();
      return Promise.resolve(subjects.correct(session, body))
        .then(result => { absorbSubjects(result); paintRail(); })
        .catch(err => toast?.(err?.message || 'The subject did not change'));
    }
    function setGroupBy(value) {
      groupBy = value === 'turn' ? 'turn' : 'subject';
      remember(); paintRail(); syncSubjects();
    }
    function renameSubject(label) {
      const to = clean(prompt('Rename this subject', label), 60);
      if (to && to !== label) correctSubjects({rename: {from: label, to}});
    }
    function setRailDrop(target) {
      if (railDrop === target) return;
      railDrop?.classList.remove('is-drop'); railDrop = target; railDrop?.classList.add('is-drop');
    }
    function railDropTarget(ev) {
      if (groupBy !== 'subject' || !subjects) return null;
      const inside = (r) => ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
      if (!inside(rail.getBoundingClientRect())) return null;
      return [...rail.querySelectorAll('[data-subject], [data-new-subject]')].find(g => inside(g.getBoundingClientRect())) || null;
    }

    // ── arrangements and the draft ────────────────────────────────────────
    function collect() {
      const latest = new Map();
      for (const s of surfaces) if (s.kind === 'arrangement' && s.lineage_key) {
        const have = latest.get(s.lineage_key);
        if (!have || newer(have, s) > 0) latest.set(s.lineage_key, s);
      }
      for (const [key, s] of latest) if (s.payload?.deleted) latest.delete(key);
      arrangements = latest;
    }
    function loadDraft(surface) {
      base = surface || null;
      const payload = surface?.payload || {};
      draft = surface ? {
        title: payload.title || surface.title || 'Arrangement',
        columns: Number(payload.width) || defaultColumns(),
        cells: (payload.cells || []).map(c => ({...c})),
      } : null;
      past = []; future = []; dirty = false; saveError = ''; selected.clear();
      mark = draft ? snapshot() : '';
    }
    function defaultColumns() {
      const width = sheetScroll.clientWidth || scroll?.clientWidth || 1200;
      return clamp(Math.floor((width - 24) / UNIT), 8, MAX_COLUMNS);
    }
    function syncDraftToSaved() {
      // A newer revision of the current arrangement arrived (another editor,
      // or our own save echoed back). Adopt it unless we hold unsaved edits.
      const saved = currentLineage ? arrangements.get(currentLineage) : null;
      if (!saved) { if (draft && currentLineage) { currentLineage = null; draft = null; base = null; } return; }
      if (!base || saved.id === base.id) { if (!base) { if (dirty || saving) base = saved; else loadDraft(saved); } return; }
      // A refresh fetched before our save committed lists an older latest;
      // keep what we saved rather than stepping back to its parent.
      if (String(base.parent_surface_id) === String(saved.id) || time(saved) < time(base)) return;
      if (!dirty) { loadDraft(saved); return; }
      if (String(saved.parent_surface_id) === String(base.id) && saved.source?.actor === viewerEmail()) { base = saved; return; }
      saveError = 'This arrangement was revised elsewhere. Reload to keep editing.';
    }
    const viewerEmail = () => viewer?.() || null;

    // ── layout helpers ────────────────────────────────────────────────────
    // What the sheet shows: the draft, or the revision being viewed.
    const view = () => viewing ? {title: viewing.payload.title, columns: viewing.payload.width || 12, cells: viewing.payload.cells || []} : draft;
    const cells = () => view()?.cells || [];
    const rowsNeeded = () => Math.max(...cells().map(c => c.y + c.h), 0);

    // ── history ───────────────────────────────────────────────────────────
    const at = s => Date.parse(s?.created_at) || 0;
    function revisions(lineage = currentLineage) {
      if (!lineage) return [];
      return surfaces.filter(s => s.kind === 'arrangement' && s.lineage_key === lineage && !s.payload?.deleted).sort((a, b) => at(a) - at(b) || Number(a.ordinal || 0) - Number(b.ordinal || 0));
    }
    function actorOf(s) {
      const actor = s?.source?.actor || '';
      if (s?.source?.origin === 'publish_arrangement') return 'Callie';
      if (actor && actor === viewer?.()) return 'You';
      return actor ? actor.split('@')[0] : 'Someone';
    }
    // Consecutive saves by the same person read as one edit while they come
    // quickly, but a long session still gets a stop every few minutes.
    const GROUP_GAP = 90 * 1000, GROUP_SPAN = 5 * 60 * 1000, GROUP_SAVES = 8;
    function historyGroups(revs = revisions()) {
      const groups = [];
      for (const r of revs) {
        const last = groups[groups.length - 1];
        const joins = last && last.actor === actorOf(r) && at(r) - at(last.last) <= GROUP_GAP
          && at(r) - at(last.first) <= GROUP_SPAN && last.members.length < GROUP_SAVES;
        if (joins) { last.last = r; last.members.push(r); }
        else groups.push({actor: actorOf(r), first: r, last: r, members: [r], before: last ? last.last : null});
      }
      return groups;
    }
    function describeEvent(group) {
      if (!group) return '';
      const edits = group.members.length;
      return `${group.actor} · ${when(group.last.created_at)} · ${edits > 1 ? `${edits} saves · ` : ''}${describeChange(group.before?.payload, group.last.payload)}`;
    }
    function setCutoff(ms) {
      const next = ms == null ? null : Number(ms);
      if (next === cutoff) return;
      cutoff = next;
      const revs = revisions();
      const latest = revs[revs.length - 1];
      let target = null;
      if (cutoff !== null && revs.length) target = [...revs].reverse().find(r => at(r) <= cutoff) || revs[0];
      if (target && latest && target.id === latest.id) target = null; // the newest revision is "now"
      viewing = target ? {surface: target, payload: target.payload || {}, at: at(target)} : null;
      if (viewing) { selected.clear(); closePopover(); }
      if (active) paint();
    }
    function leaveHistory() {
      if (cutoff === null && !viewing) return;
      cutoff = null; viewing = null;
      onTimeTravel?.(null);
    }
    function restoreRevision() {
      if (!viewing || !draft || !canEditOption?.()) return;
      const payload = viewing.payload;
      leaveHistory();
      draft.title = payload.title || draft.title; draft.columns = payload.width || draft.columns;
      draft.cells = (payload.cells || []).map(c => ({...c}));
      selected.clear(); commit('restore');
    }
    function free(rect, ignore) {
      return !cells().some(c => c.id !== ignore && overlaps(rect, c));
    }
    // The free spot closest to where the pointer let go, by distance rather
    // than ring order, so a short push past a neighbour lands beside it and
    // not back where it started. Widens its search until something fits.
    function nearestFree(rect, ignore) {
      const cols = draft.columns;
      const fits = r => r.x >= 0 && r.y >= 0 && r.x + r.w <= cols && r.y + r.h <= MAX_ROWS && free(r, ignore);
      if (fits(rect)) return rect;
      for (let reach = 4; reach <= MAX_ROWS; reach *= 2) {
        let best = null, bestScore = Infinity;
        for (let y = Math.max(0, rect.y - reach); y <= Math.min(MAX_ROWS - rect.h, rect.y + reach); y++) {
          for (let x = 0; x + rect.w <= cols; x++) {
            const score = (x - rect.x) ** 2 + (y - rect.y) ** 2 * 1.01;
            if (score >= bestScore) continue;
            const r = {...rect, x, y};
            if (fits(r)) { best = r; bestScore = score; }
          }
        }
        if (best) return best;
      }
      return null;
    }
    function firstFree(w, h) {
      const cols = draft.columns;
      for (let y = 0; y < MAX_ROWS; y++) for (let x = 0; x + w <= cols; x++) {
        const r = {x, y, w, h};
        if (free(r)) return r;
      }
      return null;
    }
    function minFor(cell) {
      const kind = isGrid(cell) ? 'grid' : cell.kind === 'text' || cell.kind === 'stat' || cell.kind === 'value' ? cell.kind : (resolveCell(cell)?.kind || cell.member_kind || 'answer');
      return MIN[kind] || [1, 1];
    }
    function resolveCell(cell) {
      if (cell.kind === 'text' || cell.kind === 'value') return null;
      if (cell.version && cell.version !== 'latest') return surfaces.find(s => s.id === cell.version) || null;
      const lineage = cell.lineage_key || surfaces.find(s => s.id === cell.ref)?.lineage_key;
      let family = lineage ? surfaces.filter(s => s.lineage_key === lineage && s.kind !== 'arrangement') : [];
      // Looking back, an unpinned block shows the member as it stood then.
      if (viewing && family.length) { const then = family.filter(s => at(s) <= viewing.at); if (then.length) family = then; }
      if (family.length) return family.sort(newer)[0];
      return surfaces.find(s => s.id === cell.ref) || null;
    }

    // ── editing ───────────────────────────────────────────────────────────
    function snapshot() { return JSON.stringify({title: draft.title, columns: draft.columns, cells: draft.cells}); }
    function commit(label) {
      const now = snapshot();
      if (now !== mark) { past.push(mark); if (past.length > 80) past.shift(); future = []; }
      mark = now; dirty = true; saveError = '';
      scheduleSave(); paint(); onChange?.(label);
    }
    function restore(json) {
      const data = JSON.parse(json);
      draft.title = data.title; draft.columns = data.columns; draft.cells = data.cells;
      selected = new Set([...selected].filter(id => draft.cells.some(c => c.id === id)));
      mark = snapshot(); dirty = true; scheduleSave(); paint();
    }
    function undo() { if (!past.length) return; future.push(snapshot()); restore(past.pop()); }
    function redo() { if (!future.length) return; past.push(snapshot()); restore(future.pop()); }
    function addSurface(surfaceId, at = null) {
      if (!canEdit?.()) return false;
      const surface = surfaces.find(s => s.id === surfaceId);
      if (!surface || !MEMBER_KINDS.has(surface.kind)) return false;
      if (!draft) { if (!startNew()) return false; }
      const [w0, h] = SIZE[surface.kind] || [6, 4];
      const w = Math.min(w0, draft.columns);
      const rect = at ? nearestFree({...at, w, h}) : firstFree(w, h);
      if (!rect) { toast?.('No room left in this arrangement'); return false; }
      const cell = {id: cellId(), kind: 'surface', ref: surface.id, lineage_key: surface.lineage_key || null, version: 'latest', ...rect};
      draft.cells.push(cell); selected = new Set([cell.id]);
      commit('add'); return true;
    }
    function addText(at = null) {
      if (!canEdit?.() || !draft) return;
      const [w0, h] = SIZE.text;
      const w = Math.min(w0, draft.columns);
      const rect = at ? nearestFree({...at, w, h}) : firstFree(w, h);
      if (!rect) return;
      const cell = {id: cellId(), kind: 'text', heading: 'Heading', body: '', ...rect};
      draft.cells.push(cell); selected = new Set([cell.id]); commit('text');
    }
    // A value block shows a number one of the team's dashboards already shows,
    // read live. It keeps a link to that dashboard and can move to another slice.
    function addValue(item, at = null) {
      if (!canEdit?.() || !item?.artifact || !item?.object) return false;
      if (!draft) { if (!startNew()) return false; }
      const grid = item.shape === 'grid';
      const [w0, h] = grid ? SIZE.grid : SIZE.value;
      const w = Math.min(w0, draft.columns);
      const rect = at ? nearestFree({...at, w, h}) : firstFree(w, h);
      if (!rect) { toast?.('No room left in this arrangement'); return false; }
      const cell = {id: cellId(), kind: 'value', shape: grid ? 'grid' : undefined, artifact: item.artifact, object: item.object, from_version: item.version || undefined,
        source_label: clean(item.label, 240), measure: item.measure || undefined, label: clean(item.label, 80), format: 'auto', ...rect};
      draft.cells.push(cell); selected = new Set([cell.id]);
      commit('value'); return true;
    }
    function setSlice(cellId, name, value) {
      const cell = draft?.cells.find(c => c.id === cellId); if (!cell || cell.kind !== 'value') return false;
      const params = {...(cell.params || {})};
      if (value === undefined) delete params[name]; else params[name] = value;
      cell.params = Object.keys(params).length ? params : undefined;
      commit('slice'); return true;
    }
    function openSlices(cell, cellEl) {
      const data = valueOf(cell), slices = data?.slices || [];
      const show = v => Array.isArray(v) ? (v.length ? v.join(', ') : '(none)') : v === '' || v == null ? '(none)' : String(v);
      openPopover(cellEl, `<h5>Taken over</h5>${slices.length ? slices.map(sl => `<button type="button" data-arrange-act="pick-slice" data-slice="${esc(sl.name)}" ${Array.isArray(sl.default) ? 'disabled' : ''} aria-checked="${Boolean(sl.changed)}"><b>${esc(sl.label || sl.name)}: ${esc(show(sl.value))}</b><small>${sl.changed ? `changed from ${esc(show(sl.default))}` : sl.fixed ? 'fixed by the dashboard' : 'the dashboard’s setting'}${sl.dimension ? ` · ${esc(sl.dimension)}` : ''}</small></button>`).join('') : '<p>This value has no slices to change.</p>'}`);
    }
    function pickSlice(cell, name) {
      const slice = (valueOf(cell)?.slices || []).find(sl => sl.name === name); if (!slice) return;
      const answer = prompt(`${slice.label || name} (blank returns to the dashboard’s own: ${slice.default ?? ''})`, slice.value ?? '');
      if (answer === null) return;
      const text = answer.trim();
      if (!text || text === String(slice.default ?? '')) { setSlice(cell.id, name, undefined); return; }
      const value = slice.type === 'integer' || slice.type === 'number' ? Number(text) : slice.type === 'boolean' ? /^(true|yes|y|1)$/i.test(text) : text;
      if ((slice.type === 'integer' || slice.type === 'number') && !Number.isFinite(value)) { toast?.('That slice takes a number'); return; }
      setSlice(cell.id, name, value);
    }
    // A stat block pulls one number out of a query result; it sits beside the
    // block it came from and follows the same revision rule.
    function addStat(fromCellId, column, at = null) {
      if (!canEdit?.() || !draft) return false;
      const from = draft.cells.find(c => c.id === fromCellId); const s = from && resolveCell(from);
      if (!s || s.kind !== 'query' || !column) return false;
      const [w0, h] = SIZE.stat;
      const w = Math.min(w0, draft.columns);
      const rect = nearestFree({...(at || {x: from.x + from.w, y: from.y}), w, h});
      if (!rect) { toast?.('No room left in this arrangement'); return false; }
      const cell = {id: cellId(), kind: 'stat', ref: from.ref, lineage_key: from.lineage_key || s.lineage_key || null, version: from.version || 'latest',
        column, row: 0, format: 'auto', label: column, ...rect};
      draft.cells.push(cell); selected = new Set([cell.id]); commit('stat'); return true;
    }
    function setVersion(cellId, version) {
      const cell = draft?.cells.find(c => c.id === cellId); if (!cell) return false;
      const ok = version === 'latest' || lineageVersions(cell).some(s => s.id === version);
      if (!ok) return false;
      cell.version = version; commit('pin'); return true;
    }
    function lineageVersions(cell) {
      const lineage = cell.lineage_key || surfaces.find(s => s.id === cell.ref)?.lineage_key;
      const family = lineage ? surfaces.filter(s => s.lineage_key === lineage && s.kind !== 'arrangement') : surfaces.filter(s => s.id === cell.ref);
      return family.sort(newer);
    }
    function statColumns(surface) {
      const payload = surface?.payload || {};
      const names = (Array.isArray(payload.columns) ? payload.columns : []).map(c => typeof c === 'string' ? c : c?.name).filter(Boolean);
      const first = Array.isArray(payload.rows) ? payload.rows[0] : null;
      const numeric = name => {
        if (!first) return true;
        const v = Array.isArray(first) ? first[names.indexOf(name)] : first[name];
        return v == null || v === '' || Number.isFinite(Number(String(v).replace(/[,$%\s]/g, '')));
      };
      return names.map(name => ({name, numeric: numeric(name)}));
    }
    // ── pickers: one small popover anchored to a block's hover bar ─────────
    function closePopover() { popover?.remove(); popover = null; }
    function openPopover(cellEl, markup) {
      closePopover();
      popover = document.createElement('div'); popover.className = 'arrange-popover'; popover.innerHTML = markup;
      cellEl.appendChild(popover);
    }
    function when(value) {
      const d = new Date(value); if (Number.isNaN(d.getTime())) return '';
      const sameDay = d.toDateString() === new Date().toDateString();
      return sameDay ? d.toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'}) : d.toLocaleDateString(undefined, {month: 'short', day: 'numeric'}) + ' ' + d.toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'});
    }
    function openVersions(cell, cellEl) {
      const family = lineageVersions(cell); if (family.length < 2) return;
      const current = cell.version && cell.version !== 'latest' ? cell.version : null;
      const ask = s => clean(turns.find(t => String(t.id) === String(s.turn_id))?.user_message || '', 60);
      openPopover(cellEl, `<h5>Which revision</h5>
        <button type="button" data-arrange-act="pick-version" data-version="latest" aria-checked="${!current}"><b>Follow the latest</b><small>changes as Callie revises it</small></button>
        ${family.map((s, i) => `<button type="button" data-arrange-act="pick-version" data-version="${esc(s.id)}" aria-checked="${current === s.id}"><b>${s.artifact_version ? `v${esc(s.artifact_version)}` : i === 0 ? 'Newest' : `${family.length - i} of ${family.length}`} · ${esc(when(s.created_at))}</b>${ask(s) ? `<small>${esc(ask(s))}</small>` : ''}</button>`).join('')}`);
    }
    function openStatPicker(cell, cellEl) {
      const s = resolveCell(cell); if (!s) return;
      const columns = statColumns(s);
      openPopover(cellEl, `<h5>Which column</h5>${columns.length ? columns.map(c => `<button type="button" data-arrange-act="pick-stat" data-column="${esc(c.name)}" ${c.numeric ? '' : 'data-text="1"'}><b>${esc(c.name)}</b>${c.numeric ? '' : '<small>text, first row</small>'}</button>`).join('') : '<p>This result has no columns to read.</p>'}`);
    }
    function removeSelected() {
      if (!selected.size) return;
      draft.cells = draft.cells.filter(c => !selected.has(c.id)); selected.clear(); commit('remove');
    }
    function duplicateSelected() {
      const copies = [];
      for (const id of selected) {
        const cell = draft.cells.find(c => c.id === id); if (!cell) continue;
        const rect = nearestFree({x: cell.x + 1, y: cell.y + 1, w: cell.w, h: cell.h}); if (!rect) continue;
        copies.push({...cell, id: cellId(), ...rect});
      }
      if (!copies.length) return;
      draft.cells.push(...copies); selected = new Set(copies.map(c => c.id)); commit('duplicate');
    }
    function nudge(dx, dy) {
      if (!selected.size) return;
      const moving = draft.cells.filter(c => selected.has(c.id));
      const others = draft.cells.filter(c => !selected.has(c.id));
      const next = moving.map(c => ({...c, x: c.x + dx, y: c.y + dy}));
      if (next.some(c => c.x < 0 || c.y < 0 || c.x + c.w > draft.columns || c.y + c.h > MAX_ROWS)) return;
      if (next.some(c => others.some(o => overlaps(c, o)))) return;
      for (const c of next) Object.assign(draft.cells.find(x => x.id === c.id), {x: c.x, y: c.y});
      commit('nudge');
    }
    function tidy() {
      if (!draft) return;
      const ordered = [...draft.cells].sort((a, b) => a.y - b.y || a.x - b.x);
      const placed = [];
      for (const cell of ordered) {
        let y = cell.y;
        while (y > 0 && !placed.some(p => overlaps({...cell, y: y - 1}, p))) y--;
        cell.y = y; placed.push(cell);
      }
      commit('tidy');
    }
    function selectAll() { selected = new Set(cells().map(c => c.id)); paint(); }

    // ── saving ────────────────────────────────────────────────────────────
    function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(save, 800); }
    async function save() {
      if (!draft || !dirty || saving) { if (saving && dirty) scheduleSave(); return; }
      const body = {title: draft.title, unit: UNIT, width: draft.columns,
        cells: draft.cells.map(({id, kind, ref, version, view, title, heading, body: text, column, row, label, caption, format, delta_column, shape, artifact, object, from_version, source_label, measure, params, x, y, w, h}) =>
          Object.fromEntries(Object.entries({id, kind, ref, version, view, title, heading, body: text, column, row, label, caption, format, delta_column, shape, artifact, object, from_version, source_label, measure, params, x, y, w, h}).filter(([, v]) => v != null && v !== '')))};
      const wasDirty = snapshot();
      const headers = {'X-Calliope-Stage': '1'};
      saving = base
        ? api(`/api/calliope/arrangements/${encodeURIComponent(base.id)}`, {method: 'PUT', headers, body: JSON.stringify(body)})
        : api(`/api/calliope/sessions/${encodeURIComponent(session)}/arrangements`, {method: 'POST', headers, body: JSON.stringify(body)});
      try {
        const result = await saving;
        if (result?.surface) {
          base = result.surface; currentLineage = result.lineage_key || base.lineage_key;
          arrangements.set(currentLineage, base);
          onSaved?.(result.surface);
          remember();
        }
        if (snapshot() === wasDirty) dirty = false;
        saveError = '';
      } catch (error) {
        if (error?.code === 'ARRANGEMENT_SUPERSEDED') saveError = 'This arrangement was revised elsewhere. Reload to keep editing.';
        else saveError = error?.message || 'Could not save the arrangement';
      } finally {
        saving = null; paint(); if (dirty && !saveError) scheduleSave();
      }
    }
    function startNew(title = '') {
      if (!canEdit?.() || !session) return false;
      const n = arrangements.size + 1;
      currentLineage = null; base = null;
      draft = {title: title || `Arrangement ${n}`, columns: defaultColumns(), cells: []};
      past = []; future = []; dirty = true; saveError = ''; selected.clear();
      mark = snapshot();
      remember(); scheduleSave(); paint(); return true;
    }
    function choose(lineage) {
      if (lineage === currentLineage && draft) return;
      leaveHistory();
      currentLineage = lineage; loadDraft(arrangements.get(lineage)); remember(); paint();
    }
    async function rename() {
      if (!draft) return;
      const title = clean(prompt('Arrangement title', draft.title), 120);
      if (!title || title === draft.title) return;
      draft.title = title; commit('rename');
    }
    async function remove() {
      if (!base || !confirm(`Delete “${draft.title}”? Its revisions stay in history.`)) return;
      try {
        await api(`/api/calliope/arrangements/${encodeURIComponent(base.id)}`, {method: 'PUT', headers: {'X-Calliope-Stage': '1'}, body: JSON.stringify({deleted: true})});
        arrangements.delete(currentLineage); currentLineage = null; draft = null; base = null; remember(); paint(); onChange?.('delete');
      } catch (error) { toast?.(error?.message || 'Could not delete the arrangement'); }
    }
    function setColumns(columns) {
      columns = clamp(Number(columns) || draft.columns, 4, MAX_COLUMNS);
      if (columns === draft.columns) return;
      if (cells().some(c => c.x + c.w > columns)) { toast?.('Some blocks would fall off the right edge'); return; }
      draft.columns = columns; commit('columns');
    }

    // ── pointer: move, resize, place from the library ─────────────────────
    function sheetPoint(e) {
      const r = sheet.getBoundingClientRect();
      return {px: e.clientX - r.left, py: e.clientY - r.top};
    }
    function beginDrag(e, mode, cell, edge) {
      if (!canEdit?.() || e.button !== 0) return;
      e.preventDefault();
      guardSelection();
      const {px, py} = sheetPoint(e);
      drag = {mode, cell, edge, startX: px, startY: py, origin: cell ? {...cell} : null, moved: false, pointer: e.pointerId, ghost: null};
      setDragging(true);
      try { sheet.setPointerCapture(e.pointerId); } catch {}
    }
    sheet.addEventListener('pointerdown', e => {
      const handle = e.target.closest('[data-resize]');
      const cellEl = e.target.closest('.arrange-cell');
      if (handle && cellEl) {
        const cell = draft.cells.find(c => c.id === cellEl.dataset.cellId);
        if (!selected.has(cell.id)) { selected = new Set([cell.id]); paint(); }
        beginDrag(e, 'resize', cell, handle.dataset.resize); return;
      }
      if (cellEl) {
        if (e.target.closest('button, a, input, textarea, select, [contenteditable="true"]')) return;
        const cell = draft.cells.find(c => c.id === cellEl.dataset.cellId);
        if (e.shiftKey) { if (selected.has(cell.id)) selected.delete(cell.id); else selected.add(cell.id); paint(); return; }
        if (!selected.has(cell.id)) { selected = new Set([cell.id]); paint(); }
        const fromBar = Boolean(e.target.closest('.arrange-cell-bar'));
        if (fromBar || !e.target.closest('iframe, .artifact-frame, .table-wrap, .chart-canvas')) beginDrag(e, 'move', cell);
        return;
      }
      if (e.target === sheet || e.target.closest('.arrange-sheet-empty')) { selected.clear(); paint(); sheet.focus({preventScroll: true}); }
    });
    sheet.addEventListener('pointermove', e => {
      if (!drag || e.pointerId !== drag.pointer) return;
      const {px, py} = sheetPoint(e);
      if (!drag.moved && Math.hypot(px - drag.startX, py - drag.startY) < 6) return;
      drag.moved = true;
      const dx = Math.round((px - drag.startX) / UNIT), dy = Math.round((py - drag.startY) / UNIT);
      if (drag.mode === 'move') {
        const r = {x: clamp(drag.origin.x + dx, 0, draft.columns - drag.origin.w), y: clamp(drag.origin.y + dy, 0, MAX_ROWS - drag.origin.h), w: drag.origin.w, h: drag.origin.h};
        drag.target = r; drag.valid = free(r, drag.cell.id);
      } else if (drag.mode === 'resize') {
        const [mw, mh] = minFor(drag.cell), o = drag.origin, edge = drag.edge;
        let {x, y, w, h} = o;
        if (edge.includes('e')) w = clamp(o.w + dx, mw, draft.columns - o.x);
        if (edge.includes('s')) h = clamp(o.h + dy, mh, MAX_ROWS - o.y);
        if (edge.includes('w')) { const nx = clamp(o.x + dx, 0, o.x + o.w - mw); w = o.w + (o.x - nx); x = nx; }
        if (edge.includes('n')) { const ny = clamp(o.y + dy, 0, o.y + o.h - mh); h = o.h + (o.y - ny); y = ny; }
        // Growing into a neighbour stops at its edge: pull the moved edges
        // back toward the original rectangle, which is known to be free.
        const wanted = {x, y, w, h};
        const settle = dims => {
          const c = {...wanted};
          for (let guard = 0; guard < 2 * MAX_ROWS; guard++) {
            if (free(c, drag.cell.id)) return c;
            let changed = false;
            if (dims.includes('w') && edge.includes('e') && c.w > o.w) { c.w--; changed = true; }
            if (dims.includes('w') && edge.includes('w') && c.x < o.x) { c.x++; c.w--; changed = true; }
            if (dims.includes('h') && edge.includes('s') && c.h > o.h) { c.h--; changed = true; }
            if (dims.includes('h') && edge.includes('n') && c.y < o.y) { c.y++; c.h--; changed = true; }
            if (!changed) return null;
          }
          return null;
        };
        const r = [settle(['h']), settle(['w']), settle(['w', 'h'])].filter(Boolean).sort((a, b) => b.w * b.h - a.w * a.h)[0] || {...o};
        drag.target = r; drag.valid = free(r, drag.cell.id);
      } else if (drag.mode === 'place') {
        const [w, h] = drag.size;
        const r = {x: clamp(Math.floor(px / UNIT) - Math.floor(w / 2), 0, draft.columns - w), y: clamp(Math.floor(py / UNIT), 0, MAX_ROWS - h), w, h};
        drag.target = r; drag.valid = free(r);
      }
      paintGhost();
    });
    function endDrag(e) {
      if (!drag || (e && e.pointerId !== drag.pointer)) return;
      const d = drag; drag = null; setDragging(false); d.ghost?.remove();
      try { sheet.releasePointerCapture(d.pointer); } catch {}
      if (!d.moved || !d.target) return;
      if (d.mode === 'place') {
        const rect = d.valid ? d.target : nearestFree(d.target);
        if (rect) { d.onPlace(rect); } else toast?.('No room there');
        return;
      }
      const rect = d.valid ? d.target : (d.mode === 'move' ? nearestFree(d.target, d.cell.id) : null);
      // The draft may have been reloaded mid-drag; find the cell again by id.
      const cell = rect && draft?.cells.find(c => c.id === d.cell.id);
      if (!cell) { paint(); return; }
      if (rect.x === d.origin.x && rect.y === d.origin.y && rect.w === d.origin.w && rect.h === d.origin.h) { paint(); return; }
      Object.assign(cell, rect); commit(d.mode);
    }
    sheet.addEventListener('pointerup', endDrag);
    sheet.addEventListener('pointercancel', endDrag);
    function paintGhost() {
      if (!drag?.target) return;
      if (!drag.ghost) { drag.ghost = document.createElement('div'); drag.ghost.className = 'arrange-ghost'; sheet.appendChild(drag.ghost); }
      const r = drag.target;
      drag.ghost.style.cssText = `left:${r.x * UNIT}px;top:${r.y * UNIT}px;width:${r.w * UNIT}px;height:${r.h * UNIT}px`;
      drag.ghost.classList.toggle('is-blocked', !drag.valid);
      if (drag.mode !== 'place') {
        const el = sheet.querySelector(`[data-cell-id="${CSS.escape(drag.cell.id)}"]`);
        if (el) el.classList.add('is-lifted');
      }
    }
    // Library tiles: press and drag onto the sheet to place; a click appends.
    rail.addEventListener('pointerdown', e => {
      const tile = e.target.closest('[data-library-surface], [data-library-value]');
      if (!tile || e.button !== 0 || !canEdit?.()) return;
      // A tile is one of this chat's pieces, or a value from a dashboard.
      const item = tile.dataset.libraryValue ? valueCatalog.items.find(v => v.key === tile.dataset.libraryValue) : null;
      const surface = item ? null : surfaces.find(s => s.id === tile.dataset.librarySurface);
      if (!item && !surface) return;
      guardSelection();
      if (!draft && !startNew()) return;
      const start = {x: e.clientX, y: e.clientY}, pointer = e.pointerId;
      const [w0, h] = item ? (item.shape === 'grid' ? SIZE.grid : SIZE.value) : SIZE[surface.kind] || [6, 4];
      const w = Math.min(w0, draft.columns);
      let placing = false;
      const move = ev => {
        if (ev.pointerId !== pointer) return;
        if (!placing && Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 6) return;
        if (!placing) {
          placing = true; setDragging(true);
          drag = {mode: 'place', size: [w, h], pointer, moved: true, ghost: null, onPlace: rect => item ? addValue(item, rect) : addSurface(surface.id, rect)};
        }
        // Over the rail, a chat piece moves between subjects instead of onto the sheet.
        setRailDrop(item ? null : railDropTarget(ev));
        const r = sheet.getBoundingClientRect();
        const inside = !railDrop && ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
        if (!inside) { drag.target = null; drag.ghost?.remove(); drag.ghost = null; return; }
        const px = ev.clientX - r.left, py = ev.clientY - r.top;
        const rect = {x: clamp(Math.floor(px / UNIT) - Math.floor(w / 2), 0, draft.columns - w), y: clamp(Math.floor(py / UNIT), 0, MAX_ROWS - h), w, h};
        drag.target = rect; drag.valid = free(rect); paintGhost();
      };
      const up = ev => {
        if (ev.pointerId !== pointer) return;
        window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up);
        if (!placing) { if (item) addValue(item); else addSurface(surface.id); return; }
        const target = railDrop; setRailDrop(null);
        endDrag(ev);
        if (!target || item) return;
        if (target.hasAttribute('data-new-subject')) {
          const label = clean(prompt('Name the new subject'), 60);
          if (label) correctSubjects({surface_id: surface.id, label});
        } else if (target.dataset.subject && target.dataset.subject !== subjectFor(surface)?.label) {
          correctSubjects({surface_id: surface.id, label: target.dataset.subject});
        }
      };
      window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up);
    });

    // ── keyboard ──────────────────────────────────────────────────────────
    sheet.addEventListener('keydown', e => {
      if (!draft || !canEdit?.()) return;
      if (e.target.closest('input, textarea, [contenteditable="true"]')) return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
      if (mod && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); return; }
      if (mod && e.key.toLowerCase() === 'd') { e.preventDefault(); duplicateSelected(); return; }
      if (mod && e.key.toLowerCase() === 'a') { e.preventDefault(); selectAll(); return; }
      if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); removeSelected(); return; }
      if (e.key === 'Escape') { selected.clear(); paint(); return; }
      const arrows = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
      if (arrows[e.key]) { e.preventDefault(); nudge(...arrows[e.key]); }
    });

    // ── clicks: bar controls, text editing, rail, toolbar ─────────────────
    const onAct = e => {
      const act = e.target.closest('[data-arrange-act]'); if (!act) return;
      e.preventDefault();
      const cellEl = act.closest('.arrange-cell'); const cell = cellEl && draft?.cells.find(c => c.id === cellEl.dataset.cellId);
      switch (act.dataset.arrangeAct) {
        case 'remove': if (cell) { selected = new Set([cell.id]); removeSelected(); } break;
        case 'source': if (cell) { const s = resolveCell(cell); if (s) focusSurface?.(s.id); } break;
        case 'pin': if (cell) { if (popover?.parentElement === cellEl) closePopover(); else openVersions(cell, cellEl); } break;
        case 'pick-version': if (cell) { closePopover(); setVersion(cell.id, act.dataset.version); } break;
        case 'stat': if (cell) { if (popover?.parentElement === cellEl) closePopover(); else openStatPicker(cell, cellEl); } break;
        case 'pick-stat': if (cell) { closePopover(); addStat(cell.id, act.dataset.column); } break;
        case 'slices': if (cell) { if (popover?.parentElement === cellEl) closePopover(); else openSlices(cell, cellEl); } break;
        case 'pick-slice': if (cell) { closePopover(); pickSlice(cell, act.dataset.slice); } break;
        case 'format': if (cell?.kind === 'stat' || (cell?.kind === 'value' && cell.shape !== 'grid')) { cell.format = FORMATS[(FORMATS.indexOf(cell.format || 'auto') + 1) % FORMATS.length]; commit('format'); } break;
        case 'view': if (cell) { const order = ['table', 'chart', 'sql']; cell.view = order[(order.indexOf(cell.view || 'table') + 1) % order.length]; commit('view'); } break;
        case 'title': if (cell?.kind === 'value') { const t = clean(prompt('Label', cell.label || cell.source_label || ''), 80); cell.label = t || clean(cell.source_label, 80) || undefined; commit('label'); }
          else if (cell?.kind === 'stat') { const t = clean(prompt('Stat label', cell.label || cell.column || ''), 80); cell.label = t || cell.column; commit('label'); }
          else if (cell) { const t = clean(prompt('Block title (blank keeps the original)', cell.title || ''), 120); cell.title = t || undefined; commit('title'); } break;
        case 'new': startNew(); break;
        case 'rename': rename(); break;
        case 'delete': remove(); break;
        case 'tidy': tidy(); break;
        case 'text': addText(); break;
        case 'undo': undo(); break;
        case 'redo': redo(); break;
        case 'rail': railOpen = !railOpen; remember(); paint(); syncSubjects(); break;
        case 'unplaced': unplacedOnly = !unplacedOnly; paint(); break;
        case 'group': setGroupBy(groupBy === 'subject' ? 'turn' : 'subject'); break;
        case 'dash': {
          // Open or close one dashboard's values. While a filter (words, or the
          // value / grid chips) narrows them every match is open, so there a click closes instead.
          const slug = act.dataset.dash, pick = valuesFiltering() ? dashShut : dashOpen;
          if (pick.has(slug)) pick.delete(slug); else pick.add(slug);
          paintRail(); break;
        }
        case 'collapse': { const k = act.dataset.group; if (collapsed.has(k)) collapsed.delete(k); else collapsed.add(k); paintRail(); break; }
        case 'add-group': {
          const group = railGroups(librarySurfaces()).find(g => g.key === act.dataset.group);
          const placed = new Set(cells().map(c => c.lineage_key || c.ref));
          for (const s of group?.list || []) if (!placed.has(s.lineage_key) && !placed.has(s.id)) addSurface(s.id);
          break;
        }
        case 'rename-subject': renameSubject(act.dataset.subject); break;
        case 'restore': restoreRevision(); break;
        case 'now': leaveHistory(); paint(); break;
        case 'kind': { const k = act.dataset.kind; if (railKinds.has(k)) railKinds.delete(k); else railKinds.add(k); paint(); break; }
        case 'reload': loadDraft(arrangements.get(currentLineage)); paint(); break;
        case 'choose': choose(act.dataset.lineage); menuOpen = false; paintControl(); break;
        case 'menu': menuOpen = !menuOpen; paintControl(); break;
        case 'close': setActive(false); break;
      }
    };
    host.addEventListener('click', onAct);
    if (railHost) rail.addEventListener('click', onAct);
    host.addEventListener('input', e => {
      const field = e.target.closest('[data-text-field]'); if (!field) return;
      const cell = draft?.cells.find(c => c.id === field.closest('.arrange-cell')?.dataset.cellId); if (!cell) return;
      cell[field.dataset.textField] = clean(field.textContent, field.dataset.textField === 'heading' ? 160 : 2000);
      dirty = true; scheduleSave();
    });
    host.addEventListener('focusout', e => {
      if (!e.target.closest?.('[data-text-field]') || !draft) return;
      const now = snapshot();
      if (now !== mark) { past.push(mark); future = []; mark = now; }
    });
    host.addEventListener('change', e => {
      if (e.target.matches('[data-arrange-columns]')) setColumns(e.target.value);
    });
    // A picker closes on any press outside it (its own buttons act first).
    document.addEventListener('pointerdown', e => {
      if (popover && !e.target.closest?.('.arrange-popover, [data-arrange-act="pin"], [data-arrange-act="stat"], [data-arrange-act="slices"]')) closePopover();
    });
    host.addEventListener('keydown', e => { if (e.key === 'Escape' && popover) { e.stopPropagation(); closePopover(); } }, true);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') previewSurface(null); });
    rail.addEventListener('input', e => { if (e.target.matches('[data-rail-query]')) { railQuery = e.target.value; paintRail(); } });
    // The page's own history scrubber: the last stop is now.
    host.addEventListener('input', e => {
      if (!e.target.matches('[data-arrange-history]')) return;
      const groups = historyGroups(), i = Number(e.target.value);
      if (i >= groups.length) leaveHistory(); else cutoff = null;
      setCutoff(i >= groups.length ? null : at(groups[i].last));
      if (i >= groups.length) paint();
    });

    // ── painting ──────────────────────────────────────────────────────────
    function librarySurfaces() {
      const placed = new Set(cells().map(c => c.lineage_key || c.ref));
      const q = railQuery.trim().toLowerCase();
      // One tile per lineage: the latest revision stands for its family.
      const latest = new Map();
      for (const s of library) {
        if (!MEMBER_KINDS.has(s.kind)) continue;
        const key = s.lineage_key || s.id, have = latest.get(key);
        if (!have || newer(have, s) > 0) latest.set(key, s);
      }
      return [...latest.values()]
        .filter(s => !railKinds.size || railKinds.has(s.kind))
        .filter(s => !unplacedOnly || !(placed.has(s.lineage_key) || placed.has(s.id)))
        .filter(s => !q || `${s.title} ${s.kind} ${s.source?.sql || ''}`.toLowerCase().includes(q));
    }
    function turnLabel(turn, index) {
      const text = clean(turn?.user_message || turn?.title || '', 72);
      return text || `Turn ${turn?.ordinal ?? index + 1}`;
    }
    // The rail's skeleton is built once so the filter box keeps focus while
    // the list underneath it repaints.
    function paintRail() {
      if (page || (!railHost && !railOpen)) { rail.hidden = true; return; }
      rail.hidden = false;
      const all = library.filter(s => MEMBER_KINDS.has(s.kind));
      if (!rail.dataset.ready) {
        rail.dataset.ready = '1';
        rail.innerHTML = `<div class="arrange-rail-head"><strong>Library</strong><span data-rail-count></span>
            ${railHost ? '' : '<button type="button" class="arrange-icon" data-arrange-act="rail" title="Hide the library" aria-label="Hide the library">«</button>'}</div>
          <div class="arrange-rail-tools"><input type="search" data-rail-query placeholder="Filter…" aria-label="Filter the library">
            ${subjects ? '<button type="button" data-arrange-act="group" title="Group by subject or by turn"></button>' : ''}
            <button type="button" data-arrange-act="unplaced" title="Only pieces not yet placed">Unplaced</button></div>
          <div class="arrange-rail-kinds"></div><div class="arrange-rail-list"></div><div class="arrange-rail-foot"></div>`;
      }
      const items = librarySurfaces();
      const editable = Boolean(canEdit?.());
      rail.querySelector('[data-rail-count]').textContent = `${items.length} of ${all.length}`;
      rail.querySelector('[data-arrange-act="unplaced"]').setAttribute('aria-pressed', String(unplacedOnly));
      const groupButton = rail.querySelector('[data-arrange-act="group"]');
      if (groupButton) groupButton.textContent = groupBy === 'subject' ? 'By subject' : 'By turn';
      loadValues();
      const kinds = [...new Set(all.map(s => s.kind))].sort();
      if (valuesApi && valueCatalog.kinds?.value) kinds.push('value');
      if (valuesApi && valueCatalog.kinds?.grid) kinds.push('grid');
      const kindsMarkup = kinds.map(k => `<button type="button" data-arrange-act="kind" data-kind="${esc(k)}" aria-pressed="${railKinds.has(k)}" title="${esc(k)}"><i>${GLYPH[k] || '•'}</i>${esc(k)}</button>`).join('');
      const kindsHost = rail.querySelector('.arrange-rail-kinds');
      if (kindsHost.dataset.markup !== kindsMarkup) { kindsHost.innerHTML = kindsMarkup; kindsHost.dataset.markup = kindsMarkup; }
      const placed = new Set(cells().map(c => c.lineage_key || c.ref));
      const unplaced = s => !(placed.has(s.lineage_key) || placed.has(s.id));
      const tile = s => {
        const thumb = thumbnailFor(s), subject = groupBy === 'turn' && subjectFor(s);
        return `<div class="arrange-tile ${unplaced(s) ? '' : 'is-placed'}" data-library-surface="${esc(s.id)}" title="${esc(s.presentation?.brief || s.title)}">
          ${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : `<i class="arrange-tile-glyph">${GLYPH[s.kind] || '•'}</i>`}
          <div class="arrange-tile-copy"><b>${esc(s.title || s.kind)}</b><span>${esc(s.kind)}${s.artifact_version ? ` · v${esc(s.artifact_version)}` : ''}${subject ? ` · ${esc(subject.label)}` : ''}</span></div>
          ${unplaced(s) ? '' : '<em title="Placed">✓</em>'}</div>`;
      };
      const groupsMarkup = railGroups(items).map(g => {
        const shut = collapsed.has(g.key);
        return `<section class="arrange-rail-group ${shut ? 'is-collapsed' : ''}" data-group="${esc(g.key)}" ${g.subject ? `data-subject="${esc(g.subject)}"` : ''}>
          <h4><button type="button" data-arrange-act="collapse" data-group="${esc(g.key)}" title="${esc(g.title)}"><i>${shut ? '▸' : '▾'}</i>${esc(g.label)}</button><span>${g.list.length}</span>
            ${g.subject && editable ? `<button type="button" class="arrange-icon" data-arrange-act="rename-subject" data-subject="${esc(g.subject)}" title="Rename this subject">✎</button>` : ''}
            ${editable && g.list.some(unplaced) ? `<button type="button" class="arrange-icon" data-arrange-act="add-group" data-group="${esc(g.key)}" title="Place every unplaced piece in this group">+ all</button>` : ''}</h4>
          <div class="arrange-rail-items">${g.list.map(tile).join('')}</div></section>`;
      }).join('');
      const valuesMarkup = valueSection();
      const listMarkup = groupsMarkup + valuesMarkup || '<p class="arrange-rail-empty">Nothing matches.</p>';
      const dropZone = groupBy === 'subject' && subjects && editable ? '<div class="arrange-rail-newgroup" data-new-subject>Drop here to start a new subject</div>' : '';
      const listHost = rail.querySelector('.arrange-rail-list');
      if (listHost.dataset.markup !== listMarkup + dropZone) { listHost.innerHTML = listMarkup + dropZone; listHost.dataset.markup = listMarkup + dropZone; }
      const foot = rail.querySelector('.arrange-rail-foot');
      const cost = Number(subjectSpend?.cost) || 0;
      const footMarkup = !subjects ? '' : groupBy === 'turn' ? '<span>Grouped by turn</span>'
        : subjectError ? `<span class="is-error">Grouping unavailable: ${esc(subjectError)}</span>`
        : subjectBusy ? '<span>Sorting…</span>'
        : subjectMode === 'off' ? '<span>Subject grouping is off</span>'
        : subjectMode === 'rules' ? '<span>Grouped by rules; Jev was unavailable</span>'
        : subjectSpend?.decisions ? `<span title="Jev decisions spent grouping this chat's library">${subjectSpend.decisions} decision${subjectSpend.decisions === 1 ? '' : 's'} · $${cost < 0.01 ? cost.toFixed(6) : cost.toFixed(4)}</span>` : '';
      if (foot && foot.dataset.markup !== footMarkup) { foot.innerHTML = footMarkup; foot.dataset.markup = footMarkup; }
    }
    // The persistent section under this chat's pieces: values from every
    // dashboard the viewer may open, narrowed by the same filter box.
    function valueSection() {
      const wantValue = railKinds.has('value'), wantGrid = railKinds.has('grid');
      if (!valuesApi || page || (railKinds.size && !wantValue && !wantGrid)) return '';
      const shapeOk = v => (!wantValue && !wantGrid) || (v.shape === 'grid' ? wantGrid : wantValue);
      if (valueCatalog.q !== null && !valueCatalog.catalog && !valueCatalog.error) return '';
      const shut = collapsed.has('values');
      const placed = new Set(cells().filter(c => c.kind === 'value').map(c => `${c.artifact}:${c.shape === 'grid' ? 'grid:' : ''}${c.object}`));
      const list = valueCatalog.items.filter(v => shapeOk(v) && (!unplacedOnly || !placed.has(v.key)));
      const filtering = valuesFiltering();
      const tile = v => {
        const grid = v.shape === 'grid';
        const names = grid ? (v.measures || []).slice(0, 2).join(', ') : v.measure && v.measure !== String(v.label || '').toLowerCase() ? v.measure : '';
        const under = [grid ? 'grid' : 'value', names].filter(Boolean).join(' · ');
        return `<div class="arrange-tile arrange-value-tile ${grid ? 'is-grid' : ''} ${placed.has(v.key) ? 'is-placed' : ''}" data-library-value="${esc(v.key)}" title="${esc(v.description || v.label)}">
          <i class="arrange-tile-glyph">${grid ? GLYPH.grid : GLYPH.value}</i>
          <div class="arrange-tile-copy"><b>${esc(v.label)}</b><span>${esc(under)}</span></div>
          ${placed.has(v.key) ? '<em title="Placed">✓</em>' : ''}</div>`;
      };
      // One subgroup per dashboard, in the order the server ranked their values.
      const byDash = new Map();
      for (const v of list) { if (!byDash.has(v.artifact)) byDash.set(v.artifact, {slug: v.artifact, name: v.artifact_name || v.artifact, owner: v.owner || '', href: v.href || '', list: []}); byDash.get(v.artifact).list.push(v); }
      const groups = [...byDash.values()].map(g => {
        const open = filtering ? !dashShut.has(g.slug) : dashOpen.has(g.slug);
        const grids = g.list.filter(v => v.shape === 'grid').length, values = g.list.length - grids;
        const mix = [values ? `${values} value${values === 1 ? '' : 's'}` : '', grids ? `${grids} grid${grids === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ');
        return `<div class="arrange-rail-dash ${open ? '' : 'is-collapsed'}" data-dash="${esc(g.slug)}">
          <h5><button type="button" data-arrange-act="dash" data-dash="${esc(g.slug)}" aria-expanded="${open}" title="${esc(g.name)}${g.owner ? ` · ${esc(g.owner)}` : ''} · ${esc(mix)}"><i>${open ? '▾' : '▸'}</i><b>${esc(g.name)}</b></button><span>${g.list.length}</span>
            ${g.href ? `<a class="arrange-icon" href="${esc(g.href)}" target="_blank" rel="noopener" title="Open ${esc(g.name)}">↗</a>` : ''}</h5>
          ${open ? `<div class="arrange-rail-items">${g.list.map(tile).join('')}</div>` : ''}</div>`;
      }).join('');
      const hidden = valueCatalog.total - valueCatalog.items.length;
      const note = valueCatalog.error ? `<p class="arrange-rail-more is-error">${esc(valueCatalog.error)}</p>`
        : valueCatalog.q === null ? '<p class="arrange-rail-more">Loading…</p>'
        : !list.length ? `<p class="arrange-rail-more">${filtering ? `No dashboard ${wantGrid && !wantValue ? 'grids' : wantValue && !wantGrid ? 'values' : 'values or grids'} match.` : 'Nothing left to place.'}</p>`
        : hidden > 0 ? `<p class="arrange-rail-more">${hidden} more · filter to narrow</p>` : '';
      return `<section class="arrange-rail-group arrange-rail-values ${shut ? 'is-collapsed' : ''}" data-group="values">
        <h4><button type="button" data-arrange-act="collapse" data-group="values" title="Numbers and grids your team’s dashboards already show, by dashboard. Drag one in; it stays live."><i>${shut ? '▸' : '▾'}</i>From dashboards</button><span>${valueCatalog.q === null ? '' : wantValue || wantGrid ? list.length : valueCatalog.total}</span></h4>
        <div class="arrange-rail-items arrange-rail-dashes">${groups}${note}</div></section>`;
    }
    // Groups for the rail: subjects newest first (pieces still waiting last),
    // or the turns that produced the pieces.
    function railGroups(items) {
      if (groupBy === 'subject' && subjects) {
        const order = new Map(subjectList.map((s, i) => [s.label, i]));
        const by = new Map();
        for (const s of items) {
          const subject = subjectFor(s), key = subject ? `subject:${subject.label}` : 'subject:';
          if (!by.has(key)) {
            const entry = subjectList.find(x => x.label === subject?.label);
            const mix = entry?.sources ? Object.entries(entry.sources).map(([k, n]) => `${k === 'person' ? 'placed by people' : k === 'rule' ? 'by rule' : k} ${n}`).join(' · ') : '';
            by.set(key, {key, subject: subject?.label || '', list: [], latest: '',
              label: subject?.label || (subjectBusy ? 'Sorting…' : subjectError || subjectMode === 'off' ? 'Not grouped' : 'Not yet grouped'),
              title: subject ? `${subject.label}${mix ? ` · ${mix}` : ''}` : 'Pieces without a subject yet'});
          }
          const g = by.get(key); g.list.push(s); g.latest = g.latest > String(s.created_at || '') ? g.latest : String(s.created_at || '');
        }
        return [...by.values()].sort((a, b) => {
          if (!a.subject !== !b.subject) return a.subject ? -1 : 1;
          const ia = order.has(a.subject) ? order.get(a.subject) : 1e9, ib = order.has(b.subject) ? order.get(b.subject) : 1e9;
          return ia - ib || (a.latest < b.latest ? 1 : a.latest > b.latest ? -1 : 0);
        }).map(g => ({...g, list: g.list.sort(newer)}));
      }
      const byTurn = new Map();
      for (const s of items) { const k = String(s.turn_id || ''); if (!byTurn.has(k)) byTurn.set(k, []); byTurn.get(k).push(s); }
      const turnOrder = [...turns].map(t => String(t.id)).reverse();
      const rank = id => { const i = turnOrder.indexOf(id); return i < 0 ? 1e9 : i; };
      return [...byTurn.entries()].sort((a, b) => rank(a[0]) - rank(b[0])).map(([turnId, list]) => {
        const label = turnLabel(turns.find(t => String(t.id) === turnId), 0);
        return {key: `turn:${turnId}`, subject: '', label, title: label, list: list.sort(newer)};
      });
    }
    function thumbnailFor(surface) {
      if (surface.kind === 'image') return surface.payload?.thumbnail_url || surface.payload?.image_url || null;
      const capture = surfaces.find(s => s.kind === 'image' && s.payload?.source_surface_id === surface.id && s.payload?.image_url)
        || (surface.artifact_slug && surface.artifact_version != null
          ? surfaces.find(s => s.kind === 'image' && s.artifact_slug === surface.artifact_slug && s.artifact_version === surface.artifact_version && s.payload?.image_url) : null);
      return capture?.payload?.image_url || null;
    }
    const pageHref = lineage => `/calliope/arrangements/${encodeURIComponent(String(lineage || '').replace(/^arrangement:/, ''))}`;
    function paintToolbar() {
      const editable = Boolean(canEdit?.());
      if (page) {
        // The page's only chrome: what this is, when it last changed, the way to its chat.
        const saved = arrangements.get(page);
        if (draft?.title && typeof document !== 'undefined' && 'title' in document) document.title = `${draft.title} · Calliope`;
        const chat = session ? `/calliope?session=${encodeURIComponent(session)}&arrangement=${encodeURIComponent(page.replace(/^arrangement:/, ''))}` : '';
        // The page scrubs its own history: one stop per grouped edit, "now" at the right.
        const groups = historyGroups();
        const stop = viewing ? groups.findIndex(g => g.members.some(m => m.id === viewing.surface.id)) : groups.length;
        const scrubber = groups.length > 1 ? `<label class="arrange-history"><span>${esc(viewing ? describeEvent(groups[stop]) : 'Now')}</span><input type="range" data-arrange-history min="0" max="${groups.length}" step="1" value="${stop < 0 ? groups.length : stop}" aria-label="Arrangement history"></label>` : '';
        toolbar.innerHTML = `<div class="arrange-page-bar"><h3 class="arrange-title">${esc(view()?.title || saved?.title || 'Arrangement')}</h3>
          ${saved?.created_at && !viewing ? `<span class="arrange-meta" title="${esc(saved.created_at)}">revised ${esc(when(saved.created_at))}</span>` : ''}
          ${scrubber}
          <span class="arrange-spacer"></span>
          ${chat ? `<a class="arrange-page-link" href="${esc(chat)}">Open in chat ↗</a>` : ''}</div>`;
        return;
      }
      if (viewing && draft) {
        // Looking back: the sheet is read-only; the only ways out are forward.
        const group = historyGroups().find(g => g.members.some(m => m.id === viewing.surface.id));
        toolbar.innerHTML = `<div class="arrange-viewing"><i>⟲</i><b>${esc(view().title)}</b><span>${esc(describeEvent(group))}</span>
          <span class="arrange-spacer"></span>
          ${canEditOption?.() ? '<button type="button" data-arrange-act="restore" title="Save this layout as the newest revision">Restore this</button>' : ''}
          <button type="button" data-arrange-act="now" title="Back to the latest revision">Back to now</button></div>`;
        return;
      }
      const status = saveError ? `<span class="arrange-status is-error">${esc(saveError)} ${currentLineage ? '<button type="button" data-arrange-act="reload">Reload</button>' : ''}</span>`
        : saving ? '<span class="arrange-status">Saving…</span>' : dirty ? '<span class="arrange-status">Unsaved</span>' : base ? '<span class="arrange-status">Saved</span>' : '';
      toolbar.innerHTML = draft ? `
        ${railOpen || railHost ? '' : '<button type="button" class="arrange-icon" data-arrange-act="rail" title="Show the library" aria-label="Show the library">»</button>'}
        <h3 class="arrange-title"><button type="button" data-arrange-act="rename" ${editable ? '' : 'disabled'} title="Rename">${esc(draft.title)}</button></h3>
        <span class="arrange-meta">${cells().length} block${cells().length === 1 ? '' : 's'} · <label>width <input type="number" data-arrange-columns min="4" max="${MAX_COLUMNS}" value="${draft.columns}" ${editable ? '' : 'disabled'}> cells</label></span>
        ${status}
        <span class="arrange-spacer"></span>
        ${editable ? `<button type="button" data-arrange-act="text" title="Add a text block">+ Text</button>
        <button type="button" data-arrange-act="tidy" title="Pack blocks upward">Tidy</button>
        <button type="button" data-arrange-act="undo" ${past.length ? '' : 'disabled'} title="Undo (⌘Z)">↶</button>
        <button type="button" data-arrange-act="redo" ${future.length ? '' : 'disabled'} title="Redo (⇧⌘Z)">↷</button>
        <button type="button" data-arrange-act="delete" ${base ? '' : 'disabled'} class="arrange-danger" title="Delete this arrangement">Delete</button>` : ''}
        ${base ? `<a class="arrange-page-link" href="${esc(pageHref(currentLineage))}" target="_blank" rel="noopener" title="Open this arrangement as its own page">Page ↗</a>` : ''}
        <button type="button" data-arrange-act="close" title="Back to the Stage">Close</button>` : `
        ${railOpen || railHost ? '' : '<button type="button" class="arrange-icon" data-arrange-act="rail" title="Show the library" aria-label="Show the library">»</button>'}
        <h3 class="arrange-title">Arrange</h3><span class="arrange-spacer"></span>
        ${editable ? '<button type="button" data-arrange-act="new">New arrangement</button>' : ''}
        <button type="button" data-arrange-act="close" title="Back to the Stage">Close</button>`;
    }
    function cellBodyKey(cell) {
      if (cell.kind === 'text') return 'text';
      if (cell.kind === 'value') { const d = valueOf(cell); return `value:${valueKey(cell)}:${cell.label || ''}:${cell.format || ''}:${cell.caption || ''}:${d?.status || ''}:${d?.at || 0}`; }
      const s = resolveCell(cell);
      return `${cell.kind}:${s?.id || cell.ref}:${cell.view || ''}:${cell.title || ''}:${cell.column || ''}:${cell.row || 0}:${cell.format || ''}:${cell.label || ''}:${cell.caption || ''}`;
    }
    function cellBody(cell) {
      if (cell.kind === 'text') {
        return `<div class="arrange-text"><h4 data-text-field="heading" contenteditable="${canEdit?.() ? 'true' : 'false'}" spellcheck="false">${esc(cell.heading || '')}</h4><p data-text-field="body" contenteditable="${canEdit?.() ? 'true' : 'false'}">${esc(cell.body || '')}</p></div>`;
      }
      if (cell.kind === 'value') {
        const data = valueOf(cell);
        return renderValue ? renderValue(cell, data) : `<div class="arrange-missing"><strong>${esc(cell.label || 'Value')}</strong><span>${esc(data?.status === 'ready' ? data.value : data?.message || '···')}</span></div>`;
      }
      const s = resolveCell(cell);
      if (!s) return `<div class="arrange-missing"><strong>Not in this chat anymore</strong><span>The piece this block showed is gone.</span></div>`;
      return renderSurface(s, cell);
    }
    function paintCell(cell, el) {
      const s = cell.kind === 'text' ? null : resolveCell(cell);
      const isValue = cell.kind === 'value', data = isValue ? valueOf(cell) : null;
      const grid = isGrid(cell);
      const kind = cell.kind === 'text' ? 'text' : grid ? 'query' : isValue ? 'stat' : !s ? 'missing' : cell.kind === 'stat' ? 'stat' : s.kind;
      el.className = `arrange-cell kind-${kind} ${isValue ? 'is-value' : ''} ${grid ? 'is-grid' : ''} ${selected.has(cell.id) ? 'is-selected' : ''}`;
      el.dataset.cellId = cell.id;
      if (s) el.dataset.surfaceId = s.id; else delete el.dataset.surfaceId;
      el.style.cssText = `left:${cell.x * UNIT}px;top:${cell.y * UNIT}px;width:${cell.w * UNIT}px;height:${cell.h * UNIT}px`;
      const key = cellBodyKey(cell);
      let body = el.querySelector(':scope > .arrange-cell-body');
      if (!body || body.dataset.key !== key) {
        const fresh = document.createElement('div'); fresh.className = 'arrange-cell-body'; fresh.dataset.key = key; fresh.innerHTML = cellBody(cell);
        if (body) body.replaceWith(fresh); else el.appendChild(fresh);
      }
      const title = isValue ? (cell.label || cell.source_label || 'Value') : cell.kind === 'stat' ? (cell.label || cell.column || 'Stat') : cell.title || s?.title || (cell.kind === 'text' ? 'Text' : '');
      const moved = isValue && Object.keys(cell.params || {}).length > 0;
      const pinned = cell.version && cell.version !== 'latest';
      const versions = s ? lineageVersions(cell).length : 0;
      const barMarkup = `<span class="arrange-cell-grip" title="Drag to move">⋮⋮</span><b title="${esc(title)}">${esc(title)}</b>${s?.artifact_version ? `<i>v${esc(s.artifact_version)}</i>` : ''}${pinned ? '<i title="Pinned to one revision">⚲</i>' : ''}<span class="arrange-spacer"></span>
        ${canEdit?.() ? `${s && versions > 1 ? `<button type="button" data-arrange-act="pin" aria-pressed="${pinned}" title="${pinned ? 'Pinned to one revision' : 'Following the latest revision'} · choose a revision">${pinned ? '⚲' : '↻'}</button>` : ''}
        ${isValue && data?.slices?.length ? `<button type="button" data-arrange-act="slices" aria-pressed="${moved}" title="${moved ? 'Moved to another slice' : 'Taken over the dashboard’s own slice'} · change it">⧉</button>` : ''}
        ${cell.kind === 'stat' || (isValue && !grid) ? `<button type="button" data-arrange-act="format" title="Number format: ${esc(cell.format || 'auto')}">${FORMAT_GLYPH[cell.format || 'auto']}</button>` : ''}
        ${isValue ? `<button type="button" data-arrange-act="title" title="${grid ? 'Title this grid' : 'Label this value'}">✎</button>` : ''}
        ${isValue && data?.href ? `<a class="arrange-bar-link" href="${esc(data.href)}" target="_blank" rel="noopener" title="Open ${esc(data.artifact_name || 'the dashboard')}">↗</a>` : ''}
        ${s?.kind === 'query' && cell.kind !== 'stat' ? '<button type="button" data-arrange-act="view" title="Table, chart or SQL">⇄</button><button type="button" data-arrange-act="stat" title="Pull one number out as a stat block">Σ</button>' : ''}
        ${s ? `<button type="button" data-arrange-act="title" title="${cell.kind === 'stat' ? 'Label this stat' : 'Rename this block'}">✎</button>` : ''}
        ${s ? '<button type="button" data-arrange-act="source" title="Show on the Stage">↗</button>' : ''}
        <button type="button" data-arrange-act="remove" title="Remove from the arrangement">×</button>` : s ? '<button type="button" data-arrange-act="source" title="Show on the Stage">↗</button>'
        : isValue && data?.href ? `<a class="arrange-bar-link" href="${esc(data.href)}" target="_blank" rel="noopener" title="Open ${esc(data.artifact_name || 'the dashboard')}">↗</a>` : ''}`;
      let bar = el.querySelector(':scope > .arrange-cell-bar');
      if (!bar) { bar = document.createElement('div'); bar.className = 'arrange-cell-bar'; el.appendChild(bar); }
      if (bar.dataset.markup !== barMarkup) { bar.innerHTML = barMarkup; bar.dataset.markup = barMarkup; }
      if (!el.querySelector(':scope > .arrange-handles')) {
        const handles = document.createElement('div'); handles.className = 'arrange-handles';
        handles.innerHTML = ['n', 'e', 's', 'w', 'ne', 'se', 'sw', 'nw'].map(d => `<i data-resize="${d}"></i>`).join('');
        el.appendChild(handles);
      }
    }
    function paintSheet() {
      if (!draft) {
        sheet.style.cssText = 'width:100%';
        if (page) {
          sheet.innerHTML = `<div class="arrange-sheet-empty"><strong>${session ? 'This arrangement isn’t here' : 'Loading…'}</strong>
            ${session ? '<span>It may have been deleted, or it belongs to a chat you cannot see.</span>' : ''}</div>`;
          return;
        }
        sheet.innerHTML = `<div class="arrange-sheet-empty"><strong>${arrangements.size ? 'Choose an arrangement' : 'Nothing arranged yet'}</strong>
          <span>${canEdit?.() ? 'Pick pieces from the library on the left, or press ⊕ on any Stage card.' : 'Arrangements this chat’s participants make appear here.'}</span>
          ${arrangements.size ? `<div class="arrange-choices">${[...arrangements.values()].sort(newer).map(a => `<button type="button" data-arrange-act="choose" data-lineage="${esc(a.lineage_key)}">${esc(a.payload?.title || a.title)}<small>${(a.payload?.cells || []).length} blocks</small></button>`).join('')}</div>` : ''}
          ${canEdit?.() ? '<button type="button" data-arrange-act="new">New arrangement</button>' : ''}</div>`;
        return;
      }
      const rows = Math.max(rowsNeeded() + SLACK_ROWS, Math.ceil((sheetScroll.clientHeight || 600) / UNIT));
      sheet.style.cssText = `width:${view().columns * UNIT}px;height:${rows * UNIT}px`;
      sheet.querySelector('.arrange-sheet-empty')?.remove();
      const seen = new Set();
      for (const cell of cells()) {
        let el = sheet.querySelector(`:scope > [data-cell-id="${CSS.escape(cell.id)}"]`);
        if (!el) { el = document.createElement('section'); el.setAttribute('role', 'group'); sheet.appendChild(el); }
        paintCell(cell, el); seen.add(el);
      }
      for (const el of [...sheet.querySelectorAll(':scope > .arrange-cell')]) if (!seen.has(el)) el.remove();
      if (!cells().length) sheet.insertAdjacentHTML('beforeend', `<div class="arrange-sheet-empty arrange-sheet-hint"><span>${canEdit?.() ? 'Drag pieces here from the library, or press ⊕ on any Stage card.' : 'Empty arrangement.'}</span></div>`);
      hydrate?.(sheet);
      ensureValues();
    }
    function paint() {
      if (!active) return;
      host.dataset.editable = String(Boolean(canEdit?.()));
      paintToolbar(); paintRail(); paintSheet(); paintControl();
    }
    const icon = '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="2.5" width="6" height="9" rx="1"/><rect x="11.5" y="2.5" width="6" height="5" rx="1"/><rect x="11.5" y="10.5" width="6" height="7" rx="1"/><rect x="2.5" y="14.5" width="6" height="3" rx="1"/></svg>';
    function paintControl() {
      if (!control) return;
      control.hidden = !session;
      const list = [...arrangements.values()].sort(newer);
      const current = currentLineage ? arrangements.get(currentLineage) : null;
      const label = active ? (draft?.title || 'Arrange') : (list.length ? `Arrange · ${list.length}` : 'Arrange');
      const markup = `<button type="button" class="arrange-trigger" data-arrange-act="${active ? 'menu' : 'open'}" aria-pressed="${active}" aria-expanded="${menuOpen}" title="${active ? 'Switch arrangement' : 'Arrange this chat’s pieces on a grid'}">${icon}<span>${esc(label)}</span></button>
        ${active && menuOpen ? `<div class="arrange-menu" role="menu">${list.map(a => `<button type="button" role="menuitemradio" aria-checked="${a === current}" data-arrange-act="choose" data-lineage="${esc(a.lineage_key)}"><b>${esc(a.payload?.title || a.title)}</b><small>${(a.payload?.cells || []).length} blocks</small></button>`).join('')}${canEdit?.() ? '<button type="button" role="menuitem" data-arrange-act="new"><b>New arrangement</b><small>Start empty</small></button>' : ''}<button type="button" role="menuitem" data-arrange-act="close"><b>Back to the Stage</b></button></div>` : ''}`;
      if (control.dataset.markup !== markup) { control.innerHTML = markup; control.dataset.markup = markup; }
    }
    control?.addEventListener('click', e => {
      const act = e.target.closest('[data-arrange-act]'); if (!act) return;
      e.preventDefault();
      switch (act.dataset.arrangeAct) {
        case 'open': setActive(true); break;
        case 'close': setActive(false); break;
        case 'menu': menuOpen = !menuOpen; paintControl(); break;
        case 'choose': choose(act.dataset.lineage); menuOpen = false; paintControl(); break;
        case 'new': startNew(); menuOpen = false; paintControl(); break;
      }
    });
    document.addEventListener('click', e => { if (menuOpen && !e.target.closest('.arrange-menu, .arrange-trigger')) { menuOpen = false; paintControl(); } });

    function setActive(on) {
      on = Boolean(on) && Boolean(session);
      if (page) on = Boolean(session); // a page cannot be closed into the Stage
      if (on === active) return;
      if (!on) previewSurface(null);
      active = on; menuOpen = false;
      if (active) {
        if (!currentLineage || !arrangements.has(currentLineage)) currentLineage = [...arrangements.values()].sort(newer)[0]?.lineage_key || null;
        loadDraft(currentLineage ? arrangements.get(currentLineage) : null);
      }
      document.getElementById('stage-rail')?.toggleAttribute('data-arrange', active);
      remember(); paintControl(); onChange?.('mode'); syncSubjects();
    }

    // ── public ────────────────────────────────────────────────────────────
    function update(sessionId, nextSurfaces, nextTurns, nextLibrary) {
      const changedSession = sessionId !== session;
      session = sessionId || ''; surfaces = nextSurfaces || []; turns = nextTurns || []; library = nextLibrary || [];
      collect();
      if (changedSession) resetSubjects();
      seedSubjects();
      if (page) {
        // A page shows one lineage: on as soon as its chat is here, loaded as
        // soon as the arrangement is, following later revisions as they land.
        active = Boolean(session); railOpen = false; currentLineage = page;
        if (!arrangements.has(page)) { draft = null; base = null; }
        else if (!base || base.lineage_key !== page) loadDraft(arrangements.get(page));
        else syncDraftToSaved();
        document.getElementById('stage-rail')?.toggleAttribute('data-arrange', active);
        paintControl();
        return;
      }
      if (changedSession) {
        const saved = recall();
        groupBy = saved.group === 'turn' ? 'turn' : 'subject';
        railOpen = saved.rail !== false; currentLineage = saved.lineage && arrangements.has(saved.lineage) ? saved.lineage : null;
        draft = null; base = null; dirty = false; past = []; future = []; selected.clear();
        // A link names one arrangement; a stale or foreign id opens nothing.
        // Surfaces can arrive after the first update, so a choice that is not
        // loadable yet waits for them instead of being dropped.
        wanted = saved.active && saved.lineage && !arrangements.has(saved.lineage) ? saved : null;
        active = Boolean(saved.active) && Boolean(session) && !wanted && (!saved.fromUrl || Boolean(currentLineage));
        if (active && !currentLineage) currentLineage = [...arrangements.values()].sort(newer)[0]?.lineage_key || null;
        if (active) loadDraft(currentLineage ? arrangements.get(currentLineage) : null);
        document.getElementById('stage-rail')?.toggleAttribute('data-arrange', active);
      } else if (wanted) {
        if (arrangements.has(wanted.lineage)) {
          currentLineage = wanted.lineage; wanted = null; active = true;
          loadDraft(arrangements.get(currentLineage));
          document.getElementById('stage-rail')?.toggleAttribute('data-arrange', true);
          onChange?.('mode');
        } else if (arrangements.size) {
          wanted = null; // this chat's arrangements are here and the wanted one is not
        }
      } else {
        syncDraftToSaved();
      }
      paintControl();
      syncSubjects();
    }
    const markup = () => '<div data-stage-arrange-slot data-stage-key="arrange"></div>';
    function mounted() { if (active) paint(); }
    // The live instance is reachable for tests and the console.
    const instance = {update, markup, mounted, setActive, addSurface, addStat, addValue, setSlice, setVersion, choose, startNew,
      values: {get catalog() { return valueCatalog; }, get data() { return valueData; }, key: valueKey,
        open: slug => { dashOpen.add(slug); dashShut.delete(slug); paintRail(); }, get opened() { return [...dashOpen]; }},
      get cells() { return draft ? draft.cells.map(c => ({...c})) : []; },
      get page() { return page; },
      setCutoff, restore: restoreRevision, preview: id => previewSurface(id, {now: true}), previewValue: key => previewValue(key, {now: true}), get previewing() { return preview.hidden ? null : previewId; }, get viewing() { return viewing ? {surface_id: viewing.surface.id, at: viewing.at, title: viewing.payload.title, cells: (viewing.payload.cells || []).length} : null; },
      // What the Stage's history slider shows while arranging: one stop per
      // grouped edit of the open arrangement, described in plain words.
      get timeline() {
        const groups = historyGroups();
        const byId = new Map(groups.map(g => [g.last.id, g]));
        return {source: `arrangement:${currentLineage || ''}`, items: groups.map(g => g.last),
          describe: event => describeEvent(byId.get(event.items?.[0]?.id)),
          mark: event => (byId.get(event.items?.[0]?.id)?.actor || '') !== 'Callie' ? false : true,
          groups: groups.map(g => ({actor: g.actor, saves: g.members.length, at: g.last.created_at, change: describeChange(g.before?.payload, g.last.payload)}))};
      },
      get active() { return active; }, get current() { return draft ? {lineage: currentLineage, title: draft.title, cells: cells().length} : null; },
      get hasArrangements() { return arrangements.size > 0; },
      get history() { return {past: past.length, future: future.length, dirty, saving: Boolean(saving)}; },
      subjects: {get groupBy() { return groupBy; }, setGroupBy, get map() { return subjectMap; }, get list() { return subjectList; },
        get spend() { return subjectSpend; }, get pending() { return subjectPending; }, get busy() { return Boolean(subjectBusy); },
        correct: (surfaceId, label) => correctSubjects({surface_id: surfaceId, label}),
        rename: (from, to) => correctSubjects({rename: {from, to}})}};
    window.CalliopeStageArrange.current = instance;
    return instance;
  }
  window.CalliopeStageArrange = {create, UNIT, MEMBER_KINDS, describeChange};
})();
