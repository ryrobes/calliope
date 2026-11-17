/* An isolated image-edit workspace; never submits a chat message. */
(() => {
  'use strict';
  window.CalliopeVisualIntent = {
    create(hooks) {
      const $ = id => document.getElementById(id);
      const dialog = $('markup-dialog');
      const target = $('visual-intent-target');
      const prompt = $('visual-intent-prompt');
      const rail = $('visual-intent-rail');
      const status = $('visual-intent-status');
      const generate = $('visual-intent-generate');
      const save = $('visual-intent-save');
      const refresh = $('visual-intent-refresh');
      const memories = new Map();
      let workspace = null, active = false, loading = false, selectionToken = 0, timer = null;
      const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
      const endpoint = w => `/api/calliope/sessions/${w.sessionId}/visual-intent`;
      const chosen = w => w.drafts.find(d => d.id === w.selectedId);
      const feedback = message => { status.textContent = message; };
      const relevant = w => workspace === w && active;
      function remember() {
        if (!workspace || !active) return;
        workspace.prompt = prompt.value;
        if (hooks.ready()) workspace.strokes.set(workspace.selectedId || `source:${workspace.source.id}`, hooks.strokes());
      }
      function chrome() {
        dialog.classList.add('visual-intent-editor');
        $('visual-intent-source').hidden = false;
        $('visual-intent-controls').hidden = false;
        $('markup-title').textContent = 'Visual intent';
        $('visual-intent-meta').textContent = workspace?.meta || '';
      }
      function render() {
        if (!workspace || !active) return;
        chrome();
        const w = workspace, selected = chosen(w);
        const running = w.drafts.some(d => d.status === 'running');
        rail.innerHTML = w.drafts.map((d, i) => `<button type="button" data-draft="${esc(d.id)}" class="visual-intent-variant ${d.id === w.selectedId ? 'selected' : ''}" aria-pressed="${d.id === w.selectedId}" title="${esc(d.prompt || 'Original capture')}">${d.image_url ? `<img src="${esc(d.image_url)}" alt="${i ? `Edit ${i}` : 'Original'}">` : '<span class="visual-intent-pending">' + (d.status === 'failed' ? 'Failed' : 'Creating…') + '</span>'}<span>${d.status === 'original' ? (i ? 'Capture' : 'Original') : `Edit ${i}`}${d.saved_surface_id ? ' · saved' : ''}</span></button>`).join('');
        if (!w.drafts.some(d => d.status === 'original' && d.source_surface_id === w.source.id)) {
          rail.insertAdjacentHTML('beforeend', `<button type="button" data-current-capture class="visual-intent-variant ${w.selectedId ? '' : 'selected'}"><img src="${esc(w.source.payload.image_url)}" alt="Current capture"><span>${w.drafts.length ? 'Fresh capture' : 'Original'}</span></button>`);
        }
        generate.disabled = loading || running || !hooks.ready() || !prompt.value.trim();
        generate.textContent = running ? 'Creating image…' : 'Generate edit';
        save.disabled = loading || selected?.status !== 'ready' || Boolean(selected?.saved_surface_id);
        save.textContent = selected?.saved_surface_id ? 'Saved to Stage' : 'Save to Stage';
        refresh.disabled = loading || running || !w.artifactId;
        target.disabled = loading;
      }
      function showImage(surface, strokes = []) {
        const token = ++selectionToken;
        loading = true;
        hooks.show(surface, () => {
          if (token !== selectionToken || !active) return;
          hooks.restore(strokes);
          loading = false;
          render();
        });
        chrome();
        render();
      }
      function select(draft, preserve = true) {
        if (!draft?.image_url) {
          feedback(draft?.error || 'This image is still being generated. You can close the editor while it runs.');
          return;
        }
        if (preserve) remember();
        workspace.selectedId = draft.id;
        showImage({id: draft.id, title: 'Visual intent', payload: {image_url: draft.image_url}}, workspace.strokes.get(draft.id) || []);
        feedback(draft.status === 'original' ? 'Original reference. Describe a change to create a new version.' : draft.saved_surface_id ? 'Saved as a visual reference. Select it on Stage when you want Calliope to use it.' : 'Editing this version creates another image. The live page stays as it is.');
      }
      async function load(w) {
        const data = await hooks.api(endpoint(w));
        w.drafts = data.drafts.filter(d => d.source_surface_id === w.source.id || (w.artifactSlug && d.source_artifact_slug === w.artifactSlug && Number(d.source_artifact_version) === Number(w.artifactVersion)) || w.sourceIds.has(d.source_surface_id));
      }
      function schedule(w) {
        clearTimeout(timer);
        if (!active || workspace !== w || !w.drafts.some(d => d.status === 'running')) return;
        timer = setTimeout(async () => {
          try {
            const before = new Set(w.drafts.filter(d => d.status === 'running').map(d => d.id));
            await load(w);
            if (!relevant(w)) return;
            const finished = w.drafts.find(d => before.has(d.id) && d.status !== 'running');
            render();
            if (finished?.status === 'ready') {
              // Only replace the canvas if the user has not moved to another branch
              // or drawn more while this edit was running.
              if (w.selectedId === finished.parent_id && JSON.stringify(hooks.strokes()) === w.submittedStrokes && prompt.value === w.submittedPrompt) {
                prompt.value = ''; w.prompt = ''; select(finished, false);
              } else feedback('Your new image is ready in the version rail.');
            } else if (finished) feedback(finished.error);
          } catch (error) { if (relevant(w)) feedback(`Could not check generation: ${error.message}. Checking again…`); }
          schedule(w);
        }, 2500);
      }
      async function open(surfaceId = null, force = false) {
        remember();
        const context = hooks.context();
        if (!context.sessionId || context.readOnly) { hooks.toast('Open a notebook you own to explore visual changes.'); return; }
        const available = context.surfaces.map(hooks.source).filter(Boolean);
        const targets = available.filter(s => s.kind === 'artifact' ? hooks.isCurrent(s) : (!hooks.isCapture(s) || !hooks.artifactForCapture(s)));
        if (!targets.length) { hooks.toast('Publish a page or add an image before opening Visual Intent.'); return; }
        const defaultTarget = targets.find(s => s.kind === "artifact") || targets[0];
        const initial = surfaceId || context.selectedId || defaultTarget.id;
        let source = available.find(s => s.id === initial) || defaultTarget;
        const requested = source;
        const sourceArtifact = source.kind === 'artifact' ? source : hooks.artifactForCapture(source);
        if (!targets.some(s => s.id === source.id)) targets.unshift(source);
        target.innerHTML = targets.map(s => `<option value="${esc(s.id)}">${esc(s.title)}${s.artifact_version ? ` · v${s.artifact_version}` : ''}</option>`).join('');
        target.value = source.id;
        active = true; loading = true;
        const opening = ++selectionToken;
        dialog.classList.add('visual-intent-editor');
        hooks.clear();
        if (!dialog.open) dialog.showModal();
        chrome();
        generate.disabled = save.disabled = refresh.disabled = target.disabled = true;
        rail.innerHTML = '';
        feedback(source.kind === 'artifact' ? 'Preparing the page capture…' : 'Opening image…');
        try {
          if (source.kind === 'artifact' || (force && sourceArtifact)) source = await hooks.capture(sourceArtifact.id, force);
          if (!active || opening !== selectionToken) return;
          const key = `${context.sessionId}:${sourceArtifact?.id || requested.id}`;
          let w = memories.get(key);
          if (!w) {
            w = {sessionId: context.sessionId, source, sourceIds: new Set([source.id]), artifactSlug: sourceArtifact?.artifact_slug, artifactVersion: sourceArtifact?.artifact_version, artifactId: sourceArtifact?.id, targetId: requested.id, drafts: [], selectedId: null, prompt: '', strokes: new Map(),
              meta: `${sourceArtifact?.title || source.title}${sourceArtifact?.artifact_version ? ` · v${sourceArtifact.artifact_version}` : ''} · ${source.created_at ? new Date(source.created_at).toLocaleString() : 'Captured now'}`};
            memories.set(key, w);
          }
          if (force) {
            w.source = source; w.sourceIds.add(source.id); w.selectedId = null; w.originalRequestId = null;
            w.meta = `${sourceArtifact?.title || source.title}${sourceArtifact?.artifact_version ? ` · v${sourceArtifact.artifact_version}` : ''} · Fresh capture ${new Date().toLocaleTimeString()}`;
          }
          workspace = w;
          await load(w);
          if (!w.prompt && !w.selectedId) {
            const last = w.drafts.at(-1);
            if (last?.status === 'failed' || last?.status === 'running') w.prompt = last.prompt;
          }
          if (!active || opening !== selectionToken) return;
          prompt.value = w.prompt;
          const selected = force ? null : chosen(w) || [...w.drafts].reverse().find(d => d.image_url);
          if (selected) select(selected, false);
          else { showImage(w.source, w.strokes.get(`source:${w.source.id}`) || []); feedback('Mark up the image or describe a change, then generate an edit.'); }
          schedule(w);
        } catch (error) {
          if (active && opening === selectionToken) { loading = false; feedback(error.message); target.disabled = false; }
        }
      }
      async function generateEdit() {
        if (generate.disabled || !workspace) return;
        remember();
        const w = workspace;
        loading = true; render();
        feedback('Sending your image and instructions…');
        try {
          const drawing = hooks.drawing();
          let parent = chosen(w);
          if (!parent) {
            w.originalRequestId ||= crypto.randomUUID();
            const result = await hooks.api(endpoint(w), {method: 'POST', body: JSON.stringify({id: w.originalRequestId, source_surface_id: w.source.id, image: drawing.clean})});
            parent = result.draft; w.drafts.push(parent); w.selectedId = parent.id;
            w.strokes.set(parent.id, w.strokes.get(`source:${w.source.id}`) || []);
          }
          // Preserve request identity across a lost HTTP response; same click can
          // be retried without spending on a second provider call.
          const signature = JSON.stringify([parent.id, prompt.value.trim(), drawing.annotated]);
          if (w.request?.signature !== signature) w.request = {signature, id: crypto.randomUUID()};
          w.submittedPrompt = prompt.value;
          w.submittedStrokes = JSON.stringify(hooks.strokes());
          const result = await hooks.api(endpoint(w), {method: 'POST', body: JSON.stringify({id: w.request.id, parent_id: parent.id, prompt: prompt.value.trim(), annotation: drawing.annotated})});
          w.request = null;
          w.drafts = [...w.drafts.filter(d => d.id !== result.draft.id), result.draft];
          if (relevant(w)) { feedback('Creating an image. You can close this editor and return later.'); schedule(w); }
        } catch (error) { if (relevant(w)) feedback(error.message); }
        finally { if (relevant(w)) { loading = false; render(); } }
      }
      async function saveImage() {
        const w = workspace, draft = chosen(w);
        if (save.disabled || !draft) return;
        loading = true; render();
        try {
          const result = await hooks.api(`${endpoint(w)}/${draft.id}/save`, {method: 'POST', body: '{}'});
          draft.saved_surface_id = result.surface.id;
          hooks.saved(result.surface, w.sessionId);
          if (relevant(w)) feedback('Saved to Stage as a visual reference. Select or pin it when you want to use it in chat.');
        } catch (error) { if (relevant(w)) feedback(error.message); }
        finally { if (relevant(w)) { loading = false; render(); } }
      }
      generate.addEventListener('click', generateEdit);
      save.addEventListener('click', saveImage);
      prompt.addEventListener('input', () => { if (workspace) workspace.prompt = prompt.value; render(); });
      target.addEventListener('change', () => void open(target.value));
      refresh.addEventListener('click', () => void open(workspace.targetId, true));
      rail.addEventListener('click', event => {
        if (event.target.closest('[data-current-capture]') && !loading) {
          remember(); workspace.selectedId = null;
          showImage(workspace.source, workspace.strokes.get(`source:${workspace.source.id}`) || []);
          return;
        }
        const button = event.target.closest('[data-draft]');
        if (button && !loading) select(workspace.drafts.find(d => d.id === button.dataset.draft));
      });
      return {
        open,
        close() {
          remember(); active = false; loading = false; ++selectionToken; clearTimeout(timer);
          dialog.classList.remove('visual-intent-editor');
          $('visual-intent-source').hidden = true; $('visual-intent-controls').hidden = true;
        },
        changed() { if (active) render(); },
      };
    },
  };
})();
