/* A temporary, read-only look at desktop execution. Never a saved surface. */
(() => {
  'use strict';
  const GRACE_MS = 15000, STALE_MS = 20000;
  const preference = session => `calliope-stage-desktop-minimized:${session}`;
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    node.className = className;
    if (text) node.textContent = text;
    return node;
  };
  window.CalliopeComputerStage = class {
    constructor({host, onChange, openDesktop}) {
      Object.assign(this, {host, onChange, openDesktop});
      this.minimized = new Map();
      this.card = element('article', 'desktop-stage-card');
      this.card.setAttribute('aria-label', 'Desktop in progress');
      const header = element('header', 'desktop-stage-head');
      const identity = element('div', 'desktop-stage-identity');
      this.name = element('span', 'desktop-stage-agent');
      this.title = element('strong', 'desktop-stage-title', 'Desktop');
      identity.append(this.name, this.title);
      const actions = element('div', 'desktop-stage-actions');
      this.open = element('button', '', 'Open');
      this.open.type = 'button'; this.open.title = 'Open the Desktop panel';
      this.open.setAttribute('aria-label', 'Open Desktop panel');
      this.open.onclick = () => this.openDesktop();
      this.toggle = element('button', '', 'Minimize');
      this.toggle.type = 'button';
      this.toggle.setAttribute('aria-controls', 'desktop-stage-screen');
      this.toggle.onclick = () => {
        const value = !this.isMinimized();
        this.minimized.set(this.context.session, value);
        try { sessionStorage.setItem(preference(this.context.session), String(value)); } catch {}
        this.draw(); this.onChange?.();
      };
      actions.append(this.open, this.toggle);
      header.append(identity, actions);
      this.screen = element('div', 'desktop-stage-screen');
      this.screen.id = 'desktop-stage-screen';
      this.screen.setAttribute('role', 'region');
      this.screen.setAttribute('aria-label', 'Read-only live desktop preview');
      this.screen.onclick = () => this.openDesktop();
      this.caption = element('p', 'desktop-stage-caption');
      this.card.append(header, this.screen, this.caption);
      this.host.append(this.card); this.host.hidden = true;
      this.onMessage = event => this.message(event);
      this.onTheme = () => this.syncTheme();
      window.addEventListener('message', this.onMessage);
      window.addEventListener('warehouse-theme-change', this.onTheme);
      document.addEventListener('visibilitychange', this.onTheme);
    }
    get hasItems() { return !this.host.hidden; }
    isMinimized() {
      const session = this.context.session;
      if (!this.minimized.has(session)) {
        let saved = false;
        try { saved = sessionStorage.getItem(preference(session)) === 'true'; } catch {}
        this.minimized.set(session, saved);
      }
      return this.minimized.get(session);
    }
    update(snapshot, context) {
      this.snapshot = snapshot; this.context = context;
      this.draw();
    }
    draw() {
      clearTimeout(this.timer);
      const state = this.snapshot, context = this.context, now = Date.now();
      const run = state?.active_run;
      if (!context?.enabled || !context.session || context.suppressed || state?.mode !== 'agent'
          || !run?.id || run.context?.session_id !== context.session
          || !context.observedAt || now - context.observedAt >= STALE_MS) {
        this.hide(); return;
      }
      // Ownership and recording can start long before execution. Only a real
      // non-terminal job can open this preview; recently completed short jobs
      // are included so polling cannot miss them entirely.
      const jobs = (state.jobs || []).filter(job => job.run_id === run.id);
      const candidates = jobs.filter(job => !job.terminal && (job.status === 'running'
        || (Number(job.ended_at) > 0 && now < Number(job.ended_at) * 1000 + GRACE_MS)));
      candidates.sort((a, b) => Number(b.status === 'running') - Number(a.status === 'running')
        || Number(b.ended_at || b.started_at) - Number(a.ended_at || a.started_at));
      const job = candidates[0];
      if (!job || jobs.some(item => item.terminal && item.status === 'running')) {
        this.hide(true); return;
      }
      this.cancelExit();
      const arrived = this.host.hidden;
      this.host.hidden = false;
      const minimized = this.isMinimized();
      this.card.classList.toggle('is-minimized', minimized);
      this.card.dataset.phase = job.status === 'running' ? 'working' : 'recent';
      this.name.textContent = `${state.active_agent?.display_name || 'Callie'} · ${job.status === 'running' ? 'using the desktop' : 'just used the desktop'}`;
      this.toggle.textContent = minimized ? 'Show' : 'Minimize';
      this.toggle.title = minimized ? 'Show the live desktop preview' : 'Keep this preview minimized in this chat';
      this.toggle.setAttribute('aria-label', minimized ? 'Show desktop preview' : 'Minimize desktop preview');
      this.toggle.setAttribute('aria-expanded', String(!minimized));
      this.screen.hidden = this.caption.hidden = minimized;
      this.caption.textContent = String(job.intent || 'Working on the computer').slice(0, 300);
      this.caption.title = this.caption.textContent;
      if (minimized) this.disconnect();
      else this.connect(state, run);
      const until = Math.min(context.observedAt + STALE_MS,
        job.status === 'running' ? Infinity : Number(job.ended_at) * 1000 + GRACE_MS);
      this.timer = setTimeout(() => this.draw(), Math.max(1, until - now));
      if (arrived) this.onChange?.();
    }
    connect(state, run) {
      const identity = `${this.context.session}:${run.id}:${state.generation}`;
      if (!this.frame || this.frameIdentity !== identity) {
        this.disconnect();
        const frame = element('iframe', 'desktop-stage-frame');
        frame.title = 'Live desktop — read-only preview';
        frame.src = window.CalliopeComputer.viewerURL(state, true);
        frame.inert = true; frame.tabIndex = -1;
        frame.onload = () => this.syncTheme();
        this.frame = frame; this.frameIdentity = identity;
        this.screen.append(frame);
      }
      this.syncTheme();
    }
    syncTheme() {
      if (!this.frame?.contentWindow) return;
      const styles = getComputedStyle(this.card);
      const token = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
      this.frame.contentWindow.postMessage({type: 'calliope-computer-theme',
        background: token('--void', '#0d0b09'), accent: token('--amber', '#d9a441'),
        text: styles.color || '#e8dfd2', interactive: false,
        visible: this.hasItems && !document.hidden}, location.origin);
    }
    message(event) {
      if (event.origin !== location.origin || event.source !== this.frame?.contentWindow) return;
      if (event.data?.type === 'calliope-computer-ready') this.syncTheme();
      if (event.data?.type === 'calliope-computer-size') {
        const {width, height} = event.data;
        if ([width, height].every(value => Number.isFinite(value) && value >= 64 && value <= 32768)) {
          this.screen.style.setProperty('--desktop-stage-aspect', `${width} / ${height}`);
        }
      }
      if (event.data?.type === 'calliope-computer-reconnect') {
        const now = Date.now();
        if (this.lastReconnect && now - this.lastReconnect < 30000) return;
        this.lastReconnect = now; this.frame.src = this.frame.getAttribute('src');
      }
    }
    disconnect() {
      this.frame?.remove(); this.frame = null; this.frameIdentity = null; this.lastReconnect = null;
    }
    cancelExit() {
      if (this.exit) { this.exit.onfinish = null; this.exit.cancel(); this.exit = null; }
    }
    hide(animate = false) {
      if (this.host.hidden || this.exit && animate) return;
      this.cancelExit();
      const finish = () => {
        this.cancelExit(); this.host.hidden = true; this.disconnect(); this.onChange?.();
      };
      if (animate && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
        this.exit = this.card.animate([{opacity: 1}, {opacity: 0}], {duration: 200, fill: 'forwards'});
        this.exit.onfinish = finish;
      } else finish();
    }
    destroy() {
      clearTimeout(this.timer); this.cancelExit(); this.disconnect();
      this.host.hidden = true; this.card.remove();
      window.removeEventListener('message', this.onMessage);
      window.removeEventListener('warehouse-theme-change', this.onTheme);
      document.removeEventListener('visibilitychange', this.onTheme);
    }
  };
})();
