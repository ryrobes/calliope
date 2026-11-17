/* One installation computer, retained independently of the selected thread. */
(() => {
  const BASE = '/api/calliope/computer';
  const PREF = 'calliope-computer-visible';
  const MODE_PREF = 'calliope-computer-mode';
  const VIEW_PREF = 'calliope-computer-tab';
  const element = (tag, text, attrs = {}) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    Object.assign(node, attrs);
    return node;
  };
  window.CalliopeComputer = class {
    static viewerURL(state, readOnly = false) {
      const url = new URL(state.viewer_url, location.origin);
      url.search = new URLSearchParams({path: 'api/calliope/computer/stream' + (readOnly ? '?view_only=1' : ''),
        resize: 'scale', autoconnect: 'true', reconnect: 'true', reconnect_delay: '1000'});
      return url.href;
    }
    constructor({host, button, api, fetch, toast, getSession, navigate, stageSlot, miniSlot, onModeChange, workspace = false, onActivity, publishSnapshot, canSnapshot, isWorking}) {
      Object.assign(this, {host, button, api, fetch, toast, getSession, navigate, stageSlot, miniSlot, onModeChange, workspace, onActivity, publishSnapshot, canSnapshot, isWorking});
      this.mode = 'hidden';
      try {
        const saved = localStorage.getItem(MODE_PREF);
        this.mode = ['hidden', 'small', 'stage'].includes(saved) ? saved : localStorage.getItem(PREF) === '1' ? 'stage' : 'hidden';
      } catch {}
      if (this.workspace) this.mode = 'stage';
      this.visible = !this.workspace && this.mode !== 'hidden';
      this.lastVisibleMode = this.visible ? this.mode : 'small';
      this.header = element('header');
      this.title = element('strong', 'Computer');
      this.status = element('span', 'Connecting…', {className: 'computer-status'});
      this.status.setAttribute('role', 'status');
      this.context = element('button', '', {type: 'button', className: 'computer-context', hidden: true});
      this.context.onclick = () => this.navigate(this.snapshot?.active_run?.context?.session_id);
      this.takeover = this.action('Take over', () => this.control(this.snapshot?.you_are_driving ? 'agent' : 'human'));
      this.view = 'history';
      try { const saved = localStorage.getItem(VIEW_PREF); if (['terminal', 'history'].includes(saved)) this.view = saved; } catch {}
      this.stop = this.action('Stop', () => this.control('finish'));
      this.tabs = element('div', undefined, {className: 'computer-tabs'});
      this.tabs.setAttribute('role', 'tablist'); this.tabs.setAttribute('aria-label', 'Below the desktop');
      this.tabButtons = {};
      const names = ['history', 'terminal'];
      for (const name of names) {
        const tab = this.action(name[0].toUpperCase() + name.slice(1), () => this.selectView(name));
        tab.id = `computer-tab-${name}`; tab.setAttribute('role', 'tab');
        tab.setAttribute('aria-controls', `computer-panel-${name}`);
        tab.onkeydown = event => {
          const index = names.indexOf(name);
          const next = ['ArrowRight', 'ArrowLeft'].includes(event.key) ? names[(index + 1) % names.length]
            : event.key === 'Home' ? names[0] : event.key === 'End' ? names.at(-1) : null;
          if (next) { event.preventDefault(); this.tabButtons[next].focus(); this.selectView(next).catch(error => this.toast(error.message, true)); }
        };
        this.tabButtons[name] = tab; this.tabs.append(tab);
      }
      this.unseen = {};
      this.sizeButton = this.action('Small', () => this.setMode(this.mode === 'small' ? 'stage' : 'small'));
      this.expand = this.action('Full screen', () => {
        if (this.mode === 'small') {
          this.selectView('desktop');
          this.setMode('stage');
        }
        return this.host.requestFullscreen();
      });
      this.collapse = this.action('Hide', () => this.setVisible(false));
      this.windowControls = element('div', undefined, {className: 'computer-window-controls'});
      this.captureButton = this.action('Snapshot', async () => {
        if (this.capturing || !this.publishSnapshot || !this.canSnapshot?.()) return;
        this.capturing = true; this.captureButton.disabled = true; this.captureButton.textContent = 'Capturing…';
        try { await this.publishSnapshot({source: 'desktop'}); }
        finally { this.capturing = false; this.captureButton.textContent = 'Snapshot'; this.captureButton.disabled = !this.canSnapshot?.(); }
      });
      this.captureButton.title = 'Send a frozen screenshot of the desktop to Stage';
      this.captureButton.hidden = !this.publishSnapshot;
      this.windowControls.append(this.captureButton, this.sizeButton, this.expand, this.collapse);
      const identity = element('div', undefined, {className: 'computer-identity'});
      identity.append(this.title, this.status, this.context);
      const actions = element('div', undefined, {className: 'computer-actions'});
      actions.append(this.takeover, this.stop, this.windowControls);
      this.header.append(identity, actions);
      this.screen = element('div', undefined, {className: 'computer-screen'});
      this.screen.setAttribute('role', 'region'); this.screen.setAttribute('aria-label', 'Live desktop');
      this.terminalHost = element('div', undefined, {className: 'computer-terminal', hidden: true});
      window.addEventListener('message', event => {
        if (event.origin !== location.origin || event.source !== this.frame?.contentWindow ||
            !['calliope-computer-size', 'calliope-computer-ready', 'calliope-computer-reconnect'].includes(event.data?.type)) return;
        if (event.data.type === 'calliope-computer-reconnect') {
          // A disconnected Kasm document loses its query parameters. Reload the
          // parent's original URL, preserving the small preview's read-only path.
          // Bound automatic recovery; a repeated failure retains a manual retry.
          const now = Date.now();
          if (!event.data.manual && this.lastViewerReconnect && now - this.lastViewerReconnect < 30000) return;
          this.lastViewerReconnect = now;
          this.frame.src = this.frame.getAttribute('src');
          return;
        }
        if (event.data.type === 'calliope-computer-ready') { this.syncViewerTheme(); return; }
        const {width, height} = event.data;
        if (!Number.isFinite(width) || !Number.isFinite(height) ||
            width < 64 || height < 64 || width > 32768 || height > 32768) return;
        this.screen.style.setProperty('--computer-aspect', `${width} / ${height}`);
      });
      this.history = element('div', undefined, {className: 'computer-history', hidden: true});
      this.teaching = element('details', undefined, {className: 'computer-teaching', hidden: true});
      this.teaching.append(element('summary', 'Explain what you changed'));
      const teachingForm = element('form', undefined, {className: 'computer-feedback'});
      this.explanation = element('textarea', undefined, {className: 'workspace-field', maxLength: 12000,
        placeholder: 'What should Callie learn from this demonstration?'});
      this.explanation.setAttribute('aria-label', 'Explain your demonstration');
      this.explanation.oninput = () => {
        if (!this.explanationContext) this.explanationContext = {
          run_id: this.snapshot?.active_run?.id, session_id: this.snapshot?.active_run?.context?.session_id,
          human_generation: this.snapshot?.generation};
      };
      this.saveExplanationButton = element('button', 'Save explanation', {type: 'submit', className: 'workspace-control'});
      teachingForm.append(this.explanation, this.saveExplanationButton);
      teachingForm.onsubmit = event => {
        event.preventDefault();
        this.saveExplanation().catch(error => this.toast(error.message, true));
      };
      this.teaching.append(teachingForm, element('p', 'Saved with this takeover and passed to Callie when you hand back.'));
      for (const [name, panel] of Object.entries({terminal: this.terminalHost, history: this.history})) {
        panel.id = `computer-panel-${name}`; panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('aria-labelledby', `computer-tab-${name}`);
      }
      this.terminalHost.append(element('p', 'Shell commands and their output appear here.', {className: 'computer-empty'}));
      this.host.replaceChildren(this.screen, this.header, this.teaching, this.tabs, this.history, this.terminalHost);
      if (!this.workspace) {
        this.button.onclick = () => this.setVisible(!this.visible);
        this.button.setAttribute('aria-controls', this.host.id);
      }
      window.addEventListener('warehouse-theme-change', () => this.syncViewerTheme());
      document.addEventListener('visibilitychange', () => this.syncViewerTheme());
      this.onFullscreen = () => { if (document.fullscreenElement === this.host) this.pauseHistory(); };
      document.addEventListener('fullscreenchange', this.onFullscreen);
      this.railResize = new ResizeObserver(() => this.onModeChange?.());
    }
    syncViewerTheme() {
      if (!this.frame?.contentWindow) return;
      const styles = getComputedStyle(this.host);
      const token = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
      this.frame.contentWindow.postMessage({type: 'calliope-computer-theme',
        background: token('--void', '#0d0b09'), accent: token('--amber', '#d9a441'),
        text: styles.color || '#e8dfd2', interactive: this.mode !== 'small',
        visible: this.visible && !document.hidden}, location.origin);
    }
    action(label, callback) {
      const button = element('button', label, {type: 'button', className: 'workspace-control'});
      button.onclick = () => Promise.resolve().then(callback).catch(error => this.toast(error.message, true));
      return button;
    }
    sync(available) {
      this.captureButton.disabled = this.capturing || !this.canSnapshot?.();
      this.available = available;
      if (available) {
        this.railResize.observe(this.button);
        if (this.stageSlot?.parentElement) this.railResize.observe(this.stageSlot.parentElement);
      } else this.railResize.disconnect();
      this.button.hidden = !available;
      this.host.hidden = !available || !this.visible;
      this.place();
      if (!this.workspace) {
        this.button.setAttribute('aria-expanded', String(!this.host.hidden));
        this.button.dataset.mode = this.mode;
        this.button.title = this.visible ? 'Hide Callie’s desktop' : 'Show Callie’s desktop';
      }
      const sid = this.getSession();
      const fastPolling = Boolean(this.isWorking?.() || (sid && this.snapshot?.active_run?.context?.session_id === sid));
      if (fastPolling && !this.fastPolling) { clearTimeout(this.timer); this.timer = null; }
      this.fastPolling = fastPolling;
      if (this.historySession !== sid) {
        this.historySession = sid;
        this.closeHistory();
      }
      if (!available) {
        this.disconnect();
        clearTimeout(this.timer);
        this.timer = null;
      } else if (!this.timer && !this.polling) {
        this.poll();
      }
      this.draw();
      this.onModeChange?.();
    }
    setVisible(value) {
      return this.setMode(value ? this.lastVisibleMode : 'hidden');
    }
    get activeView() {
      // The preview always shows the desktop; keep the expanded tab preference.
      return this.mode === 'small' ? 'desktop' : this.view;
    }
    windowControl(button, label, path) {
      button.textContent = path ? '' : label;
      button.setAttribute('aria-label', label);
      button.title = label;
      button.classList.toggle('computer-window-icon', !!path);
      if (path) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 16 16');
        svg.setAttribute('aria-hidden', 'true');
        const shape = document.createElementNS(svg.namespaceURI, 'path');
        shape.setAttribute('d', path);
        svg.append(shape); button.append(svg);
      }
    }
    setWorkspaceActive(active) {
      if (!this.workspace || this.visible === active) return;
      this.visible = active;
      this.host.hidden = !this.available || !active;
      this.host.inert = !active;
      if (active) { this.draw(); clearTimeout(this.timer); this.timer = null; this.poll(); }
      else { this.pauseHistory(); this.terminalView?.pause(); }
      this.syncViewerTheme();
    }
    place() {
      if (this.workspace) {
        this.host.classList.remove('is-small');
        this.sizeButton.hidden = this.collapse.hidden = true;
        return;
      }
      const small = this.mode === 'small';
      const destination = small ? this.miniSlot : this.stageSlot;
      if (destination && this.host.parentElement !== destination) {
        if (destination.moveBefore) destination.moveBefore(this.host, null);
        else destination.append(this.host);
      }
      this.host.classList.toggle('is-small', small);
      this.windowControl(this.collapse, small ? 'Minimize' : 'Hide', small ? 'M3 12h10' : null);
      this.windowControl(this.sizeButton, small ? 'Restore' : 'Small', small ? 'M3 6h7v7H3z M6 6V3h7v7h-3' : null);
      this.sizeButton.title = small ? 'Restore to Stage' : 'Watch above TODOs';
      this.windowControl(this.expand, 'Full screen', small ? 'M6 2H2v4 M10 2h4v4 M2 10v4h4 M14 10v4h-4' : null);
      const controls = small ? [this.collapse, this.sizeButton, this.expand] : [this.sizeButton, this.expand, this.collapse];
      if (this.windowControls.firstElementChild !== controls[0]) this.windowControls.replaceChildren(...controls);
      this.tabs.hidden = small;
      for (const control of [this.takeover, this.stop]) control.hidden = small;
    }
    async setMode(mode) {
      if (!['hidden', 'small', 'stage'].includes(mode)) return;
      if (document.fullscreenElement === this.host && mode !== 'stage') await document.exitFullscreen();
      if (mode === 'small' && this.mode !== 'small') this.closeHistory();
      this.mode = mode;
      this.visible = mode !== 'hidden';
      if (this.visible) this.lastVisibleMode = mode;
      try { localStorage.setItem(MODE_PREF, mode); } catch {}
      this.sync(this.available);
      if (this.visible) {
        this.draw();
        clearTimeout(this.timer);
        this.timer = null;
        this.poll();
      } else this.disconnect();
    }
    disconnect() {
      this.terminalView?.destroy(); this.terminalView = null;
      this.closeHistory();
      this.screen.replaceChildren();
      this.frame = null;
    }
    async poll() {
      if (this.polling || !this.available) return;
      this.polling = true;
      try {
        this.snapshot = await this.api(BASE + '/status');
        this.snapshotAt = Date.now();
        this.draw();
      } catch {
        this.status.textContent = 'Reconnecting…';
        this.takeover.disabled = this.stop.disabled = true;
      } finally {
        this.polling = false;
        clearTimeout(this.timer);
        this.timer = this.available ? setTimeout(() => { this.timer = null; this.poll(); },
          this.visible || this.fastPolling || (this.getSession() && this.snapshot?.active_run?.context?.session_id === this.getSession()) ? 3000 : 10000) : null;
      }
    }
    draw() {
      const state = this.snapshot;
      if (!state) return;
      const view = this.activeView;
      const active = state.active_run;
      const owner = active?.context?.session_id;
      const agentName = state.active_agent?.display_name || 'Callie';
      this.title.textContent = active ? agentName : 'Computer';
      this.title.title = active ? `${agentName}’s desktop task` : 'Computer';
      this.status.textContent = `${state.you_are_driving ? 'You are driving' : state.mode === 'human' ? 'Someone is driving' : active ? state.mode === 'agent' ? 'Working' : state.pause_reason?.code?.startsWith('RECORDING') ? 'Paused · recording fault' : 'Paused' : 'Ready'}${state.recording ? ' · recording' : active ? ' · recording unavailable' : ''}${state.queue_length ? ` · ${state.queue_length} queued` : ''}`;
      this.context.hidden = !owner || this.mode === 'small';
      this.context.textContent = owner === this.getSession() ? 'This thread' : 'Working thread ↗';
      this.context.title = active?.context?.goal || 'Open the working thread';
      this.takeover.textContent = state.you_are_driving ? `Let ${agentName} work` : 'Take over';
      this.takeover.disabled = this.busy || (!active && !this.getSession());
      this.stop.disabled = this.busy || !active;
      this.teaching.hidden = !state.you_are_driving || this.mode === 'small';
      const latest = (state.jobs || []).filter(job => job.terminal).at(-1);
      if (latest && latest.id !== this.lastTerminalJob) {
        this.lastTerminalJob = latest.id;
        this.terminalJob = {...latest, session: owner};
        this.unseen.terminal = view !== 'terminal';
      }
      if (this.previousRun && this.previousRun !== active?.id) {
        this.unseen.history = true;
        if (!this.history.querySelector('.is-open')) this.historyLoaded = false;
      }
      this.previousRun = active?.id;
      const activity = {
        terminal: (state.jobs || []).some(job => job.terminal && job.status === 'running'),
        desktop: state.you_are_driving || (state.jobs || []).some(job => !job.terminal && job.status === 'running'),
      };
      for (const [name, tab] of Object.entries(this.tabButtons)) {
        tab.setAttribute('aria-selected', String(view === name)); tab.tabIndex = view === name ? 0 : -1;
        tab.classList.toggle('has-activity', !!activity[name]);
        tab.classList.toggle('has-unseen', !!this.unseen[name]);
        const label = name[0].toUpperCase() + name.slice(1);
        tab.setAttribute('aria-label', label);
        tab.title = activity[name] ? `${label}: job running` : this.unseen[name] ? `${label}: new activity` : label;
      }
      const showTerminal = view === 'terminal';
      this.screen.hidden = false;
      this.screen.classList.toggle('has-activity', !!activity.desktop);
      this.terminalHost.hidden = !showTerminal;
      this.history.hidden = view !== 'history';
      this.teaching.hidden = !state.you_are_driving || this.mode === 'small';
      this.onActivity?.();
      if (!this.visible || !this.available) return;
      if (view === 'history' && !this.historyLoaded) {
        this.historyLoaded = true;
        this.loadHistory().catch(error => { this.historyLoaded = false; this.toast(error.message, true); });
      }
      if (showTerminal && this.terminalJob && (!this.terminalView || this.terminalView.jobId !== this.terminalJob.id)) {
        this.terminalView?.destroy();
        const job = this.terminalJob;
        this.terminalView = new CalliopeTerminal(this.terminalHost, this.api,
          `${BASE}/runs/${job.run_id}/jobs/${job.id}/terminal?session_id=${encodeURIComponent(job.session)}`, true);
        this.terminalView.jobId = job.id;
      } else if (!showTerminal && this.terminalView) {
        this.terminalView.destroy(); this.terminalView = null;
      }
      if (!this.frame || this.frameGeneration !== state.generation || this.frameMode !== this.mode) {
        const small = this.mode === 'small';
        this.frame = element('iframe', undefined, {src: window.CalliopeComputer.viewerURL(state, small),
          title: small ? "Callie's computer — read-only preview" : "Callie's computer"});
        this.frame.onload = () => this.syncViewerTheme();
        this.frame.inert = small;
        this.frame.tabIndex = small ? -1 : 0;
        this.frame.setAttribute('allow', 'clipboard-read; clipboard-write; fullscreen');
        this.screen.replaceChildren(this.frame);
        this.frameGeneration = state.generation;
        this.frameMode = this.mode;
      }
      this.syncViewerTheme();
    }
    async selectView(view) {
      if (view === 'desktop') view = 'history'; // Old saved modes and takeover shortcuts.
      if (!this.tabButtons[view]) return;
      this.view = view; this.unseen[view] = false;
      try { localStorage.setItem(VIEW_PREF, view); } catch {}
      if (view !== 'history') this.closeHistory();
      this.draw();
    }
    async control(mode) {
      this.busy = true;
      this.draw();
      try {
        if (mode === 'agent' && this.explanation.value.trim()) await this.saveExplanation();
        this.snapshot = await this.api(BASE + '/control', {
          method: 'POST', headers: {'X-Calliope-Computer': '1'},
          body: JSON.stringify({mode, session_id: this.getSession(), generation: this.snapshot?.generation}),
        });
        this.snapshotAt = Date.now();
        if (mode === 'human') await this.selectView('desktop');
      } finally {
        this.busy = false;
        this.draw();
      }
    }
    async saveExplanation() {
      if (this.savingExplanation) return this.savingExplanation;
      const text = this.explanation.value.trim();
      if (!text) return;
      const context = this.explanationContext;
      if (!context || context.human_generation !== this.snapshot?.generation ||
          context.run_id !== this.snapshot?.active_run?.id || !this.snapshot?.you_are_driving) {
        throw new Error('The takeover changed. Save this explanation with the original run in History.');
      }
      const signature = JSON.stringify({...context, feedback: text});
      if (this.explanationSignature !== signature) {
        this.explanationRequest = crypto.randomUUID();
        this.explanationSignature = signature;
      }
      this.saveExplanationButton.disabled = true;
      this.explanation.disabled = true;
      this.savingExplanation = this.api(BASE + '/feedback', {
        method: 'POST', headers: {'X-Calliope-Computer': '1'},
        body: JSON.stringify({...context, feedback: text, request_id: this.explanationRequest}),
      }).then(() => {
        this.explanation.value = '';
        this.explanationContext = null;
        this.toast('Explanation saved');
      }).finally(() => {
        this.savingExplanation = null;
        this.saveExplanationButton.disabled = false;
        this.explanation.disabled = false;
      });
      return this.savingExplanation;
    }
    closeHistory() {
      this.pauseHistory(true);
      this.replays?.forEach(replay => replay.destroy()); this.replays = [];
      this.historyRequest = (this.historyRequest || 0) + 1;
      this.history.hidden = true;
      this.history.replaceChildren();
      this.historyLoaded = false;
    }
    pauseHistory(unload = false, root = this.history) {
      root.querySelectorAll('video').forEach(video => {
        video.pause();
        if (unload) { video.removeAttribute('src'); video.load(); }
      });
      root.querySelectorAll('.computer-terminal').forEach(host => {
        if (unload) host.replay?.destroy(); else host.replay?.pause();
      });
    }
    async loadHistory(offset = 0) {
      const sid = this.getSession();
      if (!sid) {
        this.historyLoaded = true;
        this.history.replaceChildren(element('p', 'Open a thread to see its computer recordings.', {className: 'computer-empty'}));
        return;
      }
      const request = this.historyRequest = (this.historyRequest || 0) + 1;
      this.history.hidden = false;
      this.historyLoaded = true;
      if (!offset) {
        this.pauseHistory(true);
        this.history.replaceChildren(element('p', 'Loading this thread’s computer runs…', {className: 'computer-empty'}));
      }
      let data;
      try { data = await this.api(BASE + '/runs?session_id=' + encodeURIComponent(sid) + '&offset=' + offset); }
      catch (error) {
        if (request === this.historyRequest && !offset) {
          this.history.replaceChildren(element('p', 'Recordings could not be loaded.', {className: 'computer-empty'}),
            this.action('Try again', () => this.loadHistory()));
        }
        throw error;
      }
      if (request !== this.historyRequest) return;
      if (!offset) {
        this.unseen.history = false;
        const heading = element('div', undefined, {className: 'computer-history-heading'});
        heading.append(element('span', 'Recordings from this thread'), this.action('Refresh', () => this.loadHistory()));
        this.runGrid = element('div', undefined, {className: 'computer-run-grid'});
        this.history.replaceChildren(heading, this.runGrid);
        if (!data.runs?.length) this.runGrid.append(element('p', 'No computer runs in this thread yet.', {className: 'computer-empty'}));
      }
      this.history.querySelector('.computer-history-more')?.remove();
      for (const run of data.runs || []) {
        if ([...this.runGrid.children].some(card => card.dataset.runId === run.id)) continue;
        const card = element('article', undefined, {className: 'computer-run'});
        card.dataset.runId = run.id;
        const button = this.action('', () => this.toggleRun(card, run, sid));
        button.className = 'computer-run-open';
        button.setAttribute('aria-expanded', 'false'); button.setAttribute('aria-controls', `computer-run-${run.id}`);
        const preview = element('span', undefined, {className: 'computer-run-preview'});
        const image = element('img', undefined, {src: `${BASE}/runs/${encodeURIComponent(run.id)}/thumbnail.jpg?session_id=${encodeURIComponent(sid)}`,
          alt: '', loading: 'lazy', decoding: 'async'});
        const unavailable = element('span', 'Preview unavailable', {className: 'computer-run-placeholder', hidden: true});
        image.onerror = () => { image.hidden = true; unavailable.hidden = false; };
        const play = element('span', '▶', {className: 'computer-run-play'}); play.setAttribute('aria-hidden', 'true');
        preview.append(image, unavailable, play);
        const copy = element('span', undefined, {className: 'computer-run-copy'});
        copy.append(element('strong', run.goal || 'Computer task'));
        const meta = element('span', undefined, {className: 'computer-run-meta'});
        meta.append(element('time', new Date(run.created_at).toLocaleString([], {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}),
          {dateTime: run.created_at}), element('span', run.status.replaceAll('_', ' ')));
        copy.append(meta); button.append(preview, copy); card.append(button); this.runGrid.append(card);
      }
      if (data.next_offset != null) {
        const more = this.action('More recordings', async () => {
          more.disabled = true;
          try { await this.loadHistory(data.next_offset); } finally { more.disabled = false; }
        });
        more.classList.add('computer-history-more'); this.history.append(more);
      }
      if (!offset) this.loadStorageSettings(request);
    }
    async toggleRun(card, run, sid) {
      const open = !card.classList.contains('is-open');
      card.classList.toggle('is-open', open);
      card.firstElementChild.setAttribute('aria-expanded', String(open));
      if (!open) {
        this.pauseHistory(true, card);
        card.querySelector('.computer-run-detail')?.remove();
        return;
      }
      const detail = element('div', undefined, {className: 'computer-run-detail', id: `computer-run-${run.id}`});
      detail.append(element('p', 'Loading recording…', {className: 'computer-empty'}));
      card.append(detail);
      await this.showRun(detail, run, sid);
    }
    async loadStorageSettings(request) {
      const endpoint = '/api/calliope/admin/computer-storage';
      let settings;
      try { settings = await this.api(endpoint); } catch { return; } // Admin controls only.
      if (request !== this.historyRequest) return;
      const details = element('details', undefined, {className: 'computer-storage-settings'});
      details.append(element('summary', 'Recording storage'));
      const usage = element('p', `${(settings.video_bytes / 1024 ** 3).toFixed(2)} GiB of archived video`);
      const form = element('form', undefined, {className: 'computer-feedback'});
      const fields = {};
      for (const [key, label, max] of [['retention_days', 'Keep video for days', 36500], ['max_video_gib', 'Video storage target in GiB', 100000]]) {
        const row = element('label', label + ' ');
        fields[key] = element('input', undefined, {type: 'number', min: '0', max: String(max), step: '1', value: String(settings[key]), required: true});
        row.append(fields[key]);
        form.append(row);
      }
      form.append(element('p', '0 disables a limit. Cleanup removes the oldest completed video first. Active runs and recordings marked Keep are protected and can exceed the target. Receipts remain available.'));
      const save = element('button', 'Save recording settings', {type: 'submit'});
      form.append(save);
      form.onsubmit = async event => {
        event.preventDefault();
        save.disabled = true;
        try {
          settings = await this.api(endpoint, {method: 'PUT', headers: {'X-Calliope-Admin': '1'},
            body: JSON.stringify(Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, Number(input.value)])))});
          usage.textContent = `${(settings.video_bytes / 1024 ** 3).toFixed(2)} GiB of archived video`;
          this.toast('Recording settings saved');
        } catch (error) { this.toast(error.message, true); }
        finally { save.disabled = false; }
      };
      details.append(usage, form);
      this.history.append(details);
    }
    replayPosition(timeline, sourceSeconds) {
      for (const span of timeline || []) {
        // A receipt in a removed wait seeks to the next retained frame.
        if (sourceSeconds < span.source_start) return {seconds: span.replay_start, skipped: true};
        if (sourceSeconds < span.source_end) return {
          seconds: span.replay_start + sourceSeconds - span.source_start, skipped: false};
      }
      return {seconds: Math.max(0, (timeline?.at(-1)?.replay_end || 0) - .5), skipped: true};
    }
    async showRun(card, run, sid) {
      if (card.dataset.loaded || card.dataset.loading) return;
      card.dataset.loading = '1';
      const url = tail => `${BASE}/runs/${encodeURIComponent(run.id)}/${tail}?session_id=${encodeURIComponent(sid)}`;
      const results = await Promise.allSettled([
        this.api(url('manifest')),
        this.fetch(url('events')).then(async response => {
          if (!response.ok) throw new Error('Run events are not available yet.');
          return (await response.text()).trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
        }),
        this.api(url('receipts') + '&summary=1'),
        this.api(url('demonstrations')),
      ]);
      if (!card.isConnected) return;
      delete card.dataset.loading;
      card.dataset.loaded = '1';
      card.replaceChildren();
      const manifest = results[0].status === 'fulfilled' ? results[0].value : null;
      if (manifest?.masking_captures?.length) {
        const raw = manifest.masking_captures.filter(capture => capture.policy === 'desktop-pixels-v1');
        if (raw.length) card.append(element('p', 'Recorded as displayed on the desktop; no automatic redaction.'));
        const captures = manifest.masking_captures.filter(capture => capture.policy !== 'desktop-pixels-v1');
        const total = key => captures.reduce((sum, capture) => sum + (Number(capture[key]) || 0), 0);
        if (captures.length) card.append(element('p', `Recognized password fields masked in ${total('masked_frames')} of ${total('frames')} captured frames.`
          + (total('incomplete_frames') ? ` Field detection was incomplete in ${total('incomplete_frames')} frames.` : '')
          + (total('detector_unavailable_frames') ? ` ${total('detector_unavailable_frames')} frames were hidden because detection was unavailable.` : '')
          + ' Custom fields and ordinary text verification codes may remain visible.'));
      }
      if (!manifest?.expired_at) {
        const keep = this.action(run.keep_recording ? 'Release recording' : 'Keep recording', async () => {
          keep.disabled = true;
          try {
            const result = await this.api(BASE + '/retention', {method: 'POST', headers: {'X-Calliope-Computer': '1'},
              body: JSON.stringify({session_id: sid, run_id: run.id, keep: !run.keep_recording})});
            run.keep_recording = result.keep_recording;
            keep.textContent = run.keep_recording ? 'Release recording' : 'Keep recording';
            keep.setAttribute('aria-pressed', String(run.keep_recording));
            this.toast(run.keep_recording ? 'Recording kept beyond automatic cleanup' : 'Recording follows storage settings');
          } finally { keep.disabled = false; }
        });
        keep.setAttribute('aria-pressed', String(!!run.keep_recording));
        card.append(keep);
      }
      let replay;
      if (!manifest?.expired_at && manifest?.replay?.status === 'ready') {
        const recording = manifest.replay;
        replay = element('video', undefined, {controls: true, preload: 'metadata', src: url('video/replay.mp4')});
        const duration = seconds => `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
        const note = element('p', `${duration(recording.duration)} replay · ${duration(recording.skipped_seconds)} of unchanged screen skipped. The lower-left timestamp shows original session time.`);
        card.append(replay, note);
        replay.playAtTime = at => {
          const position = this.replayPosition(recording.timeline, at - manifest.started_at);
          const seek = () => { replay.currentTime = position.seconds; replay.play().catch(() => {}); };
          if (replay.readyState) seek();
          else { replay.addEventListener('loadedmetadata', seek, {once: true}); replay.load(); }
          if (position.skipped) this.toast('That moment was in a skipped wait. Showing the next available frame.');
        };
        const download = element('a', 'Download replay', {href: url('video/replay.mp4'), download: `computer-${run.id}.mp4`});
        card.append(download);
      } else if (manifest?.segments?.length) {
        if (['finished', 'interrupted', 'failed'].includes(run.status)) {
          card.append(element('p', manifest.replay?.status === 'deferred'
            ? 'Replay processing will retry. Original footage remains available below.'
            : 'The single-session replay is being prepared. Original footage is available below.'));
          card.append(this.action('Refresh replay', () => {
            this.pauseHistory(true, card);
            card.replaceChildren(); delete card.dataset.loaded;
            return this.showRun(card, run, sid);
          }));
        }
        replay = element('video', undefined, {controls: true, preload: 'metadata'});
        const choose = element('select');
        choose.setAttribute('aria-label', 'Recording segment');
        const playAt = (index, seconds = 0) => {
          choose.value = String(index);
          replay.src = url('video/' + manifest.segments[index].file) + '#t=' + Math.max(0, seconds);
        };
        manifest.segments.forEach((segment, index) => choose.append(element('option',
          `${Math.floor(segment.start)}–${Math.ceil(segment.end)} seconds`, {value: String(index)})));
        choose.onchange = () => playAt(Number(choose.value));
        replay.onended = () => {
          const next = Number(choose.value) + 1;
          if (next < manifest.segments.length) { playAt(next); replay.play().catch(() => {}); }
        };
        playAt(0);
        card.append(choose, replay);
        replay.playAtTime = at => {
          const seconds = at - manifest.started_at;
          const index = manifest.segments.findIndex(segment => seconds >= segment.start && seconds < segment.end);
          if (index >= 0) { playAt(index, seconds - manifest.segments[index].start); replay.play().catch(() => {}); }
        };
      } else card.append(element('p', manifest?.expired_at
        ? 'Video expired under recording storage settings. Receipts and feedback remain available.'
        : 'Recording segments are not available yet.'));
      if (results[1].status === 'fulfilled' || results[2].status === 'fulfilled') {
        const events = element('div', undefined, {className: 'computer-events'});
        const receipts = results[2].status === 'fulfilled' ? results[2].value.events || [] : [];
        const learning = receipts.filter(event => ['feedback.received', 'lessons.recalled', 'decision.requested', 'decision.completed',
          'methods.recalled', 'method.learned', 'method.reused', 'method.evaluated', 'method.retired', 'learning.reviewed', 'learning.deferred'].includes(event.event_type))
          .map(event => ({...event, type: event.event_type, at: new Date(event.created_at).getTime() / 1000}));
        for (const event of [...(results[1].status === 'fulfilled' ? results[1].value : []), ...learning].sort((a, b) => a.at - b.at)) {
          if (event.type === 'human.input') continue;
          const row = element('details');
          const data = event.payload || {};
          const label = event.type === 'method.learned' ? `Learned: ${data.title}`
            : event.type === 'method.reused' ? `Reused: ${data.title}`
            : event.type === 'method.evaluated' ? `${data.title}: ${data.outcome === 'supported' ? 'success supported by evidence' : data.outcome}`
            : event.type === 'methods.recalled' ? `Prior methods: ${(data.items || []).length}`
            : event.type === 'learning.reviewed' ? 'Learning review complete'
            : event.type === 'learning.deferred' ? 'Learning review unavailable'
            : data.intent || event.type;
          row.append(element('summary', `${new Date(event.at * 1000).toLocaleTimeString()} · ${label}${data.status ? ' · ' + data.status.replaceAll('_', ' ') : ''}`));
          if (replay) row.append(this.action('Watch this moment', () => replay.playAtTime(event.at)));
          if (data.code) row.append(element('pre', data.code));
          if (data.outcome || data.error) row.append(element('p', data.outcome || data.error));
          if (data.feedback) row.append(element('p', data.feedback));
          if (data.demonstration) {
            const span = data.demonstration;
            row.append(element('p', `Demonstration by ${span.actor} · ${new Date(span.started_at).toLocaleTimeString()}–${new Date(span.through_at).toLocaleTimeString()}`));
            if (replay) row.append(this.action('Watch demonstration', () => replay.playAtTime(new Date(span.started_at).getTime() / 1000)));
          }
          if (data.result) row.append(element('p', data.result.error?.message ||
            `${data.result.provider}: ${data.result.selected_id} · ${data.result.elapsed_ms} ms`));
          if (data.items) for (const item of data.items) row.append(element('p', item.title
            ? `${item.title} · version ${item.version} · ${item.successes} successful runs (assessed)`
            : `${item.kind}: ${item.text}`));
          if (data.reason) row.append(element('p', data.reason));
          if (event.type === 'method.evaluated') row.append(element('p',
            `Version ${data.version} · ${data.successes} successful runs · ${data.failures} failed runs. Reuse and outcome assessed from receipts.`));
          if (data.method) {
            row.append(element('p', `${data.successes} successful runs · ${data.failures} failed runs · version ${data.version}. Based on a background review of the linked evidence.`));
            row.append(element('p', data.method.when_to_use));
            for (const [key, label] of [['inputs', 'Inputs'], ['steps', 'Method'], ['checks', 'Check the result'], ['pitfalls', 'Known limits']]) {
              if (!data.method[key]?.length) continue;
              row.append(element('strong', label));
              const list = element('ul');
              for (const text of data.method[key]) list.append(element('li', text));
              row.append(list);
            }
            if (data.method.example) row.append(element('pre', data.method.example));
            const forget = this.action('Forget method', async () => {
              forget.disabled = true;
              try {
                await this.api(BASE + '/methods/forget', {method: 'POST', headers: {'X-Calliope-Computer': '1'},
                  body: JSON.stringify({session_id: sid, run_id: run.id, method_id: data.id})});
                forget.textContent = 'Forgotten';
              } catch (error) { forget.disabled = false; throw error; }
            });
            row.append(forget);
          }
          if (data.evidence) for (const evidence of data.evidence) {
            row.append(element('blockquote', evidence.quote));
            if (/^[0-9a-f-]{36}\/(code\.(py|sh)|stdout\.txt|stderr\.txt)$/.test(evidence.source)) {
              row.append(element('a', 'Source receipt', {href: url('jobs/' + evidence.source), target: '_blank', rel: 'noopener'}));
            }
          }
          if (data.exit_code !== undefined) row.append(element('p', `Exit code: ${data.exit_code}`));
          if (event.type === 'execution.started' && data.terminal && data.id) {
            const terminal = element('div', undefined, {className: 'computer-terminal'});
            row.append(this.action('Replay terminal', () => {
              terminal.replay?.destroy();
              terminal.replay = new CalliopeTerminal(terminal, this.api, url(`jobs/${data.id}/terminal`), run.status === 'active');
              (this.replays ||= []).push(terminal.replay);
            }), terminal);
          }
          if (event.type === 'execution.finished' && data.id) {
            for (const name of ['stdout.txt', 'stderr.txt']) row.append(element('a', name,
              {href: url(`jobs/${encodeURIComponent(data.id)}/${name}`), target: '_blank', rel: 'noopener'}));
          }
          events.append(row);
        }
        card.append(events);
      } else card.append(element('p', 'Run events are not available yet.'));
      const feedback = element('form', undefined, {className: 'computer-feedback'});
      const input = element('textarea', undefined, {className: 'workspace-field', placeholder: 'What worked, or what should Callie do differently?', required: true, maxLength: 12000});
      input.setAttribute('aria-label', 'Feedback for Callie');
      const submit = element('button', 'Save feedback', {type: 'submit', className: 'workspace-control'});
      const spanChoice = element('select');
      spanChoice.setAttribute('aria-label', 'Feedback applies to');
      spanChoice.append(element('option', 'Whole task', {value: ''}));
      const spans = results[3].status === 'fulfilled' ? results[3].value.demonstrations || [] : [];
      for (const span of spans) spanChoice.append(element('option',
        `Takeover at ${new Date(span.started_at).toLocaleTimeString()} · ${span.actor}`, {value: span.id}));
      if (spans.length) feedback.append(spanChoice);
      feedback.append(input, submit);
      feedback.onsubmit = async event => {
        event.preventDefault();
        submit.disabled = true;
        const signature = JSON.stringify([input.value, spanChoice.value]);
        if (feedback.lastText !== signature) {
          feedback.requestId = crypto.randomUUID();
          feedback.lastText = signature;
        }
        try {
          await this.api(BASE + '/feedback', {method: 'POST', headers: {'X-Calliope-Computer': '1'},
            body: JSON.stringify({session_id: sid, run_id: run.id, feedback: input.value, request_id: feedback.requestId,
              demonstration_id: spanChoice.value || null})});
          input.value = '';
          this.toast('Feedback saved');
        } catch (error) { this.toast(error.message, true); }
        finally { submit.disabled = false; }
      };
      card.append(feedback);
    }
    destroy() {
      document.removeEventListener('fullscreenchange', this.onFullscreen);
      this.replays?.forEach(replay => replay.destroy());
      this.available = false;
      this.railResize.disconnect();
      clearTimeout(this.timer);
      this.timer = null;
      this.disconnect();
    }
  };
})();
