(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let agents = [], selected = null, loading = false;
  const label = value => String(value || '').replace(/[_-]+/g, ' ');
  const count = value => new Intl.NumberFormat().format(Number(value) || 0);
  let previewController = null, previewContext = null, previewTimer = null;
  function stopPreview() {
    previewController?.abort(); previewController = null;
    clearTimeout(previewTimer); previewTimer = null;
    if (previewContext) void previewContext.close().catch(() => {});
    previewContext = null;
  }
  async function previewVoice() {
    const button = $('memberVoice'), status = $('memberVoiceStatus');
    const reset = () => { button.title = ''; button.textContent = '♫ Test voice'; button.setAttribute('aria-label', `Test ${agents.find(a => a.key === selected)?.name || 'agent'}'s voice`); };
    if (previewController) { stopPreview(); reset(); status.textContent = ''; return; }
    stopPreview();
    const key = selected, controller = new AbortController(); previewController = controller;
    button.textContent = '◌ Preparing voice…'; button.setAttribute('aria-label', 'Cancel voice preview'); status.textContent = 'Preparing voice preview…';
    try {
      // Create and resume on the click gesture, before fetching, just like chat playback.
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) throw new Error('Audio playback is unavailable in this browser.');
      const context = new AudioContextClass({latencyHint:'interactive'}); previewContext = context;
      await context.resume();
      if (controller.signal.aborted) return;
      const response = await fetch(`/api/calliope/team/${encodeURIComponent(key)}/voice`, {method:'POST', signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({personality:$('memberPersona').value})});
      if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.error?.message || 'Voice preview is unavailable.'); }
      const rate = Number(response.headers.get('x-audio-sample-rate'));
      if (response.headers.get('x-calliope-audio-protocol') !== 'timed-pcm-ndjson-v1' || !Number.isInteger(rate) || rate < 8000 || rate > 192000 || !response.body) throw new Error('The voice preview returned an unsupported audio stream.');
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let pending = '', nextAt = context.currentTime, samples = 0;
      const frame = line => {
        if (!line.trim() || controller.signal.aborted) return;
        const data = JSON.parse(line);
        if (data.type === 'error') throw new Error(data.error?.message || data.message || 'Voice playback was interrupted.');
        if (data.type !== 'audio' || !data.audio_base64) return;
        const raw = atob(data.audio_base64), length = raw.length / 2;
        if (!Number.isInteger(length) || !length) throw new Error('The voice preview returned incomplete audio.');
        samples += length;
        if (samples > rate * 30) throw new Error('Voice preview exceeded its duration limit.');
        const buffer = context.createBuffer(1, length, rate), channel = buffer.getChannelData(0);
        for (let i = 0; i < length; i++) { const value = raw.charCodeAt(i*2) | raw.charCodeAt(i*2+1) << 8; channel[i] = (value >= 32768 ? value - 65536 : value) / 32768; }
        const source = context.createBufferSource(); source.buffer = buffer; source.connect(context.destination);
        source.onended = () => source.disconnect();
        nextAt = Math.max(nextAt, context.currentTime + .03); source.start(nextAt); nextAt += buffer.duration;
        button.textContent = '■ Stop voice'; button.setAttribute('aria-label', 'Stop voice preview'); status.textContent = 'Playing…';
      };
      try {
        while (!controller.signal.aborted) {
          const {value, done} = await reader.read();
          pending += decoder.decode(value, {stream:!done});
          const lines = pending.split('\n'); pending = lines.pop(); lines.forEach(frame);
          if (done) { frame(pending); break; }
        }
      } finally { reader.releaseLock(); }
      if (controller.signal.aborted || selected !== key) return;
      if (!samples) throw new Error('The speech provider returned no playable audio.');
      previewTimer = setTimeout(() => {
        if (previewController !== controller) return;
        stopPreview(); reset(); status.textContent = '';
      }, Math.max(0, (nextAt - context.currentTime) * 1000) + 80);
    } catch (error) {
      if (controller.signal.aborted || selected !== key) return;
      stopPreview(); status.textContent = error.message; reset(); button.textContent = '↻ Retry voice'; button.title = error.message;
    }
  }
  async function loadSpeechPersona(key) {
    const input = $('memberPersona'), save = $('memberPersonaSave'), note = $('memberPersonaNote');
    input.disabled = true; save.disabled = true; $('memberVoice').disabled = true;
    try {
      const response = await fetch(`/api/calliope/team/${encodeURIComponent(key)}/speech-persona`, {cache:'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || 'Could not load your speech persona.');
      if (!input.isConnected || selected !== key) return;
      input.value = data.personality || ''; input.dataset.defaultPersonality = data.default_personality || ''; $('memberPersonaReset').disabled = false; input.disabled = false; save.disabled = false; $('memberVoice').disabled = false;
      note.textContent = data.source === 'custom' ? 'Your personal override. Test voice auditions your current draft.' : 'Profile default. Edit to make it your own; Test voice auditions your draft.';
    } catch (error) {
      if (!input.isConnected || selected !== key) return;
      note.textContent = error.message;
    }
  }
  async function saveSpeechPersona(reset = false) {
    const key = selected, input = $('memberPersona'), save = $('memberPersonaSave'), note = $('memberPersonaNote');
    const draft = input.value; save.disabled = true; $('memberPersonaReset').disabled = true;
    try {
      const response = await fetch(`/api/calliope/team/${encodeURIComponent(key)}/speech-persona`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({personality:reset ? '' : draft})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || 'Could not save your speech persona.');
      if (key === 'calliope') await window.WarehouseTheme?.refreshVoice?.();
      if (!input.isConnected || selected !== key) return;
      if (input.value === draft) { input.value = data.personality || ''; note.textContent = data.source === 'profile' ? 'Using the profile default. Future profile updates will apply automatically.' : 'Saved for your conversations with this agent.'; }
      else note.textContent = 'Saved. Your latest edits are still a draft.';
    } catch (error) { if (input.isConnected) note.textContent = error.message; }
    finally { if (save.isConnected) { save.disabled = false; $('memberPersonaReset').disabled = false; } }
  }
  const signatureFonts = new Set(['Homemade Apple']);
  function applySignatures(container) {
    for (const element of container.querySelectorAll('[data-agent-signature]')) {
      const agent = agents.find(a => a.key === element.dataset.agentSignature);
      const day = new Date().getHours() >= 7 && new Date().getHours() < 19;
      const color = (day ? agent?.avatar_day_color : agent?.avatar_night_color) || agent?.avatar_color;
      const surface = element.closest('.member-tile') || container;
      surface.style.removeProperty('--agent-accent');
      if (/^#[0-9a-f]{6}$/i.test(color || '')) surface.style.setProperty('--agent-accent', color);
      const family = String(agent?.signature_font || (agent?.key === 'calliope' ? 'Homemade Apple' : '')).trim();
      if (!/^[A-Za-z0-9][A-Za-z0-9 .&+\-]{0,79}$/.test(family)) continue;
      if (!signatureFonts.has(family)) {
        signatureFonts.add(family);
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        const variant = family === 'Molle' ? ':ital@1' : family === 'UnifrakturCook' ? ':wght@700' : '';
        link.href = `https://fonts.googleapis.com/css2?family=${family.split(/\s+/).map(encodeURIComponent).join('+')}${variant}&display=swap`;
        link.dataset.teamSignatureFont = family;
        document.head.append(link);
      }
      element.style.fontFamily = `"${family}", var(--serif)`;
      element.style.fontStyle = family === 'Molle' ? 'italic' : 'normal';
      element.style.fontWeight = '700';
    }
  }
  function image(agent) {
    const day = new Date().getHours() >= 7 && new Date().getHours() < 19;
    const raw = (day ? agent.avatar_day_url : agent.avatar_night_url) || agent.avatar_url || agent.avatar_day_url;
    if (!raw) return '';
    try { const url = new URL(raw, location.origin); return url.origin === location.origin || url.protocol === 'https:' ? url.href : ''; } catch { return ''; }
  }
  function portrait(agent, tile = false) {
    const src = image(agent), initials = agent.name.split(/\s+/).map(w => w[0]).slice(0, 2).join('');
    return `<div class="portrait"><span class="portrait-initials" aria-hidden="true">${esc(initials)}</span>${src ? `<img src="${esc(src)}" alt="" loading="lazy">` : ''}${tile ? `<span class="member-badge ${agent.activity?.active ? 'busy' : ''}">${agent.activity?.active ? 'Working' : agent.primary ? 'Your coordinator' : 'Specialist'}</span><span class="portrait-copy"><strong data-agent-signature="${esc(agent.key)}">${esc(agent.name)}</strong><small>${esc(agent.role_label || 'Specialist')}</small></span>` : ''}</div>`;
  }
  function render() {
    const query = $('teamSearch').value.trim().toLowerCase();
    const shown = agents.filter(a => [a.name,a.role_label,a.description,...(a.capabilities || [])].join(' ').toLowerCase().includes(query));
    $('teamGrid').innerHTML = shown.map(a => `<button type="button" class="member-tile" data-member="${esc(a.key)}" aria-label="View ${esc(a.name)}" aria-haspopup="dialog">${portrait(a,true)}<span class="tile-footer"><span>${a.activity ? `<b>${count(a.activity.requests)}</b> requests · <b>${count(a.activity.assignments)}</b> assignments` : 'Activity unavailable'}</span><span aria-hidden="true">↗</span></span></button>`).join('');
    applySignatures($('teamGrid'));
    $('teamSummary').textContent = `${agents.length} team ${agents.length === 1 ? 'member' : 'members'}\n${agents.some(a => !a.activity) ? 'Activity unavailable' : `${agents.filter(a => a.activity?.active).length} working · visible activity`}`;
    $('teamStatus').textContent = !agents.length ? 'No active team members are configured.' : !shown.length ? 'No members match that name or specialty.' : agents.some(a => !a.activity) ? 'The roster is available, but activity could not be loaded. Try Refresh.' : '';
    $('teamStatus').hidden = !$('teamStatus').textContent;
  }
  function toolChart(agent) {
    if (!agent.tools) return '<p class="empty-copy">Tool activity is temporarily unavailable.</p>';
    const tools = agent.tools.top || [];
    if (!tools.length) return '<p class="empty-copy">No recorded tool calls in work you can access during this period.</p>';
    const max = Math.max(...tools.map(t => Number(t.calls) || 0), 1);
    return `<p class="scope-note">${count(agent.tools.total)} calls across eligible tools</p><ol class="tool-chart" aria-label="Most-used tools, ranked by call count">${tools.map((tool,index) => {
      const full = String(tool.name || 'Unknown tool');
      const short = full.startsWith('mcp__') ? full.split('__').slice(2).join('__') || full : full;
      const width = Math.max(0,Math.min(100,(Number(tool.calls)||0)/max*100));
      return `<li class="tool-row" style="--tool-color:var(--chart-${index%5+1},var(--accent))"><div class="tool-label"><span title="${esc(full)}">${esc(short)}</span><b>${count(tool.calls)}<span class="sr-only"> calls</span></b></div><div class="tool-track" aria-hidden="true"><span style="width:${width}%"></span></div></li>`;
    }).join('')}</ol><p class="scope-note">Excludes tool_call and todo. Includes failed attempts; mirrored activity may lag.</p>`;
  }
  function details(key) {
    const a = agents.find(a => a.key === key); if (!a) return;
    stopPreview();
    selected = key;
    const stats = a.activity, recent = stats?.recent || [];
    const talk = `/calliope?view=zen&start=1&agent=${encodeURIComponent(a.key)}`;
    const statsMarkup = stats ? `<div class="member-stats"><div><strong>${count(stats.requests)}</strong><span>Requests</span></div><div><strong>${count(stats.assignments)}</strong><span>Assignments</span></div><div><strong>${count(stats.active)}</strong><span>Queued / running</span></div></div><p class="scope-note">Conversation turns and specialist runs, not model API calls.</p>` : '<p class="empty-copy">Activity is temporarily unavailable.</p>';
    const recentMarkup = recent.length ? `<ul class="recent-list">${recent.map(r => {
      const title = esc(r.title || 'Specialist assignment'), date = new Date(r.created_at);
      return `<li>${r.session_id ? `<a href="/calliope?view=zen&session=${encodeURIComponent(r.session_id)}">${title} ↗</a>` : `<span>${title}</span>`}<small>${esc(label(r.kind))} · ${esc(label(r.status))} · ${esc(Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined,{month:'short',day:'numeric'}))}</small></li>`;
    }).join('')}</ul>` : '<p class="empty-copy">No visible activity in this period. Start a conversation to work together.</p>';
    $('memberContent').innerHTML = `
      <div class="member-hero">${portrait(a)}<div class="member-intro">
        <div class="eyebrow">${esc(a.role_label || 'Specialist')}</div>
        <h2 id="memberName" data-agent-signature="${esc(a.key)}">${esc(a.name)}</h2>
        <p>${esc(a.description || 'An active member of your team.')}</p>
        <div class="member-actions"><a class="talk-link" href="${esc(talk)}">Talk to ${esc(a.name)} ↗</a>
        <button type="button" class="voice-preview" id="memberVoice" aria-label="Test ${esc(a.name)}'s voice">♫ Test voice</button></div>
        <p class="scope-note">${a.voice_id ? 'Character voice' : 'Using Calliope’s voice until one is assigned'}</p>
        <p id="memberVoiceStatus" class="sr-only" role="status"></p>
      </div></div>
      <div class="member-tabs" role="tablist" aria-label="Team member details">
        <button id="memberOverviewTab" role="tab" aria-selected="true" aria-controls="memberOverviewPanel">Overview</button>
        <button id="memberPersonaTab" role="tab" aria-selected="false" aria-controls="memberPersonaPanel" tabindex="-1">Voice & personality</button>
      </div>
      <div class="member-body">
       <div id="memberOverviewPanel" role="tabpanel" aria-labelledby="memberOverviewTab" tabindex="0">
        <p class="member-scope">Past 30 days · your work and shared notebooks you can access</p>
        <div class="member-columns">
          <div class="member-overview">
            <section aria-labelledby="memberActivity"><h3 id="memberActivity">Activity</h3>${statsMarkup}</section>
            <section aria-labelledby="memberSpecialties"><h3 id="memberSpecialties">Specialties</h3><div class="capabilities">${(a.capabilities || []).map(c => `<span>${esc(label(c))}</span>`).join('') || '<span>General assistance</span>'}</div></section>
            <section aria-labelledby="memberRecent"><h3 id="memberRecent">Recent work</h3>${recentMarkup}</section>
          </div>
          <section class="member-tools" aria-labelledby="memberTools"><h3 id="memberTools">Most-used tools</h3>${toolChart(a)}</section>
        </div>
       </div>
       <div id="memberPersonaPanel" class="member-persona" role="tabpanel" aria-labelledby="memberPersonaTab" hidden>
          <label for="memberPersona">How ${esc(a.name)} speaks with you</label>
          <p class="scope-note">Start with the profile personality, or shape a voice that's yours. Applies to expressive speech in your conversations with this agent.</p>
          <textarea id="memberPersona" maxlength="600" rows="6" placeholder="Tone, personality, pacing, humor… Blank restores the profile default." disabled></textarea>
          <div class="persona-actions"><button id="memberPersonaSave" type="button" disabled>Save persona</button><button id="memberPersonaReset" type="button" disabled>Use profile default</button><span>600 characters max</span></div>
          <p id="memberPersonaNote" role="status">Loading your speech persona…</p>
       </div>
      </div>`;
    applySignatures($('memberContent'));
    $('memberVoice').addEventListener('click', previewVoice);
    $('memberPersonaSave').addEventListener('click', () => saveSpeechPersona());
    $('memberPersonaReset').addEventListener('click', () => saveSpeechPersona(true));
    const tabs = [$('memberOverviewTab'), $('memberPersonaTab')];
    const selectTab = tab => {
      for (const item of tabs) {
        const active = item === tab;
        item.setAttribute('aria-selected', String(active)); item.tabIndex = active ? 0 : -1;
        $(item.getAttribute('aria-controls')).hidden = !active;
      }
      document.querySelector('.member-body').scrollTop = 0;
    };
    for (const tab of tabs) {
      tab.addEventListener('click', () => selectTab(tab));
      tab.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[1] : tabs[1 - tabs.indexOf(tab)];
        selectTab(next); next.focus();
      });
    }
    $('memberPersona').addEventListener('input', () => { $('memberPersonaNote').textContent = 'Unsaved draft. Test voice to audition, or Save persona to use in chat.'; });
    void loadSpeechPersona(key);
    if (!$('memberDialog').open) $('memberDialog').showModal();
  }
  async function load() {
    if (loading) return; loading = true; $('teamRefresh').disabled = true;
    try {
      const response = await fetch('/api/calliope/team', {headers:{Accept:'application/json'}});
      if (!response.ok) throw new Error(response.status === 401 ? 'Your session expired. Sign in again to see the team.' : 'The team could not be loaded. Try Refresh.');
      const data = await response.json();
      agents = (data.agents || []).filter(a => a.key && a.name).sort((a,b) => Number(!!b.primary)-Number(!!a.primary) || a.name.localeCompare(b.name));
      render(); if ($('memberDialog').open) { if (!agents.some(a => a.key === selected)) $('memberDialog').close(); }
    } catch (error) { $('teamStatus').hidden = false; $('teamStatus').textContent = error.message; }
    finally { loading = false; $('teamRefresh').disabled = false; }
  }
  $('teamSearch').addEventListener('input', render);
  $('teamRefresh').addEventListener('click', load);
  $('teamGrid').addEventListener('click', e => { const card = e.target.closest('[data-member]'); if (card) details(card.dataset.member); });
  $('memberDialog').addEventListener('close', stopPreview);
  $('memberClose').addEventListener('click', () => $('memberDialog').close());
  $('memberDialog').addEventListener('click', e => { if (e.target === $('memberDialog')) { const r = e.target.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) e.target.close(); } });
  document.addEventListener('error', e => { if (e.target.matches?.('.portrait img')) e.target.remove(); }, true);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  setInterval(() => { if (!document.hidden && !$('memberDialog').open && !document.activeElement?.closest('#teamGrid')) load(); },60000);
  load();
})();
