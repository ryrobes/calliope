"""Shared map lifecycle evidence for captures and the authenticated desktop."""

RUNTIME = r"""
(() => {
  const records = new Map();
  function failure(error) {
    const raw = String(error?.message || error || '');
    const code = /webgl|context lost/i.test(raw) ? 'map_webgl_error'
      : /(?:layers|sources)[.\[]|expression|paint\.|layout\./i.test(raw) ? 'map_style_error'
      : 'map_resource_error';
    // Only style-validation messages are useful to quote. Resource errors can
    // include signed URLs/credentials: expose their category, never their text.
    const detail = raw.replace(/(?:https?|mapbox):\/\/[^\s"'<>]+/gi, '[url]')
      .replace(/(?:access[_-]?token|api[_-]?key|authorization|cookie|password|secret)\s*[:=]\s*[^\s,;]+/gi, '[redacted]')
      .replace(/\b(?:pk|sk)\.[\w.-]+/g, '[redacted]').slice(0, 400);
    return {code, message: code === 'map_style_error' ? detail
      : code === 'map_webgl_error' ? 'Map graphics context failed. Restore WebGL and reload.'
      : 'Map resources failed to load. Check connection and map configuration, then reload.'};
  }
  function record(node, map) {
    let state = records.get(node);
    if (!state || (map && state.map !== map)) {
      state = {node, map, errors: [], expected: new Set()}; records.set(node, state);
    }
    return state;
  }
  function fail(node, error) {
    const state = record(node), issue = failure(error);
    if (!state.errors.some(e => e.code === issue.code && e.message === issue.message)) {
      state.errors.push(issue); state.errors = state.errors.slice(-8);
    }
    return issue;
  }
  function attach(node, map) {
    const state = record(node, map);
    const add = map.addLayer, remove = map.removeLayer;
    map.addLayer = function(layer, ...args) {
      if (typeof layer?.id === 'string') state.expected.add(layer.id);
      return add.call(this, layer, ...args);
    };
    map.removeLayer = function(id, ...args) {
      const result = remove.call(this, id, ...args); state.expected.delete(id); return result;
    };
    map.on('remove', () => { if (records.get(node) === state) records.delete(node); });
  }
  function diagnostics() {
    const maps = [...records.values()].filter(s => s.node?.isConnected).slice(0, 32).map(s => {
      let missing = [], loaded = false;
      try { loaded = !!s.map?.loaded(); missing = [...s.expected].filter(id => !s.map?.getLayer(id)); } catch {}
      return {loaded, expected_layers: s.expected.size, missing_layer_count: missing.length,
        errors: s.errors.map(e => ({...e})), healthy: !s.errors.length && !missing.length};
    });
    return {maps, healthy: maps.every(m => m.healthy)};
  }
  window.__rvbbitMapDiagnostics = {attach, fail, diagnostics};
})();
"""
