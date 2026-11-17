/* Floor's overview owns selection and navigation; cards are shared with Gallery. */
(() => {
  'use strict';
  window.CalliopeFloorMetrics = {create({api, isHistorical, returnLive, redrawHistory, openMetric, openArea}) {
    const $ = id => document.getElementById(id);
    const track = $('floorMetricsTrack');
    let metrics = [], mode = 'history', preference = null, hasHistory = false;
    // Areas of the business (what Meaning found): the third mode of this rail. A tile
    // opens a chat with the orientation board already on the Stage and Callie building
    // the first data board — the bread-board ladder (docs/MEANING_PLAN.md §3).
    let areas = [];
    let owner = null, loadedAt = 0, request = 0, loading = false, switching = false, launching = false;
    let pageSize = 1, unavailable = false;
    const periods = [
      {id:'current',label:'Readings',ticks:24}, {id:'day',label:'Daily'},
      {id:'week',label:'Weekly'}, {id:'month',label:'Monthly'},
      {id:'quarter',label:'Quarterly'},
    ];
    let period = 'week', pendingPeriod = null;
    const periodKey = () => 'calliope.floor.metric-period.v1:' + owner;
    const periodInfo = id => periods.find(item => item.id === id) || periods[2];
    const nextPeriod = () => periods[(periods.findIndex(item => item.id === period) + 1) % periods.length];
    const preferenceKey = () => 'calliope.floor.overview.v1:' + owner;
    function readPreference() {
      try { const value = localStorage.getItem(preferenceKey()); return ['metrics', 'history', 'areas'].includes(value) ? value : null; }
      catch (_) { return null; }
    }
    const trackCount = () => mode === 'areas' ? areas.length : metrics.length;
    // Areas is the default first screen; a person's own choice (Metrics or History) is
    // remembered in their browser and wins on every later visit.
    function initialMode() {
      if (preference === 'areas' && areas.length) return 'areas';
      if (preference === 'metrics' && metrics.length) return 'metrics';
      if (preference === 'history' && hasHistory) return 'history';
      return areas.length ? 'areas' : metrics.length ? 'metrics' : 'history';
    }
    function paging() {
      const card = track.firstElementChild;
      if (!card || !track.clientWidth) return;
      const step = card.getBoundingClientRect().width + 10;
      const total = trackCount();
      const first = Math.min(Math.max(0, total - pageSize), Math.round(track.scrollLeft / step));
      $('floorMetricPosition').textContent = `${first + 1}–${Math.min(total, first + pageSize)} / ${total}`;
      $('floorMetricsPrev').disabled = track.scrollLeft < 2;
      $('floorMetricsNext').disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 2;
      $('floorMetricPaging').hidden = !['metrics', 'areas'].includes(mode) || total <= pageSize;
    }
    function fit() {
      if (!track.clientWidth) return;
      pageSize = Math.max(1, Math.floor((track.clientWidth + 10) / 270));
      track.style.setProperty('--metric-card-width', `${(track.clientWidth - (pageSize - 1) * 10) / pageSize}px`);
      paging();
    }
    function render() {
      const showingMetrics = mode === 'metrics' && metrics.length > 0;
      const showingAreas = mode === 'areas' && areas.length > 0;
      const modesAvailable = (metrics.length ? 1 : 0) + (hasHistory ? 1 : 0) + (areas.length ? 1 : 0);
      $('floorOverview').hidden = !metrics.length && !hasHistory && !areas.length;
      $('floorMetricsPanel').hidden = !showingMetrics && !showingAreas;
      track.classList.toggle('is-areas', showingAreas);
      track.setAttribute('aria-label', showingAreas ? 'Areas of the business' : 'Core metrics');
      $('timebar').hidden = showingMetrics || showingAreas || !hasHistory;
      $('floorOverviewSwitch').hidden = modesAvailable < 2;
      const areasBtn = $('floorAreasBtn');
      if (areasBtn) areasBtn.hidden = !areas.length;
      const hint = $('floorAreasHint');
      if (hint) hint.hidden = !areas.length || showingAreas;
      $('floorOverviewTitle').textContent = showingAreas ? 'Areas of your business' : showingMetrics ? 'Core metrics' : 'Work history';
      const selected = periodInfo(period);
      $('floorOverviewHint').textContent = showingAreas ? `${areas.length} areas Calliope found in your data · pick one and she starts there` : showingMetrics ? (selected.ticks ? 'Latest 24 readings · headline period shown on each tile' : `${selected.label} snapshots · latest reading per ${selected.id}, not ${selected.id} totals`) + (unavailable ? ' · refresh unavailable' : '') : unavailable ? 'Metrics unavailable · showing work history' : 'Explore how the Floor changed over time';
      const cycle = $('floorMetricPeriod');
      cycle.hidden = !showingMetrics;
      cycle.textContent = `↻ ${periodInfo(pendingPeriod || period).label}${pendingPeriod ? '…' : ''}`;
      cycle.setAttribute('aria-label', `Metric period: ${selected.label}. Switch to ${nextPeriod().label}`);
      cycle.title = `Chart grain · ${selected.label}. Up to 12 calendar periods including the current partial period. Click for ${nextPeriod().label}. Headline values keep their own measurement period.`;
      cycle.disabled = loading || switching;
      for (const [id, active] of [['floorMetricsBtn', showingMetrics], ['floorHistoryBtn', !showingMetrics && !showingAreas], ['floorAreasBtn', showingAreas]]) {
        const button = $(id);
        if (!button) continue;
        button.classList.toggle('on', active);
        button.setAttribute('aria-pressed', String(active));
        button.disabled = switching;
      }
      $('floorMetricPaging').hidden = !(showingMetrics || showingAreas) || trackCount() <= pageSize;
      if (showingMetrics || showingAreas) requestAnimationFrame(fit);
    }
    async function setMode(next, remember = false) {
      if (switching) return;
      if (next === 'metrics' && !metrics.length) next = 'history';
      if (next === 'areas' && !areas.length) next = metrics.length ? 'metrics' : 'history';
      switching = true; render();
      try {
        // Never hide an AS-OF board behind current business metrics.
        if (['metrics', 'areas'].includes(next) && isHistorical()) await returnLive();
        const repaint = next !== mode && ['metrics', 'areas'].includes(next);
        mode = next;
        if (repaint) paintTrack();
        if (remember) {
          preference = next;
          try { localStorage.setItem(preferenceKey(), next); } catch (_) {}
        }
      } catch (_) {
        $('floorMetricStatus').textContent = 'Could not return to the live Floor. Try again.';
      } finally {
        switching = false; render();
        if (mode === 'history') requestAnimationFrame(redrawHistory);
      }
    }
    const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
    const compact = n => n == null ? '' : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n);
    function areaTileMarkup(area) {
      const nouns = Array.isArray(area.nouns) ? area.nouns.slice(0, 3) : [];
      const question = Array.isArray(area.questions) && area.questions.length ? String(area.questions[0]) : '';
      const meta = [area.tables ? `${area.tables} tables` : '', area.rows ? `${compact(Number(area.rows))} rows` : '', area.metrics ? `${area.metrics} metrics` : ''].filter(Boolean);
      return `<button type="button" class="floor-area-tile" data-area-open="${esc(area.key)}" title="Start a chat on ${esc(area.name)} — the orientation board is on the Stage and Calliope builds the first data board" aria-label="Start with ${esc(area.name)}">`
        + `<strong class="area-name">${esc(area.name)}</strong>`
        + `<span class="area-about">${esc(area.about || '')}</span>`
        + (nouns.length ? `<span class="area-nouns">${nouns.map(n => `<span>${esc(n)}</span>`).join('')}</span>` : '')
        + (question ? `<span class="area-question">“${esc(question)}”</span>` : '')
        + `<span class="area-foot"><span class="area-meta">${meta.map(item => `<span>${esc(item)}</span>`).join('')}</span><span class="area-go">Start here&nbsp;→</span></span></button>`;
    }
    function paintTrack() {
      const left = track.scrollLeft;
      track.innerHTML = mode === 'areas'
        ? areas.map(areaTileMarkup).join('')
        : metrics.map(metric => window.CalliopeMetricCards.cardMarkup(metric, {
          openLabel: `Talk to Calliope about ${metric.title || metric.name}`,
          rangeDays: 7, smooth: true, floor: true,
        })).join('');
      track.scrollLeft = left;
      fit();
    }
    function paintCards() { if (mode !== 'areas') paintTrack(); }
    async function refreshAreas() {
      try {
        const data = await api('/api/calliope/floor/areas');
        const next = (Array.isArray(data.areas) ? data.areas : []).filter(area => area && area.key && area.name);
        const changed = JSON.stringify(next) !== JSON.stringify(areas);
        areas = next;
        if (changed && mode === 'areas') paintTrack();
        render();
      } catch (_) { /* a Floor without Meaning has no areas; nothing to show */ }
    }
    function drift(metric) {
      const value = metric.trend?.percent;
      // Percentage changes are comparable across counts, money, and rates.
      // A zero baseline has no percentage drift; do not invent one.
      return value == null || value === '' || !Number.isFinite(Number(value)) ? -1 : Math.abs(Number(value));
    }
    async function refresh(viewer, force = false, selectedPeriod = null) {
      const identity = String(viewer?.email || '').trim().toLowerCase();
      if (!identity) return;
      if (owner !== identity) {
        owner = identity; preference = readPreference(); metrics = []; loadedAt = 0;
        period = 'week'; pendingPeriod = null;
        try { const saved = localStorage.getItem(periodKey()); if (periods.some(item => item.id === saved)) period = saved; } catch (_) {}
        loading = false; request++; mode = 'history'; track.innerHTML = ''; render();
      }
      if (loading || (!force && Date.now() - loadedAt < 60000)) return;
      loading = true; const token = ++request, target = periodInfo(selectedPeriod || period);
      pendingPeriod = selectedPeriod; render();
      const areasLoad = refreshAreas();
      try {
        const data = await api('/api/gallery/metrics?category=Core&limit=600&' + (target.ticks ? `ticks=${target.ticks}` : `floor_period=${target.id}`));
        if (token !== request) return;
        const next = (Array.isArray(data.metrics) ? data.metrics : [])
          .filter(metric => String(metric.category || '').trim().toLowerCase() === 'core')
          .sort((a, b) => drift(b) - drift(a) || String(a.name).localeCompare(String(b.name)));
        const changed = period !== target.id || JSON.stringify(metrics) !== JSON.stringify(next);
        period = target.id;
        if (selectedPeriod) { try { localStorage.setItem(periodKey(), period); } catch (_) {} }
        metrics = next; unavailable = false;
        $('floorMetricStatus').textContent = '';
        if (changed) paintCards();
        await areasLoad;
        await setMode(initialMode());
      } catch (_) {
        if (token !== request) return;
        unavailable = true; // Keep previously loaded cards on a transient failure.
        if (selectedPeriod) $('floorMetricStatus').textContent = `Could not load ${target.label} metrics. Still showing ${periodInfo(period).label}.`;
        render();
      } finally {
        if (token === request) { loading = false; pendingPeriod = null; loadedAt = Date.now(); render(); }
      }
    }
    function scroll(direction) {
      track.scrollBy({left: direction * (track.clientWidth + 10), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    }
    $('floorMetricsPrev').addEventListener('click', () => scroll(-1));
    $('floorMetricsNext').addEventListener('click', () => scroll(1));
    $('floorMetricsBtn').addEventListener('click', () => setMode('metrics', true));
    $('floorHistoryBtn').addEventListener('click', () => setMode('history', true));
    $('floorAreasBtn')?.addEventListener('click', () => setMode('areas', true));
    $('floorAreasHintBtn')?.addEventListener('click', async () => {
      await setMode('areas', true);
      $('floorOverview')?.scrollIntoView({block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    });
    $('floorMetricPeriod').addEventListener('click', () => refresh({email:owner}, true, nextPeriod().id));
    track.addEventListener('scroll', paging, {passive: true});
    track.addEventListener('keydown', event => {
      if (event.target !== track || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault(); scroll(event.key === 'ArrowLeft' ? -1 : 1);
    });
    track.addEventListener('click', async event => {
      const areaButton = event.target.closest('[data-area-open]');
      if (areaButton) {
        const area = areas.find(item => item.key === areaButton.dataset.areaOpen);
        if (!area || launching) return;
        launching = true; areaButton.disabled = true; areaButton.setAttribute('aria-busy', 'true');
        $('floorMetricStatus').textContent = `Opening ${area.name} with its orientation board…`;
        try { if (await openArea(area) === false) throw new Error('Area handoff failed'); }
        catch (_) { $('floorMetricStatus').textContent = 'That area could not be opened. Please try again.'; }
        finally { launching = false; areaButton.disabled = false; areaButton.removeAttribute('aria-busy'); }
        return;
      }
      const button = event.target.closest('[data-metric-open]');
      const metric = button && metrics.find(item => item.name === button.dataset.metricOpen);
      if (!metric || launching) return;
      launching = true; button.disabled = true; button.setAttribute('aria-busy', 'true');
      $('floorMetricStatus').textContent = `Opening ${metric.title || metric.name} with its observations…`;
      try { if (await openMetric(metric) === false) throw new Error('Metric handoff failed'); }
      catch (_) { $('floorMetricStatus').textContent = 'This metric could not be opened. Please try again.'; }
      finally { launching = false; button.disabled = false; button.removeAttribute('aria-busy'); }
    });
    new ResizeObserver(fit).observe(track);
    return {get mode() {return mode;}, refresh, updateHistory(available) {hasHistory = available; render();}};
  }};
})();
