/* Shared picker: keep selected methods outside the draft; serialize at submission. */
(() => {
  let dialog, search, results, status, retry, request, hooks, items = [];
  const cleanLabel = value => String(value || 'Playbook').replace(/[\[\]|\r\n]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 220);
  function render() {
    results.replaceChildren();
    const needle = search.value.trim().toLocaleLowerCase();
    const visible = items.filter(item => `${item.playbook.title} ${item.playbook.synopsis || ''}`.toLocaleLowerCase().includes(needle));
    status.textContent = visible.length ? `${visible.length} playbook${visible.length === 1 ? '' : 's'}${items.length === 200 ? ' · most recent 200' : ''}` : needle ? 'No matching playbooks.' : 'No playbooks yet. Saved and shared playbooks will appear here.';
    for (const item of visible) {
      const book = item.playbook;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'playbook-picker-item';
      const title = document.createElement('strong'); title.textContent = book.title || 'Playbook';
      const meta = document.createElement('small'); meta.textContent = `v${book.version} · ${book.approved ? 'Approved' : 'Draft'}${item.is_owner ? ' · Yours' : ' · Shared'}`;
      const synopsis = document.createElement('span'); synopsis.textContent = book.synopsis || '';
      button.append(title, meta, synopsis);
      button.addEventListener('click', () => {
        const ref = `${book.id}@${book.version}`;
        if (window.CalliopeObjectEditor?.parseObjectMarkers(hooks.getValue()).some(item => item.kind === 'playbook' && item.ref_id === ref)) {
          dialog.close(); return;
        }
        const selection = {kind: 'playbook', ref_id: ref, label: `${cleanLabel(book.title)} · v${book.version}`};
        if (hooks.select(selection) === false) {
          status.textContent = 'The playbook could not be added. Check that the message is editable and has room.';
          return;
        }
        dialog.close();
      });
      results.append(button);
    }
  }
  async function load() {
    request?.abort();
    const controller = new AbortController(); request = controller;
    status.textContent = 'Loading playbooks…'; retry.hidden = true;
    try {
      const response = await fetch('/api/calliope/playbooks?limit=200', {signal: controller.signal});
      if (!response.ok) throw new Error('Playbooks could not be loaded. Please try again.');
      const data = await response.json();
      if (controller.signal.aborted) return;
      items = (Array.isArray(data.playbooks) ? data.playbooks : []).filter(item => {
        const book = item?.playbook;
        return book && !book.archived && /^[a-f0-9-]{36}$/i.test(book.id) && Number.isInteger(book.version) && book.version > 0;
      });
      render();
    } catch (error) {
      if (controller.signal.aborted) return;
      status.textContent = error.message || 'Playbooks could not be loaded.'; retry.hidden = false;
    }
  }
  function mount() {
    dialog = document.createElement('dialog'); dialog.id = 'playbook-picker'; dialog.className = 'playbook-picker';
    dialog.setAttribute('aria-labelledby', 'playbook-picker-title');
    dialog.innerHTML = `<header><div><h2 id="playbook-picker-title">Add a Playbook</h2><p>Choose a method to attach when you send.</p></div><button type="button" data-close aria-label="Close playbook picker">×</button></header><label class="playbook-picker-search">Search playbooks<input type="search" placeholder="Find a playbook…" autocomplete="off"></label><p class="playbook-picker-status" role="status" aria-live="polite"></p><button type="button" data-retry hidden>Try again</button><div class="playbook-picker-results"></div>`;
    document.body.append(dialog);
    search = dialog.querySelector('input'); results = dialog.querySelector('.playbook-picker-results'); status = dialog.querySelector('[role=status]'); retry = dialog.querySelector('[data-retry]');
    search.addEventListener('input', render); retry.addEventListener('click', load);
    dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => { if (event.target === dialog && (event.clientX < dialog.getBoundingClientRect().left || event.clientX > dialog.getBoundingClientRect().right || event.clientY < dialog.getBoundingClientRect().top || event.clientY > dialog.getBoundingClientRect().bottom)) dialog.close(); });
    dialog.addEventListener('close', () => { request?.abort(); hooks?.focus?.(); });
  }
  function withReferences(text, selections = []) {
    const existing = new Set((window.CalliopeObjectEditor?.parseObjectMarkers(text) || [])
      .filter(item => item.kind === 'playbook').map(item => item.ref_id));
    const markers = [];
    for (const item of selections) {
      if (existing.has(item.ref_id)) continue;
      existing.add(item.ref_id);
      markers.push(`[[playbook:${item.ref_id}|${cleanLabel(item.label)}]]`);
    }
    return [text, ...markers].filter(Boolean).join('\n\n');
  }
  function renderSelections(host, selections, remove) {
    if (!host) return;
    host.replaceChildren(); host.hidden = !selections.length;
    for (const item of selections) {
      const chip = document.createElement('span'); chip.className = 'playbook-selection';
      const label = document.createElement('span'); label.textContent = `≋ ${item.label}`;
      const button = document.createElement('button'); button.type = 'button'; button.textContent = '×';
      button.setAttribute('aria-label', `Remove playbook ${item.label}`);
      button.addEventListener('click', () => remove(item.ref_id));
      chip.append(label, button); host.append(chip);
    }
  }
  window.CalliopePlaybookPicker = {withReferences, renderSelections, open(options) {
    if (!dialog) mount();
    if (dialog.open) return;
    hooks = options; items = []; search.value = ''; results.replaceChildren();
    dialog.showModal(); search.focus(); load();
  }};
})();
