/* Output-only terminal view. No input, clipboard, link or remote window handlers. */
(() => {
  const el = (tag, text, attrs = {}) => Object.assign(document.createElement(tag),
    {...(text === undefined ? {} : {textContent: text}), ...attrs});
  window.CalliopeTerminal = class {
    constructor(host, api, url, live = false) {
      Object.assign(this, {host, api, url, live, offset: 0, events: [], duration: 0, cursor: 0});
      this.bar = el('div', undefined, {className: 'computer-terminal-bar'});
      this.label = el('span', 'Terminal · read only');
      this.play = el('button', 'Replay', {type: 'button', disabled: true, className: 'workspace-control'});
      this.seek = el('input', undefined, {type: 'range', min: 0, max: 0, step: .1, value: 0, disabled: true});
      this.seek.setAttribute('aria-label', 'Terminal replay time');
      this.clock = el('span', '0:00');
      this.bar.append(this.label, this.play, this.seek, this.clock);
      this.command = el('details');
      this.command.append(el('summary', 'Command and intent'));
      this.intent = el('p'); this.code = el('pre');
      this.command.append(this.intent, this.code);
      this.output = el('div', undefined, {className: 'computer-terminal-output'});
      this.output.setAttribute('aria-label', 'Read-only terminal output');
      host.replaceChildren(this.bar, this.output, this.command);
      this.term = new Terminal({cols: 100, rows: 24, disableStdin: true, cursorBlink: false,
        convertEol: false, scrollback: 10000, fontSize: 12, allowTransparency: true,
        theme: {background: '#00000000', foreground: getComputedStyle(host).color}});
      this.onTheme = () => { this.term.options.theme = {background: '#00000000', foreground: getComputedStyle(host).color}; };
      window.addEventListener('warehouse-theme-change', this.onTheme);
      this.term.open(this.output);
      this.output.querySelector('textarea')?.setAttribute('tabindex', '-1');
      this.resize = new ResizeObserver(() => {
        const size = Math.max(6, Math.min(13, (host.clientWidth - 18) / this.term.cols / .62));
        this.term.options.fontSize = size;
      });
      this.resize.observe(host);
      this.play.onclick = () => this.playing ? this.pause() : this.start();
      this.seek.oninput = () => { this.pause(); this.renderAt(Number(this.seek.value)); };
      this.load();
    }
    async load() {
      try {
        const page = await this.api(this.url + '&offset=' + this.offset);
        if (this.closed) return;
        this.intent.textContent = page.intent || '';
        this.code.textContent = page.code || '';
        const output = [];
        for (const event of page.events || []) {
          if (!Array.isArray(event)) {
            if (event.version === 2 && Number.isInteger(event.width) && Number.isInteger(event.height))
              this.term.resize(Math.min(500, Math.max(1, event.width)), Math.min(200, Math.max(1, event.height)));
          } else if (event[1] === 'o' && typeof event[2] === 'string' && Number.isFinite(event[0])) {
            this.events.push(event); output.push(event[2]); this.duration = Math.max(this.duration, event[0]);
          }
        }
        this.offset = page.offset;
        if (output.length) {
          if (this.playing || !this.play.disabled) {
            this.pause(); this.term.reset();
            this.term.write(this.events.map(event => event[2]).join(''));
          } else this.term.write(output.join(''));
          this.play.disabled = this.seek.disabled = true;
        }
        this.seek.max = String(this.duration);
        if (output.length || this.play.disabled) {
          this.seek.value = String(this.duration); this.showTime(this.duration);
        }
        this.label.textContent = `Terminal · read only · ${page.complete ? page.status : 'live'}`;
        if (page.more) this.timer = setTimeout(() => this.load(), 0);
        else if (this.live && !page.complete) this.timer = setTimeout(() => this.load(), 1000);
        else {
          this.play.disabled = this.seek.disabled = !this.events.length;
          if (page.offline && this.live) this.timer = setTimeout(() => this.load(), 5000);
        }
      } catch (error) {
        if (this.closed) return;
        this.label.textContent = 'Terminal · ' + error.message;
        this.timer = setTimeout(() => this.load(), 3000);
      }
    }
    showTime(seconds) {
      this.clock.textContent = `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
    }
    renderAt(at) {
      this.term.reset(); this.cursor = 0;
      const output = [];
      while (this.cursor < this.events.length && this.events[this.cursor][0] <= at)
        output.push(this.events[this.cursor++][2]);
      this.term.write(output.join(''));
      this.seek.value = String(at); this.showTime(at);
    }
    start() {
      let at = Number(this.seek.value);
      // Range inputs round to their step; treat the final tick as the end.
      if (at + Number(this.seek.step) >= this.duration) { at = 0; this.renderAt(0); }
      this.playing = true; this.play.textContent = 'Pause replay';
      this.started = performance.now() - at * 1000;
      const tick = () => {
        if (!this.playing || this.closed) return;
        const through = Math.min(this.duration, (performance.now() - this.started) / 1000);
        const output = [];
        while (this.cursor < this.events.length && this.events[this.cursor][0] <= through)
          output.push(this.events[this.cursor++][2]);
        if (output.length) this.term.write(output.join(''));
        this.seek.value = String(through); this.showTime(through);
        if (through >= this.duration) this.pause();
        else this.animation = requestAnimationFrame(tick);
      };
      this.animation = requestAnimationFrame(tick);
    }
    pause() { this.playing = false; this.play.textContent = 'Replay'; cancelAnimationFrame(this.animation); }
    destroy() {
      if (this.closed) return;
      this.closed = true; this.pause(); clearTimeout(this.timer);
      window.removeEventListener('warehouse-theme-change', this.onTheme);
      this.resize.disconnect(); this.term.dispose();
    }
  };
})();
